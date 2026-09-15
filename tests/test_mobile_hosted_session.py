"""Offline coordinator tests: synthetic auth and hosted services are ALL mocked."""
from __future__ import annotations

import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mobile_hosted_session as session
from test_mobile_hosted_check import SyntheticServer, token
import test_mobile_api as api_tests

LABEL = "1234ab56cd78"
EMAILS = session.planned_emails(LABEL)
SECRET_ERROR = "PRIVATE_SYNTHETIC_AUTH_ERROR_DO_NOT_PRINT"


class AuthServer(SyntheticServer):
    """Explicit fake: confirmation is changed by tests, never by harness routes."""

    def __init__(self):
        super().__init__()
        self.auth_requests = []
        self.credentials = {}
        self.autoconfirm = False
        self.rejected = False
        self.confirmed = False
        self.wrong_remote_user = False

    def user(self, email):
        user_id = api_tests.USER_A if email == EMAILS[0] else api_tests.USER_B
        return {"id": user_id, "email": email, "role": "authenticated",
                "email_confirmed_at": "2026-09-01T00:00:00Z" if self.confirmed else None,
                "identities": [{"id": user_id}],
                "user_metadata": {"mobile_hosted_check": True, "mobile_hosted_run": LABEL}}

    def __call__(self, request):
        path = request.url.path
        if path in ("/auth/v1/signup", "/auth/v1/token", "/auth/v1/logout"):
            self.auth_requests.append(request)
            if self.rejected:
                return httpx.Response(429, json={"message": SECRET_ERROR})
            if path == "/auth/v1/logout":
                return httpx.Response(204)
            body = json.loads(request.content)
            assert body["email"] in EMAILS
            user = self.user(body["email"])
            if path == "/auth/v1/signup":
                self.credentials[body["email"]] = body["password"]
                self.auth_emails[user["id"]] = user
                if self.autoconfirm:
                    return httpx.Response(200, json={"access_token": SECRET_ERROR, "user": user})
                return httpx.Response(200, json=user)
            assert request.url.params["grant_type"] == "password"
            assert body["password"] == self.credentials[body["email"]]
            if not self.confirmed:
                return httpx.Response(400, json={"error_code": "email_not_confirmed", "message": SECRET_ERROR})
            value = token(user["id"])
            api_tests.TOKENS[value] = user["id"]
            self.auth_emails[user["id"]] = user
            return httpx.Response(200, json={"access_token": value, "refresh_token": SECRET_ERROR, "user": user})
        if path == "/auth/v1/user" and self.wrong_remote_user:
            return httpx.Response(200, json=self.user(EMAILS[1]))
        return super().__call__(request)


