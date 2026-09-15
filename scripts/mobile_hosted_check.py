#!/usr/bin/env python3
"""Opt-in two-synthetic-user mobile API / direct Supabase RLS smoke check.

DEFAULT IS OFFLINE. Both --execute and --confirm-synthetic-users are required.
Never pass tokens as command arguments. Never run this against real users.

Credentials via MOBILE_HOSTED_ environment variables:
  SUPABASE_URL, API_URL, PUBLISHABLE_KEY, USER_A_ID, USER_A_TOKEN,
  USER_B_ID, USER_B_TOKEN.
Or --credentials-file supplies those seven lowercase keys without the prefix:
  supabase_url, api_url, publishable_key, user_a_id, user_a_token,
  user_b_id, user_b_token.
JSON must be untracked, Git-ignored, inside this repo, non-symlink, owned by the
current OS user and mode 0600 or stricter. An operator may securely create
output/mobile-hosted-credentials.json using the existing output/ ignore rule.
This script never creates credential files or prints their contents.

Required protocol:
* Reuse the existing resumed project, migrations 0001/0002/0003 and private buckets.
* Provision TWO dedicated, idle synthetic Auth accounts separately. Each needs
  confirmed email, user_metadata.mobile_hosted_check=true, and a profiles row.
* Supply each account's expected UUID and authenticated bearer, valid for at
  least five more minutes. No password, refresh token or service-role key.
* Supabase must be HTTPS; API may be HTTPS or explicit loopback HTTP, e.g.
  http://127.0.0.1:8843. Root URLs only. Never an external plaintext URL.
* Review the offline plan first. Then explicitly authorize execution.

No model calls, email sends, sign-up/sign-in/refresh, Auth writes, migrations,
founder imports, SQLite, shared sessions or secret outputs. Synthetic DOCX and
artifact bytes are generated in memory. API protections and direct REST/Storage
RLS are tested in BOTH directions. Missing schema/expired auth are failures, not
successful access denials. Cleanup restores editable singleton fields and
deletes only run-specific fixtures. Resume recovery tombstones are intentionally
retained; cleanup must not delete or rewrite their immutable intent. Audit
timestamps can advance. Hard process
termination, concurrent edits or outages can prevent cleanup: retain the two
synthetic accounts and inspect the printed non-secret run label if incomplete.
"""

from __future__ import annotations

import argparse
import base64
import copy
import io
import json
import os
import re
import stat
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("supabase_url", "api_url", "publishable_key", "user_a_id", "user_a_token", "user_b_id", "user_b_token")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SINGLETONS = {
    "profiles": ("display_name", "phone", "base_location", "timezone", "onboarding_completed_at"),
    "job_preferences": ("target_titles", "preferred_locations", "preferred_regions", "remote_preference", "sponsorship_required", "work_authorization_notes", "minimum_match_score"),
    "candidate_context": ("career_text", "career_background"),
}
EMPTY_BACKGROUND = {"profession": "", "experience_level": "unspecified", "qualifications": []}
TABLES = {*SINGLETONS, "jobs", "resumes", "artifacts", "mobile_questions", "mobile_answers"}
UUID_PATTERN = r"[0-9a-fA-F-]{36}"


