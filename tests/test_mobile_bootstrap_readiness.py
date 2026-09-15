"""Bootstrap availability regressions: real ASGI, fake cloud, network forbidden.

Synthetic receipts use the existing readiness fixture; no model, email, hosted
database, production request, or employer submission is exercised by this suite.
"""
import asyncio
import copy
import json
import unittest
from uuid import uuid4

import httpx

from jobagent.mobile.app import create_app
from jobagent.mobile.eligibility_review import REVIEW_QUESTION, job_fingerprint, review_id
from jobagent.mobile.readiness import review_packet
from test_mobile_api import SETTINGS, USER_A, USER_B, fake_studio
import test_mobile_readiness_api as fixture


class BootstrapReadinessTests(unittest.IsolatedAsyncioTestCase):
    # Reuse setup/generation helpers, not the original test cases. Setup blocks
    # sockets and clears environment configuration before constructing the app.
    setUp = fixture.ReadinessAPITests.setUp
    generated = fixture.ReadinessAPITests.generated

    async def asyncSetUp(self):
        self.remote.own('profiles', USER_A).update(display_name='Synthetic candidate',
                        onboarding_completed_at='2026-09-15T00:00:00Z')
        self.remote.own('job_preferences', USER_A).update(target_titles=['Designer'],
                        preferred_locations=['London'], remote_preference='open')
        self.body = await self.generated()
        await review_packet(self.repo, self.job['id'], self.body)
        self.failed_job = self.job
        self.job = self.remote.job(USER_A, title='Unaffected reviewed role')
        self.remote.add('mobile_answers', USER_A, id=review_id(USER_A, self.job['id']),
            scope='job:' + self.job['id'], question=REVIEW_QUESTION,
            answer=json.dumps(dict(status='eligible', confirmed=True, reason='Synthetic self-report',
                                   job_fingerprint=job_fingerprint(self.job))))
        await review_packet(self.repo, self.job['id'], await self.generated())
        self.healthy_job = self.job
        self.job = self.failed_job
        self.unrelated_job = self.remote.job(USER_A, title='Unrelated saved role')
        self.external_job = self.remote.job(USER_A, title='Manually submitted role')
        self.remote.add('applications', USER_A, job_id=self.external_job['id'], status='submitted',
                        applied_at='2026-09-14T12:00:00Z', notes='Synthetic external history')
        self.studio = fake_studio()
        app = create_app(settings=SETTINGS, transport=httpx.MockTransport(self.remote), studio=self.studio)
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://app.test',
                                    headers={'Authorization': 'Bearer ' + self.token})
        self.addAsyncCleanup(self.api.aclose)
        self.remote.requests.clear()

    def fault_for_failed_role(self, outcome):
        def fault(request):
            if request.url.path.endswith('/mobile_packet_readiness') and json.loads(request.content)['p_job_id'] == self.job['id']:
                if callable(outcome):
                    return outcome(request)
                return outcome
        return fault

    def readiness_reads(self):
        return [request for request in self.remote.requests if request.url.path.endswith('/mobile_packet_readiness')]

    def assert_no_ai(self):
        for action in ('prepare_documents', 'rank_job', 'answer_chat'):
            getattr(self.studio, action).assert_not_called()
        self.assertFalse(any(request.url.path.endswith('/mobile_reserve_ai_usage') for request in self.remote.requests))

    def assert_isolated(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        value = response.json()
        self.assertEqual(value['profile']['display_name'], 'Synthetic candidate')
        self.assertEqual(value['preferences']['target_titles'], ['Designer'])
        self.assertEqual(len(value['resumes']), 1)
        self.assertEqual(len(value['artifacts']), 4)
        self.assertEqual(len(value['jobs']), 4)
        jobs = {row['id']: row for row in value['jobs']}
        apps = {row['job_id']: row for row in value['applications']}
        self.assertEqual(jobs[self.job['id']]['readiness_unavailable'], 'packet_check_failed')
        self.assertNotEqual(jobs[self.job['id']]['status'], 'ready')
        self.assertEqual(jobs[self.job['id']]['application_status'], 'draft')
        self.assertEqual(apps[self.job['id']]['status'], 'draft')
        self.assertEqual(apps[self.job['id']]['recorded_status'], 'ready')
        self.assertEqual(apps[self.job['id']]['readiness_unavailable'], 'packet_check_failed')
        self.assertNotIn('readiness', apps[self.job['id']])
        self.assertEqual(jobs[self.healthy_job['id']]['status'], 'ready')
        self.assertTrue(apps[self.healthy_job['id']]['readiness']['ready'])
        self.assertNotIn('readiness_unavailable', apps[self.healthy_job['id']])
        self.assertEqual(jobs[self.unrelated_job['id']]['status'], 'new')
        self.assertEqual(jobs[self.external_job['id']]['status'], 'applied')
        self.assertEqual(apps[self.external_job['id']]['status'], 'submitted')
        self.assertEqual(apps[self.external_job['id']]['applied_at'], '2026-09-14T12:00:00Z')
        self.assertNotIn('PRIVATE_UPSTREAM', response.text)
        self.assert_no_ai()
        return value

    async def test_single_rpc_failure_keeps_workspace_and_recovers_without_writes(self):
        original = copy.deepcopy(self.remote.tables)
        healthy = await self.api.get('/api/mobile/bootstrap')
        self.assertEqual(healthy.status_code, 200, healthy.text)
        self.remote.requests.clear()
        self.remote.fault = self.fault_for_failed_role(httpx.Response(503, text='PRIVATE_UPSTREAM'))
        self.assert_isolated(await self.api.get('/api/mobile/bootstrap'))
        self.assertEqual(len(self.readiness_reads()), 2)  # one attempt per Ready row, no retry
        read_rpcs = {'/rest/v1/rpc/mobile_check_access', '/rest/v1/rpc/mobile_packet_readiness'}
        self.assertTrue(all(r.method == 'GET' or (r.method == 'POST' and r.url.path in read_rpcs) for r in self.remote.requests))
        self.assertEqual(self.remote.tables, original)
        self.remote.fault = None
        recovered = await self.api.get('/api/mobile/bootstrap')
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json(), healthy.json())
        self.assertEqual(self.remote.tables, original)
        self.assert_no_ai()

    async def test_timeout_and_rate_limit_are_isolated_without_retry_or_foreign_payload(self):
        def timeout(request):
            raise httpx.ReadTimeout('PRIVATE_UPSTREAM', request=request)
        for outcome in (timeout, httpx.Response(429, headers={'Retry-After': '120'}, text='PRIVATE_UPSTREAM')):
            with self.subTest(outcome=str(outcome)):
                self.remote.requests.clear()
                self.remote.fault = self.fault_for_failed_role(outcome)
                self.assert_isolated(await self.api.get('/api/mobile/bootstrap'))
                self.assertEqual(len(self.readiness_reads()), 2)

    async def test_untrusted_rpc_result_never_becomes_readiness_authority(self):
        state = self.remote.readiness(USER_A, self.job['id'])
        foreign = {**state, 'user_id': USER_B, 'private': 'PRIVATE_UPSTREAM'}
        false_ready = {**state, 'ready': True, 'review': None}
        for value in (foreign, false_ready, [], {'unexpected': 'PRIVATE_UPSTREAM'}):
            with self.subTest(value_type=type(value).__name__):
                self.remote.fault = self.fault_for_failed_role(httpx.Response(200, json=value))
                self.assert_isolated(await self.api.get('/api/mobile/bootstrap'))
                direct = await self.api.get('/api/mobile/jobs/' + self.job['id'] + '/readiness')
                self.assertEqual(direct.status_code, 503)

    async def test_auth_and_authorization_failures_still_reject_whole_request(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.remote.fault = self.fault_for_failed_role(httpx.Response(status, text='PRIVATE_UPSTREAM'))
                response = await self.api.get('/api/mobile/bootstrap')
                self.assertEqual(response.status_code, status, response.text)
                self.assertNotIn('jobs', response.json())
                self.assertNotIn('PRIVATE_UPSTREAM', response.text)
        self.remote.fault = None
        anonymous = await self.api.get('/api/mobile/bootstrap', headers={'Authorization': ''})
        self.assertEqual(anonymous.status_code, 401)
        self.assert_no_ai()

    async def test_invalid_aggregate_is_not_hidden_by_readiness_recovery(self):
        original = copy.deepcopy(self.remote.tables['applications'])
        corruptions = [original + [original[0]],
                       original + [{**original[0], 'id': str(uuid4())}],
                       [{**original[0], 'user_id': USER_B}] + original[1:]]
        for rows in corruptions:
            with self.subTest(rows=len(rows)):
                self.remote.requests.clear()
                self.remote.fault = lambda r: httpx.Response(200, json=rows) if r.url.path == '/rest/v1/applications' else None
                response = await self.api.get('/api/mobile/bootstrap')
                self.assertIn(response.status_code, (502, 503), response.text)
                self.assertNotIn('jobs', response.json())
                self.assertFalse(self.readiness_reads())
        self.assert_no_ai()

    async def test_readiness_and_application_actions_remain_fail_closed(self):
        original = copy.deepcopy(self.remote.tables)
        reviews = copy.deepcopy(self.remote.packet_reviews)
        self.remote.fault = self.fault_for_failed_role(httpx.Response(503))
        self.assert_isolated(await self.api.get('/api/mobile/bootstrap'))
        prefix = '/api/mobile/jobs/' + self.job['id']
        self.assertEqual((await self.api.get(prefix)).status_code, 503)
        self.assertEqual((await self.api.get(prefix + '/readiness')).status_code, 503)
        result = await self.api.post(prefix + '/review-packet', json=self.body.model_dump(mode='json'))
        self.assertEqual(result.status_code, 503, result.text)
        direct = await self.api.post('/api/mobile/applications', json={'job_id': self.job['id'], 'status': 'ready'})
        self.assertEqual(direct.status_code, 422)
        self.assertEqual(self.remote.tables, original)
        self.assertEqual(self.remote.packet_reviews, reviews)
        self.assert_no_ai()

    async def test_unavailable_preparation_context_still_prevents_ai_and_budget_write(self):
        original = copy.deepcopy(self.remote.tables)
        self.remote.fault = lambda r: httpx.Response(503) if r.url.path.endswith('/mobile_packet_context') else None
        response = await self.api.post('/api/mobile/jobs/' + self.job['id'] + '/prepare',
                                       json={'resume_id': self.source['id'], 'variant': 'role_aligned'})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.remote.tables, original)
        self.assert_no_ai()

    async def test_capped_workspace_retains_existing_conservative_projection(self):
        for _ in range(196):
            self.remote.job(USER_A)
        response = await self.api.get('/api/mobile/bootstrap')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['jobs']), 200)
        self.assertTrue(all(row['status'] != 'ready' for row in response.json()['jobs']))
        self.assertTrue(all(row['readiness_unavailable'] == 'capped_workspace' for row in response.json()['jobs']))
        self.assertFalse(self.readiness_reads())
        self.assert_no_ai()

    async def test_hanging_check_is_cancelled_before_browser_timeout(self):
        cancelled = asyncio.Event()
        original = copy.deepcopy(self.remote.tables)

        async def slow_cloud(request):
            if request.url.path.endswith('/mobile_packet_readiness') and json.loads(request.content)['p_job_id'] == self.job['id']:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return self.remote(request)

        app = create_app(settings=SETTINGS, transport=httpx.MockTransport(slow_cloud), studio=self.studio)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://app.test',
                                    headers={'Authorization': 'Bearer ' + self.token}) as client:
            response = await asyncio.wait_for(client.get('/api/mobile/bootstrap'), timeout=10)
            self.assert_isolated(response)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.remote.tables, original)

    async def test_unknown_eligibility_is_still_not_ready_after_readiness_recovers(self):
        self.remote.fault = self.fault_for_failed_role(httpx.Response(503))
        self.assert_isolated(await self.api.get('/api/mobile/bootstrap'))
        answer = self.remote.own('mobile_answers', USER_A, review_id(USER_A, self.job['id']))
        value = json.loads(answer['answer'])
        value.update(status='unknown', reason='Synthetic work eligibility is not confirmed')
        answer['answer'] = json.dumps(value)
        self.remote.fault = None
        response = await self.api.get('/api/mobile/bootstrap')
        self.assertEqual(response.status_code, 200, response.text)
        row = next(a for a in response.json()['applications'] if a['job_id'] == self.job['id'])
        self.assertEqual(row['status'], 'draft')
        self.assertEqual(row['readiness']['reason'], 'eligibility_required')
        self.assertFalse(row['readiness']['ready'])
        self.assertNotIn('readiness_unavailable', row)
        result = await self.api.post('/api/mobile/jobs/' + self.job['id'] + '/review-packet', json=self.body.model_dump(mode='json'))
        self.assertEqual(result.status_code, 409)
        self.assert_no_ai()


if __name__ == '__main__':
    unittest.main()
