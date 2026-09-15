"""Live-mode CODE acceptance with offline MockTransport only. NOT live activation.

No HTTP provider calls, actual live keys, customer email or real payments.
"""
import copy
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from jobagent.mobile.billing import BillingService, BillingSettings, StripeLiveProvider, StripeTestProvider, unavailable
from test_mobile_billing import StripeFixture, CONFIG, KEY, SECRET, NOW, USER_A, event, signed

LIVE_FIXTURE_KEY = "sk_live_OfflineFixtureOnly"


class LiveStripeFixture:
    def __init__(self):
        self.test = StripeFixture()
        self.requests = []
        self.cross_mode = False

    def __call__(self, request):
        assert request.headers["Authorization"] == "Bearer " + LIVE_FIXTURE_KEY
        assert request.url.host == "api.stripe.com"
        self.requests.append(request)
        # Reuse existing fixture data, not a production adapter or fake switch.
        surrogate = httpx.Request(request.method, str(request.url).replace("cs_live_", "cs_test_"),
            content=request.content, headers={**dict(request.headers), "authorization":"Bearer "+KEY})
        result = self.test(surrogate)
        def convert(value):
            if isinstance(value, dict):
                return {k: True if k=="livemode" and not self.cross_mode else convert(v) for k,v in value.items()}
            if isinstance(value, list): return [convert(item) for item in value]
            if isinstance(value, str): return value.replace("cs_test_", "cs_live_")
            return value
        return httpx.Response(result.status_code, json=convert(result.json()))


class LiveActivationTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, fixture):
        return StripeLiveProvider(LIVE_FIXTURE_KEY, SECRET, activation_approved=True,
            portal_configuration="bpc_Fixture",transport=httpx.MockTransport(fixture),clock=lambda:NOW)

    async def test_default_and_cross_mode_credentials_fail_before_transport(self):
        fixture=LiveStripeFixture()
        for approved,key in ((False,LIVE_FIXTURE_KEY),(True,KEY),("true",LIVE_FIXTURE_KEY)):
            with self.assertRaises(HTTPException):
                StripeLiveProvider(key,SECRET,activation_approved=approved,transport=httpx.MockTransport(fixture))
        with self.assertRaises(HTTPException):
            StripeTestProvider(LIVE_FIXTURE_KEY,SECRET,transport=httpx.MockTransport(fixture))
        with self.assertRaises(ValueError):
            replace(CONFIG,mode="live")
        self.assertEqual(fixture.requests,[])

    async def test_no_test_credential_catalog_or_redirect_fallback_in_live_mode(self):
        values={"MOBILE_BILLING_PROVIDER":"stripe","MOBILE_BILLING_MODE":"live",
            "MOBILE_BILLING_LIVE_ACTIVATION_APPROVED":"true","MOBILE_BILLING_STRIPE_SECRET_KEY":KEY,
            "MOBILE_BILLING_STRIPE_WEBHOOK_SECRET":SECRET,"MOBILE_BILLING_PLANS":json.dumps({"test_only":{"price_id":"price_Test","period_limit":100,"daily_limit":10}}),
            "MOBILE_BILLING_SUCCESS_URL":"https://test.example/success"}
        with patch.dict("os.environ",values,clear=True):
            settings=BillingSettings.from_env()
            self.assertEqual(settings.plans,{})
            self.assertEqual(settings.success_url,"")
            self.assertFalse(settings.checkout_enabled)
            with self.assertRaises(HTTPException): BillingService.from_env()

    async def test_live_price_payment_proof_and_period_end_cancel_use_matching_objects(self):
        fixture=LiveStripeFixture(); provider=self.provider(fixture)
        self.assertEqual((await provider.price_details("price_Fixture"))["amount_minor"],321)
        snapshot=await provider.fetch_subscription("cus_A")
        self.assertTrue(snapshot.paid)
        fixture.test.sub["cancel_at_period_end"]=True
        self.assertTrue((await provider.fetch_subscription("cus_A")).cancel_at_period_end)
        await provider.create_portal("cus_A",CONFIG.portal_return_url)
        fixture.cross_mode=True
        with self.assertRaises(HTTPException): await provider.price_details("price_Fixture")
        with self.assertRaises(HTTPException): await provider.fetch_subscription("cus_A")
        self.assertFalse(any(r.method=="DELETE" for r in fixture.requests))

    async def test_cross_mode_webhooks_rejected_even_with_valid_signature(self):
        provider=self.provider(LiveStripeFixture())
        raw=event(livemode=True)
        self.assertEqual(provider.verify_webhook(raw,signed(raw),NOW).id,"evt_One")
        for changes in ({"livemode":False},{"livemode":True,"account":"acct_Untrusted"},{"livemode":True,"api_version":"2026-01-01"}):
            raw=event(**changes)
            with self.assertRaises(HTTPException): provider.verify_webhook(raw,signed(raw),NOW)

    async def test_database_gate_failure_precedes_any_live_checkout_write(self):
        fixture=LiveStripeFixture(); fixture.test.subscriptions=[]
        store=SimpleNamespace(rpc=AsyncMock(side_effect=unavailable()))
        service=BillingService(replace(CONFIG,mode="live",live_activation_approved=True),store,self.provider(fixture),clock=lambda:NOW)
        with self.assertRaises(HTTPException):
            await service.checkout(SimpleNamespace(user_id=USER_A,verified_email="synthetic@fixture.invalid"),"fixture")
        self.assertEqual(store.rpc.call_args.kwargs["p_mode"],"live")
        self.assertTrue(fixture.requests)
        self.assertTrue(all(r.method=="GET" for r in fixture.requests))

    async def test_live_mode_requires_independently_approved_settings_and_adapter(self):
        with self.assertRaises(ValueError):
            BillingService(CONFIG,None,self.provider(LiveStripeFixture()))
        configured=replace(CONFIG,mode="live",live_activation_approved=True)
        with self.assertRaises(ValueError):
            BillingService(configured,None,StripeTestProvider(KEY,SECRET))
