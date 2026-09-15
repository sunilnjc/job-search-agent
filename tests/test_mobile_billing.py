"""Hermetic billing transport/API integration and SQL structural checks.

MockTransport RPC model is NOT PostgreSQL, hosted RLS, or provider proof. No
live calls, external accounts, environment fake switch, SQL apply, or skips.
Run: .venv/bin/python -B -m unittest discover -s tests -p test_mobile_billing.py -v
"""
from __future__ import annotations

import copy
import base64
import hashlib
import hmac
import json
import os
import re
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from jobagent.mobile.billing import (
    BILLING_EXPORT_COLUMNS, BillingService, BillingSettings, BillingStore,
    CancellationReceipt, MAX_RESPONSE_BYTES, MAX_WEBHOOK_BYTES, Plan,
    PinnedProviderHTTP, StripeTestProvider, billing_capabilities, cancel_for_erasure,
    prepare_account_erasure, verify_stripe_signature,
)
from jobagent.mobile.app import create_app
from test_mobile_api import FakeSupabase, SETTINGS, USER_A, USER_B

NOW = 1_800_000_000
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
KEY, SECRET = "sk_test_OfflineFixtureOnly", "whsec_OfflineFixtureOnly"
CATALOG = {"fixture": Plan("price_Fixture", 100, 10)}
CONFIG = BillingSettings(provider="stripe", checkout_enabled=True, plans=CATALOG,
    success_url="https://app.example.test/paid", cancel_url="https://app.example.test/cancel",
    portal_return_url="https://app.example.test/account")
ROOT = Path(__file__).resolve().parents[1]


