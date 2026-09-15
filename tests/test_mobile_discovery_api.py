"""Real discovery route + real discovery service; all external transport is fake.

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -p test_mobile_discovery_api.py -v
Exercises FastAPI auth/access, RLS-client ownership checks, raw body middleware,
HTTP outcomes and read-only behavior. NOT live Supabase/PostgreSQL/RLS proof.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import unittest
from contextlib import ExitStack, asynccontextmanager
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from jobagent.mobile import app as mobile_app
from jobagent.mobile.discovery import DiscoveryConfig, DiscoveryService
from jobagent.mobile.schemas import JobCreate
from test_mobile_api import FakeSupabase, SETTINGS, USER_A, USER_B, fake_studio
from test_mobile_discovery import BOARD_A, BOARD_G, BOARD_L, SyntheticFeeds, ashby, response


PATH = "/api/mobile/discovery/search"
ALLOWED = "candidate-a@example.test,candidate-b@example.test"
PRIVATE_MARKER = "SYNTHETIC_PRIVATE_DO_NOT_ECHO"


class MobileDiscoveryAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": ALLOWED}, clear=True))
        for name in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection"):
            self.stack.enter_context(patch(name, side_effect=AssertionError("Real network forbidden")))
        self.supabase, self.feeds = FakeSupabase(), SyntheticFeeds()
        for user, email in ((USER_A, "candidate-a@example.test"), (USER_B, "candidate-b@example.test")):
            self.supabase.auth_emails[user] = {"email": email, "email_confirmed_at": "2026-09-01T00:00:00Z"}
        self.studio = fake_studio()
        self.app = self.make_app(None)
        original_lifespan = self.app.router.lifespan_context
        @asynccontextmanager
        async def fixture_lifespan(application):
            # Preserve the actual app hook (including privacy logging). Startup
            # event handlers are not run when FastAPI has a custom lifespan.
            async with original_lifespan(application) as state:
                # Python 3.9 locks must be constructed on the app's event loop.
                self.discovery = DiscoveryService(DiscoveryConfig((BOARD_G, BOARD_L, BOARD_A)),
                    transport=httpx.MockTransport(self.feeds))
                application.state.discovery = self.discovery
                yield state
        self.app.router.lifespan_context = fixture_lifespan
        self.client = self.client_for(self.app)
        self.addCleanup(self.assert_no_model_or_application_work)

    def make_app(self, discovery):
        return mobile_app.create_app(settings=SETTINGS, transport=httpx.MockTransport(self.supabase),
                                     studio=self.studio, discovery=discovery)

    def client_for(self, application):
        client = self.stack.enter_context(TestClient(application, raise_server_exceptions=False))
        client.headers["Authorization"] = "Bearer session-a"
        return client

    def prefs(self, user=USER_A):
        return next(row for row in self.supabase.tables["job_preferences"] if row["user_id"] == user)

    def search(self, body=None, *, client=None, **kwargs):
        before = copy.deepcopy((self.supabase.tables, self.supabase.objects))
        result = (client or self.client).post(PATH, json={} if body is None else body, **kwargs)
        self.assertEqual((self.supabase.tables, self.supabase.objects), before, "Search must not mutate any saved table or object")
        self.assertEqual(result.headers.get("cache-control"), "no-store")
        self.assertEqual(result.headers.get("x-content-type-options"), "nosniff")
        return result

    def assert_no_model_or_application_work(self):
        for name in ("rank_job", "prepare_documents", "answer_chat", "extract_resume_text"):
            getattr(self.studio, name).assert_not_called()
        allowed_requests = {
            ("GET", "/auth/v1/user"),
            ("POST", "/rest/v1/rpc/mobile_check_access"),  # Read-only membership RPC, not an AI reservation.
            ("GET", "/rest/v1/job_preferences"),
            ("GET", "/rest/v1/profiles"),
            ("GET", "/rest/v1/candidate_context"),
        }
        for request in self.supabase.requests:
            self.assertIn((request.method, request.url.path), allowed_requests)
        self.assertTrue(all(request.method == "GET" for request in self.feeds.requests))

    def test_verified_invited_member_can_search_without_saving_or_model_work(self):
        before = copy.deepcopy(self.supabase.tables)
        result = self.search()
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual((data["status"], data["returned_count"], data["persisted"]), ("ok", 3, False))
        self.assertEqual(self.supabase.tables, before)
        self.assertEqual(len(self.feeds.requests), 3)
        self.assertTrue(all(row["eligibility_status"] == "unknown" for row in data["results"]))
        self.assertFalse(data["eligibility_verified"])

    def test_discovery_uses_confirmed_context_from_the_authenticated_owner(self):
        self.supabase.tables["candidate_context"] = [
            {"user_id": USER_A, "career_text": "Confirmed accounting experience", "career_background": {}},
            {"user_id": USER_B, "career_text": "OTHER_PRIVATE_CAREER_SENTINEL", "career_background": {}},
        ]
        with patch.object(self.discovery, "search", wraps=self.discovery.search) as search:
            response = self.search()
        self.assertEqual(response.status_code, 200, response.text)
        supplied = search.call_args.kwargs["profile"]
        self.assertEqual(supplied["user_id"], USER_A)
        self.assertEqual(supplied["career_text"], "Confirmed accounting experience")
        self.assertNotIn("OTHER_PRIVATE_CAREER_SENTINEL", response.text)
        self.assertTrue(all("OTHER_PRIVATE_CAREER_SENTINEL" not in str(request.url) for request in self.feeds.requests))

    def test_missing_invalid_or_privileged_bearer_never_reaches_preferences_or_feeds(self):
        for auth in ("", "Basic session-a", "Bearer invalid-session", "Bearer " + SETTINGS.publishable_key,
                     "Bearer sb_secret_not_a_user_session"):
            with self.subTest(auth_kind=auth.split(" ")[0]):
                result = self.search(headers={"Authorization": auth})
                self.assertEqual(result.status_code, 401, result.text)
        self.assertEqual(self.feeds.requests, [])
        self.assertTrue(all(request.url.path == "/auth/v1/user" for request in self.supabase.requests))

    def test_private_gate_rejects_uninvited_user_despite_spoofed_email_header(self):
        with patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": "candidate-a@example.test"}):
            result = self.search(headers={"Authorization": "Bearer session-b",
                "Cf-Access-Authenticated-User-Email": "candidate-a@example.test"})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.feeds.requests, [])
        self.assertTrue(all(request.url.path == "/auth/v1/user" for request in self.supabase.requests))

    def test_private_gate_does_not_trust_unconfirmed_email_or_profile_metadata(self):
        self.supabase.auth_emails[USER_A] = {"email": "candidate-a@example.test", "email_confirmed_at": None,
            "user_metadata": {"email_confirmed": True, "email": "candidate-a@example.test"}}
        self.supabase.tables["profiles"][0]["email"] = "candidate-a@example.test"
        result = self.search()
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.feeds.requests, [])
        self.assertTrue(all(request.url.path == "/auth/v1/user" for request in self.supabase.requests))

    def test_empty_and_malformed_private_gate_fail_before_discovery(self):
        for value, expected in (("", 403), ("*", 503), ("not-an-email", 503)):
            with self.subTest(value=value), patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": value}):
                self.assertEqual(self.search().status_code, expected)
        self.assertEqual(self.feeds.requests, [])

    def test_membership_denial_and_access_service_failure_prevent_feed_fetch(self):
        for status, payload, expected in ((200, {"allowed": False, "user_id": USER_A, "code": "not_entitled"}, 403),
                                          (503, {"error": PRIVATE_MARKER}, 503)):
            self.supabase.fault = lambda req, status=status, payload=payload: httpx.Response(status, json=payload) \
                if req.url.path == "/rest/v1/rpc/mobile_check_access" else None
            result = self.search()
            self.assertEqual(result.status_code, expected)
            self.assertNotIn(PRIVATE_MARKER, result.text)
        self.assertEqual(self.feeds.requests, [])
        self.assertFalse(any(request.url.path == "/rest/v1/job_preferences" for request in self.supabase.requests))

    def test_access_is_rechecked_even_when_public_feed_cache_is_warm(self):
        self.assertEqual(self.search().status_code, 200)
        self.supabase.requests.clear()
        self.supabase.fault = lambda req: httpx.Response(200, json={"allowed": False, "user_id": USER_A, "code": "not_entitled"}) \
            if req.url.path == "/rest/v1/rpc/mobile_check_access" else None
        result = self.search()
        self.assertEqual(result.status_code, 403)
        self.assertEqual(len(self.feeds.requests), 3)
        self.assertFalse(any(request.url.path == "/rest/v1/job_preferences" for request in self.supabase.requests))

    def test_route_loads_each_authenticated_owners_preferences_and_shares_only_public_cache(self):
        self.prefs().update(target_titles=["Nurse"], sponsorship_required=True, work_authorization_notes=PRIVATE_MARKER)
        self.prefs(USER_B).update(target_titles=["Teacher"])
        first = self.search()
        second = self.search(headers={"Authorization": "Bearer session-b"})
        self.assertEqual([row["title"] for row in first.json()["results"]], ["Registered Nurse"])
        self.assertEqual([row["title"] for row in second.json()["results"]], ["Primary School Teacher"])
        self.assertEqual(len(self.feeds.requests), 3)
        self.assertTrue(all(source["cached"] for source in second.json()["sources"]))
        for request in self.supabase.requests:
            if request.url.path == "/rest/v1/job_preferences":
                user = USER_B if request.headers["authorization"] == "Bearer session-b" else USER_A
                self.assertEqual(request.url.params["user_id"], "eq." + user)
                self.assertEqual(request.url.params["limit"], "1")
        self.assertNotIn(PRIVATE_MARKER, first.text + second.text)
        for request in self.feeds.requests:
            wire = str(request.url) + str(request.headers) + request.content.decode()
            for private in (USER_A, USER_B, PRIVATE_MARKER, "session-a", SETTINGS.publishable_key):
                self.assertNotIn(private, wire)

    def test_foreign_preference_row_from_data_service_fails_closed(self):
        self.supabase.force_foreign_row = True
        result = self.search()
        self.assertEqual(result.status_code, 502)
        self.assertNotIn(USER_B, result.text)
        self.assertEqual(self.feeds.requests, [])

    def test_missing_preference_row_is_read_only_browsing_not_founder_fallback(self):
        self.supabase.tables["job_preferences"] = [self.prefs(USER_B)]
        result = self.search()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["returned_count"], 3)
        self.assertEqual(len(self.supabase.tables["job_preferences"]), 1)

    def test_existing_240_character_preference_fields_do_not_cause_route_422(self):
        self.prefs().update(target_titles=["Nurse " * 39 + "Nurse."],
            preferred_locations=["Toronto " * 29 + "Toronto."], preferred_regions=["Canada " * 34 + ".."])
        result = self.search()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([row["title"] for row in result.json()["results"]], ["Registered Nurse"])
        for field in ("titles", "locations"):
            rejected = self.search({"filters": {field: ["x" * 161]}})
            self.assertEqual(rejected.status_code, 422)
        self.assertEqual(len(self.feeds.requests), 3)

    def test_owner_europe_preference_matches_germany_without_broadening_to_us(self):
        self.prefs().update(preferred_regions=["Europe"])
        self.feeds.payloads["api.ashbyhq.com"]["jobs"].append(ashby(
            jobUrl="https://jobs.ashbyhq.com/synthetic-labs/us-role", location="Remote US only", address={}))
        result = self.search()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual({row["location_text"] for row in result.json()["results"]}, {"Berlin Germany", "London UK"})
        self.assertFalse(result.json()["eligibility_verified"])

    def test_remote_unknown_and_unsupported_region_warnings_survive_http_envelope(self):
        self.prefs().update(preferred_regions=["Europe"])
        self.feeds.payloads["api.ashbyhq.com"] = {"jobs": [ashby(location="Remote", address={})]}
        unknown = self.search()
        self.assertEqual(unknown.status_code, 200, unknown.text)
        self.assertEqual(unknown.json()["status"], "partial")
        self.assertIn("unconfirmed geography", " ".join(unknown.json()["warnings"]))
        remote = next(row for row in unknown.json()["results"] if row["location_text"] == "Remote")
        self.assertTrue(remote["eligibility"]["review_required"])
        self.prefs().update(preferred_regions=["Atlantis " + PRIVATE_MARKER])
        unsupported = self.search()
        self.assertEqual(unsupported.status_code, 200, unsupported.text)
        self.assertEqual((unsupported.json()["status"], unsupported.json()["results"]), ("partial", []))
        self.assertIn("no supported country map", " ".join(unsupported.json()["warnings"]))
        self.assertNotIn(PRIVATE_MARKER, unsupported.text)

    def test_body_owner_provider_urls_and_nested_injection_are_rejected_safely(self):
        cases = [{"user_id": USER_B}, {"preferences": {"target_titles": [PRIVATE_MARKER]}},
                 {"boards": [PRIVATE_MARKER]}, {"provider": "lever"}, {"url": "http://169.254.169.254/" + PRIVATE_MARKER},
                 {"filters": {"url": "http://127.0.0.1/" + PRIVATE_MARKER}}, {"filters": {"user_id": USER_B}},
                 {"filters": {"workplace_type": PRIVATE_MARKER}}, {"limit": True}, {"limit": 51},
                 {"query": PRIVATE_MARKER * 10}, {"filters": {"titles": ["Nurse"] * 11}}]
        for body in cases:
            with self.subTest(fields=list(body)):
                result = self.search(body)
                self.assertEqual(result.status_code, 422, result.text)
                self.assertNotIn(PRIVATE_MARKER, result.text)
                self.assertNotIn(USER_B, result.text)
        self.assertEqual(self.feeds.requests, [])

    def test_query_string_owner_cannot_change_authenticated_preference_scope(self):
        self.prefs().update(target_titles=["Nurse"])
        self.prefs(USER_B).update(target_titles=["Teacher"])
        result = self.search(params={"user_id": USER_B, "url": "http://127.0.0.1/"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual([row["title"] for row in result.json()["results"]], ["Registered Nurse"])
        self.assertTrue(all(req.url.params.get("user_id") == "eq." + USER_A for req in self.supabase.requests
                            if req.url.path == "/rest/v1/job_preferences"))

    def test_empty_request_filters_cannot_remove_saved_profession_constraint(self):
        self.prefs().update(target_titles=["Nurse"])
        result = self.search({"query": "Teacher", "filters": {"titles": [], "locations": []}})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["results"], [])
        self.assertEqual(result.json()["status"], "ok")

    def test_partial_source_failure_is_http_200_with_explicit_safe_failures(self):
        self.feeds.fault = lambda req: response(status=500, raw=PRIVATE_MARKER.encode()) if req.url.host == "api.lever.co" else None
        result = self.search()
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual((data["status"], data["partial"], data["returned_count"]), ("partial", True, 2))
        self.assertEqual(sum(source["status"] == "error" for source in data["sources"]), 1)
        self.assertNotIn(PRIVATE_MARKER, result.text)
        self.assertFalse(data["persisted"])

    def test_all_sources_unavailable_is_http_503_with_result_envelope_and_safe_detail(self):
        self.feeds.fault = lambda _: response(status=503, raw=PRIVATE_MARKER.encode())
        result = self.search()
        self.assertEqual(result.status_code, 503)
        data = result.json()
        self.assertEqual(data["status"], "unavailable")
        self.assertTrue(data["partial"])
        self.assertEqual(data["results"], [])
        self.assertEqual(len(data["sources"]), 3)
        self.assertEqual(data["detail"]["code"], "sources_unavailable")
        self.assertNotIn(PRIVATE_MARKER, result.text)
        self.assertFalse(data["persisted"])

    def test_no_matches_is_http_200_not_sources_unavailable(self):
        result = self.search({"query": "Astronaut"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "ok")
        self.assertEqual(result.json()["results"], [])
        self.assertFalse(result.json()["partial"])

    def test_discovery_limit_is_http_429_with_retry_after_no_extra_fetch(self):
        for _ in range(6):
            self.assertEqual(self.search().status_code, 200)
        result = self.search()
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.json()["detail"]["code"], "rate_limited")
        self.assertEqual(result.headers["retry-after"], "60")
        self.assertEqual(len(self.feeds.requests), 3)

    def test_lazy_service_created_only_after_gate_and_reused_per_application(self):
        lazy_app = self.make_app(None)
        client = self.client_for(lazy_app)
        with patch.object(mobile_app.DiscoveryConfig, "from_env", return_value=self.discovery.config) as config, \
                patch.object(mobile_app, "DiscoveryService", side_effect=lambda value: DiscoveryService(
                    value, transport=httpx.MockTransport(self.feeds))) as factory:
            self.assertEqual(self.search(client=client, headers={"Authorization": ""}).status_code, 401)
            self.assertIsNone(lazy_app.state.discovery)
            factory.assert_not_called()
            self.assertEqual(self.search(client=client).status_code, 200)
            self.assertEqual(self.search(client=client).status_code, 200)
        factory.assert_called_once_with(self.discovery.config)
        config.assert_called_once_with()
        self.assertIsInstance(lazy_app.state.discovery, DiscoveryService)
        self.assertEqual(len(self.feeds.requests), 3)

    def test_disabled_or_invalid_lazy_configuration_is_safe_503_without_feed_fetch(self):
        for raw, expected in (("{}", "not_configured"), (json.dumps({"lever": ["https://evil.test/" + PRIVATE_MARKER]}), "invalid_configuration")):
            with patch.dict(os.environ, {"MOBILE_DISCOVERY_BOARDS": raw}):
                result = self.search(client=self.client_for(self.make_app(None)))
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["detail"]["code"], expected)
            self.assertNotIn(PRIVATE_MARKER, result.text)
        self.assertEqual(self.feeds.requests, [])

    def test_raw_body_exact_8k_allowed_one_byte_over_rejected_before_auth(self):
        result = self.client.post(PATH, content=b"{}" + b" " * 8190, headers={"Content-Type": "application/json"})
        self.assertEqual(result.status_code, 200)
        self.supabase.requests.clear()
        self.feeds.requests.clear()
        result = self.client.post(PATH, content=b"{}" + b" " * 8191, headers={"Content-Type": "application/json"})
        self.assertEqual(result.status_code, 413)
        self.assertEqual(self.supabase.requests, [])
        self.assertEqual(self.feeds.requests, [])
        self.assertEqual(result.headers["cache-control"], "no-store")

    def test_chunked_and_lying_length_bodies_are_counted_before_auth(self):
        async def streamed(headers):
            events = iter([{"type": "http.request", "body": b" " * 4096, "more_body": True},
                           {"type": "http.request", "body": b" " * 4096, "more_body": True},
                           {"type": "http.request", "body": b" ", "more_body": False}])
            sent = []
            async def receive():
                return next(events, {"type": "http.disconnect"})
            async def send(event):
                sent.append(event)
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                "scheme": "https", "path": PATH, "raw_path": PATH.encode(), "root_path": "", "query_string": b"",
                "headers": [(b"authorization", b"Bearer session-a"), (b"content-type", b"application/json")] + headers,
                "client": ("127.0.0.1", 1234), "server": ("testserver", 443)}
            await self.app(scope, receive, send)
            return next(event["status"] for event in sent if event["type"] == "http.response.start")
        for headers in ([], [(b"content-length", b"2")]):
            self.assertEqual(asyncio.run(streamed(headers)), 413)
        self.assertEqual(self.supabase.requests, [])
        self.assertEqual(self.feeds.requests, [])

    def test_encoded_or_malformed_bodies_fail_without_reflecting_input(self):
        encoded = self.client.post(PATH, content=PRIVATE_MARKER.encode(), headers={"Content-Encoding": "gzip"})
        self.assertEqual(encoded.status_code, 415)
        self.assertEqual(self.supabase.requests, [])
        malformed = self.client.post(PATH, content=b'{"query":' + PRIVATE_MARKER.encode(), headers={"Content-Type": "application/json"})
        self.assertEqual(malformed.status_code, 422)
        self.assertNotIn(PRIVATE_MARKER, encoded.text + malformed.text)
        self.assertEqual(self.feeds.requests, [])

    def test_results_fit_existing_explicit_save_schema_without_auto_saving(self):
        result = self.search()
        self.assertEqual(result.status_code, 200)
        for row in result.json()["results"]:
            approved = {key: row[key] for key in ("source_url", "title", "company_name", "description", "location_text")}
            self.assertEqual(JobCreate.model_validate(approved).title, row["title"])
        self.assertEqual(self.supabase.tables["jobs"], [])
        self.assertEqual(self.supabase.tables["job_scores"], [])
        self.assertEqual(self.supabase.tables["applications"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
