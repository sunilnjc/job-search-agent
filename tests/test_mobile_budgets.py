"""JP-TEST-020 hermetic transport integration + SQL structural checks.

Run: .venv/bin/python -m unittest discover -s tests -p test_mobile_budgets.py -v

The RPC model below is an explicit httpx.MockTransport fixture, NOT PostgreSQL.
Cross-client concurrency/restart tests prove client contract behavior against a
shared model only. They are NOT live DB/RLS/transaction/hosted migration proof.
No real identities, network, providers, email, or runtime environment bypass.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from jobagent.mobile.budgets import PreAuthLimiter, require_mobile_access, reserve_ai_usage
from jobagent.mobile.repository import MobileRepository


USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
RPC_ROOT = "/rest/v1/rpc/"
PROJECT = Path(__file__).resolve().parents[1]
MIGRATION = PROJECT / "supabase/migrations/0007_mobile_usage_controls.sql"


class AuthoritativeRPCModel:
    """Server-state model for transport integration; does not execute any SQL."""

    def __init__(self):
        self.members = {}
        self.costs = {"chat": 1, "rank": 1, "prepare": 2}
        self.daily = {}
        self.reservations = {}
        self.requests = []
        self.tokens = {"session-a": USER_A, "session-b": USER_B}
        self.lock = asyncio.Lock()
        self.now = 0
        self.lose_ack = False

    def provision(self, user, *, period=10, daily=3):
        self.members[user] = {"enabled": True, "period": period, "daily": daily,
                              "reserved": 0, "expires": None, "end": 86400 * 30}

    async def __call__(self, request):
        self.requests.append(request)
        user = self.tokens.get(request.headers.get("Authorization", "").removeprefix("Bearer "))
        if request.url.path == "/auth/v1/user":
            if not user:
                return httpx.Response(401, json={"message": "upstream secret"})
            return httpx.Response(200, json={"id": user, "role": "authenticated",
                "email": "fixture@example.test", "email_confirmed_at": "2026-09-01T00:00:00Z",
                # Deliberately hostile/user-writable claims: helpers must ignore.
                "user_metadata": {"admin": True, "plan": "unlimited", "quota": 999999999}})
        if not user:
            return httpx.Response(401)
        payload = json.loads(request.content)
        async with self.lock:
            member = self.members.get(user)
            allowed = bool(member and member["enabled"] and
                           (member["expires"] is None or member["expires"] > self.now))
            if request.url.path == RPC_ROOT + "mobile_check_access":
                assert payload == {}
                return httpx.Response(200, json={"allowed": allowed, "user_id": user, "code": "not_entitled"})
            assert request.url.path == RPC_ROOT + "mobile_reserve_ai_usage"
            assert set(payload) == {"p_operation", "p_reservation_id"}
            operation, reservation_id = payload["p_operation"], payload["p_reservation_id"]
            UUID(reservation_id)
            if not allowed:
                return httpx.Response(200, json={"allowed": False, "code": "not_entitled"})
            if member["period"] <= 0 or member["daily"] <= 0 or member["end"] <= self.now:
                return httpx.Response(200, json={"allowed": False, "code": "quota_unprovisioned"})
            if operation not in self.costs:
                return httpx.Response(200, json={"allowed": False, "code": "operation_unprovisioned"})
            key = user, reservation_id
            if key in self.reservations:
                return httpx.Response(200, json={"allowed": False, "code": "duplicate_reservation"})
            day_key = user, self.now // 86400
            cost = self.costs[operation]
            if cost > min(member["period"] - member["reserved"], member["daily"] - self.daily.get(day_key, 0)):
                return httpx.Response(200, json={"allowed": False, "code": "quota_exhausted", "retry_after": 60})
            member["reserved"] += cost
            self.daily[day_key] = self.daily.get(day_key, 0) + cost
            receipt = {"allowed": True, "user_id": user, "reservation_id": reservation_id,
                       "operation": operation, "reserved_units": cost,
                       "remaining_period_units": member["period"] - member["reserved"],
                       "remaining_daily_units": member["daily"] - self.daily[day_key]}
            self.reservations[key] = receipt
            if self.lose_ack:
                self.lose_ack = False
                raise httpx.ReadTimeout("private upstream information", request=request)
            return httpx.Response(200, json=receipt)


def client_for(handler, token="session-a"):
    return httpx.AsyncClient(base_url="https://usage.example.test",
        headers={"apikey": "sb_publishable_fixture", "Authorization": "Bearer " + token},
        transport=httpx.MockTransport(handler), trust_env=False)


class UsageTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = AuthoritativeRPCModel()
        self.server.provision(USER_A)
        self.server.provision(USER_B)
        self.client = client_for(self.server)
        self.repo = MobileRepository(self.client, USER_A)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def expect_status(self, status, awaitable):
        with self.assertRaises(HTTPException) as caught:
            await awaitable
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception

    async def test_real_repository_auth_and_rpc_send_only_caller_bearer_and_server_operation(self):
        repo = await MobileRepository.authenticate(self.client)
        await require_mobile_access(repo)
        receipt = await reserve_ai_usage(repo, "prepare")
        self.assertEqual(receipt["reserved_units"], 2)
        for request in self.server.requests:
            self.assertEqual(request.headers["Authorization"], "Bearer session-a")
            self.assertEqual(request.headers["apikey"], "sb_publishable_fixture")
        self.assertEqual(json.loads(self.server.requests[-2].content), {})
        payload = json.loads(self.server.requests[-1].content)
        self.assertEqual(set(payload), {"p_operation", "p_reservation_id"})
        self.assertEqual(payload["p_operation"], "prepare")
        self.assertEqual(self.server.members[USER_A]["reserved"], 2)
        self.assertEqual(self.server.members[USER_B]["reserved"], 0)

    async def test_unprovisioned_revoked_expired_membership_and_forged_claims_deny(self):
        repo = await MobileRepository.authenticate(self.client)
        self.server.members.pop(USER_A)
        await self.expect_status(403, require_mobile_access(repo))
        await self.expect_status(403, reserve_ai_usage(repo, "chat"))
        self.server.provision(USER_A)
        await require_mobile_access(repo)
        self.server.members[USER_A]["enabled"] = False
        await self.expect_status(403, require_mobile_access(repo))
        self.server.members[USER_A]["enabled"] = True
        self.server.members[USER_A]["expires"] = self.server.now
        await self.expect_status(403, reserve_ai_usage(repo, "chat"))
        self.assertEqual(len(self.server.reservations), 0)

    async def test_membership_alone_never_provisions_free_ai_and_period_expiry_denies(self):
        for daily, period, end in ((0, 10, 100), (10, 0, 100), (10, 10, 0)):
            with self.subTest(daily=daily, period=period, end=end):
                self.server.provision(USER_A, daily=daily, period=period)
                self.server.members[USER_A]["end"] = end
                await require_mobile_access(self.repo)
                await self.expect_status(403, reserve_ai_usage(self.repo, "rank"))
        self.assertEqual(len(self.server.reservations), 0)

    async def test_cross_operation_daily_limit_shared_between_new_clients(self):
        await reserve_ai_usage(self.repo, "prepare")
        # New client/repository has no in-memory budget but sees shared RPC state.
        async with client_for(self.server) as restarted:
            new_repo = MobileRepository(restarted, USER_A)
            await reserve_ai_usage(new_repo, "rank")
            exc = await self.expect_status(429, reserve_ai_usage(new_repo, "chat"))
            self.assertEqual(exc.headers["Retry-After"], "60")
        async with client_for(self.server, "session-b") as other:
            receipt = await reserve_ai_usage(MobileRepository(other, USER_B), "prepare")
            self.assertEqual(receipt["remaining_daily_units"], 1)
        self.assertEqual(self.server.members[USER_A]["reserved"], 3)

    async def test_multiple_clients_share_one_modeled_atomic_cap(self):
        self.server.provision(USER_A, daily=5, period=5)

        async def worker():
            async with client_for(self.server) as client:
                try:
                    await reserve_ai_usage(MobileRepository(client, USER_A), "chat")
                    return 200
                except HTTPException as exc:
                    return exc.status_code

        outcomes = await asyncio.gather(*(worker() for _ in range(24)))
        self.assertEqual(outcomes.count(200), 5)
        self.assertEqual(outcomes.count(429), 19)
        self.assertEqual(self.server.members[USER_A]["reserved"], 5)
        self.assertEqual(len(self.server.reservations), 5)

    async def test_day_rollover_does_not_reset_total_period_limit(self):
        self.server.provision(USER_A, daily=2, period=3)
        await reserve_ai_usage(self.repo, "prepare")
        await self.expect_status(429, reserve_ai_usage(self.repo, "chat"))
        self.server.now += 86400
        await reserve_ai_usage(self.repo, "rank")
        await self.expect_status(429, reserve_ai_usage(self.repo, "chat"))
        self.assertEqual(self.server.members[USER_A]["reserved"], 3)

    async def test_lost_ack_is_charged_once_and_never_retried_or_refunded(self):
        self.server.lose_ack = True
        exc = await self.expect_status(503, reserve_ai_usage(self.repo, "prepare"))
        self.assertNotIn("private upstream", exc.detail)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.members[USER_A]["reserved"], 2)
        await reserve_ai_usage(self.repo, "rank")
        await self.expect_status(429, reserve_ai_usage(self.repo, "chat"))

    async def test_successful_reservation_is_not_a_replay_authorization(self):
        same_id = uuid4()
        with patch("jobagent.mobile.budgets.uuid4", return_value=same_id):
            await reserve_ai_usage(self.repo, "chat")
            await self.expect_status(409, reserve_ai_usage(self.repo, "chat"))
        self.assertEqual(self.server.members[USER_A]["reserved"], 1)

    async def test_mismatched_repo_identity_cannot_authorize_other_user_work(self):
        forged_repo = MobileRepository(self.client, USER_B)
        await self.expect_status(503, require_mobile_access(forged_repo))
        await self.expect_status(503, reserve_ai_usage(forged_repo, "chat"))
        self.assertEqual(self.server.members[USER_B]["reserved"], 0)

    async def test_unsupported_or_caller_chosen_operations_rejected_before_network(self):
        for operation in ("", "unlimited", "../admin", "Chat", 1, {"cost": 0}, None):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                await reserve_ai_usage(self.repo, operation)
        self.assertEqual(self.server.requests, [])

    async def test_missing_migration_errors_are_actionable_503_for_both_helpers(self):
        for status, code in ((404, "PGRST202"), (404, "PGRST205"), (400, "42P01"),
                             (400, "42883"), (500, "XX000"), (302, "redirect")):
            with self.subTest(status=status, code=code):
                async with client_for(lambda req: httpx.Response(status, json={
                    "code": code, "message": "secret SQL user data"},
                    headers={"Location": "https://must-not-follow.example.test"})) as client:
                    repo = MobileRepository(client, USER_A)
                    for pending in (lambda: require_mobile_access(repo), lambda: reserve_ai_usage(repo, "chat")):
                        exc = await self.expect_status(503, pending())
                        self.assertIn("0007_mobile_usage_controls.sql", exc.detail)
                        self.assertIn("provision", exc.detail)
                        self.assertNotIn("secret SQL", exc.detail)

    async def test_session_errors_and_retry_headers_are_sanitized(self):
        for status, retry, expected in ((401, "0", None), (403, "0", None),
                                        (429, "999999999", "3600"), (429, "secret header", "60")):
            with self.subTest(status=status, retry=retry):
                async with client_for(lambda req: httpx.Response(status, json={"secret": "details"},
                                     headers={"Retry-After": retry})) as client:
                    exc = await self.expect_status(status, require_mobile_access(MobileRepository(client, USER_A)))
                    self.assertNotIn("details", exc.detail)
                    if expected:
                        self.assertEqual(exc.headers["Retry-After"], expected)

    async def test_malformed_oversized_ambiguous_receipts_fail_closed(self):
        bodies = (b"not JSON", b"[]", b"null", b"{}", b'{"allowed":1}',
                  b'{"allowed":false,"code":"unknown"}', b'{"allowed":true,"user_id":null}',
                  b'{"allowed":false,"allowed":true}', b"x" * 17000, b"[" * 2000)
        for body in bodies:
            with self.subTest(body=body[:80]):
                async with client_for(lambda req: httpx.Response(200, content=body)) as client:
                    repo = MobileRepository(client, USER_A)
                    await self.expect_status(503, require_mobile_access(repo))
                    await self.expect_status(503, reserve_ai_usage(repo, "chat"))

    async def test_wrong_reservation_operation_or_noninteger_counters_fail_closed(self):
        mutations = ({"reservation_id": str(uuid4())}, {"operation": "rank"}, {"user_id": USER_B},
                     {"reserved_units": 0}, {"reserved_units": True}, {"remaining_daily_units": -1},
                     {"remaining_period_units": 2.0}, {"remaining_period_units": "2"})
        for mutation in mutations:
            async def handler(request):
                response = await self.server(request)
                return httpx.Response(200, json={**response.json(), **mutation})
            with self.subTest(mutation=mutation):
                self.server.provision(USER_A, daily=100, period=100)
                async with client_for(handler) as client:
                    await self.expect_status(503, reserve_ai_usage(MobileRepository(client, USER_A), "chat"))

    async def test_no_reservation_refund_after_provider_failure(self):
        provider = AsyncMock(side_effect=RuntimeError("fixture provider failed"))
        await reserve_ai_usage(self.repo, "prepare")
        with self.assertRaises(RuntimeError):
            await provider()
        self.assertEqual(self.server.members[USER_A]["reserved"], 2)
        self.assertEqual(len(self.server.requests), 1)


def peer_request(peer="192.0.2.1", *, token="bad", forwarded="198.51.100.1"):
    return StarletteRequest({"type": "http", "method": "GET", "path": "/api/mobile/bootstrap",
        "client": (peer, 1234) if peer else None,
        "headers": [(b"authorization", ("Bearer " + token).encode()),
                    (b"x-forwarded-for", forwarded.encode())]})


class PreAuthTests(unittest.TestCase):
    def test_bad_token_rotation_and_spoofed_forwarding_cannot_change_peer_budget(self):
        limiter = PreAuthLimiter(per_peer=2, total=10)
        limiter.check(peer_request(token="bad1"))
        limiter.check(peer_request(token="bad2", forwarded="203.0.113.1"))
        with self.assertRaises(HTTPException) as caught:
            limiter.check(peer_request(token="bad3", forwarded="203.0.113.2"))
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(len(limiter._peers), 1)
        self.assertNotIn("192.0.2.1", repr(limiter._peers))
        self.assertNotIn("bad", repr(limiter._peers))

    def test_global_cap_bounds_rotating_peers_and_rollover_cleans_stale_state(self):
        now = [0]
        limiter = PreAuthLimiter(per_peer=1, total=3, window_seconds=60, clock=lambda: now[0])
        for index in range(3):
            limiter.check(peer_request("192.0.2." + str(index)))
        with self.assertRaises(HTTPException):
            limiter.check(peer_request("192.0.2.100"))
        self.assertEqual(len(limiter._peers), 3)
        now[0] = 60
        limiter.check(peer_request("192.0.2.100"))
        self.assertEqual(len(limiter._peers), 1)
        self.assertEqual(len(limiter._global), 1)

    def test_capacity_never_evicts_live_peers_to_admit_new_attackers(self):
        limiter = PreAuthLimiter(per_peer=1, total=10, max_peers=2)
        limiter.check(peer_request("192.0.2.1"))
        limiter.check(peer_request("192.0.2.2"))
        for index in range(3, 100):
            with self.assertRaises(HTTPException):
                limiter.check(peer_request("192.0.2." + str(index)))
        with self.assertRaises(HTTPException):
            limiter.check(peer_request("192.0.2.1"))
        self.assertEqual(len(limiter._peers), 2)

    def test_no_client_address_uses_one_bounded_bucket(self):
        limiter = PreAuthLimiter(per_peer=1)
        limiter.check(peer_request(None))
        with self.assertRaises(HTTPException):
            limiter.check(peer_request(None, token="rotated"))

    def test_local_thread_concurrency_is_bounded(self):
        limiter = PreAuthLimiter(per_peer=5)
        def attempt(_):
            try:
                limiter.check(peer_request())
                return 200
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(40)))
        self.assertEqual(results.count(200), 5)
        self.assertEqual(results.count(429), 35)

    def test_invalid_configuration_rejected(self):
        for config in ({"per_peer": 0}, {"total": -1}, {"max_peers": 0}, {"per_peer": True},
                       {"window_seconds": float("inf")}, {"window_seconds": float("nan")},
                       {"window_seconds": "60"}, {"window_seconds": 0}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                PreAuthLimiter(**config)


class WiringHarnessTests(unittest.IsolatedAsyncioTestCase):
    """Minimal ASGI integration of the contract, not proof main app is wired."""

    async def test_pre_auth_limit_runs_before_upstream_invalid_session_calls(self):
        server = AuthoritativeRPCModel()
        limiter = PreAuthLimiter(per_peer=2)
        harness = FastAPI()
        async with client_for(server, "invalid") as upstream:
            @harness.get("/check")
            async def route(request: Request):
                limiter.check(request)
                await MobileRepository.authenticate(upstream)
                return {"ok": True}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=harness),
                                         base_url="http://harness.test") as browser:
                results = [await browser.get("/check", headers={"X-Forwarded-For": str(index)}) for index in range(10)]
        self.assertEqual([result.status_code for result in results], [401, 401] + [429] * 8)
        self.assertEqual(len(server.requests), 2)

    async def test_missing_migration_denies_before_provider_integration(self):
        provider = AsyncMock()
        server = AuthoritativeRPCModel()
        async def missing(request):
            if request.url.path == "/auth/v1/user":
                return await server(request)
            return httpx.Response(404, json={"code": "PGRST202"})
        async with client_for(missing) as client:
            repo = await MobileRepository.authenticate(client)
            with self.assertRaises(HTTPException) as caught:
                await require_mobile_access(repo)
                await reserve_ai_usage(repo, "prepare")
                await provider()
        self.assertEqual(caught.exception.status_code, 503)
        provider.assert_not_awaited()


class ActualAppBoundaryTests(unittest.TestCase):
    """Real app + injected FakeSupabase/MockTransport; still not database proof."""

    def setUp(self):
        from test_mobile_api import FakeSupabase, SETTINGS, fake_studio
        from jobagent.mobile.app import create_app
        from jobagent.mobile.repository import SupabaseSettings
        self.supabase = FakeSupabase()
        self.supabase.auth_emails[USER_A] = {"email": "fixture@example.test",
            "email_confirmed_at": "2026-09-01T00:00:00Z"}
        self.studio = fake_studio()
        self.app = create_app(settings=SETTINGS, studio=self.studio,
                              transport=httpx.MockTransport(self.supabase))
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.headers["Authorization"] = "Bearer session-a"
        self.addCleanup(self.client.close)
        # Explicit fixture configuration; keep the real private gate active.
        env_patch = patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": "fixture@example.test"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        settings_patch = patch.object(SupabaseSettings, "from_env", side_effect=AssertionError("No environment settings read"))
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.job = self.supabase.job()
        self.resume = self.supabase.resume()

    def billed_routes(self):
        return (("chat", {"message": "Help me prepare for an interview.", "mode": "interview"}),
                ("jobs/" + self.job["id"] + "/rank", {}),
                ("jobs/" + self.job["id"] + "/prepare", {"resume_id": self.resume["id"]}))

    def assert_no_provider(self):
        self.studio.answer_chat.assert_not_called()
        self.studio.rank_job.assert_not_called()
        self.studio.prepare_documents.assert_not_called()

    def test_missing_access_migration_blocks_bootstrap_and_every_billed_route(self):
        self.supabase.fault = lambda req: httpx.Response(404, json={"code": "PGRST202"}) if req.url.path.startswith(RPC_ROOT) else None
        response = self.client.get("/api/mobile/bootstrap")
        self.assertEqual(response.status_code, 503, response.text)
        for route, body in self.billed_routes():
            response = self.client.post("/api/mobile/" + route, json=body)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertIn("0007_mobile_usage_controls.sql", response.text)
        self.assert_no_provider()
        self.assertEqual(self.supabase.tables["model_runs"], [])

    def test_reservation_denials_and_missing_rpc_block_all_three_provider_calls(self):
        for code, expected in (("quota_unprovisioned", 403), ("quota_exhausted", 429), ("missing", 503)):
            def fault(req):
                if req.url.path != RPC_ROOT + "mobile_reserve_ai_usage":
                    return None
                self.assert_no_provider()
                return (httpx.Response(404, json={"code": "PGRST202"}) if code == "missing" else
                        httpx.Response(200, json={"allowed": False, "code": code, "retry_after": 60}))
            self.supabase.fault = fault
            for route, body in self.billed_routes():
                with self.subTest(code=code, route=route):
                    response = self.client.post("/api/mobile/" + route, json=body)
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assert_no_provider()
        self.assertTrue(all(run["status"] == "failed" for run in self.supabase.tables["model_runs"]))

    def test_private_gate_stays_before_entitlement_rpc(self):
        with patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": "different@example.test"}):
            response = self.client.get("/api/mobile/bootstrap")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual([req.url.path for req in self.supabase.requests], ["/auth/v1/user"])

    def test_invalid_sessions_limited_before_upstream_and_health_exempt(self):
        self.app.state.pre_auth_limits = PreAuthLimiter(per_peer=2)
        self.client.headers["Authorization"] = "Bearer invalid-session"
        responses = [self.client.get("/api/mobile/bootstrap") for _ in range(6)]
        self.assertEqual([response.status_code for response in responses], [401, 401, 429, 429, 429, 429])
        self.assertEqual([req.url.path for req in self.supabase.requests], ["/auth/v1/user"] * 2)
        self.assertEqual(self.client.get("/api/mobile/health").status_code, 200)


class UsageSQLStructureTests(unittest.TestCase):
    """Offline SQL assertions only: intentionally no live-database claims."""

    @classmethod
    def setUpClass(cls):
        cls.sql = re.sub(r"--[^\n]*", "", MIGRATION.read_text())
        cls.compact = re.sub(r"\s+", " ", cls.sql).lower()

    def test_all_new_tables_rls_and_explicit_privileges(self):
        tables = ("mobile_usage_memberships", "mobile_ai_operation_costs", "mobile_ai_daily_usage", "mobile_ai_reservations")
        for table in tables:
            self.assertIn("create table public." + table, self.compact)
            self.assertIn("alter table public." + table + " enable row level security", self.compact)
        self.assertRegex(self.compact, r"revoke all on public\.mobile_usage_memberships, .*? from public, anon, authenticated")
        self.assertRegex(self.compact, r"grant all on public\.mobile_usage_memberships, .*? to service_role")
        for statement in self.compact.split(";"):
            if statement.strip().startswith("grant") and "to authenticated" in statement:
                self.assertRegex(statement.strip(), r"^grant (select|execute) ")
        self.assertNotRegex(self.compact, r"create policy \w+ on public\.mobile_ai_operation_costs")
        for table in ("mobile_usage_memberships", "mobile_ai_daily_usage", "mobile_ai_reservations"):
            self.assertRegex(self.compact, r"on public\." + table +
                             r" for select to authenticated using \(\(select auth.uid\(\)\) = user_id\)")

    def test_auth_bound_security_definers_and_explicit_execute_grants(self):
        functions = re.findall(r"create function public\.(\w+)\([^)]*\).*?\$\$;", self.sql, flags=re.S)
        self.assertEqual(set(functions), {"mobile_has_access", "mobile_check_access", "mobile_reserve_ai_usage"})
        self.assertEqual(self.compact.count("security definer set search_path = ''"), 3)
        self.assertIn("v_user uuid := auth.uid()", self.compact)
        self.assertIn("auth.role() is distinct from 'authenticated'", self.compact)
        self.assertIn("mobile_reserve_ai_usage(p_operation text, p_reservation_id uuid)", self.compact)
        self.assertNotRegex(self.compact, r"p_(user_id|quota|limit|cost|plan|units|metadata)")
        self.assertNotIn("user_metadata", self.compact)
        self.assertNotIn("raw_user_meta_data", self.compact)
        self.assertIn("revoke all on function public.mobile_reserve_ai_usage(text, uuid) from public, anon, authenticated, service_role", self.compact)
        self.assertIn("grant execute on function public.mobile_reserve_ai_usage(text, uuid) to authenticated", self.compact)

    def test_atomic_checks_precede_writes_and_replays_never_authorize(self):
        reserve = self.compact.split("create function public.mobile_reserve_ai_usage", 1)[1].split("$$;", 1)[0]
        self.assertIn("where user_id = v_user for update", reserve)
        self.assertIn("where operation = p_operation and enabled for share", reserve)
        self.assertLess(reserve.index("for share"), reserve.index("v_now := clock_timestamp()"))
        self.assertIn("v_day := (v_now at time zone 'utc')::date", reserve)
        self.assertIn("where user_id = v_user and usage_day = v_day for update", reserve)
        writes = reserve.index("update public.mobile_usage_memberships")
        for check in ("'not_entitled'", "'quota_unprovisioned'", "'operation_unprovisioned'", "'duplicate_reservation'",
                      "v_member.period_limit - v_member.period_reserved", "v_member.daily_limit - v_daily"):
            self.assertLess(reserve.index(check), writes)
        self.assertIn("'allowed', false, 'code', 'duplicate_reservation'", reserve)
        self.assertIn("set period_reserved = period_reserved + v_cost where user_id = v_user", reserve)
        self.assertIn("primary key (user_id, usage_day)", self.compact)
        self.assertIn("primary key (user_id, reservation_id)", self.compact)
        self.assertNotIn("exception when", reserve)
        self.assertNotIn("delete from", reserve)
        self.assertNotIn("commit", reserve)
        self.assertTrue(self.compact.strip().startswith("begin;"))
        self.assertTrue(self.compact.strip().endswith("commit;"))

    def test_founder_safe_preflight_no_pii_no_metadata_seed_no_default_quota(self):
        self.assertIn("current_setting('jobpursuit.member_user_ids', true)", self.compact)
        self.assertIn("current_setting('jobpursuit.blocked_user_ids', true)", self.compact)
        self.assertIn("where not (p.user_id = any(v_allowed) or p.user_id = any(v_blocked))", self.compact)
        self.assertIn("raise exception 'usage controls not applied", self.compact)
        self.assertIn("v_allowed && v_blocked", self.compact)
        self.assertNotRegex(self.compact, r"[0-9a-f]{8}-[0-9a-f-]{27,36}")
        self.assertNotIn("@", self.compact)
        self.assertIn("period_limit bigint not null default 0", self.compact)
        self.assertIn("daily_limit bigint not null default 0", self.compact)
        self.assertNotIn("insert into public.mobile_ai_operation_costs", self.compact)
        self.assertNotIn("references public.profiles", self.compact)

    def test_restrictive_entitlement_covers_every_existing_owner_table_and_only_target_buckets(self):
        for table in ("profiles", "job_preferences", "resumes", "jobs", "job_scores", "applications", "artifacts",
                      "model_runs", "application_attempts", "candidate_context", "mobile_questions", "mobile_answers",
                      "mobile_resume_operations", "mobile_artifact_operations"):
            self.assertIn("'" + table + "'", self.compact)
        self.assertEqual(self.compact.count("as restrictive for all to anon, authenticated"), 2)
        self.assertIn("using ((select auth.uid()) = user_id and (select public.mobile_has_access()))", self.compact)
        self.assertIn("with check ((select auth.uid()) = user_id and (select public.mobile_has_access()))", self.compact)
        self.assertIn("create policy mobile_storage_entitlement_required on storage.objects", self.compact)
        self.assertEqual(self.compact.count("bucket_id not in ('resumes', 'application-artifacts')"), 2)
        self.assertNotIn("drop policy", self.compact)
        self.assertNotIn("disable row level security", self.compact)
        self.assertNotIn("grant all on public.profiles", self.compact)
        self.assertNotIn("grant all on storage.objects", self.compact)

    def test_module_has_no_environment_or_implicit_test_bypass(self):
        source = (PROJECT / "src/jobagent/mobile/budgets.py").read_text()
        self.assertNotIn("os.getenv", source)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("Fake", source)
        self.assertNotIn("service_role", source)


if __name__ == "__main__":
    unittest.main()