class HostedSessionTests(unittest.TestCase):
    def setUp(self):
        for name in ("connect", "connect_ex"):
            guard = patch.object(socket.socket, name, side_effect=AssertionError("Network forbidden in unit tests"))
            guard.start()
            self.addCleanup(guard.stop)
        mappings = patch.dict(api_tests.TOKENS, {}, clear=True)
        mappings.start()
        self.addCleanup(mappings.stop)
        self.server = AuthServer()
        self.wire = session.Wire(api_tests.SETTINGS, transport=httpx.MockTransport(self.server))
        self.addCleanup(self.wire.close)
        self.coordinator = session.Coordinator(self.wire, LABEL)

    def confirmed_session(self):
        self.coordinator.signup()
        self.server.confirmed = True  # Simulates the human's clicks in mock only.
        return self.coordinator.signin(operator_confirmed=True)

    def test_signup_is_exactly_two_normal_password_requests_and_no_bypass(self):
        self.coordinator.signup()
        self.assertEqual(len(self.server.auth_requests), 2)
        passwords = []
        for request, email in zip(self.server.auth_requests, EMAILS):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/auth/v1/signup")
            self.assertEqual(request.url.query, b"")
            self.assertNotIn("authorization", request.headers)
            data = json.loads(request.content)
            self.assertEqual(set(data), {"email", "password", "data"})
            self.assertEqual(data["email"], email)
            self.assertEqual(data["data"], {"mobile_hosted_check": True, "mobile_hosted_run": LABEL})
            self.assertGreaterEqual(len(data["password"]), 44)
            passwords.append(data["password"])
        self.assertNotEqual(*passwords)
        with self.assertRaises(session.SessionFailure):
            self.coordinator.signup()
        with self.assertRaises(session.SessionFailure):
            self.coordinator.signin()
        self.assertEqual(len(self.server.auth_requests), 2)
        self.assertEqual(self.coordinator.state, "awaiting-clicks")

    def test_fixed_health_redirect_uses_signup_query_without_browser_credentials(self):
        coordinator = session.Coordinator(self.wire, LABEL, confirmation_redirect_health=True)
        coordinator.signup()
        expected = b"redirect_to=https%3A%2F%2Fwww.thejobpursuit.com%2Fapi%2Fmobile%2Fhealth"
        self.assertEqual(session.CONFIRMATION_REDIRECT_QUERY.encode("ascii"), expected)
        self.assertEqual(len(self.server.auth_requests), 2)
        for request in self.server.auth_requests:
            self.assertEqual(request.url.host, "mobile.example.test")
            self.assertEqual(request.url.path, "/auth/v1/signup")
            self.assertEqual(request.url.query, expected)
            self.assertEqual(dict(request.url.params), {"redirect_to": session.CONFIRMATION_REDIRECT})
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("authorization", request.headers)
            self.assertEqual(set(json.loads(request.content)), {"email", "password", "data"})
        self.assertEqual(self.wire.count, 2)  # Neither landing page nor browser is accessed.
        self.assertTrue(all(not account.token for account in coordinator.accounts))

    def test_wire_redirect_accepts_only_exact_fixed_encoding(self):
        fixed = session.CONFIRMATION_REDIRECT_QUERY
        invalid = (
            "redirect_to=https%3A%2F%2Fevil.example%2Fapi%2Fmobile%2Fhealth",
            "redirect_to=https%3A%2F%2Fwww.thejobpursuit.com.evil.example%2Fapi%2Fmobile%2Fhealth",
            fixed.replace("https%3A", "http%3A"),
            fixed.replace("%2Fapi%2Fmobile%2Fhealth", "%2Fbeta"),
            fixed + "&redirect_to=https%3A%2F%2Fevil.example",
            fixed + "&access_token=" + SECRET_ERROR,
            fixed + "%23access_token%3D" + SECRET_ERROR,
            "redirect_to=" + session.CONFIRMATION_REDIRECT,
            fixed.replace("%3A", "%3a"),
        )
        for query in invalid:
            with self.subTest(query_index=invalid.index(query)), self.assertRaises(session.SessionFailure):
                self.wire.send(httpx.Request("POST", api_tests.SETTINGS.url + "/auth/v1/signup?" + query,
                                             headers={"apikey": api_tests.SETTINGS.publishable_key}), auth=True)
        self.assertEqual(self.wire.count, 0)
        self.assertEqual(self.server.auth_requests, [])

    def test_redirect_flag_is_offline_and_cannot_take_arbitrary_url(self):
        with patch.object(session, "load_public_settings", side_effect=AssertionError("no config reads")), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(session.main(["--run-label", LABEL, "--confirmation-redirect-health"]), 0)
            body = json.loads(output.getvalue())
            self.assertEqual(body["confirmation_redirect"], session.CONFIRMATION_REDIRECT)
            self.assertTrue(body["redirect_allowlist_must_be_verified_by_main"])
            with self.assertRaises(SystemExit):
                session.main(["--confirmation-redirect-health", "https://evil.example/?token=" + SECRET_ERROR])
        self.assertNotIn(SECRET_ERROR, output.getvalue() + errors.getvalue())
        with self.assertRaises(session.SessionFailure):
            session.Coordinator(self.wire, LABEL, confirmation_redirect_health="https://evil.example")
        self.assertEqual(self.wire.count, 0)

    def test_autoconfirm_and_rate_limit_stop_without_retry_or_second_email(self):
        for condition in ("autoconfirm", "rejected"):
            with self.subTest(condition=condition):
                server = AuthServer()
                setattr(server, condition, True)
                wire = session.Wire(api_tests.SETTINGS, transport=httpx.MockTransport(server))
                coordinator = session.Coordinator(wire, LABEL)
                with self.assertRaises(session.SessionFailure) as error:
                    coordinator.signup()
                self.assertNotIn(SECRET_ERROR, str(error.exception))
                self.assertEqual(len(server.auth_requests), 1)
                self.assertEqual(coordinator.signup_attempts, 1)
                coordinator.close()

    def test_human_signal_does_not_bypass_server_email_confirmation(self):
        self.coordinator.signup()
        with self.assertRaises(session.SessionFailure):
            self.coordinator.signin(operator_confirmed=True)
        self.assertEqual(self.coordinator.signin_attempts, 1)
        self.assertFalse(any(r.url.path.startswith("/rest/") for r in self.server.requests))
        with self.assertRaises(session.SessionFailure):
            self.coordinator.signin(operator_confirmed=True)
        self.assertEqual(self.coordinator.signin_attempts, 1)

    def test_remote_identity_must_match_signup_not_just_token_response(self):
        self.coordinator.signup()
        self.server.confirmed = True
        self.server.wrong_remote_user = True
        with self.assertRaisesRegex(session.SessionFailure, "auth-email-mismatch"):
            self.coordinator.signin(operator_confirmed=True)
        self.assertEqual(self.wire.user_ids, set())

    def test_wire_forwards_only_empty_read_only_membership_rpc_for_verified_qa_session(self):
        config = self.confirmed_session()
        before = self.wire.count
        response = self.wire.send(httpx.Request("POST",
            api_tests.SETTINGS.url + "/rest/v1/rpc/mobile_check_access",
            headers={"apikey": api_tests.SETTINGS.publishable_key,
                     "Authorization": "Bearer " + config.user_a_token}, json={}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"allowed": True, "user_id": api_tests.USER_A})
        self.assertEqual(self.wire.count, before + 1)
        forwarded = self.server.requests[-1]
        self.assertEqual(forwarded.url.path, "/rest/v1/rpc/mobile_check_access")
        self.assertEqual(forwarded.method, "POST")
        self.assertEqual(forwarded.content, b"{}")
        self.assertEqual(forwarded.headers["Authorization"], "Bearer " + config.user_a_token)

    def test_wire_rejects_membership_rpc_parameters_unverified_sessions_and_paid_reservations(self):
        config = self.confirmed_session()
        before, received = self.wire.count, len(self.server.requests)
        root = "/rest/v1/rpc/"
        cases = (
            ("GET", "mobile_check_access", {}, config.user_a_token),
            ("POST", "mobile_check_access?user_id=eq." + api_tests.USER_B, {}, config.user_a_token),
            ("POST", "mobile_check_access", {"user_id": api_tests.USER_B}, config.user_a_token),
            ("POST", "mobile_check_access", {"quota": 999999, "admin": True}, config.user_a_token),
            ("POST", "mobile_check_access", None, config.user_a_token),
            ("POST", "mobile_check_access", {}, ""),
            ("POST", "mobile_check_access", {}, "unverified-session"),
            ("POST", "mobile_reserve_ai_usage", {"p_operation": "prepare", "p_reservation_id": api_tests.USER_A}, config.user_a_token),
            ("POST", "admin", {}, config.user_a_token),
        )
        for method, path, body, bearer in cases:
            with self.subTest(method=method, path=path, fields=sorted(body or {})), self.assertRaises(session.SessionFailure):
                self.wire.send(httpx.Request(method, api_tests.SETTINGS.url + root + path,
                    headers={"apikey": api_tests.SETTINGS.publishable_key,
                             "Authorization": "Bearer " + bearer}, json=body))
        self.assertEqual(self.wire.count, before)
        self.assertEqual(len(self.server.requests), received)

    def test_membership_denial_is_forwarded_not_replaced_by_qa_signup_metadata(self):
        config = self.confirmed_session()
        self.server.fault = lambda request: httpx.Response(200, json={
            "allowed": False, "code": "not_entitled"}) if request.url.path == "/rest/v1/rpc/mobile_check_access" else None
        report = session.run_hosted_check(self.coordinator, config)
        self.assertFalse(report.ok)
        self.assertEqual(report.failures, ["preflight: unexpected-http-status"])
        # Verified Auth identities are not membership: preflight must stop
        # before granting workspace access or mutating any candidate data.
        self.assertEqual(report.passed, ["both synthetic identities remotely verified"])
        self.assertTrue(any(request.url.path == "/rest/v1/rpc/mobile_check_access"
                            for request in self.server.requests))
        self.assertFalse(any(request.method in {"POST", "PATCH", "DELETE"}
                             and request.url.path.startswith("/rest/v1/")
                             and request.url.path != "/rest/v1/rpc/mobile_check_access"
                             for request in self.server.requests))
        self.assertFalse(any(request.url.path == "/rest/v1/rpc/mobile_reserve_ai_usage"
                             for request in self.server.requests))

    def test_all_18_checks_via_real_local_api_and_mock_supabase_restore_qa_fields(self):
        baseline = copy.deepcopy(self.server.tables)
        config = self.confirmed_session()
        with patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": "owner-only@example.test"}):
            report = session.run_hosted_check(self.coordinator, config)
            self.assertEqual(os.environ["MOBILE_ALLOWED_EMAILS"], "owner-only@example.test")
        self.assertTrue(report.ok, (report.failures, report.cleanup_failures))
        self.assertEqual(len(report.passed), 18)
        self.assertEqual(self.server.objects, {})
        for table, rows in baseline.items():
            actual = self.server.tables[table]
            if table == "mobile_resume_operations":
                # This run removes fixture bytes/metadata but deliberately
                # retains immutable, owner-scoped terminal delete intents.
                prior_ids = {(row["id"], row["user_id"]) for row in rows}
                retained = [row for row in actual if (row["id"], row["user_id"]) not in prior_ids]
                markers = {config.user(actor)[0]: report.run_label + "-" + actor for actor in ("A", "B")}
                writes = [json.loads(req.content) for req in self.server.requests
                          if req.method == "POST" and req.url.path == "/rest/v1/resumes"]
                uploaded = {(row["id"], row["user_id"]): row for row in writes
                            if row.get("label") == markers.get(row.get("user_id"))}
                self.assertEqual(len(uploaded), 2)
                self.assertEqual({owner for _, owner in uploaded}, set(markers))
                self.assertEqual(len(retained), 2)
                self.assertEqual({(row["id"], row["user_id"]) for row in retained}, set(uploaded))
                for row in retained:
                    self.assertEqual(row["state"], "deleted")
                    self.assertEqual(row["resume_data"]["id"], row["id"])
                    self.assertEqual(row["resume_data"]["label"], markers[row["user_id"]])
                    self.assertEqual(row["resume_data"]["storage_path"], uploaded[row["id"], row["user_id"]]["storage_path"])
                    self.assertNotIn(("resumes", row["resume_data"]["storage_path"]), self.server.objects)
                actual = [row for row in actual if (row["id"], row["user_id"]) in prior_ids]
            self.assertEqual(len(actual), len(rows), table)
            for before, after in zip(rows, actual):
                for key in before.keys() | after.keys():
                    self.assertEqual(before.get(key), after.get(key), table + "." + key)
        self.assertFalse(any(req.method == "DELETE" and req.url.path.endswith("mobile_resume_operations") for req in self.server.requests))
        self.assertEqual(self.coordinator.signup_attempts, 2)
        self.assertEqual(self.coordinator.signin_attempts, 2)
        self.assertEqual(self.coordinator.close(), [])
        self.assertTrue(all(not a.password and not a.token for a in self.coordinator.accounts))
        logouts = [r for r in self.server.auth_requests if r.url.path == "/auth/v1/logout"]
        self.assertEqual(len(logouts), 2)
        self.assertTrue(all(dict(r.url.params) == {"scope": "local"} for r in logouts))

    def test_wire_refuses_other_origins_admin_auth_writes_and_owner_data(self):
        for url, auth in (("https://evil.example/auth/v1/signup", True),
                          (api_tests.SETTINGS.url + "/auth/v1/admin/users", True),
                          (api_tests.SETTINGS.url + "/auth/v1/verify", True),
                          (api_tests.SETTINGS.url + "/auth/v1/resend", True),
                          (api_tests.SETTINGS.url + "/rest/v1/profiles?user_id=eq.owner", False)):
            with self.subTest(url=url), self.assertRaises(session.SessionFailure):
                self.wire.send(httpx.Request("POST", url, headers={"apikey": api_tests.SETTINGS.publishable_key}), auth=auth)
        self.assertEqual(self.wire.count, 0)
        self.assertEqual(self.server.auth_requests, [])

    def test_default_and_missing_flags_never_read_config_or_connect(self):
        with patch.object(session, "load_public_settings", side_effect=AssertionError("no config reads")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(session.main(["--run-label", LABEL]), 0)
            self.assertEqual(session.main(["--execute"]), 2)
            self.assertEqual(session.main(["--confirm-two-emails"]), 2)
        self.assertIn("plan-only", output.getvalue())
        self.assertEqual(self.server.auth_requests, [])

    def test_cli_and_upstream_failures_do_not_echo_credentials(self):
        self.server.rejected = True
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(session, "load_public_settings", return_value=api_tests.SETTINGS), patch.object(session, "Wire", return_value=self.wire), patch("sys.stdin.isatty", return_value=True), redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(session.main(["--execute", "--confirm-two-emails", "--run-label", LABEL, "--base-email", session.BASE_EMAIL]), 1)
            with self.assertRaises(SystemExit):
                session.main(["--password", SECRET_ERROR])
        self.assertNotIn(SECRET_ERROR, output.getvalue() + errors.getvalue())
        for password in self.server.credentials.values():
            self.assertNotIn(password, output.getvalue() + errors.getvalue())

    def test_plus_addresses_only_and_secret_reprs(self):
        for label in ("", "bad+label", "x@gmail.com", "A" * 12, "a" * 21):
            with self.assertRaises(session.SessionFailure):
                session.planned_emails(label)
        for account in self.coordinator.accounts:
            self.assertNotEqual(account.email, session.BASE_EMAIL)
            self.assertNotIn(account.password, repr(account))

    def test_public_loader_selects_only_public_fields_without_env_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text(".env.mobile\n")
            target = root / ".env.mobile"
            target.write_text("MOBILE_SUPABASE_URL=" + api_tests.SETTINGS.url + "\nMOBILE_SUPABASE_PUBLISHABLE_KEY=" + api_tests.SETTINGS.publishable_key + "\nSUPABASE_SERVICE_ROLE_KEY=" + SECRET_ERROR + "\n")
            before = dict(os.environ)
            from dotenv import dotenv_values

            def only_public(*args, **kwargs):
                contents = kwargs["stream"].getvalue()
                self.assertNotIn(SECRET_ERROR, contents)
                self.assertNotIn("SERVICE_ROLE", contents)
                self.assertFalse(kwargs["interpolate"])
                return dotenv_values(*args, **kwargs)

            with patch("dotenv.dotenv_values", side_effect=only_public):
                self.assertEqual(session.load_public_settings(root=root), api_tests.SETTINGS)
            self.assertEqual(dict(os.environ), before)
            subprocess.run(["git", "-C", str(root), "add", "-f", ".env.mobile"], check=True)
            with self.assertRaises(session.SessionFailure):
                session.load_public_settings(root=root)

    def test_click_command_timeout_and_abort_are_offline(self):
        with patch.object(session.select, "select", return_value=(["ready"], [], [])):
            session.wait_for_clicks(io.StringIO("CONFIRMED\n"), timeout=1)
            for command in ("ABORT\n", "", "https://example.test/verify?token=PRIVATE\n"):
                with self.assertRaises(session.SessionFailure):
                    session.wait_for_clicks(io.StringIO(command), timeout=1)
        with self.assertRaisesRegex(session.SessionFailure, "confirmation-wait-expired"):
            session.wait_for_clicks(io.StringIO(), timeout=0)
        self.assertEqual(self.wire.count, 0)


if __name__ == "__main__":
    unittest.main()
