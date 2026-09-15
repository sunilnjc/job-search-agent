"""Offline hosted-harness tests. All HTTP uses in-memory mock transports."""

from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import httpx
from fastapi.testclient import TestClient

import test_mobile_api as api_tests
from jobagent.mobile.app import create_app

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mobile_hosted_check", ROOT / "scripts/mobile_hosted_check.py")
hosted = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hosted
SPEC.loader.exec_module(hosted)


def token(user_id, **changes):
    claims = {"sub": user_id, "role": "authenticated", "iss": api_tests.SETTINGS.url + "/auth/v1", "exp": int(time.time()) + 3600, **changes}
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return encode({"alg": "HS256", "typ": "JWT"}) + "." + encode(claims) + ".synthetic_signature"


def values():
    return {"supabase_url": api_tests.SETTINGS.url, "api_url": "http://127.0.0.1:8843", "publishable_key": api_tests.SETTINGS.publishable_key, "user_a_id": api_tests.USER_A, "user_a_token": token(api_tests.USER_A), "user_b_id": api_tests.USER_B, "user_b_token": token(api_tests.USER_B)}


class SyntheticServer(api_tests.FakeSupabase):
    def __init__(self):
        super().__init__()
        self.leak_rows = False
        self.leak_storage = False
        self.harness_requests = []
        for user_id in (api_tests.USER_A, api_tests.USER_B):
            self.auth_emails[user_id] = {"email_confirmed_at": "2026-09-01T00:00:00Z", "user_metadata": {"mobile_hosted_check": True}}

    def __call__(self, request):
        user = api_tests.TOKENS.get(request.headers.get("authorization", "").removeprefix("Bearer "))
        path = request.url.path
        if path.startswith("/rest/v1/"):
            target = request.url.params.get("user_id", "").removeprefix("eq.")
            if target and user != target:
                self.requests.append(request)
                if self.leak_rows and request.method == "GET":
                    return httpx.Response(200, json=[{"id": "synthetic", "user_id": target}])
                return httpx.Response(403, json={"code": "42501"}) if request.method == "POST" else httpx.Response(200, json=[])
        if path.startswith("/storage/v1/object/"):
            parts = path.removeprefix("/storage/v1/object/").split("/")
            if parts[0] == "list":
                self.requests.append(request)
                return httpx.Response(200, json=[])
            if parts[0] == "public":
                self.requests.append(request)
                return httpx.Response(404)
            bucket, owner = parts[:2]
            name = "/".join(parts[1:])
            if user != owner:
                self.requests.append(request)
                if self.leak_storage and request.method == "GET":
                    return httpx.Response(200, content=b"private fixture data")
                return httpx.Response(403)
            if request.method == "PUT":
                self.requests.append(request)
                self.objects[bucket, name] = request.content
                return httpx.Response(200, json={"updated": True})
        return super().__call__(request)


