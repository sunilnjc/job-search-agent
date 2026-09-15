"""Launch acceptance and remaining observations; synthetic, offline, test-only.

Run from repository root:
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONPATH=src:tests \
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest \
    discover -s tests -p test_launch_privacy_probe.py -v

Tests named ``test_observes_*`` deliberately assert current defects/limitations:
their passing result reproduces an observation, NOT acceptance of that behavior.
Repaired pre-auth throttling, partial ranking success, compact bootstrap and
founder middleware boundaries have positive acceptance names/assertions. Profile
atomic save and owner-authenticated privacy routes now have acceptance checks.
Legacy active-PDF behavior is being replaced with safe pre-storage rejection.
TenantLimits' ordinary request history remains process-local; it is NOT the AI
spending authority. Durable AI RPC contracts have focused test_mobile_budgets
coverage (also mocked/static, not live database proof).
FakeSupabase is an in-memory API double, not a PostgreSQL/RLS implementation.
No hosted scripts, founder application/service/config, credentials or real data
are imported. Founder main.py is parsed only; its isolated auth middleware is
exercised with synthetic development credentials, never production JWKS/network.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import copy
import io
import json
import os
import socket
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from jobagent.mobile.app import TenantLimits, create_app
from jobagent.mobile.budgets import PreAuthLimiter
from test_mobile_api import FakeSupabase, SETTINGS, USER_A, USER_B, docx_bytes, fake_studio

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "SYNTHETIC_PRIVATE_DIAGNOSTIC_DO_NOT_ECHO"


class OfflineCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        for method in ("connect", "connect_ex", "sendto"):
            self.stack.enter_context(patch.object(
                socket.socket, method, side_effect=AssertionError("No network in privacy probes")
            ))
        self.stack.enter_context(patch.object(
            socket, "getaddrinfo", side_effect=AssertionError("No DNS in privacy probes")
        ))


class CommitThenTimeoutSupabase(FakeSupabase):
    """Simulate a remote commit whose success response never reaches the API."""

    after_commit = None

    def __call__(self, request):
        response = super().__call__(request)
        if self.after_commit and self.after_commit(request):
            raise httpx.ReadTimeout(PRIVATE, request=request)
        return response


class LaunchPrivacyProbeTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.remote = CommitThenTimeoutSupabase()
        self.studio = fake_studio()
        self.app = create_app(settings=SETTINGS, transport=httpx.MockTransport(self.remote), studio=self.studio)
        self.client = self.stack.enter_context(TestClient(self.app, raise_server_exceptions=False))
        self.client.headers["Authorization"] = "Bearer session-a"

    def request(self, method, path, **kwargs):
        return self.client.request(method, "/api/mobile/" + path, **kwargs)

    def upload(self, content=None, filename="probe.docx"):
        return self.request("POST", "resumes", json={
            "filename": filename, "label": "Synthetic audit resume",
            "content_base64": base64.b64encode(content or docx_bytes()).decode(),
        })

    def test_all_registered_mobile_operations_deny_missing_bearer_before_network(self):
        self.client.headers.clear()
        tested = 0
        for route in self.app.routes:
            if route.path == "/api/mobile/health":
                continue
            path = route.path
            for name in ("job_id", "resume_id", "artifact_id", "question_id"):
                path = path.replace("{" + name + "}", USER_A)
            for method in route.methods:
                with self.subTest(method=method, path=route.path):
                    response = self.client.request(method, path, json={})
                    # Only the exact signed provider webhook is user-auth-free;
                    # it rejects missing signature before any upstream request.
                    expected = 400 if route.path == "/api/mobile/billing/webhook" else 401
                    self.assertEqual(response.status_code, expected)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    tested += 1
        self.assertGreaterEqual(tested, 17)
        self.assertEqual(self.remote.requests, [])

    def test_cross_tenant_ids_deny_before_studio_or_storage(self):
        foreign_job = self.remote.job(USER_B)
        foreign_resume = self.remote.resume(USER_B)
        foreign_question = self.remote.add("mobile_questions", USER_B, job_id=foreign_job["id"], prompt="Synthetic question", status="pending", answer=None)
        for method, path, body in (
            ("POST", f"jobs/{foreign_job['id']}/rank", {}),
            ("POST", f"jobs/{foreign_job['id']}/prepare", {"resume_id": foreign_resume["id"]}),
            ("POST", "applications", {"job_id": foreign_job["id"], "status": "draft"}),
            ("POST", f"questions/{foreign_question['id']}/answer", {"answer": "Synthetic answer"}),
            ("DELETE", f"resumes/{foreign_resume['id']}", None),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, json=body).status_code, 404)
        self.studio.prepare_documents.assert_not_called()
        self.studio.rank_job.assert_not_called()
        self.assertFalse(any(req.url.path.startswith("/storage/") for req in self.remote.requests))

    def test_validation_and_backend_errors_do_not_echo_synthetic_pii(self):
        response = self.request("PUT", "profile", json={"unexpected": PRIVATE})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(PRIVATE, response.text)
        self.remote.fault = lambda req: httpx.Response(500, text=PRIVATE)
        response = self.request("GET", "bootstrap")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(PRIVATE, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_application_record_does_not_submit_or_allow_operational_fields(self):
        job = self.remote.job()
        response = self.request("POST", "applications", json={
            "job_id": job["id"], "status": "submitted", "notes": "I manually submitted (synthetic).",
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("applied_at", response.json())
        self.assertNotIn("submission_url", response.json())
        self.assertEqual({req.url.host for req in self.remote.requests}, {"mobile.example.test"})
        self.assertEqual({req.url.path for req in self.remote.requests}, {
            "/auth/v1/user", "/rest/v1/rpc/mobile_check_access", "/rest/v1/jobs", "/rest/v1/applications",
        })
        membership = [req for req in self.remote.requests if req.url.path == "/rest/v1/rpc/mobile_check_access"]
        self.assertEqual(len(membership), 1)
        self.assertEqual(membership[0].method, "POST")
        self.assertEqual(json.loads(membership[0].content), {})
        for field in ("applied_at", "submission_url", "user_id"):
            response = self.request("POST", "applications", json={
                "job_id": job["id"], "status": "submitted", "notes": "Synthetic confirmation", field: PRIVATE,
            })
            self.assertEqual(response.status_code, 422)

    def test_pre_auth_limiter_bounds_invalid_sessions_before_upstream_auth(self):
        now = [0]
        self.app.state.pre_auth_limits = PreAuthLimiter(per_peer=2, total=10, clock=lambda: now[0])
        self.app.state.limits.limits["requests"] = 1
        responses = [self.request("GET", "bootstrap", headers={
            "Authorization": f"Bearer invalid-audit-session-{index}",
            "X-Forwarded-For": f"192.0.2.{index}",
        }) for index in range(5)]
        self.assertEqual([r.status_code for r in responses], [401, 401, 429, 429, 429])
        self.assertEqual(len(self.remote.requests), 2)
        self.assertTrue(all(req.url.path == "/auth/v1/user" for req in self.remote.requests))
        self.assertEqual(self.app.state.limits.events, {})
        self.assertTrue(all(response.headers["retry-after"] == "60" for response in responses[2:]))
        self.assertEqual(self.request("GET", "health").status_code, 200)
        now[0] = 60
        self.assertEqual(self.request("GET", "bootstrap").status_code, 200)
        self.assertIn(USER_A, self.app.state.limits.events)

    def test_upstream_429_preserves_bounded_retry_after(self):
        self.remote.fault = lambda req: httpx.Response(429, headers={"Retry-After": "37"}, text=PRIVATE)
        response = self.request("GET", "bootstrap")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "37")
        self.assertNotIn(PRIVATE, response.text)

    def test_lost_storage_ack_reconciles_bytes_and_metadata(self):
        self.remote.after_commit = lambda req: req.method == "POST" and req.url.path.startswith("/storage/v1/object/resumes/")
        response = self.upload()
        self.assertEqual(response.status_code, 201)
        self.assertNotIn(PRIVATE, response.text)
        self.assertEqual(len(self.remote.tables["resumes"]), 1)
        self.assertEqual(len(self.remote.objects), 1)
        self.assertFalse(any(req.method == "DELETE" for req in self.remote.requests))
        self.assertEqual(len(self.request("GET", "bootstrap").json()["resumes"]), 1)

    def test_lost_metadata_ack_keeps_committed_resumes_bytes(self):
        self.remote.after_commit = lambda req: req.method == "POST" and req.url.path == "/rest/v1/resumes"
        response = self.upload()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.remote.tables["resumes"]), 1)
        self.assertEqual(len(self.remote.objects), 1)
        row = self.remote.tables["resumes"][0]
        self.assertEqual(self.request("GET", f"resumes/{row['id']}/download").status_code, 200)

    def test_metadata_failure_retains_journaled_upload_for_recovery(self):
        def fail(request):
            if request.method == "POST" and request.url.path == "/rest/v1/resumes":
                return httpx.Response(500, text=PRIVATE)
            if request.method == "DELETE" and request.url.path.startswith("/storage/"):
                return httpx.Response(503, text=PRIVATE)
            return None
        self.remote.fault = fail
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.remote.tables["resumes"], [])
        self.assertEqual(len(self.remote.objects), 1)
        pending = self.request("GET", "resume-operations").json()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["state"], "upload_pending")
        self.assertFalse(any(req.method == "DELETE" for req in self.remote.requests))
        self.assertNotIn(PRIVATE, response.text)
        self.remote.fault = None
        recovered = self.request("POST", f"resumes/{pending[0]['id']}/recover")
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(len(self.remote.tables["resumes"]), 1)

    def test_failed_metadata_delete_never_deletes_bytes_or_needs_restore(self):
        row = self.remote.resume()
        def fail(request):
            if request.method == "DELETE" and request.url.path == "/rest/v1/resumes":
                return httpx.Response(500)
            if request.method == "POST" and request.url.path.startswith("/storage/"):
                return httpx.Response(503)
            return None
        self.remote.fault = fail
        response = self.request("DELETE", f"resumes/{row['id']}")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.remote.tables["resumes"], [row])
        self.assertEqual(len(self.remote.objects), 1)
        self.assertFalse(any(req.method in {"DELETE", "POST"} and req.url.path.startswith("/storage/") for req in self.remote.requests))

    def test_storage_400_nosuchkey_allows_stale_metadata_deletion(self):
        row = self.remote.resume()
        self.remote.objects.clear()
        self.remote.fault = lambda req: httpx.Response(400, json={"statusCode": "404", "error": "not_found", "code": "NoSuchKey", "message": "Object not found"}) if req.method == "GET" and req.url.path.startswith("/storage/") else None
        response = self.request("DELETE", f"resumes/{row['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.remote.tables["resumes"], [])

    def test_saved_rank_reports_pending_log_sync_without_false_failure_or_second_call(self):
        job = self.remote.job()
        def fail(request):
            if request.method == "PATCH" and request.url.path == "/rest/v1/model_runs" and json.loads(request.content).get("status") == "succeeded":
                return httpx.Response(500, text=PRIVATE)
            return None
        self.remote.fault = fail
        response = self.request("POST", f"jobs/{job['id']}/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["operation_status"], "saved_sync_pending")
        self.assertTrue(result["warnings"])
        self.assertTrue(all(isinstance(warning, str) and warning.strip() for warning in result["warnings"]))
        self.assertNotIn(PRIVATE, response.text)
        run = self.remote.tables["model_runs"][0]
        self.assertEqual(run["status"], "running")
        self.assertEqual(result["model_run_id"], run["id"])
        self.assertEqual(len(self.remote.tables["job_scores"]), 1)
        self.assertEqual((result["id"], result["status"], result["score"]), (job["id"], "matched", 8.5))
        visible = self.request("GET", "bootstrap").json()["jobs"][0]
        self.assertEqual((visible["status"], visible["score"]), ("matched", 8.5))
        self.studio.rank_job.assert_called_once()
        reservations = [request for request in self.remote.requests
                        if request.url.path == "/rest/v1/rpc/mobile_reserve_ai_usage"]
        self.assertEqual(len(reservations), 1)
        self.assertEqual(json.loads(reservations[0].content)["p_operation"], "rank")

    def test_atomic_profile_failure_does_not_fall_back_to_partial_rest_writes(self):
        self.remote.add("candidate_context", career_text="Original synthetic career", career_background={})
        before = copy.deepcopy(self.remote.tables["candidate_context"])
        original_profile = copy.deepcopy(self.remote.tables["profiles"])
        self.remote.fault = lambda req: httpx.Response(500) if req.method == "POST" and req.url.path == "/rest/v1/rpc/mobile_save_profile" else None
        response = self.request("PUT", "profile", json={"display_name": "Changed synthetic name", "career_text": "New synthetic career"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "profile_save_unconfirmed")
        self.assertEqual(self.remote.tables["profiles"], original_profile)
        self.assertEqual(self.remote.tables["candidate_context"], before)

    def test_bootstrap_compacts_large_job_rows_and_detail_preserves_description(self):
        from jobagent.mobile.schemas import JobCreate
        for index in range(110):
            payload = JobCreate.model_validate({
                "source_url": f"https://example.test/audit/{index}", "title": "Synthetic role",
                "company_name": "Synthetic employer", "description": "x" * 80_000,
            }).model_dump()
            self.remote.job(**payload)
        response = self.request("GET", "bootstrap")
        self.assertEqual(response.status_code, 200, response.text[:200])
        summaries = response.json()["jobs"]
        self.assertEqual(len(summaries), 110)
        self.assertTrue(all(row["description"] is None for row in summaries))
        self.assertLess(len(response.content), 256 * 1024)
        self.assertEqual(len(self.remote.tables["jobs"]), 110)
        self.assertTrue(all(row["description"] == "x" * 80_000 for row in self.remote.tables["jobs"]))
        detail = self.request("GET", "jobs/" + summaries[0]["id"])
        self.assertEqual(detail.status_code, 200, detail.text[:200])
        self.assertEqual(detail.json()["description"], "x" * 80_000)
        self.assertEqual(detail.json()["id"], summaries[0]["id"])

    def test_no_unconfirmed_direct_delete_and_privacy_routes_require_verified_email(self):
        for method, path in (("DELETE", "account"), ("GET", "account/export"), ("DELETE", "profile")):
            with self.subTest(path=path):
                self.assertIn(self.request(method, path).status_code, (404, 405))
        self.assertEqual(self.request("GET", "account").status_code, 403)
        self.assertEqual(self.request("POST", "account/exports", json={}).status_code, 403)
        self.assertEqual(self.request("POST", "account/erasure", json={}).status_code, 403)

    def test_active_pdf_javascript_rejected_with_fixed_message_before_storage(self):
        # Harmless marker only. No PDF viewer is opened; JavaScript is never run.
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
        from jobagent.mobile import studio
        from jobagent.mobile.pdf_safety import PDF_REJECTION_MESSAGE
        buffer = io.BytesIO()
        page = canvas.Canvas(buffer)
        page.drawString(72, 720, "Synthetic candidate builds reliable reporting services.")
        page.save()
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(buffer.getvalue())))
        writer.add_js("/* SYNTHETIC_INERT_AUDIT_MARKER */")
        output = io.BytesIO()
        writer.write(output)
        content = output.getvalue()
        self.app.state.studio = studio
        before_objects = copy.deepcopy(self.remote.objects)
        before_tables = copy.deepcopy(self.remote.tables)
        request_offset = len(self.remote.requests)
        # Spy only: use the actual adapter and resource-limited PDF subprocess.
        # Persistence is not mocked, so any attempted Storage/metadata write is
        # observable through the existing offline Supabase transport.
        with patch.object(studio, "extract_resume_text", wraps=studio.extract_resume_text) as extract:
            response = self.upload(content, filename="synthetic-active.pdf")
        extract.assert_called_once_with(content, "synthetic-active.pdf")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"detail": PDF_REJECTION_MESSAGE})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("SYNTHETIC_INERT_AUDIT_MARKER", response.text)
        requests = self.remote.requests[request_offset:]
        self.assertFalse(any(request.url.path.startswith("/storage/") for request in requests))
        self.assertEqual([(request.method, request.url.path) for request in requests], [
            ("GET", "/auth/v1/user"),
            ("POST", "/rest/v1/rpc/mobile_check_access"),  # Read-only access check.
        ])
        self.assertEqual(self.remote.objects, before_objects)
        self.assertEqual(self.remote.tables, before_tables)


class QuotaProbeTests(OfflineCase):
    def test_observes_separate_process_state_doubles_one_request_budget(self):
        first = TenantLimits(1, 1, 1)
        second = TenantLimits(1, 1, 1)
        first.check(USER_A)
        second.check(USER_A)
        for limits in (first, second):
            with self.assertRaises(HTTPException) as caught:
                limits.check(USER_A)
            self.assertEqual(caught.exception.status_code, 429)
        # A new app/restart also starts without the previous hourly history.
        TenantLimits(1, 1, 1).check(USER_A)


class BetaJWTProbeTests(OfflineCase):
    """Exercise the separate JWT helper with a new in-memory signing key only."""

    def setUp(self):
        super().setUp()
        try:
            import jwt
            from cryptography.hazmat.primitives.asymmetric import ec
        except ModuleNotFoundError:
            self.skipTest("Existing .venv lacks PyJWT/crypto; report as untested dependency gap, do not install")
        from jobagent.beta import auth
        self.jwt, self.auth = jwt, auth
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.settings = auth.BetaAuthSettings(SETTINGS.url, SETTINGS.url + "/auth/v1", "authenticated")
        self.jwks = Mock()
        self.jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.key.public_key())
        self.stack.enter_context(patch.object(auth._jwks_clients, "get", return_value=self.jwks))

    def encoded(self, **changes):
        claims = {"sub": USER_A, "role": "authenticated", "email": "synthetic@example.test", "iat": int(time.time()) - 5, "exp": int(time.time()) + 300, "aud": "authenticated", "iss": self.settings.jwt_issuer, **changes}
        return self.jwt.encode(claims, self.key, algorithm="ES256", headers={"kid": "in-memory-audit-key"})

    def test_valid_signature_and_identity_accept_only_verified_claims(self):
        claims = self.auth._decode_access_token(self.encoded(), self.settings)
        self.assertEqual(str(self.auth._to_authenticated_user(claims).id), USER_A)

    def test_wrong_issuer_audience_expiry_role_subject_and_signature_deny(self):
        for changes in ({"iss": "https://other.example.test/auth/v1"}, {"aud": "other"}, {"exp": 1}, {"nbf": int(time.time()) + 300}, {"role": "service_role"}, {"sub": "not-a-uuid"}):
            with self.subTest(fields=list(changes)), self.assertRaises(HTTPException) as caught:
                self.auth._to_authenticated_user(self.auth._decode_access_token(self.encoded(**changes), self.settings))
            self.assertEqual(caught.exception.status_code, 401)
        token = self.encoded()
        header, payload, signature = token.split(".")
        changed = ("A" if signature[0] != "A" else "B") + signature[1:]
        with self.assertRaises(HTTPException) as caught:
            self.auth._decode_access_token(".".join((header, payload, changed)), self.settings)
        self.assertEqual(caught.exception.status_code, 401)

    def test_symmetric_algorithm_is_rejected_before_jwks(self):
        token = self.jwt.encode({"sub": USER_A}, b"synthetic-only-signing-material-32", algorithm="HS256")
        with self.assertRaises(HTTPException) as caught:
            self.auth._decode_access_token(token, self.settings)
        self.assertEqual(caught.exception.status_code, 401)
        self.jwks.get_signing_key_from_jwt.assert_not_called()


class StaticBoundaryProbeTests(OfflineCase):
    def test_founder_auth_middleware_wraps_routes_and_denies_untrusted_identity(self):
        # Parse only: importing this file initializes the founder SQLite database.
        tree = ast.parse((ROOT / "src/jobagent/api/main.py").read_text())
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                   and node.module == "jobagent.api.founder_auth"]
        self.assertEqual(len(imports), 1)
        self.assertTrue({"FounderAuthMiddleware", "FounderAuthSettings"}.issubset(
            {alias.name for alias in imports[0].names}))
        registrations = [node.value for node in tree.body if isinstance(node, ast.Expr)
                         and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                         and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == "app"
                         and node.value.func.attr == "add_middleware"]
        self.assertTrue(registrations)
        last = registrations[-1]
        self.assertEqual(ast.unparse(last.args[0]), "FounderAuthMiddleware")
        self.assertEqual({keyword.arg: ast.unparse(keyword.value) for keyword in last.keywords},
                         {"settings": "founder_auth_settings"})
        routes = []
        for name in ("list_jobs", "send_outreach_draft", "trigger_autopilot", "trigger_direct_apply"):
            node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            decorators = [decorator for decorator in node.decorator_list if isinstance(decorator, ast.Call)
                          and isinstance(decorator.func, ast.Attribute) and decorator.args
                          and isinstance(decorator.args[0], ast.Constant)]
            self.assertEqual(len(decorators), 1)
            decorator = decorators[0]
            routes.append((decorator.func.attr.upper(), decorator.args[0].value.replace("{job_id}", "1")))

        # Import ONLY the side-effect-free auth module. This isolated downstream
        # handler can record access but cannot load owner data or launch work.
        from jobagent.api.founder_auth import FounderAuthMiddleware, FounderAuthSettings, is_founder_path
        token = "synthetic-only-development-token-" + "x" * 32
        settings = FounderAuthSettings(mode="loopback-dev", dev_token=token,
                                       allowed_origins=("http://localhost",))
        entered = []
        async def downstream(scope, receive, send):
            entered.append((scope["method"], scope["path"]))
            await JSONResponse({"synthetic": True})(scope, receive, send)
        middleware = FounderAuthMiddleware(downstream, settings=settings)
        routes.extend((('GET', '/admin/launch'), ('GET', '/openapi.json'), ('GET', '/api/mobile/lookalike')))

        async def exercise():
            async with httpx.AsyncClient(base_url="http://localhost", transport=httpx.ASGITransport(
                    app=middleware, client=("127.0.0.1", 1234))) as client:
                for method, path in routes:
                    self.assertTrue(is_founder_path(path))
                    for headers in ({}, {"Cf-Access-Authenticated-User-Email": "synthetic@example.test"},
                                    {"Authorization": "Bearer session-a", "X-Forwarded-For": "127.0.0.1"}):
                        response = await client.request(method, path, headers=headers)
                        self.assertEqual(response.status_code, 401, response.text)
                        self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(entered, [])
                for method, path in routes:
                    response = await client.request(method, path, headers={"Authorization": "Bearer " + token})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(entered, routes)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
