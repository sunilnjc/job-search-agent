#!/usr/bin/env python3
"""RAM-only two-account coordinator for the unchanged 18-check hosted checker.

Default: print a plan, without reading configuration or making network requests.
AFTER main coordinates confirmation clicks, run in a dedicated terminal process:
  .venv/bin/python scripts/mobile_hosted_session.py --execute --confirm-two-emails --run-label <planned-label> --base-email <approved-email> --confirmation-redirect-health
Use --confirmation-redirect-health only after main verifies the fixed health URL
is permitted by the existing Supabase redirect allowlist. It requests a JSON-only
landing page instead of the default SiteURL; this harness never visits either
callback or consumes browser credentials. No arbitrary redirect URL is accepted.
Keep that process alive; type CONFIRMED only after clicking BOTH normal signup
emails. Do not paste links, passwords or tokens. ABORT stops. No automatic retry,
resend, password reset, refresh, browser access, admin API or configuration writes.

Only public URL/key fields from ignored/untracked .env.mobile are selected.
Passwords and session tokens never enter argv, environment, files or logs.
The mobile API is real, in-process TestClient; Supabase is real hosted HTTP during
execution (mocked only in unit tests). No port binds, Cloudflare or published API.
A process-local QA email allowlist keeps the real verified-email gate enabled;
it cannot change the separately running public API's owner-only allowlist.
Each confirmed QA identity must also have independently administrator-provisioned
database membership. This harness only checks that membership; signup metadata
does not grant it. It never provisions membership/quota or reserves paid AI use.

The existing checker cleans run-specific fixtures and restores ONLY the two new
QA accounts' editable fields. Auth accounts remain for main's separately scoped
cleanup; local session signout is attempted. Process loss forfeits the passwords.
Python drops references at shutdown; it cannot guarantee physical RAM erasure.

Official protocol references read before implementation:
https://supabase.com/docs/guides/auth/passwords
https://supabase.com/docs/reference/python/auth-signup
https://supabase.com/docs/reference/javascript/auth-signinwithpassword
https://supabase.com/docs/guides/auth/auth-email-templates
https://github.com/supabase/auth-js/blob/master/src/GoTrueClient.ts
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import secrets
import select
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import mobile_hosted_check as hosted
from jobagent.mobile.repository import COLUMNS, SupabaseSettings

LOCAL_API = "http://127.0.0.1:8845"  # Transport routing label only; never bound.
BASE_EMAIL = "qa@example.test"  # Plan/tests only; execution requires an explicit address.
CONFIRMATION_REDIRECT = "https://www.thejobpursuit.com/api/mobile/health"
# auth-js maps emailRedirectTo to this URLSearchParams-style query, not JSON.
CONFIRMATION_REDIRECT_QUERY = urlencode({"redirect_to": CONFIRMATION_REDIRECT})
CONFIRMATION_SECONDS = 1800
MAX_REQUESTS = 1200
MAX_RESPONSE = 2 * 1024 * 1024
PUBLIC_NAMES = ("MOBILE_SUPABASE_URL", "BETA_SUPABASE_URL", "SUPABASE_URL",
                "MOBILE_SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY")


class SessionFailure(Exception):
    """Only fixed internal codes; never raw auth/provider messages."""


def require(condition, code):
    if not condition:
        raise SessionFailure(code)


def planned_emails(label, base_email=BASE_EMAIL):
    require(isinstance(label, str) and re.fullmatch(r"[a-z0-9]{8,20}", label), "invalid-run-label")
    require(isinstance(base_email, str) and len(base_email) <= 160 and
            re.fullmatch(r"[a-z0-9._-]+@[a-z0-9.-]+\.[a-z]{2,}", base_email), "invalid-base-email")
    local, domain = base_email.split("@")
    return tuple(f"{local}+mobileqa-{label}-{actor}@{domain}" for actor in ("a", "b"))


def plan(label, *, confirmation_redirect_health=False, base_email=BASE_EMAIL):
    return {
        "status": "plan-only", "run_label": label, "signup_emails": list(planned_emails(label, base_email)),
        "signup_attempt_limit": 2, "password_signin_attempt_limit": 2,
        "confirmation_wait_seconds": CONFIRMATION_SECONDS,
        "confirmation_redirect": CONFIRMATION_REDIRECT if confirmation_redirect_health else None,
        "redirect_allowlist_must_be_verified_by_main": confirmation_redirect_health,
        "actions": [
            "Read only public Supabase URL/key fields from ignored .env.mobile.",
            "Generate two independent passwords in RAM; send one normal signup per plus-address with mobile_hosted_check=true.",
            "Wait in this process for both email clicks and the operator's CONFIRMED command; do not consume callback sessions.",
            "Sign in once per account with password, then verify identity, confirmed email and QA metadata through /auth/v1/user.",
            "Run unchanged HostedCheck against local in-process FastAPI and hosted Supabase Auth/REST/Storage.",
            "Require all 18 checks and cleanup; attempt local signout of these QA sessions; retain Auth accounts for main's cleanup.",
        ],
        "api": "real in-process TestClient; no listener or hosted API/Cloudflare coverage",
        "supabase": "real hosted only after explicit execution; mocks only in tests",
        "settings_changes": "none persisted; QA allowlist only in this dedicated process, restored afterward",
        "paid_model_calls": 0,
    }


def load_public_settings(*, root=ROOT):
    """No dotenv environment mutation/interpolation, no private credential fields."""
    from dotenv import dotenv_values

    target = root / ".env.mobile"
    try:
        require(not any(p.is_symlink() for p in (target, *target.parents)), "public-config-symlink-refused")
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "--quiet", "--", ".env.mobile"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", ".env.mobile"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        require(ignored and not tracked, "public-config-must-be-ignored-untracked")
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        selected, seen = [], set()
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_size <= 65536, "invalid-public-config-file")
            for line in handle:
                match = re.match(r"\s*(?:export\s+)?([A-Z_]+)\s*=", line)
                if match and match[1] in PUBLIC_NAMES:
                    require(match[1] not in seen, "duplicate-public-config-field")
                    seen.add(match[1])
                    selected.append(line)
        values = dotenv_values(stream=io.StringIO("".join(selected)), interpolate=False)
        first = lambda names: next((values[n].strip() for n in names if values.get(n)), "")
        settings = SupabaseSettings(first(PUBLIC_NAMES[:3]), first(PUBLIC_NAMES[3:]))
        settings.validate()
        require(settings.url.startswith("https://"), "hosted-supabase-requires-https")
        return settings
    except SessionFailure:
        raise
    except Exception:
        raise SessionFailure("public-config-unavailable-or-invalid") from None


@dataclass(repr=False)
class Account:
    email: str
    password: str = field(default_factory=lambda: secrets.token_urlsafe(32) + "aA9!")
    user_id: str = ""
    token: str = ""


class Wire:
    """One pinned Supabase origin, no redirects/proxies/retries, bounded bodies."""

    def __init__(self, settings, *, transport=None):
        settings.validate()
        require(settings.url.startswith("https://"), "hosted-supabase-requires-https")
        self.settings = settings
        self.origin = str(httpx.URL(settings.url).copy_with(path="/"))
        self.client = httpx.Client(transport=transport or httpx.HTTPTransport(retries=0),
                                  timeout=httpx.Timeout(25, connect=10), trust_env=False, follow_redirects=False)
        self.count = 0
        self.lock = threading.Lock()
        self.user_ids = set()
        self.tokens = set()

    def send(self, request, *, auth=False):
        url = request.url
        require(str(url.copy_with(path="/", query=None, fragment=None)) == self.origin
                and not url.username and not url.password and not url.fragment, "outbound-origin-refused")
        path = url.path
        require(".." not in path and "%" not in path, "outbound-path-refused")
        require(request.headers.get("apikey") == self.settings.publishable_key, "public-key-header-required")
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        if auth:
            allowed = (request.method, path, str(url.query, "ascii")) in {
                ("POST", "/auth/v1/signup", ""), ("POST", "/auth/v1/token", "grant_type=password"),
                ("POST", "/auth/v1/signup", CONFIRMATION_REDIRECT_QUERY),
                ("GET", "/auth/v1/user", ""), ("POST", "/auth/v1/logout", "scope=local"),
            }
        else:
            allowed = request.method == "GET" and path == "/auth/v1/user"
            if path.startswith("/rest/v1/"):
                allowed = (path.removeprefix("/rest/v1/") in COLUMNS and
                           url.params.get("user_id") in {"eq." + uid for uid in self.user_ids})
                if path == "/rest/v1/rpc/mobile_check_access":
                    # Read-only, auth.uid()-bound membership check required by
                    # the real API. No arbitrary RPC, quota writes/reservation,
                    # caller identity/metadata parameters, or anonymous session.
                    allowed = (request.method == "POST" and not url.query
                               and request.content == b"{}" and bool(bearer)
                               and bearer in self.tokens)
                if path == "/rest/v1/rpc/mobile_save_profile":
                    # Narrow replacement for the old two-table profile writes.
                    # Still limited to remotely verified synthetic QA sessions.
                    from jobagent.mobile.schemas import ProfileUpdate
                    try:
                        body = json.loads(request.content)
                        ProfileUpdate.model_validate(body.get("p_patch"))
                        allowed = (request.method == "POST" and not url.query and set(body) == {"p_patch"}
                                   and bool(bearer) and bearer in self.tokens)
                    except (ValueError, TypeError, AttributeError):
                        allowed = False
            if path.startswith("/storage/v1/object/"):
                parts = path.removeprefix("/storage/v1/object/").split("/")
                if parts[0] == "list":
                    body = json.loads(request.content)
                    allowed = request.method == "POST" and len(parts) == 2 and parts[1] in {"resumes", "application-artifacts"} and body.get("prefix") in {uid + "/" for uid in self.user_ids}
                else:
                    if parts[0] == "public":
                        parts = parts[1:]
                    allowed = len(parts) == 3 and parts[0] in {"resumes", "application-artifacts"} and parts[1] in self.user_ids
            require(not bearer or bearer in self.tokens, "unknown-session-refused")
        require(allowed, "outbound-route-refused")
        with self.lock:
            require(self.count < MAX_REQUESTS, "request-budget-exhausted")
            self.count += 1
        try:
            with self.client.stream(request.method, url, headers=dict(request.headers), content=request.content) as response:
                require(not 300 <= response.status_code < 400, "redirect-refused")
                content = bytearray()
                for chunk in response.iter_bytes():
                    require(len(content) + len(chunk) <= MAX_RESPONSE, "response-too-large")
                    content.extend(chunk)
                headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}}
                return httpx.Response(response.status_code, headers=headers, content=bytes(content))
        except httpx.HTTPError:
            raise SessionFailure("network-failed-no-retry") from None

    def close(self):
        self.client.close()
        self.tokens.clear()


class Coordinator:
    def __init__(self, wire, label, *, confirmation_redirect_health=False, base_email=BASE_EMAIL):
        require(type(confirmation_redirect_health) is bool, "fixed-redirect-toggle-required")
        self.wire = wire
        self.confirmation_redirect_health = confirmation_redirect_health
        self.accounts = [Account(email) for email in planned_emails(label, base_email)]
        self.label = label
        self.signup_attempts = 0
        self.signin_attempts = 0
        self.state = "new"

    def auth(self, method, path, *, body=None, token=""):
        headers = {"apikey": self.wire.settings.publishable_key}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = httpx.Request(method, self.wire.settings.url.rstrip("/") + path, headers=headers, json=body)
        return self.wire.send(request, auth=True)

    @staticmethod
    def data(response):
        require(response.status_code in (200, 201), "auth-rejected-no-retry")
        try:
            body = response.json()
            require(isinstance(body, dict), "auth-response-invalid")
            return body
        except ValueError:
            raise SessionFailure("auth-response-invalid") from None

    def validate_user(self, user, account, *, confirmed):
        require(isinstance(user, dict) and user.get("email", "").casefold() == account.email, "auth-email-mismatch")
        try:
            uid = str(UUID(user["id"]))
        except (ValueError, KeyError, TypeError):
            raise SessionFailure("auth-user-id-invalid") from None
        require(not account.user_id or account.user_id == uid, "auth-user-id-mismatch")
        metadata = user.get("user_metadata", {})
        require(isinstance(metadata, dict) and metadata.get("mobile_hosted_check") is True
                and metadata.get("mobile_hosted_run") == self.label, "synthetic-marker-mismatch")
        require(bool(user.get("email_confirmed_at")) is confirmed, "email-confirmation-state-mismatch")
        if confirmed:
            require(user.get("role") == "authenticated", "authenticated-user-required")
        return uid

    def signup(self):
        require(self.state == "new", "signup-cannot-repeat")
        self.state = "signup-started"
        path = "/auth/v1/signup"
        if self.confirmation_redirect_health:
            path += "?" + CONFIRMATION_REDIRECT_QUERY
        for account in self.accounts:
            self.signup_attempts += 1
            body = self.data(self.auth("POST", path, body={
                "email": account.email, "password": account.password,
                "data": {"mobile_hosted_check": True, "mobile_hosted_run": self.label},
            }))
            # Plain GoTrue REST returns a user, or a session with nested user if
            # autoconfirm is enabled. Never accept or use that auto-session.
            require(not any(body.get(k) for k in ("access_token", "refresh_token", "session")), "signup-autoconfirm-refused")
            user = body.get("user", body)
            account.user_id = self.validate_user(user, account, confirmed=False)
            require(user.get("identities") != [], "existing-obfuscated-account-refused")
        require(self.accounts[0].user_id != self.accounts[1].user_id, "distinct-new-users-required")
        self.state = "awaiting-clicks"

    def signin(self, *, operator_confirmed=False):
        require(self.state == "awaiting-clicks" and operator_confirmed is True, "confirmation-clicks-required")
        self.state = "signin-started"
        for account in self.accounts:
            self.signin_attempts += 1
            body = self.data(self.auth("POST", "/auth/v1/token?grant_type=password", body={"email": account.email, "password": account.password}))
            token = body.get("access_token")
            require(isinstance(token, str) and 1 <= len(token) <= 8192, "session-missing")
            account.token = token  # Kept for local signout even if verification fails.
            account.password = ""
            self.validate_user(body.get("user"), account, confirmed=True)
            remote = self.data(self.auth("GET", "/auth/v1/user", token=token))
            self.validate_user(remote, account, confirmed=True)
        values = {"supabase_url": self.wire.settings.url.rstrip("/"), "api_url": LOCAL_API,
                  "publishable_key": self.wire.settings.publishable_key}
        for actor, account in zip(("a", "b"), self.accounts):
            values.update({f"user_{actor}_id": account.user_id, f"user_{actor}_token": account.token})
        config = hosted.Credentials.parse(values)
        self.wire.user_ids = {a.user_id for a in self.accounts}
        self.wire.tokens = {a.token for a in self.accounts}
        self.state = "verified"
        return config

    def close(self):
        failures = []
        for index, account in enumerate(self.accounts):
            if account.token:
                try:
                    response = self.auth("POST", "/auth/v1/logout?scope=local", token=account.token)
                    require(response.status_code in (200, 204), "qa-signout-failed")
                except Exception:
                    failures.append("QA " + str(index + 1) + " local signout unconfirmed")
            account.password = ""
            account.token = ""
        try:
            self.wire.close()
        except Exception:
            failures.append("QA transport close unconfirmed")
        self.state = "closed"
        return failures


def run_hosted_check(coordinator, config):
    """Reuse the checker unchanged; API routing is local, Supabase isn't mocked."""
    from fastapi.testclient import TestClient
    from starlette.concurrency import run_in_threadpool
    from jobagent.mobile.app import create_app

    require(coordinator.state == "verified", "two-verified-users-required")
    wire = coordinator.wire

    class AppSupabaseTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await request.aread()
            forwarded = httpx.Request(request.method, request.url, headers=dict(request.headers), content=request.content)
            return await run_in_threadpool(wire.send, forwarded)

    class SplitTransport(httpx.BaseTransport):
        def handle_request(self, request):
            if str(request.url.copy_with(path="/", query=None, fragment=None)) == LOCAL_API + "/":
                require("apikey" not in request.headers, "public-key-not-for-local-api")
                result = api.request(request.method, request.url.raw_path.decode("ascii"), headers=dict(request.headers), content=request.content)
                return httpx.Response(result.status_code, headers=result.headers, content=result.content)
            return wire.send(request)

    emails = ",".join(account.email for account in coordinator.accounts)
    # Dedicated process memory only, never .env.mobile, launchd, the deployed
    # server, remote settings or the public API's allowlist. Real gate still runs.
    with patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": emails}):
        app = create_app(settings=wire.settings, transport=AppSupabaseTransport())
        with TestClient(app, raise_server_exceptions=False) as api:
            report = hosted.HostedCheck(config, transport=SplitTransport()).run()
    return report