class HostedHarnessTests(unittest.TestCase):
    def setUp(self):
        # Reuse one synthetic-token snapshot: regenerating exp from time.time()
        # can cross a second boundary before file/env round-trip assertions.
        self.credential_values = values()
        self.config = hosted.Credentials.parse(self.credential_values)
        self.server = SyntheticServer()
        self.studio = api_tests.fake_studio()

        def extract(content, filename):
            with ZipFile(io.BytesIO(content)) as archive:
                return "".join(ElementTree.fromstring(archive.read("word/document.xml")).itertext())
        self.studio.extract_resume_text.side_effect = extract
        self.mapping = patch.dict(api_tests.TOKENS, {self.config.user_a_token: api_tests.USER_A, self.config.user_b_token: api_tests.USER_B}, clear=True)
        self.mapping.start()
        self.addCleanup(self.mapping.stop)
        application = create_app(settings=api_tests.SETTINGS, transport=httpx.MockTransport(self.server), studio=self.studio)
        self.api = TestClient(application, raise_server_exceptions=False)
        self.addCleanup(self.api.close)

        def dispatch(request):
            self.server.harness_requests.append(request)
            if request.url.host == "127.0.0.1":
                response = self.api.request(request.method, request.url.raw_path.decode(), headers=dict(request.headers), content=request.content)
                return httpx.Response(response.status_code, content=response.content)
            return self.server(request)
        self.check = hosted.HostedCheck(self.config, transport=httpx.MockTransport(dispatch))
        self.addCleanup(self.check.client.close)

    def test_complete_two_user_workflow_and_cleanup(self):
        baseline = copy.deepcopy(self.server.tables)
        report = self.check.run()
        self.assertTrue(report.ok, (report.failures, report.cleanup_failures))
        self.assertEqual(len(report.passed), 18)
        self.assertEqual(self.server.objects, {})
        for table, original in baseline.items():
            actual = self.server.tables[table]
            if table == "mobile_resume_operations":
                # Immutable delete tombstones are a recovery guarantee, not
                # leaked fixture bytes. Only this run's two terminal intents
                # may be added; every preexisting journal row stays unchanged.
                prior_ids = {(row["id"], row["user_id"]) for row in original}
                retained = [row for row in actual if (row["id"], row["user_id"]) not in prior_ids]
                self.assertEqual(len(retained), 2)
                self.assertEqual({(row["id"], row["user_id"]) for row in retained}, {
                    (self.check.fixtures[actor]["resume"], self.config.user(actor)[0]) for actor in ("A", "B")
                })
                for row in retained:
                    self.assertEqual(row["state"], "deleted")
                    self.assertEqual(row["resume_data"]["id"], row["id"])
                    self.assertTrue(row["resume_data"]["label"].startswith(report.run_label))
                    self.assertNotIn(("resumes", row["resume_data"]["storage_path"]), self.server.objects)
                actual = [row for row in actual if (row["id"], row["user_id"]) in prior_ids]
            self.assertEqual(len(actual), len(original), table)
            for before, after in zip(original, actual):
                for key in before.keys() | after.keys():
                    self.assertEqual(before.get(key), after.get(key), table + "." + key)
        self.studio.prepare_documents.assert_not_called()
        self.studio.rank_job.assert_not_called()
        self.studio.answer_chat.assert_not_called()
        for request in self.server.harness_requests:
            if "/auth/" in request.url.path:
                self.assertEqual((request.method, request.url.path), ("GET", "/auth/v1/user"))
            if request.url.host == "127.0.0.1":
                self.assertNotIn("apikey", request.headers)
        self.assertFalse(any(req.method == "DELETE" and req.url.path.endswith("mobile_resume_operations") for req in self.server.requests))

    def test_interrupted_workflow_cleanup_finishes_resume_journals(self):
        with patch.object(self.check, "cross_user", side_effect=hosted.CheckFailure("synthetic-interruption")):
            report = self.check.run()
        self.assertFalse(report.ok)
        self.assertEqual(report.cleanup_failures, [])
        self.assertEqual(self.server.objects, {})
        self.assertEqual(self.server.tables["resumes"], [])
        journals = self.server.tables["mobile_resume_operations"]
        self.assertEqual(len(journals), 2)
        self.assertTrue(all(row["state"] == "deleted" for row in journals))

    def test_failed_resume_cleanup_never_falls_back_to_raw_storage_deletion(self):
        self.server.fault = lambda req: httpx.Response(500) if req.method == "DELETE" and req.url.path == "/rest/v1/resumes" else None
        with patch.object(self.check, "cross_user", side_effect=hosted.CheckFailure("synthetic-interruption")):
            report = self.check.run()
        self.assertFalse(report.ok)
        self.assertTrue(report.cleanup_failures)
        self.assertEqual(len(self.server.tables["resumes"]), 2)
        journals = self.server.tables["mobile_resume_operations"]
        self.assertEqual(len(journals), 2)
        self.assertTrue(all(row["state"] == "delete_pending" for row in journals))
        for row in journals:
            self.assertIn(("resumes", row["resume_data"]["storage_path"]), self.server.objects)
        self.assertFalse(any(req.method == "DELETE" and req.url.path.startswith("/storage/v1/object/resumes/") for req in self.server.requests))

    def test_missing_second_synthetic_marker_stops_before_writes(self):
        self.server.auth_emails[api_tests.USER_B]["user_metadata"] = {}
        report = self.check.run()
        self.assertFalse(report.ok)
        self.assertEqual(len(self.server.harness_requests), 2)
        self.assertTrue(all(request.method == "GET" for request in self.server.harness_requests))
        self.assertEqual(self.check.changes, {})

    def test_remote_identity_is_verified_not_just_jwt_claims(self):
        self.server.fault = lambda req: httpx.Response(200, json={"id": api_tests.USER_B, "role": "authenticated", "email_confirmed_at": "yes", "user_metadata": {"mobile_hosted_check": True}}) if req.url.path == "/auth/v1/user" else None
        report = self.check.run()
        self.assertIn("remote-user-identity-mismatch", report.failures[0])
        self.assertEqual(self.check.changes, {})

    def test_detects_broken_rls_and_cleans_fixtures(self):
        self.server.leak_rows = True
        report = self.check.run()
        self.assertFalse(report.ok)
        self.assertIn("cross-user-singleton-visible", report.failures[0])
        self.assertEqual(report.cleanup_failures, [])
        self.assertEqual(self.server.tables["jobs"], [])
        self.assertEqual(self.server.objects, {})

    def test_detects_private_storage_leak(self):
        self.server.leak_storage = True
        report = self.check.run()
        self.assertFalse(report.ok)
        self.assertIn("cross-user-storage-readable", report.failures[0])
        self.assertEqual(report.cleanup_failures, [])

    def test_model_auth_write_and_arbitrary_routes_are_blocked_before_transport(self):
        for surface, method, path in (("api", "POST", "/api/mobile/chat"), ("api", "POST", "/api/mobile/jobs/" + api_tests.USER_A + "/rank"), ("api", "POST", "/api/mobile/jobs/" + api_tests.USER_A + "/prepare"), ("supabase", "PUT", "/auth/v1/user"), ("supabase", "POST", "/auth/v1/token"), ("supabase", "POST", "/rest/v1/rpc/admin")):
            with self.subTest(path=path), self.assertRaises(hosted.CheckFailure):
                self.check.request(surface, "A", method, path)
        self.assertEqual(self.server.harness_requests, [])

    def test_no_execute_or_missing_affirmation_never_loads_credentials(self):
        output = io.StringIO()
        with patch.object(hosted, "load_credentials", side_effect=AssertionError("must not load")), patch.object(hosted, "HostedCheck", side_effect=AssertionError("must not construct")), redirect_stdout(output):
            self.assertEqual(hosted.main([]), 0)
            self.assertEqual(hosted.main(["--execute"]), 2)
        self.assertIn("OFFLINE", output.getvalue())

    def test_cli_pass_requires_all_checks_and_cleanup_to_pass(self):
        for report, expected in ((hosted.Report("synthetic-run"), 0), (hosted.Report("synthetic-run", cleanup_failures=["cleanup incomplete"]), 1), (hosted.Report("synthetic-run", failures=["failed"]), 1)):
            with patch.object(hosted, "load_credentials", return_value=self.config), patch.object(hosted.HostedCheck, "run", return_value=report), redirect_stdout(io.StringIO()):
                self.assertEqual(hosted.main(["--execute", "--confirm-synthetic-users"]), expected)

    def test_credential_errors_and_cli_arguments_do_not_echo_values(self):
        secret = "do-not-echo-synthetic-credential"
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(hosted, "load_credentials", side_effect=ValueError(secret)), redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(hosted.main(["--execute", "--confirm-synthetic-users"]), 2)
            with self.assertRaises(SystemExit):
                hosted.main(["--token", secret])
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())
        self.assertNotIn(self.config.user_a_token, repr(self.config))

    def test_validates_credentials_scope_roles_expiry_and_urls(self):
        for update in ({"user_b_id": api_tests.USER_A}, {"user_a_token": token(api_tests.USER_A, role="service_role")}, {"user_a_token": token(api_tests.USER_A, exp=0)}, {"user_a_token": token(api_tests.USER_B)}, {"publishable_key": "sb_" + "secret_" + "synthetic_forbidden_key"}, {"api_url": "http://external.example"}, {"api_url": "https://example.test/?token=forbidden"}, {"supabase_url": "http://127.0.0.1:8843"}):
            with self.subTest(fields=list(update)), self.assertRaises(hosted.CheckFailure):
                hosted.Credentials.parse({**values(), **update})
        self.assertEqual(hosted.load_credentials(None, {"MOBILE_HOSTED_" + key.upper(): value for key, value in self.credential_values.items()}), self.config)

    def test_private_ignored_json_file_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            # Only generated synthetic credentials are written by this unit test.
            (root / ".gitignore").write_text("private.json\n")
            target = root / "private.json"
            target.write_text(json.dumps(self.credential_values))
            target.chmod(0o600)
            self.assertEqual(hosted.load_credentials(str(target), root=root), self.config)
            target.chmod(0o644)
            with self.assertRaises(hosted.CheckFailure):
                hosted.load_credentials(str(target), root=root)
            target.chmod(0o600)
            subprocess.run(["git", "-C", str(root), "add", "-f", "private.json"], check=True)
            with self.assertRaises(hosted.CheckFailure):
                hosted.load_credentials(str(target), root=root)
            linked = root / "link.json"
            linked.symlink_to(target)
            with self.assertRaises(hosted.CheckFailure):
                hosted.load_credentials(str(linked), root=root)

    def test_redirects_and_upstream_errors_are_redacted(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(302, headers={"Location": "https://outside.example"}, text="secret upstream material"))
        check = hosted.HostedCheck(self.config, transport=transport)
        report = check.run()
        self.assertFalse(report.ok)
        self.assertIn("redirect-refused", report.failures[0])
        self.assertNotIn("secret", repr(report))

    def test_storage_reads_bypass_stale_cdn_entries_with_unique_nonces(self):
        observed = []
        def cached_storage(request):
            observed.append(request)
            if request.url.params.get("mobile_check_nonce") and request.headers.get("cache-control") == "no-cache":
                return httpx.Response(400, json={"statusCode": "404", "error": "not_found", "code": "NoSuchKey", "message": "Object not found"})
            return httpx.Response(200, content=b"stale deleted synthetic content")
        check = hosted.HostedCheck(self.config, transport=httpx.MockTransport(cached_storage))
        self.addCleanup(check.client.close)
        path = "/storage/v1/object/resumes/" + api_tests.USER_A + "/synthetic.docx"
        for _ in range(2):
            self.assertTrue(check.storage_denied(check.request("supabase", "A", "GET", path)))
        self.assertNotEqual(observed[0].url.params["mobile_check_nonce"], observed[1].url.params["mobile_check_nonce"])

    def test_preexisting_synthetic_records_and_context_are_preserved(self):
        background = {"profession": "Synthetic teaching", "experience_level": "student", "qualifications": []}
        original = self.server.add("candidate_context", api_tests.USER_A, career_text="Original synthetic facts", career_background=background)
        baseline = copy.deepcopy(original)
        existing = self.server.job(api_tests.USER_A)
        report = self.check.run()
        self.assertTrue(report.ok, (report.failures, report.cleanup_failures))
        self.assertEqual(self.server.tables["jobs"], [existing])
        self.assertEqual(self.server.tables["candidate_context"], [baseline])

    def test_missing_background_column_fails_before_writes(self):
        self.server.fault = lambda req: httpx.Response(400, json={"code": "42703"}) if req.method == "GET" and req.url.path == "/rest/v1/candidate_context" and "career_background" in req.url.params.get("select", "") else None
        report = self.check.run()
        self.assertFalse(report.ok)
        self.assertIn("preflight", report.failures[0])
        self.assertEqual(self.check.changes, {})
        self.assertTrue(all(req.method == "GET" for req in self.server.harness_requests))

    def test_omission_regression_is_detected_and_cleanup_restores_profile(self):
        original_api = self.check.api
        def dropped_background(actor, method, path, **options):
            response = original_api(actor, method, path, **options)
            if method == "PUT" and path == "profile" and set(options.get("json", {})) == {"display_name"}:
                body = response.json()
                body["career_background"] = hosted.EMPTY_BACKGROUND
                return httpx.Response(200, json=body)
            return response
        with patch.object(self.check, "api", side_effect=dropped_background):
            report = self.check.run()
        self.assertIn("omitted-background-not-preserved", report.failures[0])
        self.assertEqual(report.cleanup_failures, [])
        self.assertEqual(self.server.tables["candidate_context"], [])

    def test_failed_clear_restores_previously_attempted_background(self):
        baseline = {"profession": "Synthetic teacher", "experience_level": "student", "qualifications": []}
        original = copy.deepcopy(self.server.add("candidate_context", api_tests.USER_A, career_text="Original synthetic facts", career_background=baseline))
        original_api = self.check.api
        def fail_clear(actor, method, path, **options):
            if method == "PUT" and path == "profile" and options.get("json") == {"career_background": None}:
                return httpx.Response(503)
            return original_api(actor, method, path, **options)
        with patch.object(self.check, "api", side_effect=fail_clear):
            report = self.check.run()
        self.assertFalse(report.ok)
        self.assertEqual(report.cleanup_failures, [])
        self.assertEqual(self.server.tables["candidate_context"], [original])

    def test_cleanup_preserves_unrelated_concurrent_background_change(self):
        self.check.preflight()
        self.check.own_workflow("A")
        current = self.server.tables["candidate_context"][0]
        current["career_background"] = {"profession": "Unrelated concurrent synthetic edit", "experience_level": "mid", "qualifications": []}
        expected = copy.deepcopy(current)
        self.check.cleanup()
        self.assertTrue(self.check.report.cleanup_failures)
        self.assertEqual(self.server.tables["candidate_context"], [expected])

    def test_cleanup_failure_prevents_pass(self):
        with patch.object(self.check, "cleanup", side_effect=RuntimeError("secret cleanup details")):
            report = self.check.run()
        self.assertFalse(report.ok)
        self.assertTrue(report.cleanup_failures)
        self.assertNotIn("secret", repr(report))


if __name__ == "__main__":
    unittest.main()