def signed(raw, timestamp=NOW):
    digest = hmac.new(SECRET.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return "t=" + str(timestamp) + ",v1=" + digest


def event(event_id="evt_One", **overrides):
    return json.dumps({"id": event_id, "object": "event", "livemode": False,
        "api_version": StripeTestProvider.api_version, "type": "invoice.paid",
        "data": {"object": {"customer": "cus_A", "metadata": {"user_id": USER_B, "quota": 999999}}},
        **overrides}, separators=(",", ":")).encode()


def listed(rows):
    return {"object": "list", "has_more": False, "data": copy.deepcopy(rows)}


class StripeFixture:
    """Explicit transport fixture, never registered in runtime configuration."""
    def __init__(self):
        self.requests, self.timeline = [], []
        self.fault = None
        self.customer = {"id": "cus_A", "object": "customer", "livemode": False}
        self.price = {"id": "price_Fixture", "livemode": False, "type": "recurring",
                      "recurring": {"usage_type": "licensed"}}
        self.sub = {"id": "sub_A", "object": "subscription", "livemode": False, "customer": "cus_A",
            "status": "active", "current_period_start": NOW - 100, "current_period_end": NOW + 100,
            "latest_invoice": "in_A", "cancel_at_period_end": False,
            "items": listed([{"id": "si_A", "quantity": 1, "price": self.price}])}
        self.invoice = {"id": "in_A", "object": "invoice", "livemode": False, "customer": "cus_A",
            "subscription": "sub_A", "status": "paid", "paid": True, "paid_out_of_band": False,
            "amount_remaining": 0, "amount_paid": 321, "charge": "ch_A", "lines": listed([{
                "type": "subscription", "subscription": "sub_A", "subscription_item": "si_A",
                "proration": False, "quantity": 1, "price": self.price,
                "period": {"start": NOW - 100, "end": NOW + 100}}])}
        self.charge = {"id": "ch_A", "object": "charge", "livemode": False, "customer": "cus_A",
            "invoice": "in_A", "paid": True, "captured": True, "status": "succeeded", "refunded": False,
            "disputed": False, "amount_refunded": 0, "amount_captured": 321}
        self.subscriptions = [self.sub]
        self.sessions, self.schedules, self.pending = [], [], []
        self.invoices = [self.invoice]
        self.portal_config = {"id": "bpc_Fixture", "object": "billing_portal.configuration",
            "livemode": False, "active": True, "features": {"subscription_update": {"enabled": False}}}
        self.after_fetch = None

    def session(self):
        return {"id": "cs_test_Fixture", "object": "checkout.session", "livemode": False,
            "customer": "cus_A", "mode": "subscription", "status": "open",
            "expires_at": NOW + 1000, "url": "https://checkout.stripe.com/c/pay/fixture"}

    def __call__(self, request):
        self.requests.append(request)
        self.timeline.append("provider:" + request.method + ":" + request.url.path)
        assert request.url.host == "api.stripe.com"
        assert request.headers["Authorization"] == "Bearer " + KEY
        assert request.headers["Stripe-Version"] == StripeTestProvider.api_version
        if self.fault:
            result = self.fault(request)
            if result is not None:
                return result
        path, method = request.url.path, request.method
        if path == "/v1/customers" and method == "POST":
            result = self.customer
        elif path == "/v1/customers/cus_A":
            result = self.customer
        elif path == "/v1/subscriptions" and method == "GET":
            assert request.url.params["customer"] == "cus_A" and request.url.params["status"] == "all"
            result = listed(self.subscriptions)
        elif path == "/v1/subscriptions/sub_A":
            if method == "DELETE":
                assert parse_qs(request.content.decode()) == {"invoice_now": ["false"], "prorate": ["false"]}
                self.sub["status"] = "canceled"
            result = self.sub
        elif path == "/v1/invoices/in_A":
            result = self.invoice
        elif path == "/v1/charges/ch_A":
            result = self.charge
            if self.after_fetch:
                self.after_fetch()
        elif path == "/v1/checkout/sessions":
            if method == "POST":
                if not self.sessions:
                    self.sessions.append(self.session())
                result = self.sessions[0]
            else:
                result = listed(self.sessions)
        elif path == "/v1/checkout/sessions/cs_test_Fixture":
            result = self.sessions[0]
        elif path == "/v1/checkout/sessions/cs_test_Fixture/expire":
            self.sessions[0]["status"] = "expired"
            result = self.sessions[0]
        elif path == "/v1/billing_portal/configurations/bpc_Fixture":
            result = self.portal_config
        elif path == "/v1/billing_portal/sessions":
            result = {"object": "billing_portal.session", "livemode": False, "customer": "cus_A",
                      "url": "https://billing.stripe.com/p/session/fixture"}
        elif path in ("/v1/subscription_schedules", "/v1/invoices", "/v1/invoiceitems"):
            result = listed({"/v1/subscription_schedules": self.schedules,
                             "/v1/invoices": self.invoices, "/v1/invoiceitems": self.pending}[path])
        else:
            raise AssertionError("Unexpected offline Stripe route " + path)
        return httpx.Response(200, json=copy.deepcopy(result))


class BillingRPCModel:
    """Contract simulation: transaction-shaped state, NOT SQL execution/proof."""
    def __init__(self):
        self.requests, self.timeline, self.events, self.operations = [], [], {}, {}
        self.now, self.auth_exists, self.erasing = NOW, True, False
        self.lose_apply_ack = False
        self.account = {"id": str(uuid4()), "user_id": USER_A, "provider": "stripe", "mode": "test",
            "customer_id": "cus_A", "status": "none", "lease_token": None, "lifecycle": "open"}
        self.member = {"source": "billing", "enabled": False, "start": None, "end": None,
                       "reserved": 7, "daily_used": 3, "period_limit": 0, "daily_limit": 0}
        self.receipts = {}

    def claim(self):
        self.account.update(lease_token=str(uuid4()), lease_until=self.now + 180)
        return copy.deepcopy(self.account)

    def __call__(self, request):
        self.requests.append(request)
        assert request.headers["apikey"] == "sb_secret_OfflineFixture"
        assert "Authorization" not in request.headers
        name, p = request.url.path.rsplit("/", 1)[-1], json.loads(request.content)
        self.timeline.append("rpc:" + name)
        result = self.run(name, p)
        if name == "mobile_billing_apply_snapshot" and self.lose_apply_ack:
            self.lose_apply_ack = False
            raise httpx.ReadTimeout("PRIVATE PROVIDER CONTENT", request=request)
        return httpx.Response(200, json=result)

    def run(self, name, p):
        if name == "mobile_billing_release":
            if p["p_lease_token"] == self.account.get("lease_token"):
                self.account["lease_token"] = None
            return {"status": "released"}
        if name == "mobile_billing_begin":
            assert set(p) == {"p_user_id", "p_provider", "p_mode"}
            if self.erasing or p["p_user_id"] != USER_A:
                return {"status": "blocked"}
            return self.claim()
        if name == "mobile_billing_operation":
            kind = p["p_kind"]
            if kind not in self.operations:
                self.operations[kind] = {"id": str(uuid4()), "kind": kind, "state": "pending",
                    "created_at": datetime.fromtimestamp(NOW, timezone.utc).isoformat(),
                    "fingerprint": p["p_fingerprint"], "price_id": p["p_price_id"]}
            operation = self.operations[kind]
            return copy.deepcopy(operation) if operation["fingerprint"] == p["p_fingerprint"] else {"status": "conflict"}
        if name == "mobile_billing_complete_operation":
            operation = next(row for row in self.operations.values() if row["id"] == p["p_operation_id"])
            operation.update(state="complete", object_id=p["p_object_id"])
            if operation["kind"] == "customer":
                self.account["customer_id"] = p["p_object_id"]
            return {"status": "saved"}
        if name == "mobile_billing_event_claim":
            key = p["p_event_id"]
            if key in self.events and self.events[key]["digest"] != p["p_body_sha256"]:
                return {"status": "conflict"}
            if key in self.events and self.events[key]["state"] != "pending":
                return {"status": "duplicate"}
            state = "ignored" if self.erasing or p["p_customer_id"] != self.account["customer_id"] else "pending"
            self.events[key] = {"digest": p["p_body_sha256"], "state": state}
            if state == "ignored":
                return {"status": state}
            account = self.claim()
            self.events[key]["lease_token"] = account["lease_token"]
            return account
        if name == "mobile_billing_apply_snapshot":
            row = self.events[p["p_event_id"]]
            if (self.erasing or p["p_lease_token"] != self.account["lease_token"]
                    or p["p_lease_token"] != row["lease_token"] or self.now >= self.account["lease_until"]):
                return {"status": "stale"}
            s, m = p["p_snapshot"], self.member
            if m["source"] == "billing":
                m["enabled"] = s["eligible"]
                if s["eligible"]:
                    if m["start"] is not None and s["period_start"] > m["start"] and s["period_start"] >= m["end"]:
                        m["reserved"] = 0
                    m.update(start=s["period_start"], end=s["period_end"],
                             period_limit=s["period_limit"], daily_limit=s["daily_limit"])
            row["state"] = "applied"
            return {"status": "applied"}
        if name == "mobile_billing_cancel_claim":
            assert set(p) == {"p_user_id", "p_request_id"} and p["p_user_id"] == USER_A
            if p["p_request_id"] in self.receipts:
                return {"status": "ready"}
            if not self.auth_exists:
                return {"status": "blocked"}
            self.erasing = True
            self.member["enabled"] = False
            if not self.account.get("customer_id") and not self.operations:
                self.receipts[p["p_request_id"]] = True
                return {"status": "ready"}
            return {**self.claim(), "operations": copy.deepcopy(list(self.operations.values()))}
        if name == "mobile_billing_cancel_finish":
            assert p["p_receipt"] == {"customer_id": self.account["customer_id"], "cancellation_confirmed": True,
                "no_future_collection": True, "open_subscription_ids": [], "open_checkout_ids": [],
                "unresolved_operation_ids": []}
            self.receipts[p["p_request_id"]] = True
            return {"status": "ready"}
        raise AssertionError("Unexpected offline RPC " + name)


class SignatureTests(unittest.TestCase):
    def test_exact_raw_body_and_rotation(self):
        raw = event()
        self.assertEqual(verify_stripe_signature(raw, signed(raw) + ",v1=" + "0" * 64, SECRET, now=NOW)["id"], "evt_One")
        for timestamp in (NOW - 300, NOW + 300):
            self.assertEqual(verify_stripe_signature(raw, signed(raw, timestamp), SECRET, now=NOW)["id"], "evt_One")

    def test_tampering_replay_window_ambiguous_headers_and_json_denied(self):
        raw = event()
        cases = [(raw + b" ", signed(raw)), (raw, signed(raw, NOW - 301)), (raw, signed(raw, NOW + 301)),
                 (raw, signed(raw) + ",t=" + str(NOW)), (raw, signed(raw).replace("v1=", "v0=")),
                 (raw, "broken"), (raw, "x" * 4097), (b'{"a":1,"a":2}', signed(b'{"a":1,"a":2}')),
                 (b"x" * (MAX_WEBHOOK_BYTES + 1), signed(b"x" * (MAX_WEBHOOK_BYTES + 1)))]
        for body, signature in cases:
            with self.subTest(length=len(body), header=signature[:10]), self.assertRaises(HTTPException) as caught:
                verify_stripe_signature(body, signature, SECRET, now=NOW)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertNotIn(SECRET, caught.exception.detail)

    def test_live_wrong_version_or_connect_events_rejected(self):
        provider = StripeTestProvider(KEY, SECRET)
        for changes in ({"livemode": True}, {"api_version": "2026-01-01"}, {"account": "acct_Other"}):
            raw = event(**changes)
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as caught:
                provider.verify_webhook(raw, signed(raw), NOW)
            self.assertEqual(caught.exception.status_code, 400)


class ConfigurationTests(unittest.TestCase):
    def test_default_disabled_and_no_admin_env_or_network_required_for_status(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(BillingSettings.from_env().checkout_enabled)
            self.assertEqual(billing_capabilities()["checkout_enabled"], False)
            self.assertEqual(billing_capabilities()["plan_keys"], [])

    def test_live_keys_and_live_mode_rejected(self):
        for key in (KEY.replace("sk_test_", "sk_live_"), "sb_secret_Fixture", "", KEY + "\n"):
            with self.subTest(key_kind=key[:7]), self.assertRaises(HTTPException):
                StripeTestProvider(key, SECRET)
        with self.assertRaises(ValueError):
            replace(CONFIG, mode="live")

    def test_untrusted_plan_shapes_and_caps_rejected(self):
        for catalog in ('[]', '{"a": {"price_id":"price_X","period_limit":true,"daily_limit":1}}',
                        '{"a": {"price_id":"price_X","period_limit":0,"daily_limit":1}}',
                        '{"a":{},"a":{}}'):
            with patch.dict(os.environ, {"MOBILE_BILLING_PLANS": catalog}, clear=True), self.assertRaises(HTTPException):
                BillingSettings.from_env()

    def test_pairwise_privacy_credentials_fallback_and_partial_override_failure(self):
        values = {"MOBILE_PRIVACY_SUPABASE_URL": "https://privacy.example.test",
                  "MOBILE_PRIVACY_SUPABASE_SECRET_KEY": "sb_secret_OfflineFixture"}
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(BillingStore.from_env()._url, values["MOBILE_PRIVACY_SUPABASE_URL"])
            with patch.dict(os.environ, {"MOBILE_BILLING_SUPABASE_URL": "https://other.example.test"}):
                with self.assertRaises(HTTPException):
                    BillingStore.from_env()
        for key in ("sb_publishable_Fixture", "session-a", ""):
            with self.assertRaises(HTTPException):
                BillingStore("https://privacy.example.test", key)

    def test_from_env_pins_real_test_adapter_and_cannot_enable_fake(self):
        values = {"MOBILE_BILLING_PROVIDER": "stripe", "MOBILE_BILLING_STRIPE_SECRET_KEY": KEY,
            "MOBILE_BILLING_STRIPE_WEBHOOK_SECRET": SECRET, "MOBILE_PRIVACY_SUPABASE_URL": "https://privacy.example.test",
            "MOBILE_PRIVACY_SUPABASE_SECRET_KEY": "sb_secret_OfflineFixture"}
        with patch.dict(os.environ, values, clear=True):
            service = BillingService.from_env()
            self.assertIsInstance(service.provider, StripeTestProvider)
            self.assertFalse(billing_capabilities(service)["checkout_enabled"])
            with patch.dict(os.environ, {"MOBILE_BILLING_PROVIDER": "fake", "TESTING": "true"}):
                self.assertIsNone(BillingService.from_env().provider)
                self.assertFalse(billing_capabilities()["configuration_ready"])

    def test_origin_and_request_pins_are_code_owned(self):
        for origin in ("http://api.stripe.com", "https://user@api.stripe.com", "https://api.stripe.com/path",
                       "https://api.stripe.com#secret", "https://api.stripe.com?secret"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                PinnedProviderHTTP(origin=origin, headers={}, allowed_routes=())


class BillingTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rpc, self.stripe = BillingRPCModel(), StripeFixture()
        self.timeline = []
        self.rpc.timeline = self.stripe.timeline = self.timeline
        self.provider = StripeTestProvider(KEY, SECRET, portal_configuration="bpc_Fixture",
            transport=httpx.MockTransport(self.stripe), clock=lambda: NOW)
        self.store = BillingStore("https://billing.example.test", "sb_secret_OfflineFixture",
            transport=httpx.MockTransport(self.rpc))
        self.service = BillingService(CONFIG, self.store, self.provider, clock=lambda: NOW)
        self.repo = SimpleNamespace(user_id=USER_A, verified_email="verified@example.test")

    async def send_event(self, event_id="evt_One", **changes):
        raw = event(event_id, **changes)
        return await self.service.webhook(raw, signed(raw))

    async def test_checkout_verified_identity_server_price_no_entitlement_write(self):
        self.rpc.account["customer_id"] = None
        self.stripe.subscriptions = []
        before = copy.deepcopy(self.rpc.member)
        result = await self.service.checkout(self.repo, "fixture")
        self.assertEqual(result, {"url": "https://checkout.stripe.com/c/pay/fixture"})
        self.assertEqual(self.rpc.member, before)
        creation = next(r for r in self.stripe.requests if r.url.path == "/v1/customers")
        self.assertEqual(parse_qs(creation.content.decode())["email"], [self.repo.verified_email])
        checkout = next(r for r in self.stripe.requests if r.method == "POST" and r.url.path == "/v1/checkout/sessions")
        data = parse_qs(checkout.content.decode())
        self.assertEqual(data["line_items[0][price]"], ["price_Fixture"])
        self.assertEqual(data["customer"], ["cus_A"])
        self.assertEqual(data["client_reference_id"], [USER_A])
        self.assertEqual(data["payment_method_types[0]"], ["card"])
        self.assertEqual(checkout.headers["Idempotency-Key"], self.rpc.operations["checkout"]["id"])

    async def test_checkout_retry_retrieves_saved_session_not_new_subscription(self):
        self.stripe.subscriptions = []
        await self.service.checkout(self.repo, "fixture")
        await self.service.checkout(self.repo, "fixture")
        self.assertEqual(sum(r.method == "POST" and r.url.path == "/v1/checkout/sessions" for r in self.stripe.requests), 1)

    async def test_invalid_plan_disabled_checkout_active_subscription_and_missing_identity(self):
        for service, repo, plan, status in (
            (self.service, self.repo, "price_Attacker", 422),
            (BillingService(replace(CONFIG, checkout_enabled=False), self.store, self.provider), self.repo, "fixture", 503),
            (self.service, SimpleNamespace(user_id=USER_A, verified_email=None), "fixture", 403),
            (self.service, self.repo, "fixture", 409)):
            with self.subTest(status=status), self.assertRaises(HTTPException) as caught:
                await service.checkout(repo, plan)
            self.assertEqual(caught.exception.status_code, status)
        self.assertFalse(any(r.method == "POST" for r in self.stripe.requests))

    async def test_hosted_redirect_and_cross_customer_response_rejected(self):
        self.stripe.subscriptions = []
        for patch_values in ({"url": "https://checkout.stripe.com.attacker.test/path"}, {"customer": "cus_Other"}, {"livemode": True}):
            self.stripe.fault = lambda request: httpx.Response(200, json={**self.stripe.session(), **patch_values}) if request.url.path == "/v1/checkout/sessions" else None
            with self.subTest(values=patch_values), self.assertRaises(HTTPException):
                await self.service.checkout(self.repo, "fixture")
            self.assertFalse(self.rpc.member["enabled"])

    async def test_portal_server_customer_and_configuration_disallow_plan_changes(self):
        result = await self.service.portal(self.repo)
        self.assertEqual(result["url"], "https://billing.stripe.com/p/session/fixture")
        data = parse_qs(self.stripe.requests[-1].content.decode())
        self.assertEqual(data, {"customer": ["cus_A"], "configuration": ["bpc_Fixture"],
                                "return_url": [CONFIG.portal_return_url]})
        self.stripe.portal_config["features"]["subscription_update"]["enabled"] = True
        with self.assertRaises(HTTPException):
            await self.service.portal(self.repo)

    async def test_webhook_claim_precedes_authoritative_fetch_not_client_metadata(self):
        result = await self.send_event()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.timeline[0], "rpc:mobile_billing_event_claim")
        self.assertTrue(self.rpc.member["enabled"])
        self.assertEqual((self.rpc.member["period_limit"], self.rpc.member["daily_limit"]), (100, 10))
        apply = next(r for r in self.rpc.requests if r.url.path.endswith("mobile_billing_apply_snapshot"))
        self.assertNotIn(USER_B, apply.content.decode())
        self.assertEqual(json.loads(apply.content)["p_snapshot"]["period_limit"], 100)

    async def test_replay_same_cycle_and_lost_apply_ack_preserve_spending(self):
        self.rpc.lose_apply_ack = True
        with self.assertRaises(HTTPException):
            await self.send_event()
        calls = len(self.stripe.requests)
        self.assertEqual((await self.send_event())["status"], "duplicate")
        self.assertEqual(len(self.stripe.requests), calls)
        await self.send_event("evt_Second")
        self.assertEqual((self.rpc.member["reserved"], self.rpc.member["daily_used"]), (7, 3))

    async def test_new_cycle_resets_period_only_not_same_day_usage(self):
        self.rpc.member.update(start=NOW - 300, end=NOW - 100)
        await self.send_event()
        self.assertEqual((self.rpc.member["reserved"], self.rpc.member["daily_used"]), (0, 3))

    async def test_out_of_order_old_event_refetches_current_cancelled_state(self):
        await self.send_event("evt_Newer", created=NOW)
        self.stripe.sub["status"] = "canceled"
        await self.send_event("evt_Older", created=NOW - 1000)
        self.assertFalse(self.rpc.member["enabled"])
        self.assertEqual(self.rpc.member["reserved"], 7)

    async def test_manual_membership_is_preserved_for_unrelated_billing_event(self):
        self.rpc.member.update(source="manual", enabled=True)
        before = copy.deepcopy(self.rpc.member)
        self.stripe.sub["status"] = "canceled"
        await self.send_event()
        self.assertEqual(self.rpc.member, before)

    async def test_unknown_customer_or_erasure_ignored_without_fetch(self):
        self.assertEqual((await self.send_event(data={"object": {"customer": "cus_Other"}}))["status"], "ignored")
        self.rpc.erasing = True
        self.assertEqual((await self.send_event("evt_Erased"))["status"], "ignored")
        self.assertEqual(self.stripe.requests, [])

    async def test_same_event_id_different_body_conflicts(self):
        await self.send_event()
        with self.assertRaises(HTTPException):
            await self.send_event(created=1)

    async def test_expired_lease_fences_old_apply(self):
        self.stripe.after_fetch = lambda: setattr(self.rpc, "now", NOW + 181)
        with self.assertRaises(HTTPException):
            await self.send_event()
        self.assertFalse(self.rpc.member["enabled"])
        self.assertEqual(self.rpc.events["evt_One"]["state"], "pending")

    async def test_checkout_completion_without_invoice_payment_does_not_grant(self):
        self.stripe.invoice["paid"] = False
        await self.send_event(type="checkout.session.completed")
        self.assertFalse(self.rpc.member["enabled"])

    async def test_unsupported_or_unpaid_snapshots_are_ineligible(self):
        original = copy.deepcopy((self.stripe.sub, self.stripe.invoice, self.stripe.charge))
        mutations = [("sub", "status", "trialing"), ("sub", "current_period_end", NOW - 1),
            ("sub", "schedule", "sub_sched_A"), ("invoice", "paid_out_of_band", True),
            ("invoice", "subscription", "sub_Other"), ("invoice", "charge", None),
            ("charge", "amount_refunded", 1), ("charge", "disputed", True)]
        for number, (target, key, value) in enumerate(mutations):
            self.stripe.sub, self.stripe.invoice, self.stripe.charge = copy.deepcopy(original)
            self.stripe.subscriptions = [self.stripe.sub]
            getattr(self.stripe, target)[key] = value
            with self.subTest(target=target, key=key):
                await self.send_event("evt_Case" + str(number))
                self.assertFalse(self.rpc.member["enabled"])

    async def test_price_quantity_or_invoice_line_period_cannot_increase_quota(self):
        original_sub, original_invoice = copy.deepcopy((self.stripe.sub, self.stripe.invoice))
        for number, change in enumerate(("price", "quantity", "period", "multisub")):
            with self.subTest(change=change):
                self.service.settings = CONFIG
                self.stripe.sub, self.stripe.invoice = copy.deepcopy((original_sub, original_invoice))
                self.stripe.subscriptions = [self.stripe.sub]
                if change == "price":
                    self.service.settings = replace(CONFIG, plans={"other": Plan("price_Other", 900, 90)})
                elif change == "quantity":
                    self.stripe.sub["items"]["data"][0]["quantity"] = 10
                elif change == "period":
                    self.stripe.invoice["lines"]["data"][0]["period"]["end"] = NOW + 900000
                else:
                    self.stripe.subscriptions.append({**self.stripe.sub, "id": "sub_B"})
                await self.send_event("evt_Bad" + str(number))
                self.assertFalse(self.rpc.member["enabled"])

    async def test_incomplete_pagination_cross_tenant_and_mode_fail_closed(self):
        for payload in ({"object": "list", "has_more": True, "data": []},
                        listed([{**self.stripe.sub, "customer": "cus_Other"}]),
                        listed([{**self.stripe.sub, "livemode": True}])):
            self.stripe.fault = lambda request: httpx.Response(200, json=payload)
            with self.subTest(payload_kind=payload["object"]), self.assertRaises(HTTPException):
                await self.provider.fetch_subscription("cus_A")

    async def test_subscription_pagination_uses_bound_customer_and_detects_ambiguity(self):
        def page(request):
            if request.url.path != "/v1/subscriptions":
                return None
            if "starting_after" not in request.url.params:
                return httpx.Response(200, json={**listed([self.stripe.sub]), "has_more": True})
            self.assertEqual(request.url.params["starting_after"], "sub_A")
            return httpx.Response(200, json=listed([{**self.stripe.sub, "id": "sub_B"}]))
        self.stripe.fault = page
        self.assertEqual((await self.provider.fetch_subscription("cus_A")).status, "ambiguous")

    async def test_transport_paths_redirects_oversize_and_secret_errors_are_bounded(self):
        for method, path in (("POST", "/v1/charges"), ("GET", "https://evil.test/v1/subscriptions"),
                             ("GET", "/v1/subscriptions/../customers"), ("GET", "/v1/subscriptions?x=secret")):
            with self.assertRaises(HTTPException):
                await self.provider.http.request(method, path)
        self.assertEqual(self.stripe.requests, [])
        for response in (httpx.Response(302, headers={"Location": "https://evil.test"}),
                         httpx.Response(500, text=KEY), httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)),
                         httpx.Response(200, content=b'{"a":1,"a":2}')):
            self.stripe.fault = lambda request: response
            before = len(self.stripe.requests)
            with self.assertRaises(HTTPException) as caught:
                await self.provider.fetch_subscription("cus_A")
            self.assertEqual(caught.exception.status_code, 503)
            self.assertNotIn(KEY, caught.exception.detail)
            self.assertEqual(len(self.stripe.requests), before + 1)

    async def test_missing_migration_returns_actionable_503(self):
        store = BillingStore("https://billing.example.test", "sb_secret_OfflineFixture",
            transport=httpx.MockTransport(lambda request: httpx.Response(404, json={"code": "PGRST202", "detail": KEY})))
        with self.assertRaises(HTTPException) as caught:
            await store.rpc("mobile_billing_begin", p_user_id=USER_A)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("0009_billing.sql", caught.exception.detail)
        self.assertNotIn(KEY, caught.exception.detail)

    async def test_capabilities_only_advertise_usable_config_without_network_or_secret_fields(self):
        result = billing_capabilities(self.service)
        self.assertTrue(result["checkout_enabled"])
        self.assertEqual(result["plan_keys"], ["fixture"])
        self.assertNotIn("price_Fixture", json.dumps(result))
        self.assertNotIn(KEY, json.dumps(result))
        self.assertEqual(self.rpc.requests + self.stripe.requests, [])
        self.assertFalse(billing_capabilities(BillingService(CONFIG, self.store))["checkout_enabled"])
        self.provider._portal_configuration = ""
        self.assertFalse(billing_capabilities(self.service)["checkout_enabled"])
        with self.assertRaises(HTTPException):
            await self.service.checkout(self.repo, "fixture")
        self.assertEqual(self.rpc.requests + self.stripe.requests, [])

    async def test_pending_checkout_lost_ack_retries_identical_key_and_body(self):
        self.stripe.subscriptions = []
        lost = [False]
        def fault(request):
            if request.method == "POST" and request.url.path == "/v1/checkout/sessions" and not lost[0]:
                lost[0] = True
                self.stripe.sessions = [self.stripe.session()]
                raise httpx.ReadTimeout("PRIVATE LOST ACK", request=request)
        self.stripe.fault = fault
        with self.assertRaises(HTTPException):
            await self.service.checkout(self.repo, "fixture")
        self.assertEqual(self.rpc.operations["checkout"]["state"], "pending")
        await self.service.checkout(self.repo, "fixture")
        posts = [r for r in self.stripe.requests if r.method == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"])
        self.assertEqual(posts[0].content, posts[1].content)
        self.assertFalse(self.rpc.member["enabled"])

    async def test_aged_pending_creation_fails_before_provider_post(self):
        operation = {"id": str(uuid4()), "state": "pending",
                     "created_at": datetime.fromtimestamp(NOW - 23 * 3600, timezone.utc).isoformat()}
        with self.assertRaises(HTTPException):
            await self.provider.create_customer(USER_A, "verified@example.test", operation)
        self.assertFalse(self.stripe.requests)

    async def test_ignored_signed_event_has_no_rpc_or_provider_fetch(self):
        result = await self.send_event(type="irrelevant.event")
        self.assertEqual(result["status"], "ignored")
        self.assertEqual(self.rpc.requests + self.stripe.requests, [])

    async def test_worker_default_uses_privacy_pair_without_email_or_provider_for_never_billed(self):
        self.rpc.account["customer_id"] = None
        env = {"MOBILE_PRIVACY_SUPABASE_URL": "https://privacy.example.test",
               "MOBILE_PRIVACY_SUPABASE_SECRET_KEY": "sb_secret_OfflineFixture"}
        with patch.dict(os.environ, env, clear=True):
            result = await cancel_for_erasure(USER_A, REQUEST_ID, store_transport=httpx.MockTransport(self.rpc))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.rpc.requests[0].url.host, "privacy.example.test")
        self.assertEqual(json.loads(self.rpc.requests[0].content), {"p_user_id": USER_A, "p_request_id": REQUEST_ID})
        self.assertFalse(self.stripe.requests)

    async def test_legacy_service_role_jwt_uses_bearer_but_caller_jwt_is_denied(self):
        def jwt(role):
            encode = lambda body: base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
            return encode({"alg": "HS256"}) + "." + encode({"role": role}) + ".fixture"
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"status": "ready"})
        key = jwt("service_role")
        store = BillingStore("https://billing.example.test", key, transport=httpx.MockTransport(handler))
        await store.rpc("mobile_billing_cancel_claim", p_user_id=USER_A, p_request_id=REQUEST_ID)
        self.assertEqual(requests[0].headers["apikey"], key)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer " + key)
        with self.assertRaises(HTTPException):
            BillingStore("https://billing.example.test", jwt("authenticated"))

    async def test_erasure_immediate_no_proration_refund_then_refetched_receipt(self):
        self.stripe.sessions = [self.stripe.session()]
        result = await cancel_for_erasure(USER_A, REQUEST_ID, service=self.service)
        self.assertEqual(result["status"], "ready")
        self.assertFalse(self.rpc.member["enabled"])
        mutations = [(r.method, r.url.path) for r in self.stripe.requests if r.method != "GET"]
        self.assertEqual(mutations, [("POST", "/v1/checkout/sessions/cs_test_Fixture/expire"),
                                    ("DELETE", "/v1/subscriptions/sub_A")])
        self.assertEqual(self.stripe.sub["status"], "canceled")
        self.assertTrue(self.timeline.index("rpc:mobile_billing_cancel_finish") >
                        self.timeline.index("provider:GET:/v1/invoiceitems"))

    async def test_lost_auth_delete_ack_uses_request_tombstone_no_duplicate_cancellation(self):
        self.assertEqual((await self.service.cancel_for_erasure(USER_A, REQUEST_ID))["status"], "ready")
        self.rpc.auth_exists = False
        self.rpc.account["user_id"] = None
        before = len(self.stripe.requests)
        self.assertEqual((await prepare_account_erasure(USER_A, REQUEST_ID, service=self.service))["status"], "ready")
        self.assertEqual(len(self.stripe.requests), before)
        self.assertEqual((await self.service.cancel_for_erasure(USER_A, str(uuid4())))["status"], "blocked")

    async def test_no_mapping_without_auth_or_receipt_is_blocked(self):
        self.rpc.auth_exists = False
        self.rpc.account["customer_id"] = None
        self.assertEqual((await self.service.cancel_for_erasure(USER_A, REQUEST_ID))["status"], "blocked")
        self.assertFalse(self.stripe.requests)

    async def test_never_billed_sql_evidence_ready_without_provider(self):
        self.rpc.account["customer_id"] = None
        service = BillingService(CONFIG, self.store)
        self.assertEqual((await service.cancel_for_erasure(USER_A, REQUEST_ID))["status"], "ready")
        self.assertIn(REQUEST_ID, self.rpc.receipts)

    async def test_active_billing_without_provider_blocks_before_ready(self):
        self.assertEqual((await BillingService(CONFIG, self.store).cancel_for_erasure(USER_A, REQUEST_ID))["status"], "blocked")
        self.assertEqual(self.rpc.receipts, {})

    async def test_pending_creation_cannot_be_replayed_by_erasure_worker(self):
        self.rpc.operations["checkout"] = {"id": str(uuid4()), "state": "pending", "kind": "checkout"}
        self.assertEqual((await self.service.cancel_for_erasure(USER_A, REQUEST_ID))["status"], "blocked")
        self.assertFalse(self.stripe.requests)

    async def test_cancelled_subscription_with_outstanding_invoices_or_schedules_stays_blocked(self):
        for obstacle in ("invoice", "schedule", "pending"):
            self.stripe.invoices, self.stripe.schedules, self.stripe.pending = [], [], []
            if obstacle == "invoice":
                self.stripe.invoices = [{**self.stripe.invoice, "status": "open"}]
            elif obstacle == "schedule":
                self.stripe.schedules = [{"id": "sub_sched_A", "object": "subscription_schedule",
                    "livemode": False, "customer": "cus_A", "status": "not_started"}]
            else:
                self.stripe.pending = [{"id": "ii_A", "object": "invoiceitem", "livemode": False, "customer": "cus_A"}]
            with self.subTest(obstacle=obstacle):
                self.assertEqual((await self.service.cancel_for_erasure(USER_A, REQUEST_ID))["status"], "blocked")
                self.assertEqual(self.rpc.receipts, {})

    async def test_cancellation_lost_ack_refetch_prevents_second_delete(self):
        lost = [False]
        def fault(request):
            if request.method == "DELETE" and not lost[0]:
                lost[0] = True
                self.stripe.sub["status"] = "canceled"
                raise httpx.ReadTimeout("PRIVATE", request=request)
        self.stripe.fault = fault
        self.assertEqual((await self.service.cancel_for_erasure(USER_A, REQUEST_ID))["status"], "blocked")
        self.assertEqual((await self.service.cancel_for_erasure(USER_A, REQUEST_ID))["status"], "ready")
        self.assertEqual(sum(r.method == "DELETE" for r in self.stripe.requests), 1)

    async def test_forged_or_partial_receipt_cannot_finish(self):
        receipts = [CancellationReceipt("cus_Other", True, True), CancellationReceipt("cus_A", True, False),
            CancellationReceipt("cus_A", True, True, open_checkout_ids=("cs_test_StillOpen",)),
            CancellationReceipt("cus_A", True, True, unresolved_operation_ids=(str(uuid4()),))]
        for receipt in receipts:
            with patch.object(self.provider, "cancel_for_erasure", return_value=receipt):
                self.assertEqual((await self.service.cancel_for_erasure(USER_A, REQUEST_ID))["status"], "blocked")
            self.assertEqual(self.rpc.receipts, {})


