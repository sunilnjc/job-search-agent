"""Commercial customer contract: real ASGI, SQL, billing adapter; mocked Stripe.

No secrets/environment settings, provider network, actual payments, email or
hosted accounts. The local auth dependency is explicit; this does not prove
Supabase auth or Stripe-hosted Checkout UX. Run with unittest discover -s tests.
"""
import asyncio
import json
import re
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from jobagent.mobile.billing import BillingService, BillingSettings, Plan, StripeTestProvider, StripeLiveProvider, unavailable
from jobagent.mobile.billing_customer import customer_billing_router
from local_postgres import DisposablePostgres, SUPABASE_TEST_SCHEMAS
from test_mobile_billing import StripeFixture, KEY, SECRET, event, signed, CONFIG, NOW
from test_mobile_billing_sql import MIGRATIONS

ROOT = Path(__file__).resolve().parents[1]
A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"


class SQLStore:
    """Local SQL RPC transport, never accepts an external DSN or HTTP origin."""
    def __init__(self, db):
        self.db, self.calls = db, []

    async def rpc(self, name, **params):
        assert re.fullmatch(r"mobile_billing_[a-z_]+", name)
        self.calls.append(name)
        args = []
        for key, value in params.items():
            assert re.fullmatch(r"p_[a-z_]+", key)
            literal = "null" if value is None else "'" + (json.dumps(value) if isinstance(value, dict) else str(value)).replace("'", "''") + "'"
            args.append(key + " => " + literal)
        raw = self.db.execute("begin; set local request.jwt.claim.role='service_role'; select public." + name + "(" + ",".join(args) + "); commit;")
        result = json.loads(raw.strip())
        if result.get("status") in ("busy", "conflict", "stale", "blocked"):
            raise unavailable()
        return result


class SQLRepo:
    def __init__(self, db, user_id=A):
        self.db, self.user_id, self.verified_email, self.client = db, user_id, "qa@fixture.invalid", None

    async def request(self, client, method, path, **kwargs):
        assert client is None and method == "POST" and path == "/rest/v1/rpc/mobile_billing_customer_status"
        assert kwargs["json"] == {} and kwargs["max_bytes"] <= 16*1024
        return self.db.execute("begin; set local role authenticated; set local request.jwt.claim.role='authenticated'; "
            "set local request.jwt.claim.sub='" + self.user_id + "'; select public.mobile_billing_customer_status(); rollback;").strip().encode()


class CommercialFixture:
    """Reusable real SQL lifecycle used by both unittest and the browser harness."""
    def __init__(self):
        try:
            self.db = DisposablePostgres()
        except unittest.SkipTest as exc:
            raise RuntimeError("Commercial billing gate requires local PostgreSQL") from exc
        try:
            self.db.execute(SUPABASE_TEST_SCHEMAS)
            for name in (*MIGRATIONS, "0013_billing_customer_flow.sql", "0014_billing_live_activation.sql"):
                self.db.execute((ROOT / "supabase/migrations" / name).read_text())
            self.db.execute("insert into auth.users(id,email,email_confirmed_at) values "
                "('"+A+"','qa-a@fixture.invalid',now()),('"+B+"','qa-b@fixture.invalid',now());")
        except BaseException:
            self.db.close()
            raise
        self.stripe = StripeFixture()
        self.now = int(time.time())
        self.stripe.sub.update(current_period_start=self.now-1000, current_period_end=self.now+86400)
        self.stripe.invoice["lines"]["data"][0]["period"] = {"start": self.now-1000, "end": self.now+86400}
        self.stripe.session = lambda: {"id": "cs_test_Fixture", "object": "checkout.session", "livemode": False,
            "customer": "cus_A", "mode": "subscription", "status": "open", "expires_at": self.now+1800,
            "url": "https://checkout.stripe.com/c/pay/fixture"}
        self.stripe.subscriptions = []
        self.store = SQLStore(self.db)
        self.provider = StripeTestProvider(KEY, SECRET, portal_configuration="bpc_Fixture",
                                           transport=httpx.MockTransport(self.stripe), clock=lambda: self.now)
        self.service = BillingService(CONFIG, self.store, self.provider, clock=lambda: self.now)
        self.repo = SQLRepo(self.db)
        self.app = FastAPI()

        def invited():
            return self.repo

        self.app.include_router(customer_billing_router(lambda: self.service, invited))

        @self.app.post("/api/mobile/billing/checkout")
        async def checkout(body: dict, repo=Depends(invited)):
            if set(body) != {"plan_key"}:
                raise HTTPException(422, "Only a configured plan key is accepted")
            return await self.service.checkout(repo, body["plan_key"])

        @self.app.post("/api/mobile/billing/portal")
        async def portal(repo=Depends(invited)):
            return await self.service.portal(repo)

    def expire_cooldown(self):
        self.db.execute("update public.mobile_billing_accounts set reconcile_requested_at=now()-interval '31 seconds' where user_id='"+A+"';")

    def settle(self):
        self.stripe.subscriptions = [self.stripe.sub]
        self.stripe.sessions[0]["status"] = "complete"

    def close(self):
        self.db.close()


class CustomerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = CommercialFixture()
        cls.addClassCleanup(cls.fixture.close)

    def test_complete_customer_lifecycle_and_fail_closed_controls(self):
        f = self.fixture
        with TestClient(f.app) as client:
            get = lambda: client.get("/api/mobile/billing/account").json()
            before = get()
            self.assertFalse(before["access"]["allowed"])
            self.assertFalse(before["account_exists"])
            prices = client.get("/api/mobile/billing/plans")
            self.assertEqual(prices.status_code, 200)
            self.assertEqual(prices.json()["plans"][0]["amount_minor"], 321)
            self.assertNotIn("price_Fixture", prices.text)
            # Unauthenticated and owner/entitlement injection do not acquire access.
            self.assertEqual(client.post("/api/mobile/billing/reconcile", json={"user_id": B, "paid": True}).status_code, 422)
            checkout = client.post("/api/mobile/billing/checkout", json={"plan_key": "fixture"})
            self.assertEqual(checkout.status_code, 200, checkout.text)
            self.assertFalse(get()["access"]["allowed"])
            self.assertEqual(client.get("/api/mobile/billing/account?session_id=forged&success=true").json()["access"]["allowed"], False)
            self.assertEqual(client.post("/api/mobile/billing/reconcile", json={}).status_code, 200)
            self.assertFalse(get()["access"]["allowed"])
            self.assertEqual(client.post("/api/mobile/billing/reconcile", json={}).status_code, 429)
            # Checkout completion alone remains insufficient. Independently fetched
            # invoice+charge+subscription proof is required before real SQL grants.
            f.settle()
            self.assertFalse(get()["access"]["allowed"])
            f.expire_cooldown()
            self.assertEqual(client.post("/api/mobile/billing/reconcile", json={}).json(), {"status": "applied"})
            paid = get()
            self.assertTrue(paid["access"]["allowed"])
            self.assertEqual((paid["access"]["period_remaining"], paid["access"]["daily_remaining"]), (100,10))
            self.assertEqual(client.post("/api/mobile/billing/checkout", json={"plan_key":"fixture"}).status_code, 409)
            # Replayed signed webhook and explicit sync share transaction semantics.
            raw = event("evt_CustomerPaid")
            self.assertEqual(asyncio.run(f.service.webhook(raw, signed(raw, f.now)))["status"], "applied")
            self.assertEqual(asyncio.run(f.service.webhook(raw, signed(raw, f.now)))["status"], "duplicate")
            f.db.execute("update public.mobile_usage_memberships set period_reserved=7 where user_id='"+A+"'; "
                "insert into public.mobile_ai_daily_usage(user_id,usage_day,reserved_units) values ('"+A+"',(now() at time zone 'utc')::date,3);")
            portal = client.post("/api/mobile/billing/portal", json={})
            self.assertEqual(portal.status_code, 200)
            self.assertFalse(get()["subscription"]["cancel_at_period_end"])
            # Simulated operator action at provider boundary, never a real charge.
            f.stripe.sub["cancel_at_period_end"] = True
            f.expire_cooldown()
            self.assertEqual(client.post("/api/mobile/billing/reconcile", json={}).status_code, 200)
            self.assertTrue(get()["access"]["allowed"])
            self.assertTrue(get()["subscription"]["cancel_at_period_end"])
            self.assertEqual((get()["access"]["period_remaining"], get()["access"]["daily_remaining"]), (93,7))
            # Provider outage preserves last confirmed state, exposes pending sync;
            # success on retry uses same durable pending sync, not an unlimited grant.
            f.stripe.fault = lambda req: httpx.Response(503)
            f.expire_cooldown()
            self.assertEqual(client.post("/api/mobile/billing/reconcile", json={}).status_code, 503)
            self.assertTrue(get()["reconciliation_pending"])
            f.stripe.fault = None
            f.expire_cooldown()
            self.assertEqual(client.post("/api/mobile/billing/reconcile", json={}).status_code, 200)
            self.assertFalse(get()["reconciliation_pending"])
            self.assertEqual(get()["access"]["period_remaining"], 93)
            # A new authenticated identity cannot inspect or reconcile A's payment.
            f.repo = SQLRepo(f.db, B)
            other = get()
            self.assertFalse(other["account_exists"])
            self.assertFalse(other["access"]["allowed"])
            count = len(f.stripe.requests)
            self.assertEqual(client.post("/api/mobile/billing/reconcile", json={}).json(), {"status":"none"})
            self.assertEqual(len(f.stripe.requests), count)
            f.repo = SQLRepo(f.db, A)
        # Fresh client/repository is a session boundary: no browser-local paid flag.
        with TestClient(f.app) as returning:
            state = returning.get("/api/mobile/billing/account").json()
            self.assertTrue(state["access"]["allowed"])
            self.assertEqual(state["access"]["period_remaining"], 93)
            f.stripe.sub["status"] = "canceled"
            f.expire_cooldown()
            self.assertEqual(returning.post("/api/mobile/billing/reconcile", json={}).status_code, 200)
            self.assertFalse(returning.get("/api/mobile/billing/account").json()["access"]["allowed"])
        # Normal cancellation path never calls provider DELETE or invoice payment.
        self.assertFalse(any(r.method == "DELETE" for r in f.stripe.requests))
        self.assertTrue(all(r.method == "GET" or r.url.path in ("/v1/customers", "/v1/checkout/sessions", "/v1/billing_portal/sessions") for r in f.stripe.requests))

    def test_live_sql_is_disabled_by_default_and_segregates_mode_after_review(self):
        # Roll back the entire synthetic approval scenario. This never enables
        # the fixture's test adapter or any hosted live configuration.
        self.fixture.db.execute("""
        begin;
        set local request.jwt.claim.role='service_role';
        do $$ declare s jsonb; a jsonb; b jsonb; begin
          assert not public.mobile_billing_mode_enabled('live');
          assert public.mobile_billing_begin('00000000-0000-4000-8000-000000000002','stripe','live')->>'status'='blocked';
          assert public.mobile_billing_event_claim('stripe','live','evt_NoLive','cus_A',repeat('a',64))->>'status'='blocked';
          update public.mobile_billing_live_control set enabled=true,reviewed_at=now(),review_reference='LOCAL SYNTHETIC TEST ONLY';
          -- Fresh user has no test-mode mapping, unlike the lifecycle persona.
          insert into auth.users(id,email,email_confirmed_at) values
            ('00000000-0000-4000-8000-000000000009','live-fixture@fixture.invalid',now());
          a:=public.mobile_billing_begin('00000000-0000-4000-8000-000000000009','stripe','live');
          assert a->>'mode'='live';
          perform public.mobile_billing_release((a->>'id')::uuid,(a->>'lease_token')::uuid);
          assert public.mobile_billing_begin('00000000-0000-4000-8000-000000000009','stripe','test')->>'status'='blocked';
          update public.mobile_billing_accounts set customer_id='cus_LiveFixture' where id=(a->>'id')::uuid;
          assert public.mobile_billing_event_claim('stripe','test','evt_WrongMode','cus_LiveFixture',repeat('a',64))->>'status'='ignored';
          b:=public.mobile_billing_event_claim('stripe','live','evt_RightMode','cus_LiveFixture',repeat('b',64));
          assert b->>'id'=a->>'id';
          assert not exists(select 1 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000009' and enabled);
        end; $$;
        rollback;
        """)

    def test_clients_cannot_enable_live_or_invoke_service_reconciliation(self):
        self.fixture.db.execute("""
        begin; set local role authenticated; set local request.jwt.claim.role='authenticated';
        set local request.jwt.claim.sub='00000000-0000-4000-8000-000000000002';
        do $$ begin
          begin
            update public.mobile_billing_live_control set enabled=true;
            raise exception 'Unexpected live control write';
          exception when insufficient_privilege then null; end;
          begin
            perform public.mobile_billing_reconcile_claim('00000000-0000-4000-8000-000000000001','stripe','test');
            raise exception 'Unexpected service RPC access';
          exception when insufficient_privilege then null; end;
          begin
            perform public.mobile_billing_customer_status_private();
            raise exception 'Unexpected private implementation access';
          exception when insufficient_privilege then null; end;
          assert public.mobile_billing_customer_status()->>'user_id'='00000000-0000-4000-8000-000000000002';
        end; $$; rollback;
        """)


class PriceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_reject_ambiguous_live_free_metered_or_unsupported_prices(self):
        mutations = [{"livemode":True}, {"active":False}, {"unit_amount":0}, {"unit_amount":True},
                     {"currency":"jpy"}, {"billing_scheme":"tiered"}, {"recurring":{}},
                     {"recurring":{"usage_type":"metered","interval":"month","interval_count":1}}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                fixture = StripeFixture(); fixture.price.update(mutation)
                provider = StripeTestProvider(KEY, SECRET, transport=httpx.MockTransport(fixture))
                with self.assertRaises(HTTPException):
                    await provider.price_details("price_Fixture")
                self.assertTrue(all(r.method == "GET" for r in fixture.requests))

    async def test_price_cache_and_disabled_configuration_perform_no_writes(self):
        fixture = StripeFixture()
        provider = StripeTestProvider(KEY, SECRET, portal_configuration="bpc_Fixture", transport=httpx.MockTransport(fixture))
        service = BillingService(CONFIG, None, provider)
        self.assertEqual(await service.plans(), await service.plans())
        self.assertEqual(len(fixture.requests), 1)
        self.assertEqual(await BillingService(BillingSettings(), None).plans(), {"mode":"test","plans":[]})

    async def test_portal_must_allow_period_end_cancellation_not_proration(self):
        for cancellation in ({"enabled":False}, {"enabled":True,"mode":"immediately"}, None):
            fixture = StripeFixture(); fixture.portal_config["features"]["subscription_cancel"] = cancellation
            provider = StripeTestProvider(KEY, SECRET, portal_configuration="bpc_Fixture", transport=httpx.MockTransport(fixture))
            with self.assertRaises(HTTPException):
                await provider.create_portal("cus_A", CONFIG.portal_return_url)
            self.assertTrue(all(r.method == "GET" for r in fixture.requests))


class CustomerRouterTests(unittest.TestCase):
    def setUp(self):
        self.allowed = True
        self.identity = SimpleNamespace(user_id=A,verified_email="synthetic@fixture.invalid",client=None)
        self.body = {"user_id":A,"account_exists":False,"subscription":None,"reconciliation_pending":False,
                     "access":{"allowed":False,"grant_source":None,"expires_at":None,"period_end":None,
                               "period_remaining":0,"daily_remaining":0}}

        async def request(*args, **kwargs):
            self.assertEqual(args[1:3], ("POST","/rest/v1/rpc/mobile_billing_customer_status"))
            self.assertEqual(kwargs["json"], {})
            return json.dumps(self.body).encode()

        self.identity.request = request
        self.service = BillingService(BillingSettings(), None)
        self.service.reconcile = AsyncMock(return_value={"status":"none"})
        def invited():
            if not self.allowed:
                raise HTTPException(403,"Not invited")
            return self.identity
        app=FastAPI()
        app.include_router(customer_billing_router(lambda:self.service, invited))
        self.client=TestClient(app)
        self.addCleanup(self.client.close)

    def test_unpaid_owner_can_read_account_and_disabled_prices_without_access(self):
        response=self.client.get("/api/mobile/billing/account")
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()["access"]["allowed"])
        self.assertNotIn("user_id",response.json())
        self.assertEqual(self.client.get("/api/mobile/billing/plans").json(),{"mode":"test","plans":[]})

    def test_all_new_routes_keep_invitation_dependency(self):
        self.allowed=False
        for path in ("account","plans"):
            self.assertEqual(self.client.get("/api/mobile/billing/"+path).status_code,403)
        self.assertEqual(self.client.post("/api/mobile/billing/reconcile",json={}).status_code,403)
        self.service.reconcile.assert_not_called()

    def test_reconcile_body_cannot_override_identity_access_or_receipt(self):
        for field in ("user_id","plan_key","session_id","customer_id","paid","period_limit"):
            self.assertEqual(self.client.post("/api/mobile/billing/reconcile",json={field:"untrusted"}).status_code,422)
        self.assertEqual(self.client.post("/api/mobile/billing/reconcile",json={}).json(),{"status":"none"})
        self.service.reconcile.assert_awaited_once_with(self.identity)

    def test_foreign_or_incomplete_account_result_cannot_become_access(self):
        self.body["user_id"]=B
        response=self.client.get("/api/mobile/billing/account")
        self.assertEqual(response.status_code,503)
        self.assertIn("0013",response.json()["detail"])
        self.body["user_id"]=A
        self.body["access"]["allowed"]="true"
        self.assertEqual(self.client.get("/api/mobile/billing/account").status_code,503)

    def test_live_ui_stays_disabled_without_database_approval_or_on_mode_mismatch(self):
        provider=StripeLiveProvider("sk_live_OfflineFixtureOnly",SECRET,activation_approved=True,
            portal_configuration="bpc_Fixture",transport=httpx.MockTransport(lambda r: self.fail("No provider IO on status")))
        self.service=BillingService(replace(CONFIG,mode="live",live_activation_approved=True),None,provider)
        self.body.update(billing_mode=None,live_activation_enabled=False)
        state=self.client.get("/api/mobile/billing/account").json()
        self.assertEqual(state["mode"],"live")
        self.assertFalse(state["checkout_enabled"])
        self.assertFalse(state["configuration_ready"])
        self.body.update(billing_mode="test",live_activation_enabled=True)
        state=self.client.get("/api/mobile/billing/account").json()
        self.assertTrue(state["mode_mismatch"])
        self.assertFalse(state["portal_enabled"])
