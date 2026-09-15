"""HTTP boundary only; provider cryptography/lifecycle tested by billing suite."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from jobagent.mobile.app import create_app
from test_mobile_api import FakeSupabase, SETTINGS, USER_A, USER_B


class BillingRouteTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeSupabase()
        self.db.auth_emails[USER_A] = {'email': 'synthetic@example.test', 'email_confirmed_at': '2026-09-15T00:00:00Z'}
        self.db.fault = lambda r: httpx.Response(200, json={'allowed': False, 'user_id':USER_A,
            'code':'not_entitled'}) if r.url.path.endswith('/mobile_check_access') else None
        self.billing = SimpleNamespace(settings=SimpleNamespace(mode='test',checkout_enabled=False,plans={}),
            checkout=AsyncMock(return_value={'url':'https://checkout.example.test/synthetic'}),
            portal=AsyncMock(return_value={'url':'https://billing.example.test/synthetic'}),
            webhook=AsyncMock(return_value={'received':True}))
        self.client = TestClient(create_app(settings=SETTINGS, transport=httpx.MockTransport(self.db), billing=self.billing))
        self.headers={'Authorization':'Bearer session-a'}

    def tearDown(self): self.client.close()

    def test_checkout_can_begin_without_paid_membership(self):
        self.assertEqual(self.client.get('/api/mobile/bootstrap',headers=self.headers).status_code,403)
        r=self.client.post('/api/mobile/billing/checkout',headers=self.headers,json={'plan_key':'standard'})
        self.assertEqual(r.status_code,200)
        repo,plan=self.billing.checkout.call_args.args
        self.assertEqual((repo.user_id,repo.verified_email,plan),(USER_A,'synthetic@example.test','standard'))

    def test_invite_and_auth_still_required(self):
        self.assertEqual(self.client.post('/api/mobile/billing/checkout',json={'plan_key':'standard'}).status_code,401)
        with patch.dict('os.environ',{'MOBILE_ALLOWED_EMAILS':'other@example.test'}):
            self.assertEqual(self.client.post('/api/mobile/billing/checkout',headers=self.headers,json={'plan_key':'standard'}).status_code,403)
        self.billing.checkout.assert_not_called()

    def test_cannot_inject_price_user_redirect_or_caps(self):
        for field,value in (('user_id',USER_B),('price_id','price_untrusted'),('return_url','https://evil.example'),('period_limit',9999)):
            r=self.client.post('/api/mobile/billing/checkout',headers=self.headers,json={'plan_key':'standard',field:value})
            self.assertEqual(r.status_code,422)
        self.billing.checkout.assert_not_called()

    def test_portal_requires_empty_body_and_does_not_accept_customer(self):
        self.assertEqual(self.client.post('/api/mobile/billing/portal',headers=self.headers,json={'customer_id':'wrong'}).status_code,422)
        self.assertEqual(self.client.post('/api/mobile/billing/portal',headers=self.headers,json={}).status_code,200)
        self.assertEqual(self.billing.portal.call_args.args[0].user_id,USER_A)

    def test_webhook_receives_exact_raw_bytes_without_user_authorization(self):
        body=b'{ "event" : "synthetic", "spacing":true }\n'
        r=self.client.post('/api/mobile/billing/webhook',content=body,headers={'stripe-signature':'synthetic-signature'})
        self.assertEqual(r.status_code,200)
        self.billing.webhook.assert_awaited_once_with(body,'synthetic-signature')
        self.assertEqual(self.db.requests,[])

    def test_missing_duplicate_mixed_signatures_rejected_before_provider(self):
        for headers in ({},[('stripe-signature','a'),('stripe-signature','b')],
                        {'stripe-signature':'a','billing-signature':'b'}):
            self.assertEqual(self.client.post('/api/mobile/billing/webhook',content=b'{}',headers=headers).status_code,400)
        self.billing.webhook.assert_not_called()

    def test_webhook_size_bounded_before_provider(self):
        r=self.client.post('/api/mobile/billing/webhook',content=b'x'*(256*1024+1),headers={'stripe-signature':'a'})
        self.assertEqual(r.status_code,413)
        self.billing.webhook.assert_not_called()

    def test_disabled_status_is_not_a_payment_success(self):
        r=self.client.get('/api/mobile/billing',headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.assertFalse(r.json()['checkout_enabled'])
        self.assertEqual(r.json()['subscription_status'],'not_loaded')
        self.billing.checkout.assert_not_called()


if __name__=='__main__': unittest.main()