class CheckFailure(Exception):
    """Only internal non-sensitive error codes are allowed in this exception."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CheckFailure(code)


def jwt_claims(value: str) -> dict:
    try:
        require(isinstance(value, str) and len(value) <= 8192 and re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value) is not None, "invalid-token-format")
        part = value.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        require(isinstance(claims, dict), "invalid-token-claims")
        return claims
    except (ValueError, UnicodeError):
        raise CheckFailure("invalid-token-format") from None


@dataclass(frozen=True, repr=False)
class Credentials:
    supabase_url: str
    api_url: str
    publishable_key: str
    user_a_id: str
    user_a_token: str
    user_b_id: str
    user_b_token: str

    @classmethod
    def parse(cls, values: Mapping[str, Any]) -> "Credentials":
        require(set(values) == set(KEYS), "credentials-require-exact-seven-fields")
        require(all(isinstance(values[key], str) and values[key] and values[key].strip() == values[key] for key in KEYS), "invalid-credential-field")
        config = cls(**values)
        for name in ("supabase_url", "api_url"):
            url = getattr(config, name)
            try:
                parts = urlsplit(url)
                secure = parts.scheme == "https"
                local = name == "api_url" and parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")
                valid = (secure or local) and bool(parts.hostname) and parts.username is None and parts.password is None and not parts.query and not parts.fragment and parts.path in ("", "/")
                _ = parts.port
            except ValueError:
                valid = False
            require(valid and re.search(r"[\s\\\x00-\x1f]", url) is None, "https-or-loopback-api-root-required")
        if not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{1,8000}", config.publishable_key):
            require(jwt_claims(config.publishable_key).get("role") == "anon", "public-key-required-never-service-role")
        require(config.user_a_id != config.user_b_id and config.user_a_token != config.user_b_token, "two-distinct-synthetic-users-required")
        for actor in ("A", "B"):
            user_id, token = config.user(actor)
            try:
                require(str(UUID(user_id)) == user_id, "canonical-user-uuid-required")
            except ValueError:
                raise CheckFailure("canonical-user-uuid-required") from None
            claims = jwt_claims(token)
            require(claims.get("role") == "authenticated" and claims.get("sub") == user_id, "authenticated-token-must-match-expected-user")
            require(claims.get("iss") == config.supabase_url.rstrip("/") + "/auth/v1", "token-project-mismatch")
            require(type(claims.get("exp")) in (int, float) and claims["exp"] > time.time() + 300, "fresh-user-tokens-required")
        return config

    def user(self, actor: str) -> tuple:
        require(actor in ("A", "B"), "unknown-test-actor")
        return (self.user_a_id, self.user_a_token) if actor == "A" else (self.user_b_id, self.user_b_token)


def load_credentials(filename: Optional[str], environ: Optional[Mapping[str, str]] = None, *, root: Path = ROOT) -> Credentials:
    if filename is None:
        env = os.environ if environ is None else environ
        return Credentials.parse({key: env.get("MOBILE_HOSTED_" + key.upper(), "") for key in KEYS})
    try:
        target = Path(filename).absolute()
        require(not any(item.is_symlink() for item in (target, *target.parents)), "credential-file-must-not-be-symlink")
        relative = target.resolve().relative_to(root.resolve())
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "--quiet", "--", relative.as_posix()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative.as_posix()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        require(ignored and not tracked, "credential-file-must-be-git-ignored-and-untracked")
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            require(stat.S_ISREG(info.st_mode) and not stat.S_IMODE(info.st_mode) & 0o077 and info.st_uid == os.geteuid(), "credential-file-requires-private-owner-permissions")
            content = handle.read(32769)
        require(len(content) <= 32768, "credential-file-too-large")
        values = json.loads(content)
        require(isinstance(values, dict), "credential-file-requires-json-object")
        return Credentials.parse(values)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise CheckFailure("credential-file-could-not-be-safely-loaded") from None


def synthetic_docx(marker: str) -> bytes:
    """Tiny text-only document; no local resume/template/profile is read."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic test candidate. No real career claims. ' + marker + '</w:t></w:r></w:p></w:body></w:document>')
    return stream.getvalue()


@dataclass
class Report:
    run_label: str
    passed: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    cleanup_failures: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.cleanup_failures


