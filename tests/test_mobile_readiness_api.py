"""Offline readiness transport and real ASGI tests.

ReadinessFakeSupabase is reusable by the browser harness as its cloud boundary.
It is NOT PostgreSQL proof; test_mobile_readiness_sql runs the actual migration.
No fixture may access a network, external employer, mailbox, or paid provider.
"""
import asyncio
import copy
import hashlib
import json
import os
import socket
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from jobagent.mobile.eligibility_review import decode_review, job_fingerprint, review_id, REVIEW_QUESTION
from jobagent.mobile.readiness import (PacketReviewRequest, bind_preparation_context, capture_preparation_context,
    checked_readiness, packet_readiness, project_application_state, review_packet)
from jobagent.mobile.repository import MobileRepository
from test_mobile_api import FakeSupabase, SETTINGS, TOKENS, USER_A, USER_B


def hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class ReadinessFakeSupabase(FakeSupabase):
    """Contract fixture only; preserves existing fake Auth/Storage/REST behavior."""
    def __init__(self):
        super().__init__()
        self.packet_contexts = {}
        self.packet_reviews = {}

    def own(self, table, user, item_id=None):
        return next((r for r in self.tables[table] if r['user_id'] == user and (item_id is None or r.get('id') == item_id)), None)

    def snapshot(self, user, job_id, source_id):
        job, source = self.own('jobs', user, job_id), self.own('resumes', user, source_id)
        if not job: return {'error': 'job_missing'}
        if not source or ('resumes', source['storage_path']) not in self.objects: return {'error': 'source_missing'}
        context = self.own('candidate_context', user)
        if not (context or {}).get('career_text') or not job.get('description'): return {'error': 'facts_missing'}
        content = self.objects[('resumes', source['storage_path'])]
        answers = [r for r in self.tables['mobile_answers'] if r['user_id'] == user and r['scope'] in ('profile', 'job:' + job_id)]
        questions = [r for r in self.tables['mobile_questions'] if r['user_id'] == user and r.get('job_id') in (job_id, None) and r['status'] == 'answered']
        if len(answers) + len(questions) > 100: return {'error': 'too_many_records'}
        source_hash = hashlib.sha256(content).hexdigest()
        def fields(r): return {k: v for k, v in (r or {}).items() if k not in ('created_at', 'updated_at')}
        facts = dict(profile=fields(self.own('profiles', user)), career=fields(context),
            preferences=fields(self.own('job_preferences', user)),
            job={k: job.get(k) for k in ('id', 'title', 'company_name', 'description', 'source', 'source_job_id', 'source_url', 'location_text', 'workplace_type', 'employment_type')},
            source_id=source_id, source_sha256=source_hash,
            answers=sorted(answers, key=lambda r: r['id']), questions=sorted(questions, key=lambda r: r['id']))
        return {'user_id': user, 'job_id': job_id, 'resume_id': source_id,
            'context_fingerprint': hash_json(facts), 'capture_version': hash_json(facts),
            'source': {'storage_path': source['storage_path'], 'byte_size': len(content), 'sha256': source_hash}}

    def readiness(self, user, job_id):
        job = self.own('jobs', user, job_id)
        if not job: return {'error': 'job_missing'}
        packets = []
        for (owner, run_id), c in self.packet_contexts.items():
            if owner != user or c['job_id'] != job_id: continue
            run = self.own('model_runs', user, run_id)
            if not run or run['status'] != 'succeeded': continue
            summary = run.get('output_summary', {})
            ids = summary.get('artifact_ids', [])
            if not 2 <= len(ids) <= 6 or len(set(ids)) != len(ids): continue
            rows = [self.own('artifacts', user, i) for i in ids]
            if any(not r or r.get('job_id') != job_id or r.get('resume_id') != c['resume_id'] for r in rows): continue
            current = self.snapshot(user, job_id, c['resume_id'])
            hashes = {d['artifact_id']: d['sha256'] for d in summary.get('documents', [])}
            if set(hashes) != set(ids): continue
            for fmt, mime in [('pdf', 'application/pdf'), ('docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')]:
                pair = [r for kind in ('tailored_resume', 'cover_letter') for r in rows if r['kind'] == kind and r['mime_type'] == mime]
                if len(pair) != 2 or [r['kind'] for r in pair] != ['tailored_resume', 'cover_letter']: continue
                if any(('application-artifacts', r['storage_path']) not in self.objects for r in rows): continue
                fp = hash_json([c, fmt, rows, hashes, [hashlib.sha256(self.objects[('application-artifacts', r['storage_path'])]).hexdigest() for r in rows]])
                matches = current.get('context_fingerprint') == c['context_fingerprint']
                packets.append(dict(key=run_id+':'+fmt, run_id=run_id, resume_id=c['resume_id'], variant=c['variant'], format=fmt,
                    generated_at=run['completed_at'], current=matches, issue=None if matches else current.get('error', 'generation_context_changed'),
                    context_fingerprint=c['context_fingerprint'], packet_fingerprint=fp, source_sha256=c['resume_sha256'],
                    artifacts=[{k: v for k, v in {**r, 'sha256': hashes[r['id']]}.items() if k not in ('storage_path', 'user_id', 'model_provider', 'model_name', 'updated_at')} for r in pair]))
        er = decode_review(self.own('mobile_answers', user, review_id(user, job_id)), job, user)
        pending = sum(q['user_id'] == user and q.get('job_id') in (job_id, None) and q['status'] != 'answered' for q in self.tables['mobile_questions'])
        prior = self.packet_reviews.get((user, job_id))
        reviewed = bool(prior and any(p['current'] and p['packet_fingerprint'] == prior['packet_fingerprint'] for p in packets))
        if pending: reason = 'pending_questions'
        elif not er or er['status'] != 'eligible': reason = 'eligibility_required'
        elif prior and not reviewed: reason = 'review_stale'
        else: reason = None if reviewed else 'packet_review_required'
        app = next((a for a in self.tables['applications'] if a['user_id'] == user and a['job_id'] == job_id), {})
        ready = bool(app.get('status') == 'ready' and reviewed and reason is None)
        status = 'draft' if app.get('status', 'draft') == 'ready' and not ready else app.get('status', 'draft')
        return dict(user_id=user, job_id=job_id, version='packet-v1', packets=packets, review={**prior, 'current': reviewed and reason is None} if prior else None,
                    ready=ready, reason=reason, pending_questions=pending, application_status=status,
                    recorded_status=app.get('status'), application_id=app.get('id'))

    def __call__(self, request):
        name = request.url.path.removeprefix('/rest/v1/rpc/')
        if name not in {'mobile_packet_context', 'mobile_bind_packet_context', 'mobile_packet_readiness', 'mobile_review_packet'}:
            return super().__call__(request)
        self.requests.append(request)
        assert request.url.host == 'mobile.example.test' and request.headers.get('apikey') == SETTINGS.publishable_key
        user = TOKENS.get(request.headers.get('authorization', '').removeprefix('Bearer '))
        if not user: return httpx.Response(401)
        if self.fault:
            failure = self.fault(request)
            if failure is not None: return failure
        body = json.loads(request.content)
        assert not any('user' in k for k in body), 'Never pass owner selectors to readiness RPCs'
        if name == 'mobile_packet_context':
            value = self.snapshot(user, body['p_job_id'], body['p_resume_id'])
        elif name == 'mobile_bind_packet_context':
            run = self.own('model_runs', user, body['p_run_id'])
            if not run: return httpx.Response(404)
            value = self.snapshot(user, run['job_id'], body['p_resume_id'])
            if any(body['p_' + k] != value.get(k) for k in ('context_fingerprint', 'capture_version')):
                return httpx.Response(409)
            self.packet_contexts[(user, run['id'])] = dict(job_id=run['job_id'], resume_id=body['p_resume_id'], variant=body['p_variant'],
                context_fingerprint=value['context_fingerprint'], resume_sha256=body['p_resume_sha256'])
            value = dict(user_id=user, run_id=run['id'], bound=True)
        elif name == 'mobile_packet_readiness':
            value = self.readiness(user, body['p_job_id'])
        else:
            state = self.readiness(user, body['p_job_id'])
            packet = next((p for p in state.get('packets', []) if p['current'] and p['packet_fingerprint'] == body['p_packet_fingerprint']
                and p['run_id'] == body['p_run_id'] and p['artifacts'][0]['id'] == body['p_resume_artifact_id'] and p['artifacts'][1]['id'] == body['p_letter_artifact_id']), None)
            if not packet or body['p_confirmed'] is not True or state['reason'] in ('pending_questions', 'eligibility_required'):
                return httpx.Response(409)
            app = next((a for a in self.tables['applications'] if a['user_id'] == user and a['job_id'] == body['p_job_id']), None)
            if app and app['status'] not in ('draft', 'ready'): return httpx.Response(409)
            self.packet_reviews[(user, body['p_job_id'])] = dict(id=str(uuid4()), run_id=body['p_run_id'],
                resume_artifact_id=body['p_resume_artifact_id'], letter_artifact_id=body['p_letter_artifact_id'],
                packet_fingerprint=body['p_packet_fingerprint'], reviewed_at=datetime.now(timezone.utc).isoformat(), current=True)
            if app: app.update(status='ready', updated_at=datetime.now(timezone.utc).isoformat())
            else: app = self.add('applications', user, job_id=body['p_job_id'], status='ready', applied_at=None, notes=None)
            value = dict(user_id=user, application=app, readiness=self.readiness(user, body['p_job_id']))
        return httpx.Response(200, json=value)


class ReadinessAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        for method in ('connect', 'connect_ex', 'sendto'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('No network')))
        self.remote = ReadinessFakeSupabase()
        self.remote.add('candidate_context', USER_A, career_text='Designed accessible products.')
        self.job = self.remote.job(USER_A)
        self.source = self.remote.resume(USER_A)
        self.source['byte_size'] = len(self.remote.objects[('resumes', self.source['storage_path'])])
        self.remote.add('mobile_answers', USER_A, id=review_id(USER_A, self.job['id']), scope='job:'+self.job['id'], question=REVIEW_QUESTION,
            answer=json.dumps(dict(status='eligible', confirmed=True, reason='Synthetic self-report', job_fingerprint=job_fingerprint(self.job))))
        self.token = next(t for t, user in TOKENS.items() if user == USER_A)
        self.client = httpx.AsyncClient(base_url='https://mobile.example.test', transport=httpx.MockTransport(self.remote),
            headers={'apikey': SETTINGS.publishable_key, 'authorization': 'Bearer '+self.token})
        self.addAsyncCleanup(self.client.aclose)
        self.repo = MobileRepository(self.client, USER_A)

    async def generated(self):
        snapshot = await capture_preparation_context(self.repo, self.job['id'], self.source['id'])
        run = self.remote.add('model_runs', USER_A, job_id=self.job['id'], operation='prepare_documents', status='running', input_summary={'context_sha256': 'a'*64})
        await bind_preparation_context(self.repo, run['id'], self.source['id'], 'role_aligned', snapshot)
        rows = []
        for kind in ('tailored_resume', 'cover_letter'):
            item = str(uuid4()); path = USER_A+'/'+item+'.pdf'; content = (kind+' synthetic bytes').encode()
            self.remote.objects[('application-artifacts', path)] = content
            rows.append(self.remote.add('artifacts', USER_A, id=item, job_id=self.job['id'], resume_id=self.source['id'], kind=kind,
                storage_path=path, filename=kind+'.pdf', mime_type='application/pdf', byte_size=len(content)))
        run.update(status='succeeded', completed_at=datetime.now(timezone.utc).isoformat(),
            output_summary={'artifact_ids': [r['id'] for r in rows], 'documents': [dict(artifact_id=r['id'],
                sha256=hashlib.sha256(self.remote.objects[('application-artifacts', r['storage_path'])]).hexdigest()) for r in rows]})
        state = await packet_readiness(self.repo, self.job['id']); p = state['packets'][0]
        return PacketReviewRequest(run_id=run['id'], resume_artifact_id=rows[0]['id'], letter_artifact_id=rows[1]['id'],
            packet_fingerprint=p['packet_fingerprint'], confirmed=True)

    async def test_capture_bind_review_and_returning_status_projection(self):
        body = await self.generated()
        result = await review_packet(self.repo, self.job['id'], body)
        self.assertTrue(result['readiness']['ready'])
        self.assertIsNone(result['application']['applied_at'])
        jobs, apps = await project_application_state(self.repo, [self.job], self.remote.tables['applications'])
        self.assertEqual((jobs[0]['status'], apps[0]['status']), ('ready', 'ready'))
        self.remote.own('candidate_context', USER_A)['career_text'] = 'Changed fact'
        jobs, apps = await project_application_state(self.repo, [self.job], self.remote.tables['applications'])
        self.assertNotEqual(jobs[0]['status'], 'ready'); self.assertEqual(apps[0]['status'], 'draft')
        self.assertEqual(apps[0]['recorded_status'], 'ready')
        self.assertEqual(self.remote.tables['applications'][0]['status'], 'ready')
        reads = [r for r in self.remote.requests if r.url.path.startswith('/storage/') and r.method == 'GET']
        self.assertTrue(all('reconcile_nonce' in r.url.params for r in reads))

    async def test_new_answer_invalidates_generation_no_review_write(self):
        body = await self.generated()
        self.remote.add('mobile_questions', USER_A, job_id=self.job['id'], prompt='Availability', answer='Later', status='answered')
        with self.assertRaises(HTTPException) as raised: await review_packet(self.repo, self.job['id'], body)
        self.assertEqual(raised.exception.status_code, 409); self.assertFalse(self.remote.packet_reviews)

    async def test_external_status_survives_stale_context_and_no_new_review(self):
        body = await self.generated()
        self.remote.add('applications', USER_A, job_id=self.job['id'], status='submitted', notes='Manually applied externally')
        with self.assertRaises(HTTPException): await review_packet(self.repo, self.job['id'], body)
        jobs, apps = await project_application_state(self.repo, [self.job], self.remote.tables['applications'])
        self.assertEqual((jobs[0]['status'], apps[0]['status']), ('applied', 'submitted'))
        self.assertFalse(self.remote.packet_reviews)

    async def test_storage_tampering_and_foreign_pair_are_denied(self):
        body = await self.generated()
        artifact = self.remote.own('artifacts', USER_A, str(body.resume_artifact_id))
        self.remote.objects[('application-artifacts', artifact['storage_path'])] = b'replaced'
        with self.assertRaises(HTTPException): await review_packet(self.repo, self.job['id'], body)
        self.assertFalse(self.remote.packet_reviews)
        bad = body.model_copy(update={'resume_artifact_id': uuid4()})
        with self.assertRaises(HTTPException): await review_packet(self.repo, self.job['id'], bad)

    async def test_unconfirmed_write_no_automatic_retries_or_sensitive_error(self):
        body = await self.generated()
        count = 0
        def fault(request):
            nonlocal count
            if request.url.path.endswith('/mobile_review_packet'):
                count += 1
                raise httpx.ReadTimeout('SYNTHETIC_SECRET_DIAGNOSTIC', request=request)
        self.remote.fault = fault
        with self.assertRaises(HTTPException) as raised: await review_packet(self.repo, self.job['id'], body)
        self.assertEqual(raised.exception.status_code, 503); self.assertEqual(count, 1)
        self.assertEqual(raised.exception.detail['code'], 'packet_review_unconfirmed')
        self.assertNotIn('SYNTHETIC_SECRET', str(raised.exception.detail))

    async def test_auth_limits_redirect_missing_migration_and_invalid_responses(self):
        await self.generated()
        for response, expected in [(httpx.Response(403), 403), (httpx.Response(429, headers={'Retry-After': '120'}), 429),
            (httpx.Response(404), 404), (httpx.Response(302, headers={'Location': 'https://external.invalid'}), 503),
            (httpx.Response(200, content=b'[]'), 503), (httpx.Response(200, content=b'{"user_id":"x","user_id":"y"}'), 503)]:
            self.remote.fault = lambda request, r=response: r if request.url.path.endswith('/mobile_packet_readiness') else None
            with self.assertRaises(HTTPException) as raised: await packet_readiness(self.repo, self.job['id'])
            self.assertEqual(raised.exception.status_code, expected)

    async def test_bounded_projections_preserve_workspace_and_known_external_records(self):
        jobs = [{**self.job, 'id': str(uuid4()), 'status': 'ready'} for _ in range(200)]
        apps = [dict(id=str(uuid4()), user_id=USER_A, job_id=jobs[0]['id'], status='submitted'),
                dict(id=str(uuid4()), user_id=USER_A, job_id=jobs[1]['id'], status='ready')]
        projected_jobs, projected_apps = await project_application_state(self.repo, jobs, apps)
        self.assertEqual(len(projected_jobs), 200)
        self.assertEqual(projected_jobs[0]['status'], 'applied')
        self.assertTrue(all(j['status'] != 'ready' for j in projected_jobs))
        self.assertEqual(projected_apps[1]['status'], 'draft')
        self.assertEqual(projected_apps[1]['recorded_status'], 'ready')
        self.assertTrue(all(j['readiness_unavailable'] == 'capped_workspace' for j in projected_jobs))
        self.assertFalse(self.remote.requests)

    async def test_cross_owner_and_ambiguous_projections_fail_closed(self):
        for jobs, apps in [([{**self.job, 'user_id': USER_B}], []), ([self.job]*2, [])]:
            with self.assertRaises(HTTPException): await project_application_state(self.repo, jobs, apps)

    async def test_strict_response_validation_denies_false_ready_and_mixed_pair(self):
        await self.generated()
        valid = await packet_readiness(self.repo, self.job['id'])
        mutations = [lambda s: s.update(user_id=USER_B), lambda s: s.update(ready=True),
            lambda s: s['packets'].append(s['packets'][0]), lambda s: s['packets'][0].update(current='true'),
            lambda s: s['packets'][0]['artifacts'][1].update(resume_id=str(uuid4())),
            lambda s: s['packets'][0].update(packet_fingerprint='not-a-hash'), lambda s: s.update(pending_questions=202)]
        for mutate in mutations:
            result = copy.deepcopy(valid); mutate(result)
            with self.assertRaises((ValueError, TypeError, KeyError)): checked_readiness(result, USER_A, self.job['id'])

    async def test_actual_asgi_receipt_routes_and_bootstrap_detail_are_consistent(self):
        from jobagent.mobile.app import create_app
        body = await self.generated()
        app = create_app(settings=SETTINGS, transport=httpx.MockTransport(self.remote))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://app.test',
                                    headers={'Authorization': 'Bearer '+self.token}) as client:
            prefix = '/api/mobile/jobs/'+self.job['id']
            before = await client.get(prefix+'/readiness')
            self.assertEqual(before.status_code, 200, before.text)
            response = await client.post(prefix+'/review-packet', json=body.model_dump(mode='json'))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['readiness']['ready'])
            bootstrap = await client.get('/api/mobile/bootstrap'); detail = await client.get(prefix)
            self.assertEqual(bootstrap.json()['jobs'][0]['status'], 'ready')
            self.assertEqual(bootstrap.json()['applications'][0]['status'], 'ready')
            self.assertEqual(detail.json()['status'], 'ready')
            self.remote.own('candidate_context', USER_A)['career_text'] = 'New confirmed fact'
            bootstrap = await client.get('/api/mobile/bootstrap'); detail = await client.get(prefix)
            self.assertEqual(bootstrap.json()['applications'][0]['status'], 'draft')
            self.assertNotEqual(detail.json()['status'], 'ready')

    async def test_actual_asgi_direct_ready_gate_and_unconfirmed_boolean(self):
        from jobagent.mobile.app import create_app
        body = await self.generated()
        app = create_app(settings=SETTINGS, transport=httpx.MockTransport(self.remote))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://app.test',
                                    headers={'Authorization': 'Bearer '+self.token}) as client:
            response = await client.post('/api/mobile/applications', json={'job_id': self.job['id'], 'status': 'ready'})
            self.assertEqual(response.status_code, 422)
            response = await client.post('/api/mobile/jobs/'+self.job['id']+'/review-packet',
                json={**body.model_dump(mode='json'), 'confirmed': 'true'})
            self.assertEqual(response.status_code, 422)
            self.assertFalse(self.remote.packet_reviews)

    def test_review_request_cannot_inject_owner_or_truthy_confirmation(self):
        body = dict(run_id=str(uuid4()), resume_artifact_id=str(uuid4()), letter_artifact_id=str(uuid4()), packet_fingerprint='a'*64, confirmed=True)
        for change in ({'user_id': USER_B}, {'confirmed': 'true'}, {'confirmed': 1}, {'status': 'submitted'}):
            with self.assertRaises(ValidationError): PacketReviewRequest.model_validate({**body, **change})


if __name__ == '__main__': unittest.main()