class BillingAPIContractTests(unittest.TestCase):
    def setUp(self):
        self.fake, self.rpc, self.stripe = FakeSupabase(), BillingRPCModel(), StripeFixture()
        self.fake.auth_emails[USER_A] = {"email": "candidate-a@example.test", "email_confirmed_at": "2026-01-01T00:00:00Z"}
        self.fake.auth_emails[USER_B] = {"email": "candidate-b@example.test", "email_confirmed_at": "2026-01-01T00:00:00Z"}
        self.stripe.subscriptions = []
        provider = StripeTestProvider(KEY, SECRET, portal_configuration="bpc_Fixture",
            transport=httpx.MockTransport(self.stripe), clock=lambda: NOW)
        self.service = BillingService(CONFIG, BillingStore("https://billing.example.test", "sb_secret_OfflineFixture",
            transport=httpx.MockTransport(self.rpc)), provider, clock=lambda: NOW)
        self.client = TestClient(create_app(settings=SETTINGS, transport=httpx.MockTransport(self.fake), billing=self.service))
        self.addCleanup(self.client.close)
        self.auth = {"Authorization": "Bearer session-a"}
        self.env = patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": "candidate-a@example.test"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_billing_routes_require_verified_invited_identity_but_not_paid_membership(self):
        for method, route, body in (("GET", "/api/mobile/billing", None),
                ("POST", "/api/mobile/billing/checkout", {"plan_key": "fixture"}),
                ("POST", "/api/mobile/billing/portal", {})):
            self.assertEqual(self.client.request(method, route, json=body).status_code, 401)
            response = self.client.request(method, route, json=body, headers={"Authorization": "Bearer session-b"})
            self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/mobile/billing/checkout", json={"plan_key": "fixture"}, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(any(r.url.path.endswith("mobile_check_access") for r in self.fake.requests))

    def test_api_rejects_customer_price_quota_and_redirect_injection(self):
        for field in ("user_id", "customer_id", "price_id", "quota", "success_url"):
            response = self.client.post("/api/mobile/billing/checkout", headers=self.auth,
                json={"plan_key": "fixture", field: "injected"})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.post("/api/mobile/billing/portal", headers=self.auth,
            json={"customer_id": "cus_Other"}).status_code, 422)
        self.assertFalse(self.stripe.requests)

    def test_webhook_exact_bytes_single_signature_and_bounded_body(self):
        raw = event()
        for headers in ({}, [("stripe-signature", signed(raw)), ("stripe-signature", signed(raw))],
                        {"stripe-signature": signed(raw), "billing-signature": signed(raw)}):
            response = self.client.post("/api/mobile/billing/webhook", content=raw, headers=headers)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/mobile/billing/webhook", content=raw + b" ",
            headers={"stripe-signature": signed(raw)}).status_code, 400)
        self.assertEqual(self.client.post("/api/mobile/billing/webhook", content=b"x" * (MAX_WEBHOOK_BYTES + 1),
            headers={"stripe-signature": signed(raw)}).status_code, 413)
        response = self.client.post("/api/mobile/billing/webhook", content=raw, headers={"stripe-signature": signed(raw)})
        self.assertEqual(response.status_code, 200, response.text)


class BillingSQLStructuralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = (ROOT / "supabase/migrations/0009_billing.sql").read_text()
        cls.privacy = (ROOT / "supabase/migrations/0010_account_privacy.sql").read_text()

    def routine(self, name):
        return self.sql.split("create function public." + name + "(", 1)[1].split("$$;", 1)[0]

    def test_service_only_rpc_grants_role_checks_and_definer_search_paths(self):
        names = re.findall(r"create function public\.(mobile_billing_\w+)\(", self.sql)
        for name in names:
            with self.subTest(name=name):
                routine = self.routine(name)
                if name == "mobile_billing_guard_auth_delete":
                    self.assertIn("revoke all on function public." + name, self.sql)
                elif name != "mobile_billing_require_service":
                    self.assertIn("perform public.mobile_billing_require_service()", routine)
                    self.assertIn("security definer set search_path = ''", routine)
                    self.assertIn("'" + name + "(", self.sql)
        self.assertIn("from public, anon, authenticated, service_role", self.sql)
        self.assertIn("to service_role'", self.sql)
        self.assertIn("auth.role() is distinct from 'service_role'", self.sql)

    def test_safe_view_owner_rls_no_vendor_pii_or_client_writes(self):
        for table in ("accounts", "events", "operations"):
            self.assertIn("alter table public.mobile_billing_" + table + " enable row level security", self.sql)
        view = self.sql.split("create view public.mobile_billing_export", 1)[1].split(";", 1)[0]
        self.assertIn("security_invoker = true", view)
        for column in BILLING_EXPORT_COLUMNS:
            self.assertIn(column, view)
        for forbidden in ("customer_id", "subscription_id", "email", "redirect_url", "body_sha256"):
            self.assertNotIn(forbidden, view)
        self.assertIn("using ((select auth.uid()) = user_id)", self.sql)
        self.assertNotRegex(self.sql, r"grant (?:insert|update|delete|all)[^;]+to authenticated")

    def test_manual_members_preserved_and_only_new_nonoverlap_period_renews(self):
        apply = self.routine("mobile_billing_apply_snapshot")
        self.assertIn("default 'manual'", self.sql)
        self.assertIn("m.grant_source = 'billing' and m.billing_account_id = a.id", apply)
        self.assertIn("v_start > m.period_start", apply)
        self.assertIn("v_start >= m.period_end then 0 else m.period_reserved end", apply)
        self.assertNotRegex(apply, r"(?:update|delete from) public.mobile_ai_(?:daily_usage|reservations)")
        completion = self.routine("mobile_billing_complete_operation")
        self.assertNotRegex(completion, r"(?:insert into|update) public.mobile_usage_memberships")

    def test_event_hash_fences_locks_and_atomic_application(self):
        claim, apply = self.routine("mobile_billing_event_claim"), self.routine("mobile_billing_apply_snapshot")
        self.assertIn("for update", claim)
        self.assertIn("e.body_sha256 is distinct from p_body_sha256", claim)
        self.assertIn("e.state <> 'pending'", claim)
        self.assertIn("p_lease_token is null", apply)
        self.assertIn("a.lease_until <= clock_timestamp()", apply)
        self.assertIn("e.lease_token is distinct from p_lease_token", apply)
        self.assertLess(apply.index("for update"), apply.index("update public.mobile_usage_memberships"))
        self.assertLess(apply.index("update public.mobile_usage_memberships"), apply.index("set state = 'applied'"))

    def test_erasure_tombstone_precedes_auth_lookup_and_never_billed_requires_evidence(self):
        claim = self.routine("mobile_billing_cancel_claim")
        self.assertIn("erasure_request_id uuid unique", self.sql)
        self.assertIn("on delete set null", self.sql)
        self.assertLess(claim.index("erasure_request_id = p_request_id"), claim.index("from auth.users"))
        self.assertIn("a.cancellation_status = 'confirmed'", claim)
        self.assertIn("a.user_id is null", claim)
        self.assertLess(claim.index("from auth.users"), claim.index("insert into public.mobile_billing_accounts"))
        self.assertIn("if not found then return jsonb_build_object('status', 'blocked')", claim)
        self.assertIn("a.customer_id is null and a.subscription_id is null and a.status = 'none'", claim)
        self.assertIn("jsonb_array_length(v_operations) = 0", claim)
        self.assertIn("cancellation_status <> 'confirmed'", self.routine("mobile_billing_guard_auth_delete"))

    def test_0010_dependency_fences_membership_and_exports_safe_view(self):
        self.assertIn("to_regclass('public.mobile_privacy_requests')", self.sql)
        self.assertIn("kind=''erase''", self.routine("mobile_billing_user_erasing"))
        for name in ("mobile_billing_begin", "mobile_billing_operation", "mobile_billing_complete_operation",
                     "mobile_billing_event_claim", "mobile_billing_apply_snapshot"):
            self.assertIn("mobile_billing_user_erasing", self.routine(name))
        self.assertIn("mobile_billing_export", self.privacy)
        self.assertIn("mobile_has_access", self.privacy)
        self.assertIn("new.enabled", self.privacy.lower())


if __name__ == "__main__":
    unittest.main()
