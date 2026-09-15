"""Hermetic API + real ZIP worker regression. No live Auth deletions/emails."""
import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

from jobagent.mobile.app import create_app
from jobagent.mobile.privacy_worker import PrivacyWorker, WorkerSettings, PrivacyBlocked, object_key
from test_mobile_api import FakeSupabase, SETTINGS, USER_A, USER_B

RID = '33333333-3333-4333-8333-333333333333'
LEASE = '44444444-4444-4444-8444-444444444444'
EMAIL = 'synthetic@example.test'


class PrivacyApiTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeSupabase()
        self.db.auth_emails[USER_A] = {'email': EMAIL, 'email_confirmed_at': '2026-01-01T00:00:00Z'}
        self.rows = []
        self.ready = True
        self.calls = []

        def fault(request):
            path = request.url.path
            if path == '/rest/v1/rpc/mobile_check_access':
                return httpx.Response(200, json={'allowed': False, 'user_id': USER_A, 'code': 'not_entitled'})
            if '/rpc/mobile_privacy_' in path:
                self.calls.append(request)
                if path.endswith('mobile_privacy_status'):
                    return httpx.Response(200, json={'user_id': USER_A, 'requests': self.rows, 'worker_ready': self.ready})
                data = json.loads(request.content)
                if not self.ready:
                    return httpx.Response(200, json={'user_id': USER_A, 'error': 'worker_unavailable'})
                row = {'id': RID, 'kind': data['p_kind'], 'state': 'queued', 'created_at': '2026-09-15T00:00:00Z'}
                return httpx.Response(200, json={'user_id': USER_A, 'request': row})
        self.db.fault = fault
        self.client = TestClient(create_app(settings=SETTINGS, transport=httpx.MockTransport(self.db)))
        self.headers = {'Authorization': 'Bearer session-a'}

    def tearDown(self):
        self.client.close()

    def test_privacy_available_without_membership_or_invite(self):
        with patch.dict('os.environ', {'MOBILE_ALLOWED_EMAILS': 'another@example.test'}):
            self.assertEqual(self.client.get('/api/mobile/bootstrap', headers=self.headers).status_code, 403)
            r = self.client.get('/api/mobile/account', headers=self.headers)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()['capability']['processing_configured'])

    def test_anonymous_and_unverified_are_denied_before_privacy_rpc(self):
        self.assertEqual(self.client.get('/api/mobile/account').status_code, 401)
        self.assertEqual(self.client.get('/api/mobile/account', headers={'Authorization': 'Bearer session-b'}).status_code, 403)
        self.assertEqual(self.calls, [])

    def test_export_body_cannot_select_user(self):
        r = self.client.post('/api/mobile/account/exports', headers=self.headers, json={'user_id': USER_B})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.calls, [])

    def test_export_has_no_implicit_delete(self):
        r = self.client.post('/api/mobile/account/exports', headers=self.headers, json={})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(json.loads(self.calls[0].content), {'p_kind': 'export'})

    def test_erasure_requires_exact_confirmation_and_verified_email(self):
        for body in ({'confirmation': 'DELETE', 'email': EMAIL}, {'confirmation': 'DELETE MY ACCOUNT', 'email': 'other@example.test'}):
            self.assertEqual(self.client.post('/api/mobile/account/erasure', headers=self.headers, json=body).status_code, 422)
        self.assertEqual(self.calls, [])
        r = self.client.post('/api/mobile/account/erasure', headers=self.headers,
            json={'confirmation': 'DELETE MY ACCOUNT', 'email': EMAIL})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()['state'], 'queued')
        self.assertFalse(r.json()['download_ready'])

    def test_offline_worker_accepts_nothing(self):
        self.ready = False
        r = self.client.post('/api/mobile/account/exports', headers=self.headers, json={})
        self.assertEqual(r.status_code, 503)

    def test_lost_erasure_ack_does_not_claim_deletion_has_stopped(self):
        original_fault = self.db.fault
        def fault(request):
            if request.url.path.endswith('/rpc/mobile_privacy_request'):
                # Simulate SQL committing before its acknowledgement is lost.
                self.rows = [{'id': RID, 'kind': 'erase', 'state': 'queued',
                              'created_at': '2026-09-15T00:00:00Z'}]
                raise httpx.ReadTimeout('PRIVATE_TRANSPORT_SENTINEL')
            return original_fault(request)
        self.db.fault = fault
        result = self.client.post('/api/mobile/account/erasure', headers=self.headers,
            json={'confirmation': 'DELETE MY ACCOUNT', 'email': EMAIL})
        self.assertEqual(result.status_code, 503)
        self.assertIn('may already be queued or processing', result.json()['detail'])
        self.assertNotIn('PRIVATE_TRANSPORT_SENTINEL', result.text)
        status = self.client.get('/api/mobile/account', headers=self.headers)
        self.assertEqual(status.json()['requests'][0]['state'], 'queued')

    def ready_export(self):
        self.rows = [{'id': RID, 'kind': 'export', 'state': 'complete', 'created_at': '2026-09-15T00:00:00Z',
            'expires_at': '2099-01-01T00:00:00Z', 'export_path': f'{USER_A}/{RID}/account.zip',
            'lease_token': LEASE, 'error_code': 'private-error-sentinel'}]

    def test_status_does_not_leak_private_fields(self):
        self.ready_export()
        r = self.client.get('/api/mobile/account', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        for private in ('export_path', 'lease_token', 'private-error-sentinel'):
            self.assertNotIn(private, r.text)

    def test_download_is_owner_path_and_not_available_until_ready(self):
        self.ready_export()
        self.db.objects[('account-exports', f'{USER_A}/{RID}/account.zip')] = b'PK synthetic download'
        r = self.client.get(f'/api/mobile/account/exports/{RID}/download', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers['content-type'], 'application/zip')
        self.assertEqual(r.headers['cache-control'], 'no-store')
        self.rows[0]['export_path'] = f'{USER_B}/{RID}/account.zip'
        self.assertEqual(self.client.get(f'/api/mobile/account/exports/{RID}/download', headers=self.headers).status_code, 503)
        self.rows[0]['state'] = 'processing'
        self.assertEqual(self.client.get(f'/api/mobile/account/exports/{RID}/download', headers=self.headers).status_code, 409)

    def test_expired_download_denied(self):
        self.ready_export()
        self.rows[0]['expires_at'] = '2020-01-01T00:00:00Z'
        self.assertEqual(self.client.get(f'/api/mobile/account/exports/{RID}/download', headers=self.headers).status_code, 409)

    def test_worker_cli_is_dry_run_by_default_and_never_prints_secret(self):
        # Invalid/nonexistent network host would fail if the dry-run contacted it.
        result = subprocess.run([sys.executable, 'scripts/privacy_worker.py'], capture_output=True,
            text=True, timeout=5, env={'PATH':os.environ.get('PATH','/usr/bin:/bin'), 'PYTHONPATH':'src',
                'MOBILE_PRIVACY_SUPABASE_URL':'https://dryrun.invalid',
                'MOBILE_PRIVACY_SUPABASE_SECRET_KEY':'sb_secret_PRIVATE_TEST_SENTINEL'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Dry run',result.stdout)
        self.assertNotIn('PRIVATE_TEST_SENTINEL',result.stdout+result.stderr)
        self.assertNotIn('PRIVATE_TEST_SENTINEL',repr(WorkerSettings('https://dryrun.invalid','sb_secret_PRIVATE_TEST_SENTINEL')))


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.job = {'id': RID, 'user_id': USER_A, 'lease_token': LEASE, 'kind': 'export'}
        self.objects = {('resumes', USER_A + '/source.pdf'): b'original source',
                        ('application-artifacts', USER_A + '/orphan.docx'): b'orphan derivative'}
        self.auth = True
        self.calls = []
        self.finishes = []
        self.allow_cancel = True
        self.lost_delete = False
        self.stuck_remove = False

        async def cancel(user_id, request_id):
            self.calls.append(('BILLING', user_id))
            return {'status': 'ready' if self.allow_cancel else 'blocked', 'request_id': request_id}

        def transport(request):
            self.calls.append((request.method, request.url.path))
            self.assertEqual(request.url.host, 'privacy.example.test')
            self.assertEqual(request.headers['apikey'], 'sb_secret_synthetic')
            path = request.url.path
            data = json.loads(request.content) if request.content and request.headers.get('content-type') == 'application/json' else {}
            if '/rpc/' in path:
                name = path.rsplit('/', 1)[1]
                if name == 'mobile_privacy_heartbeat': return httpx.Response(200, json=None)
                if name == 'mobile_privacy_expired_exports': return httpx.Response(200, json=[])
                if name == 'mobile_privacy_claim': return httpx.Response(200, json=self.job)
                if name == 'mobile_privacy_snapshot':
                    return httpx.Response(200, json={'user_id': USER_A, 'account': {'email': EMAIL} if self.auth else None,
                        'tables': {'profiles': [{'user_id': USER_A, 'display_name': 'Synthetic Candidate'}]},
                        'objects': [{'bucket': b, 'path': p} for b,p in self.objects]})
                if name == 'mobile_privacy_finish':
                    self.finishes.append(data)
                    return httpx.Response(200, json=True)
            if path == '/auth/v1/admin/users/' + USER_A:
                if request.method == 'DELETE':
                    self.assertEqual(data, {'should_soft_delete': False})
                    self.auth = False
                    if self.lost_delete:
                        self.lost_delete = False
                        raise httpx.ReadTimeout('private provider response must not escape')
                    return httpx.Response(200, json={})
                return httpx.Response(200 if self.auth else 404, json={})
            if path.startswith('/storage/v1/object/'):
                remainder = path.removeprefix('/storage/v1/object/')
                if request.method == 'DELETE':
                    for prefix in data['prefixes']:
                        if not self.stuck_remove: self.objects.pop((remainder, prefix), None)
                    return httpx.Response(200, json=[])
                bucket, key = remainder.split('/', 1)
                if request.method == 'POST':
                    self.assertEqual(request.headers['x-upsert'], 'false')
                    self.assertEqual(json.loads(base64.b64decode(request.headers['x-metadata'])),
                                     {'privacy_lease': self.job['lease_token']})
                    self.objects[(bucket, key)] = request.content
                    return httpx.Response(200, json={})
                return httpx.Response(200, content=self.objects[(bucket, key)])
            raise AssertionError('Unexpected endpoint ' + path)
        self.worker = PrivacyWorker(WorkerSettings('https://privacy.example.test','sb_secret_synthetic'),
            transport=httpx.MockTransport(transport), cancel_billing=cancel)

    async def asyncTearDown(self):
        await self.worker.close()

    async def test_export_contains_source_orphan_and_safe_profile(self):
        result = await self.worker.run_once()
        self.assertEqual(result['state'], 'complete')
        raw = self.objects[('account-exports', f'{USER_A}/{RID}/account.zip')]
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertEqual(len(z.namelist()), 3)
            data = json.loads(z.read('account.json'))
            self.assertEqual(data['account']['email'], EMAIL)
            self.assertEqual(len(data['file_manifest']), 2)
        self.assertTrue(self.auth)
        self.assertFalse(any(method in ('DELETE','BILLING') for method,_ in self.calls))

    async def test_export_recovers_already_uploaded_without_rewriting(self):
        await self.worker.run_once()
        self.calls.clear()
        self.assertEqual((await self.worker.run_once())['state'], 'complete')
        self.assertFalse(any(method == 'POST' and '/storage/' in path for method,path in self.calls))

    async def test_billing_block_prevents_any_deletion(self):
        self.job['kind'] = 'erase'; self.allow_cancel = False
        result = await self.worker.run_once()
        self.assertEqual(result['code'], 'billing_blocked')
        self.assertTrue(self.auth)
        self.assertFalse(any(method == 'DELETE' for method,_ in self.calls))

    async def test_erasure_cancels_then_removes_all_objects_then_auth(self):
        self.job['kind'] = 'erase'
        self.assertEqual((await self.worker.run_once())['state'], 'complete')
        self.assertEqual(self.objects, {})
        self.assertFalse(self.auth)
        methods = [method for method,_ in self.calls]
        self.assertLess(methods.index('BILLING'), methods.index('DELETE'))
        deletes = [path for method,path in self.calls if method == 'DELETE']
        self.assertTrue(deletes[-1].startswith('/auth/'))

    async def test_failed_storage_delete_blocks_auth_delete(self):
        self.job['kind'] = 'erase'; self.stuck_remove = True
        self.assertEqual((await self.worker.run_once())['state'], 'blocked')
        self.assertTrue(self.auth)

    async def test_foreign_scope_blocks_before_billing_or_delete(self):
        self.job['kind'] = 'erase'
        self.objects[('resumes', USER_B + '/foreign.pdf')] = b'private'
        self.assertEqual((await self.worker.run_once())['code'], 'storage_scope')
        self.assertFalse(any(method in ('DELETE','BILLING') for method,_ in self.calls))

    async def test_lost_auth_delete_ack_resumes(self):
        self.job['kind'] = 'erase'; self.lost_delete = True
        self.assertEqual((await self.worker.run_once())['state'], 'blocked')
        self.assertFalse(self.auth)
        self.assertEqual((await self.worker.run_once())['state'], 'complete')

    async def test_rejects_publishable_key_and_insecure_origin(self):
        for settings in (WorkerSettings('https://privacy.example.test','sb_publishable_fake'),
                         WorkerSettings('http://privacy.example.test','sb_secret_fake'),
                         WorkerSettings('https://privacy.example.test/extra','sb_secret_fake')):
            with self.assertRaises(ValueError): settings.validate()

    async def test_object_scope_rejects_traversal_and_other_buckets(self):
        for item in ({'bucket':'resumes','path':USER_A+'/../x'}, {'bucket':'resumes','path':USER_A+'/%2e/x'},
                     {'bucket':'private-other-app','path':USER_A+'/x'},
                     {'bucket':'resumes','path':USER_A+'/x','owner_id':USER_B}):
            with self.assertRaises(PrivacyBlocked): object_key(USER_A, item)


if __name__ == '__main__': unittest.main()