class HostedCheck:
    def __init__(self, config: Credentials, *, transport: Optional[httpx.BaseTransport] = None):
        self.config = config
        self.client = httpx.Client(transport=transport, timeout=httpx.Timeout(25, connect=10), follow_redirects=False, trust_env=False)
        self.report = Report("mobile-hosted-check-" + uuid4().hex)
        self.snapshots: dict = {}
        self.changes: dict = {}
        self.attempted_values: dict = {}
        self.records: list = []
        self.objects: list = []
        self.managed_resume_paths: set = set()
        self.fixtures: dict = {}
        self.deadline = time.monotonic() + 600

    def request(self, surface: str, actor: Optional[str], method: str, path: str, **options: Any) -> httpx.Response:
        require(time.monotonic() < self.deadline, "check-time-budget-exceeded")
        # Central route/method allowlist excludes every model and auth-write route.
        require(method in ("GET", "POST", "PUT", "PATCH", "DELETE") and "?" not in path and ".." not in path and "%" not in path, "request-outside-test-contract")
        if surface == "supabase":
            base, headers = self.config.supabase_url, {"apikey": self.config.publishable_key}
            allowed = path == "/auth/v1/user" and method == "GET"
            allowed |= path.startswith("/rest/v1/") and path.removeprefix("/rest/v1/") in TABLES
            allowed |= re.fullmatch(r"/storage/v1/object/(?:public/)?(?:resumes|application-artifacts)/" + UUID_PATTERN + r"/[A-Za-z0-9_.-]+", path) is not None
            allowed |= method == "POST" and path in ("/storage/v1/object/list/resumes", "/storage/v1/object/list/application-artifacts")
        elif surface == "api":
            base, headers = self.config.api_url, {}
            allowed = method == "GET" and path in ("/api/mobile/health", "/api/mobile/bootstrap", "/api/profile", "/api/jobs", "/api/runs")
            allowed |= method == "PUT" and path in ("/api/mobile/profile", "/api/mobile/preferences")
            allowed |= method == "POST" and path in ("/api/mobile/jobs", "/api/mobile/resumes")
            allowed |= method == "PATCH" and re.fullmatch(r"/api/mobile/jobs/" + UUID_PATTERN, path) is not None
            allowed |= method == "GET" and re.fullmatch(r"/api/mobile/(?:resumes|artifacts)/" + UUID_PATTERN + r"/(?:download|text)", path) is not None
            allowed |= method == "DELETE" and re.fullmatch(r"/api/mobile/resumes/" + UUID_PATTERN, path) is not None
            allowed |= method == "POST" and re.fullmatch(r"/api/mobile/questions/" + UUID_PATTERN + r"/answer", path) is not None
        else:
            raise CheckFailure("unknown-test-surface")
        require(bool(allowed), "request-outside-test-contract")
        if actor is not None:
            headers["Authorization"] = "Bearer " + self.config.user(actor)[1]
        additional = options.pop("headers", {})
        require(not any(key.lower() in ("authorization", "apikey", "host") for key in additional), "authentication-header-override-forbidden")
        headers.update(additional)
        if surface == "supabase" and method == "GET" and path.startswith("/storage/v1/object/"):
            # Storage's CDN can briefly serve cached bytes after a successful
            # object deletion. Isolation/deletion checks need a fresh origin
            # decision, not a cached 200 (or cached denial). No CDN settings or
            # global invalidation are changed; only this request gets a nonce.
            options["params"] = {**options.get("params", {}), "mobile_check_nonce": uuid4().hex}
            headers.update({"Cache-Control": "no-cache", "Pragma": "no-cache"})
        try:
            with self.client.stream(method, base.rstrip("/") + path, headers=headers, **options) as response:
                require(not 300 <= response.status_code < 400, "redirect-refused")
                content = bytearray()
                for chunk in response.iter_bytes():
                    require(len(content) + len(chunk) <= 2 * 1024 * 1024, "response-too-large")
                    content.extend(chunk)
                return httpx.Response(response.status_code, content=bytes(content))
        except httpx.HTTPError:
            raise CheckFailure("network-or-tls-failure") from None

    @staticmethod
    def data(response: httpx.Response, statuses=(200, 201)) -> Any:
        require(response.status_code in statuses, "unexpected-http-status")
        try:
            return response.json()
        except ValueError:
            raise CheckFailure("invalid-json-response") from None

    def rest(self, actor: str, method: str, table: str, *, target: Optional[str] = None, filters: Optional[dict] = None, data: Any = None, select: str = "*") -> httpx.Response:
        params = {"select": select, "user_id": "eq." + self.config.user(target or actor)[0], **(filters or {})}
        if method == "GET":
            params["limit"] = "25"
        options = {"params": params, "headers": {"Prefer": "return=representation"}}
        if data is not None:
            options["json"] = data
        return self.request("supabase", actor, method, "/rest/v1/" + table, **options)

    def rows(self, response: httpx.Response, *, owner: Optional[str] = None) -> list:
        rows = self.data(response)
        require(isinstance(rows, list) and len(rows) <= 25 and all(isinstance(row, dict) for row in rows), "unexpected-row-response")
        if owner is not None:
            require(all(row.get("user_id") == self.config.user(owner)[0] for row in rows), "foreign-row-visible")
        return rows

    def row(self, response: httpx.Response, actor: str) -> dict:
        rows = self.rows(response, owner=actor)
        require(len(rows) == 1, "expected-one-owned-row")
        return rows[0]

    def api(self, actor: Optional[str], method: str, path: str, **options: Any) -> httpx.Response:
        return self.request("api", actor, method, "/api/mobile/" + path, **options)

    def track(self, actor: str, table: str, filters: dict) -> None:
        require(table not in SINGLETONS and bool(filters), "unsafe-cleanup-target")
        self.records.append((actor, table, dict(filters)))

    def track_singleton_change(self, actor: str, table: str, values: dict) -> None:
        """Remember every attempted value so ambiguous writes can be restored.

        A timeout may leave either the old or new value on the server. Never
        accept arbitrary concurrent values, or restore fields we did not edit.
        """
        require(table in SINGLETONS and set(values) <= set(SINGLETONS[table]), "unsafe-singleton-change")
        changed = self.changes.setdefault((actor, table), {})
        for key, value in values.items():
            history = self.attempted_values.setdefault((actor, table, key), [])
            if key in changed:
                history.append(copy.deepcopy(changed[key]))
            history.append(copy.deepcopy(value))
            changed[key] = copy.deepcopy(value)

    def storage_path(self, actor: str, bucket: str, name: str) -> str:
        require(bucket in ("resumes", "application-artifacts") and isinstance(name, str) and re.fullmatch(re.escape(self.config.user(actor)[0]) + r"/[A-Za-z0-9_-]+\.(?:docx|txt)", name) is not None, "unsafe-storage-target")
        path = "/storage/v1/object/" + bucket + "/" + name
        if (actor, path) not in self.objects:
            self.objects.append((actor, path))
        return path

    @staticmethod
    def storage_denied(response: httpx.Response) -> bool:
        if response.status_code in (403, 404):
            return True
        if response.status_code == 400:
            try:
                body = response.json()
                return isinstance(body, dict) and (str(body.get("statusCode")) in ("403", "404") or body.get("code") in ("AccessDenied", "Unauthorized", "NoSuchKey"))
            except ValueError:
                return False
        return False

    def preflight(self) -> None:
        # Verify BOTH expected/marked users before writing to either account.
        for actor in ("A", "B"):
            user = self.data(self.request("supabase", actor, "GET", "/auth/v1/user"), (200,))
            require(isinstance(user, dict) and user.get("id") == self.config.user(actor)[0] and user.get("role") == "authenticated", "remote-user-identity-mismatch")
            metadata = user.get("user_metadata")
            require(isinstance(metadata, dict) and metadata.get("mobile_hosted_check") is True and bool(user.get("email_confirmed_at")), "dedicated-confirmed-synthetic-account-required")
        self.report.passed.append("both synthetic identities remotely verified")
        require(self.data(self.api(None, "GET", "health")) == {"status": "ok", "service": "job-pursuit-mobile"}, "isolated-mobile-health-mismatch")
        require(self.api(None, "GET", "bootstrap").status_code == 401, "unauthenticated-bootstrap-not-rejected")
        for path in ("/api/profile", "/api/jobs", "/api/runs"):
            require(self.request("api", None, "GET", path).status_code == 404, "founder-api-route-exposed")
        for actor in ("A", "B"):
            for table, fields in SINGLETONS.items():
                rows = self.rows(self.rest(actor, "GET", table, select="user_id," + ",".join(fields)), owner=actor)
                require(len(rows) <= 1 and (table != "profiles" or len(rows) == 1), "profile-trigger-or-singleton-schema-missing")
                self.snapshots[actor, table] = rows[0] if rows else None
            self.bootstrap(actor)
        self.report.passed.append("health, unauthenticated denial, founder isolation and baseline bootstrap")

    def bootstrap(self, actor: str) -> dict:
        body = self.data(self.api(actor, "GET", "bootstrap"))
        require(isinstance(body, dict) and set(body) == {"profile", "preferences", "jobs", "resumes", "artifacts", "applications", "questions", "capabilities"}, "bootstrap-contract-mismatch")
        user_id = self.config.user(actor)[0]
        require(isinstance(body["profile"], dict) and body["profile"].get("user_id") == user_id, "bootstrap-profile-owner-mismatch")
        for table in ("jobs", "resumes", "artifacts", "applications", "questions"):
            require(isinstance(body[table], list) and all(isinstance(row, dict) and row.get("user_id") == user_id for row in body[table]), "bootstrap-foreign-row-visible")
        return body

    def own_workflow(self, actor: str) -> None:
        user_id = self.config.user(actor)[0]
        marker = self.report.run_label + "-" + actor
        background = {
            "profession": "Teaching" if actor == "A" else "Operations",
            "experience_level": "career_change",
            "qualifications": [{"name": "Synthetic training only", "kind": "education", "status": "in_progress", "jurisdiction": "Synthetic location", "expires_on": None, "evidence_note": marker + ": self-reported fixture, not a verified credential."}],
        }
        profile = {"display_name": "Synthetic Hosted Check " + actor, "career_text": marker + ": synthetic career facts only.", "career_background": background}
        self.changes[actor, "profiles"] = {"display_name": profile["display_name"]}
        self.track_singleton_change(actor, "candidate_context", {"career_text": profile["career_text"], "career_background": background})
        saved = self.data(self.api(actor, "PUT", "profile", json=profile))
        require(saved.get("career_text") == profile["career_text"] and saved.get("display_name") == profile["display_name"], "profile-round-trip-failed")
        require(saved.get("career_background") == background, "career-background-round-trip-failed")
        omitted = self.data(self.api(actor, "PUT", "profile", json={"display_name": profile["display_name"]}))
        require(omitted.get("career_background") == background and omitted.get("career_text") == profile["career_text"], "omitted-background-not-preserved")
        self.track_singleton_change(actor, "candidate_context", {"career_background": EMPTY_BACKGROUND})
        cleared = self.data(self.api(actor, "PUT", "profile", json={"career_background": None}))
        require(cleared.get("career_background") == EMPTY_BACKGROUND and cleared.get("career_text") == profile["career_text"] and cleared.get("display_name") == profile["display_name"], "background-clear-changed-other-fields")
        require(self.row(self.rest(actor, "GET", "candidate_context"), actor).get("career_background") == EMPTY_BACKGROUND, "background-clear-not-persisted")
        self.track_singleton_change(actor, "candidate_context", {"career_background": background})
        restored = self.data(self.api(actor, "PUT", "profile", json={"career_background": background}))
        require(restored.get("career_background") == background, "background-replacement-failed")
        self.report.passed.append(actor + " career background persistence, omission preservation and explicit clear")
        preferences = {"target_titles": ["Synthetic Career Role"], "preferred_locations": ["Synthetic location"], "preferred_regions": [], "remote_preference": "open", "sponsorship_required": False, "work_authorization_notes": marker, "minimum_match_score": 7.0}
        self.changes[actor, "job_preferences"] = preferences
        saved = self.data(self.api(actor, "PUT", "preferences", json=preferences))
        require(all(saved.get(key) == value for key, value in preferences.items()), "preferences-round-trip-failed")
        for table in SINGLETONS:
            row = self.row(self.rest(actor, "GET", table), actor)
            require(all(row.get(key) == value for key, value in self.changes[actor, table].items()), "direct-singleton-round-trip-failed")
        self.report.passed.append(actor + " profile/context/preferences via API and direct REST")

        source_url = "https://example.invalid/" + marker
        self.track(actor, "jobs", {"source_url": "eq." + source_url})
        payload = {"source_url": source_url, "title": "Synthetic Career Role", "company_name": marker, "description": "Synthetic smoke fixture only. Do not fetch or apply.", "location_text": "Synthetic location"}
        job = self.data(self.api(actor, "POST", "jobs", json=payload), (201,))
        require(job.get("user_id") == user_id and job.get("source_url") == source_url and job.get("duplicate") is False, "manual-job-create-failed")
        job_id = str(UUID(job["id"]))
        duplicate = self.data(self.api(actor, "POST", "jobs", json=payload), (200,))
        require(duplicate.get("id") == job_id and duplicate.get("duplicate") is True, "manual-job-deduplication-failed")
        updated = self.data(self.api(actor, "PATCH", "jobs/" + job_id, json={"status": "ready"}))
        require(updated.get("status") == "ready", "job-update-failed")
        self.row(self.rest(actor, "PATCH", "jobs", filters={"id": "eq." + job_id}, data={"status": "matched"}), actor)

        content = synthetic_docx(marker)
        self.track(actor, "resumes", {"label": "eq." + marker})
        resume = self.data(self.api(actor, "POST", "resumes", json={"filename": marker + ".docx", "label": marker, "role_focus": "Synthetic only", "content_base64": base64.b64encode(content).decode()}), (201,))
        require(resume.get("user_id") == user_id and resume.get("label") == marker, "resume-owner-or-label-mismatch")
        resume_id = str(UUID(resume["id"]))
        self.track(actor, "resumes", {"id": "eq." + resume_id})
        resume_path = self.storage_path(actor, "resumes", resume["storage_path"])
        self.managed_resume_paths.add((actor, resume_path))
        direct = self.row(self.rest(actor, "GET", "resumes", filters={"id": "eq." + resume_id}), actor)
        require(direct.get("storage_path") == resume["storage_path"], "resume-metadata-round-trip-failed")
        for response in (self.api(actor, "GET", "resumes/" + resume_id + "/download"), self.request("supabase", actor, "GET", resume_path)):
            require(response.status_code == 200 and response.content == content, "resume-binary-round-trip-failed")
        text = self.data(self.api(actor, "GET", "resumes/" + resume_id + "/text"))
        require(isinstance(text, dict) and set(text) == {"text"} and isinstance(text["text"], str) and marker in text["text"] and len(text["text"]) <= 100000, "resume-text-round-trip-failed")
        self.report.passed.append(actor + " manual job dedup/update and private resume upload/download/text")

        question_id = str(uuid4())
        self.track(actor, "mobile_questions", {"id": "eq." + question_id})
        question = self.row(self.rest(actor, "POST", "mobile_questions", data={"id": question_id, "user_id": user_id, "job_id": job_id, "prompt": marker + ": why this synthetic company?", "status": "pending", "remember": False}), actor)
        self.track(actor, "mobile_answers", {"source_question_id": "eq." + question_id})
        answer = marker + ": synthetic answer for this job only."
        saved = self.data(self.api(actor, "POST", "questions/" + question_id + "/answer", json={"answer": answer, "remember": True}))
        require(saved.get("status") == "answered" and saved.get("answer") == answer, "question-answer-round-trip-failed")
        memory = self.row(self.rest(actor, "GET", "mobile_answers", filters={"source_question_id": "eq." + question_id}), actor)
        require(memory.get("scope") == "job:" + job_id and memory.get("answer") == answer and memory.get("question") == question["prompt"] and bool(memory.get("confirmed_at")), "answer-memory-scope-or-confirmation-failed")
        self.report.passed.append(actor + " question/confirmed job-scoped answer without AI")

        artifact_id = str(uuid4())
        artifact_name = user_id + "/" + artifact_id + ".txt"
        artifact_content = (marker + ": synthetic artifact, no model was called.").encode()
        artifact_path = self.storage_path(actor, "application-artifacts", artifact_name)
        self.track(actor, "artifacts", {"id": "eq." + artifact_id})
        response = self.request("supabase", actor, "POST", artifact_path, content=artifact_content, headers={"Content-Type": "text/plain", "x-upsert": "false"})
        require(response.status_code in (200, 201), "private-artifact-upload-failed")
        self.row(self.rest(actor, "POST", "artifacts", data={"id": artifact_id, "user_id": user_id, "job_id": job_id, "resume_id": resume_id, "kind": "other", "filename": marker + ".txt", "storage_path": artifact_name, "mime_type": "text/plain", "byte_size": len(artifact_content)}), actor)
        response = self.api(actor, "GET", "artifacts/" + artifact_id + "/download")
        require(response.status_code == 200 and response.content == artifact_content, "artifact-download-failed")
        for path, file_bytes, mime in ((resume_path, content, DOCX_MIME), (artifact_path, artifact_content, "text/plain")):
            response = self.request("supabase", actor, "PUT", path, content=file_bytes, headers={"Content-Type": mime})
            require(response.status_code in (200, 201), "owner-storage-update-failed")
            fetched = self.request("supabase", actor, "GET", path)
            require(fetched.status_code == 200 and fetched.content == file_bytes, "owner-storage-update-round-trip-failed")
        self.fixtures[actor] = {"job": job_id, "resume": resume_id, "question": question_id, "answer": memory["id"], "artifact": artifact_id, "resume_path": resume_path, "resume_content": content, "artifact_path": artifact_path, "artifact_content": artifact_content}
        workspace = self.bootstrap(actor)
        require(workspace["profile"].get("career_text") == profile["career_text"], "bootstrap-career-text-mismatch")
        require(workspace["profile"].get("career_background") == background, "bootstrap-background-mismatch")
        for key, record_id in (("jobs", job_id), ("resumes", resume_id), ("questions", question_id), ("artifacts", artifact_id)):
            require(any(row.get("id") == record_id for row in workspace[key]), "fixture-missing-from-bootstrap")
        self.report.passed.append(actor + " private artifact and persisted bootstrap without AI")

    def cross_user(self, attacker: str, owner: str) -> None:
        fixture = self.fixtures[owner]
        for table in SINGLETONS:
            require(self.rows(self.rest(attacker, "GET", table, target=owner)) == [], "direct-cross-user-singleton-visible")
        before_context = self.row(self.rest(owner, "GET", "candidate_context"), owner)
        response = self.rest(attacker, "PATCH", "candidate_context", target=owner, data={"career_background": EMPTY_BACKGROUND})
        require(response.status_code == 403 or (response.status_code == 200 and self.rows(response) == []), "cross-user-background-update-not-denied")
        require(self.row(self.rest(owner, "GET", "candidate_context"), owner) == before_context, "cross-user-background-changed-owner-row")
        for table, key, change in (("jobs", "job", {"status": "archived"}), ("resumes", "resume", {"label": "forbidden cross-user change"}), ("artifacts", "artifact", {"filename": "forbidden.txt"}), ("mobile_questions", "question", {"prompt": "forbidden cross-user question"}), ("mobile_answers", "answer", {"answer": "forbidden cross-user answer"})):
            filters = {"id": "eq." + fixture[key]}
            before = self.row(self.rest(owner, "GET", table, filters=filters), owner)
            require(self.rows(self.rest(attacker, "GET", table, target=owner, filters=filters)) == [], "direct-cross-user-row-visible")
            for method in ("PATCH", "DELETE"):
                response = self.rest(attacker, method, table, target=owner, filters=filters, data=change if method == "PATCH" else None)
                require(response.status_code == 403 or (response.status_code == 200 and self.rows(response) == []), "direct-cross-user-write-not-denied")
                after = self.row(self.rest(owner, "GET", table, filters=filters), owner)
                require(after == before, "direct-cross-user-write-changed-owner-row")
        forged_id = str(uuid4())
        self.track(owner, "jobs", {"id": "eq." + forged_id})
        forged = self.rest(attacker, "POST", "jobs", target=owner, data={"id": forged_id, "user_id": self.config.user(owner)[0], "source": "manual", "source_url": "https://example.invalid/" + self.report.run_label + "/forged/" + attacker, "title": "Synthetic forbidden insert", "company_name": self.report.run_label})
        require(forged.status_code == 403, "direct-cross-user-insert-not-denied")
        require(self.rows(self.rest(owner, "GET", "jobs", filters={"id": "eq." + forged_id})) == [], "forged-owner-record-exists")
        self.report.passed.append(attacker + " -> " + owner + " direct REST invisible read/update/delete and denied forged ownership insert")

        for path in ("resumes/" + fixture["resume"] + "/download", "resumes/" + fixture["resume"] + "/text", "artifacts/" + fixture["artifact"] + "/download"):
            require(self.api(attacker, "GET", path).status_code == 404, "api-cross-user-file-visible")
        require(self.api(attacker, "PATCH", "jobs/" + fixture["job"], json={"status": "archived"}).status_code == 404, "api-cross-user-job-update-not-denied")
        require(self.api(attacker, "DELETE", "resumes/" + fixture["resume"]).status_code == 404, "api-cross-user-resume-delete-not-denied")
        require(self.api(attacker, "POST", "questions/" + fixture["question"] + "/answer", json={"answer": "Synthetic forbidden answer", "remember": True}).status_code == 404, "api-cross-user-question-answer-not-denied")

        for bucket, path_key, content_key, mime in (("resumes", "resume_path", "resume_content", DOCX_MIME), ("application-artifacts", "artifact_path", "artifact_content", "text/plain")):
            path, content = fixture[path_key], fixture[content_key]
            require(self.storage_denied(self.request("supabase", attacker, "GET", path)), "cross-user-storage-readable")
            require(self.storage_denied(self.request("supabase", None, "GET", path)), "anonymous-storage-readable")
            require(self.storage_denied(self.request("supabase", None, "GET", path.replace("/object/", "/object/public/", 1))), "bucket-is-public")
            listed = self.data(self.request("supabase", attacker, "POST", "/storage/v1/object/list/" + bucket, json={"prefix": self.config.user(owner)[0] + "/", "search": path.rsplit("/", 1)[1], "limit": 10, "offset": 0}))
            require(listed == [], "cross-user-storage-list-visible")
            require(self.storage_denied(self.request("supabase", attacker, "PUT", path, content=b"forbidden synthetic overwrite", headers={"Content-Type": mime})), "cross-user-storage-overwrite-not-denied")
            deleted = self.request("supabase", attacker, "DELETE", path)
            require(self.storage_denied(deleted) or deleted.status_code in (200, 204), "unexpected-storage-delete-status")
            remaining = self.request("supabase", owner, "GET", path)
            require(remaining.status_code == 200 and remaining.content == content, "cross-user-storage-write-changed-owner-object")
            suffix = ".docx" if bucket == "resumes" else ".txt"
            forged_path = self.storage_path(owner, bucket, self.config.user(owner)[0] + "/" + self.report.run_label + "-forged-" + attacker + suffix)
            response = self.request("supabase", attacker, "POST", forged_path, content=content, headers={"Content-Type": mime, "x-upsert": "false"})
            require(self.storage_denied(response), "cross-user-storage-insert-not-denied")
            require(self.storage_denied(self.request("supabase", owner, "GET", forged_path)), "forged-storage-object-exists")
        self.report.passed.append(attacker + " -> " + owner + " API isolation and both private buckets: anonymous/cross-user read/list/create/overwrite/delete")

    def cleanup(self) -> None:
        self.deadline = time.monotonic() + 180
        # Exact run-specific filters also discover ambiguous/time-out writes.
        # Never enumerate/delete an entire user's collection.
        for actor, table, filters in reversed(self.records):
            try:
                rows = self.rows(self.rest(actor, "GET", table, filters=filters), owner=actor)
                require(len(rows) < 25, "cleanup-fixture-count-exceeded")
                for row in rows:
                    if table in ("resumes", "artifacts"):
                        path = self.storage_path(actor, "resumes" if table == "resumes" else "application-artifacts", row["storage_path"])
                    exact = {**filters, "id": "eq." + str(UUID(row["id"]))}
                    if table == "resumes":
                        # Complete the durable delete intent through the API.
                        # A failed API delete must never fall through to raw
                        # Storage deletion or leave a ready journal with no bytes.
                        self.managed_resume_paths.add((actor, path))
                        deleted = self.data(self.api(actor, "DELETE", "resumes/" + str(UUID(row["id"]))))
                        require(deleted.get("deleted") is True, "fixture-resume-delete-unconfirmed")
                    else:
                        self.rows(self.rest(actor, "DELETE", table, filters=exact), owner=actor)
                require(self.rows(self.rest(actor, "GET", table, filters=filters), owner=actor) == [], "fixture-record-cleanup-unconfirmed")
            except Exception:
                self.report.cleanup_failures.append(actor + " " + table + " fixture cleanup incomplete")
        for actor, path in reversed(self.objects):
            try:
                existing = self.request("supabase", actor, "GET", path)
                if existing.status_code == 200 and (actor, path) not in self.managed_resume_paths:
                    deleted = self.request("supabase", actor, "DELETE", path)
                    require(deleted.status_code in (200, 204), "fixture-object-delete-failed")
                else:
                    require(self.storage_denied(existing), "fixture-object-cleanup-unconfirmed")
                require(self.storage_denied(self.request("supabase", actor, "GET", path)), "fixture-object-remains")
            except Exception:
                self.report.cleanup_failures.append(actor + " private fixture object cleanup incomplete")
        for (actor, table), changed in reversed(list(self.changes.items())):
            try:
                baseline = self.snapshots[actor, table]
                current = self.rows(self.rest(actor, "GET", table), owner=actor)
                require(len(current) <= 1, "singleton-restore-ambiguous")
                if current:
                    # Do not clobber an unrelated edit made during the check.
                    require(all(current[0].get(key) in [value, (baseline or {}).get(key), *self.attempted_values.get((actor, table, key), [])] for key, value in changed.items()), "concurrent-singleton-edit-detected")
                if baseline is None:
                    self.rows(self.rest(actor, "DELETE", table), owner=actor)
                else:
                    require(len(current) == 1, "baseline-singleton-missing")
                    self.row(self.rest(actor, "PATCH", table, data={key: baseline.get(key) for key in changed}), actor)
                restored = self.rows(self.rest(actor, "GET", table), owner=actor)
                require((not restored and baseline is None) or (len(restored) == 1 and baseline is not None and all(restored[0].get(key) == baseline.get(key) for key in changed)), "singleton-restore-unconfirmed")
            except Exception:
                self.report.cleanup_failures.append(actor + " " + table + " restore incomplete; inspect synthetic account")

    def run(self) -> Report:
        stage = "preflight"
        try:
            self.preflight()
            for actor in ("A", "B"):
                stage = actor + " owner workflow"
                self.own_workflow(actor)
            for attacker, owner in (("A", "B"), ("B", "A")):
                stage = attacker + " to " + owner + " isolation"
                self.cross_user(attacker, owner)
            for actor in ("A", "B"):
                stage = actor + " owner deletion"
                fixture = self.fixtures[actor]
                deleted = self.data(self.api(actor, "DELETE", "resumes/" + fixture["resume"]))
                require(deleted.get("deleted") is True, "owner-resume-delete-failed")
                require(self.rows(self.rest(actor, "GET", "resumes", filters={"id": "eq." + fixture["resume"]})) == [], "owner-resume-record-still-exists")
                require(self.storage_denied(self.request("supabase", actor, "GET", fixture["resume_path"])), "owner-resume-object-still-exists")
                self.report.passed.append(actor + " API resume delete verified in REST and Storage")
        except CheckFailure as exc:
            self.report.failures.append(stage + ": " + str(exc))
        except KeyboardInterrupt:
            self.report.failures.append(stage + ": interrupted-cleanup-attempted")
        except Exception:
            self.report.failures.append(stage + ": unexpected-failure-details-withheld")
        finally:
            try:
                self.cleanup()
            except (Exception, KeyboardInterrupt):
                self.report.cleanup_failures.append("cleanup interrupted; inspect both synthetic accounts")
            finally:
                self.client.close()
        return self.report


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unrecognized argument values, possibly a token.
        self.exit(2, "Invalid arguments; use --help. Never pass credentials as arguments.\n")


