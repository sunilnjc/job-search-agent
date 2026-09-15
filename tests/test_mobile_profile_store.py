"""JP-024 helper transport/validation tests and SQL-source checks, all offline.

Mock responses DO NOT demonstrate PostgreSQL transaction or RLS behavior. The
separate test_mobile_profile_sql module supplies a disposable native SQL harness.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from jobagent.mobile.profile_store import MAX_RESPONSE_BYTES, RPC_PATH, save_profile
from jobagent.mobile.repository import MobileRepository

OWNER = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
PRIVATE = "SYNTHETIC_PRIVATE_PROFILE_DIAGNOSTIC"
NEUTRAL = {"profession": "", "experience_level": "unspecified", "qualifications": []}
ROOT = Path(__file__).resolve().parents[1]


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        for method in ("connect", "connect_ex", "sendto"):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError("No real network")))
        self.stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=AssertionError("No DNS")))
        self.requests = []
        self.result = {"user_id": OWNER, "display_name": "Synthetic Name", "phone": "Existing phone",
                       "base_location": None, "timezone": "UTC", "onboarding_completed_at": None,
                       "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-02T00:00:00Z",
                       "career_text": "Existing synthetic career", "career_background": copy.deepcopy(NEUTRAL)}
        self.response = None
        self.fault = None

    def call(self, payload):
        def dispatch(request):
            self.requests.append(request)
            if self.fault:
                raise self.fault(request)
            return self.response if self.response is not None else httpx.Response(200, json=self.result)

        async def invoke():
            async with httpx.AsyncClient(base_url="https://profile.invalid", transport=httpx.MockTransport(dispatch),
                                         headers={"Authorization": "Bearer synthetic-only"}) as client:
                return await save_profile(MobileRepository(client, OWNER), patch=payload)
        return asyncio.run(invoke())

    def test_one_rpc_only_supplied_fields_and_full_merged_result(self):
        result = self.call({"display_name": " Synthetic Name "})
        self.assertEqual(result, self.result)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual((request.method, request.url.path), ("POST", RPC_PATH))
        self.assertEqual(json.loads(request.content), {"p_patch": {"display_name": "Synthetic Name"}})
        self.assertEqual(request.headers["Authorization"], "Bearer synthetic-only")
        self.assertEqual(request.url.query, b"")

    def test_empty_patch_never_fills_omitted_values(self):
        self.call({})
        self.assertEqual(json.loads(self.requests[0].content), {"p_patch": {}})

    def test_explicit_nulls_are_not_discarded(self):
        payload = {"phone": None, "onboarding_completed_at": None, "career_text": None, "career_background": None}
        self.call(payload)
        self.assertEqual(json.loads(self.requests[0].content), {"p_patch": payload})

    def test_background_replacement_preserves_qualification_defaults_and_iso_dates(self):
        self.call({"career_background": {"profession": "Teaching", "qualifications": [
            {"name": "Synthetic qualification", "kind": "education", "expires_on": "2028-02-29"}]}})
        background = json.loads(self.requests[0].content)["p_patch"]["career_background"]
        self.assertEqual(background["experience_level"], "unspecified")
        self.assertEqual(background["qualifications"][0], {"name": "Synthetic qualification", "kind": "education",
                         "status": "unknown", "jurisdiction": "", "expires_on": "2028-02-29", "evidence_note": ""})

    def test_qualification_validation_rejects_unknown_verified_and_invalid_dates_before_rpc(self):
        valid = {"name": "Synthetic qualification", "kind": "licence"}
        for invalid in ({**valid, "status": "verified"}, {**valid, "verified": True},
                        {**valid, "expires_on": "2026-02-30"}, {**valid, "expires_on": "2026-1-01"},
                        {**valid, "name": " "}, {**valid, "name": 7}, {**valid, "kind": "license"}):
            with self.subTest(invalid=invalid), self.assertRaises(HTTPException) as caught:
                self.call({"display_name": "Changed", "career_background": {"qualifications": [invalid]}})
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.requests, [])

    def test_owner_operational_preferences_and_nonobject_inputs_are_rejected(self):
        for invalid in ({"user_id": OTHER}, {"created_at": "2099-01-01"}, {"enabled": True},
                        {"preferences": {}}, {"career_text": PRIVATE, "unexpected": True}, None, [], "not object"):
            with self.subTest(kind=type(invalid).__name__), self.assertRaises(HTTPException) as caught:
                self.call(invalid)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertNotIn(PRIVATE, str(caught.exception.detail))
        self.assertEqual(self.requests, [])

    def test_profile_and_career_limits_validate_before_rpc(self):
        for invalid in ({"display_name": "x" * 161}, {"phone": "x" * 61}, {"base_location": "x" * 241},
                        {"timezone": "x" * 101}, {"career_text": "x" * 100001},
                        {"onboarding_completed_at": "2026-09-01T12:00:00"}):
            with self.subTest(field=next(iter(invalid))), self.assertRaises(HTTPException) as caught:
                self.call(invalid)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.requests, [])

    def test_malformed_foreign_or_extra_response_is_not_returned_as_success(self):
        original = copy.deepcopy(self.result)
        for invalid in ([], {}, {**original, "user_id": OTHER}, {**original, "private_internal": PRIVATE},
                        {**original, "career_background": {"qualifications": [{"verified": True}]}},
                        {**original, "career_background": None}, {**original, "created_at": None}):
            self.response = httpx.Response(200, json=invalid)
            with self.subTest(kind=type(invalid).__name__), self.assertRaises(HTTPException) as caught:
                self.call({"display_name": "Changed"})
            self.assertEqual(caught.exception.status_code, 503)
            self.assertNotIn(PRIVATE, str(caught.exception.detail))
        self.assertEqual(len(self.requests), 7)

    def test_invalid_json_and_duplicate_keys_are_unconfirmed_not_silently_accepted(self):
        for raw in (b"invalid json", b'{"user_id":"first","user_id":"second"}'):
            self.response = httpx.Response(200, content=raw)
            with self.assertRaises(HTTPException) as caught:
                self.call({})
            self.assertEqual(caught.exception.detail["code"], "profile_save_unconfirmed")

    def test_lost_ack_has_no_retry_fallback_or_claim_of_rollback(self):
        self.fault = lambda request: httpx.ReadTimeout(PRIVATE, request=request)
        with self.assertRaises(HTTPException) as caught:
            self.call({"display_name": "Changed", "career_text": "Changed career"})
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail["code"], "profile_save_unconfirmed")
        self.assertIn("fully committed", caught.exception.detail["message"])
        self.assertNotIn(PRIVATE, str(caught.exception.detail))
        self.assertEqual(len(self.requests), 1)

    def test_missing_rpc_redirect_or_upstream_failure_never_falls_back(self):
        for status in (301, 302, 400, 404, 500, 503):
            self.response = httpx.Response(status, text=PRIVATE, headers={"Location": "https://elsewhere.invalid/"})
            before = len(self.requests)
            with self.subTest(status=status), self.assertRaises(HTTPException) as caught:
                self.call({})
            self.assertEqual(caught.exception.status_code, 503)
            self.assertNotIn(PRIVATE, str(caught.exception.detail))
            self.assertEqual(len(self.requests), before + 1)

    def test_expected_error_statuses_are_safe_and_preserved(self):
        for status in (401, 403, 409, 422):
            self.response = httpx.Response(status, text=PRIVATE)
            with self.subTest(status=status), self.assertRaises(HTTPException) as caught:
                self.call({})
            self.assertEqual(caught.exception.status_code, status)
            self.assertNotIn(PRIVATE, str(caught.exception.detail))

    def test_retry_guidance_is_bounded_without_forwarding_private_headers(self):
        for raw, expected in (("17", "17"), ("0", "1"), ("999999999", "3600"), ("date or junk", "60")):
            self.response = httpx.Response(429, text=PRIVATE, headers={"Retry-After": raw, "Private-Debug": PRIVATE})
            with self.assertRaises(HTTPException) as caught:
                self.call({})
            self.assertEqual(caught.exception.status_code, 429)
            self.assertEqual(caught.exception.headers, {"Retry-After": expected})

    def test_oversized_success_is_unconfirmed_without_retry(self):
        self.response = httpx.Response(200, content=b" " * (MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(HTTPException) as caught:
            self.call({})
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(len(self.requests), 1)

    def test_absent_context_and_sql_valid_partial_background_have_neutral_defaults(self):
        self.result["career_text"] = None
        self.result["career_background"] = {}
        self.assertEqual(self.call({})["career_background"], NEUTRAL)


class AtomicProfileSQLSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = (ROOT / "supabase/migrations/0008_atomic_profile.sql").read_text().lower()
        cls.code = "\n".join(line for line in cls.sql.splitlines() if not line.lstrip().startswith("--"))

    def test_invoker_uses_auth_identity_membership_and_no_owner_parameter(self):
        self.assertIn("mobile_save_profile(p_patch jsonb)", self.code)
        self.assertIn("security invoker", self.code)
        self.assertNotIn("security definer", self.code)
        self.assertIn("set search_path = ''", self.code)
        self.assertIn("v_user uuid := auth.uid()", self.code)
        self.assertIn("auth.role() is distinct from 'authenticated'", self.code)
        self.assertLess(self.code.index("public.mobile_has_access()"), self.code.index("insert into public.profiles"))

    def test_only_function_execute_granted_no_table_policy_or_constraint_changes(self):
        grants = [line.strip() for line in self.code.splitlines() if line.strip().startswith("grant ")]
        self.assertEqual(grants, ["grant execute on function public.mobile_save_profile(jsonb) to authenticated;"])
        self.assertIn("from public, anon, authenticated, service_role", self.code)
        for prohibited in ("alter table", "create policy", "drop policy", "drop constraint", "delete from", "execute format"):
            self.assertNotIn(prohibited, self.code)

    def test_existing_qualification_validator_and_bounded_known_keys_precede_writes(self):
        self.assertIn("public.mobile_career_background_is_valid(v_background) is distinct from true", self.code)
        self.assertIn("octet_length(p_patch::text) > 1048576", self.code)
        self.assertIn("p_patch - array[", self.code)
        self.assertLess(self.code.index("mobile_career_background_is_valid"), self.code.index("insert into public.profiles"))

    def test_row_lock_patch_semantics_and_uncaught_write_failure(self):
        self.assertIn("where user_id = v_user for update", self.code)
        self.assertLess(self.code.index("for update"), self.code.index("update public.profiles"))
        for key in ("phone", "display_name", "career_text", "career_background"):
            self.assertIn("case when p_patch ? '" + key + "'", self.code)
        self.assertNotIn("exception", self.code[self.code.index("insert into public.profiles"):])
        self.assertNotIn("job_preferences", self.code)