@contextmanager
def quiet_dependencies():
    previous = logging.root.manager.disable
    logging.disable(sys.maxsize)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield
    finally:
        logging.disable(previous)


def wait_for_clicks(stream, timeout=CONFIRMATION_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([stream], [], [], min(30, max(0, deadline - time.monotonic())))
        if ready:
            require(stream.readline(64).strip() == "CONFIRMED", "operator-aborted-or-invalid-command")
            return
    raise SessionFailure("confirmation-wait-expired")


def main(argv=None):
    parser = hosted.QuietParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-two-emails", action="store_true")
    parser.add_argument("--run-label")
    parser.add_argument("--base-email", help="Explicitly approved mailbox for the two plus-addresses; not a password or key.")
    parser.add_argument("--confirmation-redirect-health", action="store_true",
                        help="Use only the fixed health redirect, after main verifies it is allowlisted; never accepts a URL.")
    args = parser.parse_args(argv)
    if args.execute and (not args.confirm_two_emails or not args.run_label or not args.base_email):
        print("REFUSED: execution needs --confirm-two-emails, --base-email and the planned --run-label. No network.")
        return 2
    if args.confirm_two_emails and not args.execute:
        print("REFUSED: no --execute; no signup or email sent.")
        return 2
    try:
        label = args.run_label or secrets.token_hex(6)
        proposed = plan(label, confirmation_redirect_health=args.confirmation_redirect_health,
                        base_email=args.base_email or BASE_EMAIL)
    except SessionFailure:
        print("REFUSED: invalid run label. No network.")
        return 2
    print(json.dumps(proposed, indent=2), flush=True)
    if not args.execute:
        return 0
    if not sys.stdin.isatty():
        print("REFUSED: use a dedicated interactive terminal to keep RAM credentials alive for confirmation.")
        return 2
    coordinator = None
    result = {"status": "failed", "passed_checks": 0, "expected_checks": 18,
              "paid_model_calls": 0, "api": proposed["api"], "supabase": "real hosted HTTP"}
    try:
        with quiet_dependencies():
            coordinator = Coordinator(Wire(load_public_settings()), label,
                                      confirmation_redirect_health=args.confirmation_redirect_health,
                                      base_email=args.base_email)
            coordinator.signup()
        print("AWAITING_CONFIRMATION: click BOTH signup emails, then type CONFIRMED here. ABORT stops. Do not paste links or tokens.", flush=True)
        wait_for_clicks(sys.stdin)
        with quiet_dependencies():
            config = coordinator.signin(operator_confirmed=True)
            report = run_hosted_check(coordinator, config)
        result.update(status="passed" if report.ok and len(report.passed) == 18 else "failed",
                      run_label=report.run_label, passed_checks=len(report.passed),
                      checks=report.passed, failures=report.failures, cleanup_failures=report.cleanup_failures)
    except (SessionFailure, hosted.CheckFailure) as exc:
        # Our and the existing checker's exceptions contain fixed safe codes only.
        result["failure_code"] = str(exc)
    except (Exception, KeyboardInterrupt):
        result["failure_code"] = "interrupted-or-unexpected-failure-details-withheld"
    finally:
        if coordinator is not None:
            result["signup_attempts"] = coordinator.signup_attempts
            result["signin_attempts"] = coordinator.signin_attempts
            result["qa_accounts"] = [{"email": a.email, "user_id": a.user_id or None} for a in coordinator.accounts]
            with quiet_dependencies():
                result["session_cleanup_failures"] = coordinator.close()
            result["supabase_http_requests"] = coordinator.wire.count
            if result["session_cleanup_failures"]:
                result["status"] = "failed"
            result["auth_accounts_deleted"] = False
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