def main(argv=None) -> int:
    parser = QuietParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="Authorize scoped hosted writes; default is offline.")
    parser.add_argument("--confirm-synthetic-users", action="store_true", help="Affirm both expected UUIDs are dedicated, idle synthetic accounts.")
    parser.add_argument("--credentials-file", help="Path only; private Git-ignored/untracked JSON.")
    args = parser.parse_args(argv)
    if not args.execute:
        print("OFFLINE PLAN ONLY: no credentials loaded and no network requests made.")
        print("Checks: two marked synthetic identities; isolated API; profile/career-background persistence, omission and clear; preferences/job/resume/text; question memory; two-way direct REST and private Storage isolation; scoped cleanup.")
        print("No model requests, email sends, auth writes, service-role keys, or real-user changes.")
        print("Review --help. Execution requires BOTH --execute and --confirm-synthetic-users.")
        return 0
    if not args.confirm_synthetic_users:
        print("REFUSED: --confirm-synthetic-users is required. No network requests made.")
        return 2
    try:
        config = load_credentials(args.credentials_file)
        report = HostedCheck(config).run()
    except Exception:
        print("REFUSED/FAILED: credentials or check setup invalid; details withheld. See --help.")
        return 2
    print("Run label: " + report.run_label)
    for label in report.passed:
        print("PASS: " + label)
    for label in report.failures:
        print("FAIL: " + label)
    for label in report.cleanup_failures:
        print("CLEANUP INCOMPLETE: " + label)
    if report.ok:
        print("PASS: all hosted checks passed; fixture deletion and editable-field restoration verified. Audit timestamps may advance.")
        return 0
    print("NOT VERIFIED: inspect the synthetic accounts using the run label; no live-security pass is claimed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
