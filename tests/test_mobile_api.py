"""Hermetic mobile API contract/security tests. No network or paid model calls.

Run: .venv/bin/python -m unittest discover -s tests -p test_mobile_api.py -v
The fake implements tenant-filtered PostgREST and private Storage over httpx.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import os
import re
import subprocess
import sys
import threading
import unittest
import zipfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient

from jobagent.mobile.app import DOCX_MIME, create_app
from jobagent.mobile.repository import COLUMNS, SupabaseSettings
from jobagent.mobile.schemas import MAX_BODY_BYTES, MAX_RESUME_BYTES, CareerBackground

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
SETTINGS = SupabaseSettings("https://mobile.example.test", "sb_publishable_unit_test")
TOKENS = {"session-a": USER_A, "session-b": USER_B}
EMPTY_BACKGROUND = {"profession": "", "experience_level": "unspecified", "qualifications": []}


def docx_bytes(extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Sample candidate builds reliable software systems.</w:t></w:r></w:p></w:body></w:document>')
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return buffer.getvalue()


class AuditDict(dict):
    model_metadata = {"provider": "mock", "model_name": "test-model", "prompt_version": "test-v1", "input_tokens": 10, "output_tokens": 20, "secret": "must-not-be-copied"}


class AuditList(list):
    model_metadata = AuditDict.model_metadata


class FakeMissingFactsError(ValueError):
    def __init__(self, questions):
        self.questions = questions
        super().__init__("Private studio detail must not be exposed")


def fake_studio():
    return SimpleNamespace(
        MissingFactsError=FakeMissingFactsError,
        extract_resume_text=Mock(return_value="Sample candidate builds reliable software systems."),
        prepare_documents=Mock(return_value=AuditList([{"kind": "tailored_resume", "filename": "resume.txt", "mime_type": "text/plain", "content": b"Confirmed resume text"}])),
        rank_job=Mock(return_value=AuditDict(score=8.5, recommendation="strong_match", rationale="Confirmed engineering experience overlaps the role.")),
        answer_chat=Mock(return_value=AuditDict(reply="Please confirm your availability.", evidence=["Your saved profile"], questions=["When can you start?"])),
    )


class FakeSupabase:
    def __init__(self):
        self.tables = {name: [] for name in COLUMNS}
        self.objects = {}
        self.requests = []
        self.fault = None
        self.force_foreign_row = False
        self.auth_emails = {}
        for user in (USER_A, USER_B):
            self.add("profiles", user, display_name="Candidate A" if user == USER_A else "Candidate B")
            self.add("job_preferences", user, target_titles=[], preferred_locations=[], preferred_regions=[], remote_preference="open", sponsorship_required=False, minimum_match_score=7)

    def add(self, table, user=USER_A, **values):
        row = {"user_id": user, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}
        if table not in ("profiles", "job_preferences", "candidate_context"):
            row["id"] = str(uuid4())
        if table == "mobile_answers":
            row["confirmed_at"] = "2026-09-01T00:00:00Z"
        row.update(values)
        self.tables[table].append(row)
        return row

    def job(self, user=USER_A, **values):
        return self.add("jobs", user, **{"title": "Backend Engineer", "company_name": "Example Company", "source": "manual", "source_url": "https://example.test/jobs/" + str(uuid4()), "description": "Build reliable software", "status": "new", **values})

    def resume(self, user=USER_A, **values):
        storage_path = user + "/" + str(uuid4()) + ".docx"
        self.objects[("resumes", storage_path)] = docx_bytes()
        return self.add("resumes", user, **{"storage_path": storage_path, "original_filename": "resume.docx", "label": "Source resume", "mime_type": DOCX_MIME, "is_default": False, **values})

    def __call__(self, request):
        self.requests.append(request)
        if request.url.host != "mobile.example.test":
            raise AssertionError("Unexpected network destination")
        if request.headers.get("apikey") != SETTINGS.publishable_key:
            raise AssertionError("Publishable key was not sent as apikey")
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        user = TOKENS.get(token)
        if self.fault:
            failure = self.fault(request)
            if failure is not None:
                return failure
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": user, "role": "authenticated", **self.auth_emails.get(user, {})}) if user else httpx.Response(401, json={"error": "secret upstream message"})
        if not user:
            return httpx.Response(401)
        if request.url.path == "/rest/v1/rpc/mobile_check_access":
            return httpx.Response(200, json={"allowed": True, "user_id": user})
        if request.url.path == "/rest/v1/rpc/mobile_save_profile":
            # Transport contract fake only; SQL atomicity/RLS is separately tested
            # in an actual database. Do not simulate two independent REST writes.
            from jobagent.mobile.schemas import ProfileUpdate
            data = json.loads(request.content)
            if set(data) != {"p_patch"}:
                raise AssertionError("Invalid profile RPC shape")
            fields = ProfileUpdate.model_validate(data["p_patch"]).model_dump(mode="json", exclude_unset=True)
            profile = next((row for row in self.tables["profiles"] if row["user_id"] == user), None)
            if profile is None: profile = self.add("profiles", user)
            context = next((row for row in self.tables["candidate_context"] if row["user_id"] == user), None)
            if set(fields) & {"career_text", "career_background"}:
                if context is None: context = self.add("candidate_context", user)
                for field in ("career_text", "career_background"):
                    if field in fields:
                        context[field] = fields.pop(field) or ("" if field == "career_text" else EMPTY_BACKGROUND.copy())
            profile.update(fields)
            result = ProfileUpdate().model_dump(mode="json")
            result.update({key: value for key, value in profile.items() if key in result or key in ("user_id", "created_at", "updated_at")})
            result["career_text"] = (context or {}).get("career_text")
            result["career_background"] = (context or {}).get("career_background") or EMPTY_BACKGROUND.copy()
            return httpx.Response(200, json=result)
        if request.url.path == "/rest/v1/rpc/mobile_reserve_ai_usage":
            data = json.loads(request.content)
            if set(data) != {"p_operation", "p_reservation_id"} or data["p_operation"] not in {"chat", "rank", "prepare"}:
                raise AssertionError("Invalid server-owned usage reservation")
            return httpx.Response(200, json={"allowed": True, "user_id": user,
                "reservation_id": data["p_reservation_id"], "operation": data["p_operation"],
                "reserved_units": 1, "remaining_period_units": 100, "remaining_daily_units": 20})
        if request.url.path.startswith("/storage/v1/object/"):
            bucket, storage_path = request.url.path.removeprefix("/storage/v1/object/").split("/", 1)
            if not storage_path.startswith(user + "/"):
                raise AssertionError("Cross-tenant storage request")
            key = (bucket, storage_path)
            if request.method == "GET":
                return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
            if request.method == "POST":
                if request.headers["x-upsert"] != "false":
                    raise AssertionError("Unexpected overwrite")
                if key in self.objects:
                    return httpx.Response(409)
                self.objects[key] = request.content
                return httpx.Response(200, json={"Key": storage_path})
            if request.method == "DELETE":
                self.objects.pop(key, None)
                return httpx.Response(200, json={"deleted": True})
        table = request.url.path.removeprefix("/rest/v1/")
        if table not in self.tables or request.url.params.get("user_id") != "eq." + user:
            raise AssertionError("Every REST request must explicitly filter user_id")
        rows = self.tables[table]
        selected = [row for row in rows if row["user_id"] == user]
        for key, expression in request.url.params.items():
            if key in ("user_id", "select", "order", "limit", "offset", "on_conflict") or "." in key:
                continue
            if expression.startswith("eq."):
                target = expression[3:]
                selected = [row for row in selected if str(row.get(key)).lower() == target.lower()]
            elif expression.startswith("in.("):
                ids = expression[4:-1].split(",")
                selected = [row for row in selected if row.get(key) in ids]
            elif expression == "is.null":
                selected = [row for row in selected if row.get(key) is None]
            else:
                raise AssertionError("Unsupported filter")
        if request.method == "GET":
            if self.force_foreign_row:
                return httpx.Response(200, json=[{"user_id": USER_B}])
            order = request.url.params.get("order", "created_at.desc").split(".")
            selected.sort(key=lambda row: row.get(order[0]) or "", reverse=order[-1] == "desc")
            offset = int(request.url.params.get("offset", "0"))
            result = copy.deepcopy(selected[offset:offset + int(request.url.params.get("limit", "200"))])
            if table == "jobs" and "job_scores(" in request.url.params.get("select", ""):
                if request.url.params.get("job_scores.user_id") != "eq." + user or request.url.params.get("job_scores.limit") != "1":
                    raise AssertionError("Nested scores must be tenant scoped and bounded")
                for row in result:
                    scores = [score for score in self.tables["job_scores"] if score["job_id"] == row["id"] and score["user_id"] == user]
                    row["job_scores"] = sorted(copy.deepcopy(scores), key=lambda score: score["created_at"], reverse=True)[:1]
            # Mirror PostgREST projection so aggregate-response tests don't
            # silently return fields the actual API explicitly excluded.
            projection = request.url.params.get("select", "*")
            if projection != "*":
                top_level = projection.split(",job_scores(", 1)[0].split(",")
                if "job_scores(" in projection:
                    top_level.append("job_scores")
                result = [{key: value for key, value in row.items() if key in top_level} for row in result]
            return httpx.Response(200, json=result)
        if request.method == "DELETE":
            self.tables[table] = [row for row in rows if row not in selected]
            return httpx.Response(200, json=selected)
        data = json.loads(request.content)
        payloads = data if isinstance(data, list) else [data]
        for payload in payloads:
            if (request.method == "POST" and payload.get("user_id") != user) or set(payload) - set(COLUMNS[table].split(",")):
                raise AssertionError("Unknown DB column or caller-provided foreign ownership")
            if table in ("candidate_context", "mobile_questions", "mobile_answers"):
                denied = {"created_at", "updated_at", "confirmed_at"}
                if request.method == "PATCH":
                    denied |= {"id", "user_id"}
                if set(payload) & denied:
                    raise AssertionError("Payload violates column-level grants")
        if request.method == "PATCH":
            for row in selected:
                row.update(data)
            return httpx.Response(200, json=selected)
        if request.method == "POST":
            result = []
            conflict_keys = request.url.params.get("on_conflict", "").split(",")
            for payload in payloads:
                existing = next((row for row in rows if all(row.get(key) == payload.get(key) for key in conflict_keys)), None) if conflict_keys != [""] else None
                if table == "jobs" and any(row["user_id"] == user and row["source_url"] == payload["source_url"] for row in rows):
                    return httpx.Response(409)
                if not existing and any(row.get("id") == payload.get("id") for row in rows) and payload.get("id"):
                    return httpx.Response(409)
                if existing is not None:
                    existing.update(payload)
                    result.append(existing)
                else:
                    data_copy = dict(payload)
                    data_copy.pop("user_id")
                    result.append(self.add(table, user, **data_copy))
            return httpx.Response(201, json=result)
        raise AssertionError("Unexpected request")


class MobileAPITests(unittest.TestCase):
    def setUp(self):
        self.supabase = FakeSupabase()
        self.studio = fake_studio()
        self.app = create_app(settings=SETTINGS, transport=httpx.MockTransport(self.supabase), studio=self.studio)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.headers["Authorization"] = "Bearer session-a"
        self.addCleanup(self.client.close)

    def request(self, method, path, **kwargs):
        return self.client.request(method, "/api/mobile/" + path, **kwargs)

    def upload(self, content=None, **extra):
        return self.request("POST", "resumes", json={"filename": "resume.docx", "label": "Engineering", "content_base64": base64.b64encode(content if content is not None else docx_bytes()).decode(), **extra})

    def test_public_health_is_minimal_and_never_contacts_supabase(self):
        self.client.headers.clear()
        response = self.request("GET", "health")
        self.assertEqual(response.json(), {"status": "ok", "service": "job-pursuit-mobile"})
        self.assertEqual(self.supabase.requests, [])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_unconfigured_fails_closed_but_health_works(self):
        with patch.dict(os.environ, {}, clear=True), TestClient(create_app(), raise_server_exceptions=False) as client:
            self.assertEqual(client.get("/api/mobile/health").status_code, 200)
            self.assertEqual(client.get("/api/mobile/bootstrap").status_code, 503)
            self.assertEqual(client.get("/docs").status_code, 404)

    def test_env_precedence_and_legacy_anon_key(self):
        payload = base64.urlsafe_b64encode(b'{"role":"anon"}').decode().rstrip("=")
        legacy = "header." + payload + ".signature"
        with patch.dict(os.environ, {"BETA_SUPABASE_URL": SETTINGS.url, "SUPABASE_ANON_KEY": legacy}, clear=True):
            self.assertEqual(SupabaseSettings.from_env().publishable_key, legacy)
            os.environ["MOBILE_SUPABASE_PUBLISHABLE_KEY"] = SETTINGS.publishable_key
            os.environ["MOBILE_SUPABASE_URL"] = SETTINGS.url
            self.assertEqual(SupabaseSettings.from_env(), SETTINGS)

    def test_service_role_configuration_is_rejected_before_network(self):
        for key in ("sb_secret_never-use", "x." + base64.urlsafe_b64encode(b'{"role":"service_role"}').decode() + ".x"):
            with self.subTest(key_type=key.split("_")[0]), TestClient(create_app(settings=SupabaseSettings(SETTINGS.url, key)), raise_server_exceptions=False) as client:
                self.assertEqual(client.get("/api/mobile/bootstrap").status_code, 503)

    def test_invalid_auth_and_publishable_bearer_never_reach_data(self):
        for value in ("", "Basic session-a", "Bearer " + SETTINGS.publishable_key, "Bearer invalid-session"):
            with self.subTest(value=value):
                response = self.request("GET", "bootstrap", headers={"Authorization": value})
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("secret upstream", response.text)
        self.assertTrue(all(request.url.path == "/auth/v1/user" for request in self.supabase.requests))

    def test_missing_or_invalid_auth_user_uuid_is_not_trusted(self):
        self.supabase.fault = lambda req: httpx.Response(200, json={"id": "not-a-uuid"}) if req.url.path == "/auth/v1/user" else None
        self.assertEqual(self.request("GET", "bootstrap").status_code, 401)
        self.assertEqual(len(self.supabase.requests), 1)

    def test_private_beta_accepts_only_verified_allowlisted_email(self):
        self.supabase.auth_emails[USER_A] = {"email": "Candidate-A@example.test", "email_confirmed_at": "2026-09-01T00:00:00Z"}
        with patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": " candidate-a@example.test "}):
            self.assertEqual(self.request("GET", "bootstrap").status_code, 200)
            self.supabase.requests.clear()
            denied = self.request("GET", "bootstrap", headers={"Authorization": "Bearer session-b", "Cf-Access-Authenticated-User-Email": "candidate-a@example.test"})
            self.assertEqual(denied.status_code, 403)
            self.assertTrue(all(req.url.path == "/auth/v1/user" for req in self.supabase.requests))

    def test_private_beta_does_not_trust_unconfirmed_email_or_profile_claims(self):
        self.supabase.auth_emails[USER_A] = {"email": "candidate-a@example.test", "email_confirmed_at": None, "user_metadata": {"email_confirmed": True}}
        self.supabase.tables["profiles"][0]["email"] = "candidate-a@example.test"
        with patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": "candidate-a@example.test"}):
            response = self.request("POST", "chat", json={"message": "Help me prepare", "mode": "application", "history": []})
        self.assertEqual(response.status_code, 403)
        self.studio.answer_chat.assert_not_called()
        self.assertTrue(all(req.url.path == "/auth/v1/user" for req in self.supabase.requests))

    def test_private_beta_empty_and_malformed_configuration_fail_closed(self):
        self.supabase.auth_emails[USER_A] = {"email": "candidate-a@example.test", "email_confirmed_at": "2026-09-01T00:00:00Z"}
        for value, status in (("", 403), (" , ", 403), ("*", 503), ("*@example.test", 503), ("not-an-email", 503), ("a@b\nb@c", 503)):
            with self.subTest(configuration_kind=status), patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": value}):
                self.assertEqual(self.request("GET", "bootstrap").status_code, status)
                self.assertEqual(self.request("GET", "health", headers={"Authorization": ""}).status_code, 200)

    def test_private_beta_preserves_unauthenticated_denial(self):
        with patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": "candidate-a@example.test"}):
            self.assertEqual(self.request("GET", "bootstrap", headers={"Authorization": ""}).status_code, 401)
        self.assertEqual(self.supabase.requests, [])

    def test_bootstrap_merges_career_and_returns_latest_score(self):
        self.supabase.add("candidate_context", career_text="Confirmed candidate experience.")
        job = self.supabase.job()
        self.supabase.job(USER_B)
        self.supabase.add("job_scores", job_id=job["id"], score="5.00", rationale="Older", created_at="2026-09-01")
        self.supabase.add("job_scores", job_id=job["id"], score="8.25", rationale="Latest", created_at="2026-09-02")
        result = self.request("GET", "bootstrap")
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual(set(data), {"profile", "preferences", "jobs", "resumes", "artifacts", "applications", "questions", "capabilities"})
        self.assertEqual(data["profile"]["career_text"], "Confirmed candidate experience.")
        self.assertEqual(len(data["jobs"]), 1)
        self.assertEqual((data["jobs"][0]["score"], data["jobs"][0]["rationale"]), (8.25, "Latest"))
        self.assertFalse(data["capabilities"]["automatic_submission"])
        for request in self.supabase.requests:
            self.assertEqual(request.headers["authorization"], "Bearer session-a")
            self.assertEqual(request.headers["apikey"], SETTINGS.publishable_key)

    def test_requests_do_not_share_authentication_between_tenants(self):
        first = self.request("GET", "bootstrap").json()
        second = self.request("GET", "bootstrap", headers={"Authorization": "Bearer session-b"}).json()
        self.assertEqual(first["profile"]["user_id"], USER_A)
        self.assertEqual(second["profile"]["user_id"], USER_B)

    def test_even_an_upstream_foreign_row_is_rejected(self):
        self.supabase.force_foreign_row = True
        response = self.request("GET", "bootstrap")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(USER_B, response.text)

    def test_profile_and_preferences_only_write_exact_schema_fields(self):
        response = self.request("PUT", "profile", json={"display_name": "Updated Candidate", "phone": None, "base_location": "Remote", "career_text": "User-confirmed facts"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["career_text"], "User-confirmed facts")
        self.assertNotIn("career_text", self.supabase.tables["profiles"][0])
        response = self.request("PUT", "preferences", json={"target_titles": ["Engineer"], "preferred_locations": ["Remote"], "preferred_regions": [], "remote_preference": "hybrid", "sponsorship_required": True, "work_authorization_notes": None, "minimum_match_score": 8})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["minimum_match_score"], 8)

    def test_unknown_fields_and_invalid_bounds_are_rejected_without_echo(self):
        cases = [("profile", {"user_id": USER_B}), ("profile", {"full_name": "do-not-echo"}), ("profile", {"career_text": "x" * 120001}), ("preferences", {"minimum_match_score": 11}), ("preferences", {"sponsorship_required": "yes"}), ("preferences", {"target_titles": ["x" * 161]}), ("preferences", {"remote_preference": "remote_preferred"})]
        for endpoint, body in cases:
            with self.subTest(endpoint=endpoint, fields=list(body)):
                response = self.request("PUT", endpoint, json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("do-not-echo", response.text)

    def test_manual_import_normalizes_and_is_idempotent_without_fetching_url(self):
        body = {"source_url": "HTTPS://EXAMPLE.COM:443/jobs/1#section", "title": "Engineer", "company_name": "Example", "description": "Build useful software", "location_text": "Remote"}
        first = self.request("POST", "jobs", json=body)
        second = self.request("POST", "jobs", json={**body, "description": "Must not overwrite"})
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["source_url"], "https://example.com/jobs/1")
        self.assertFalse(first.json()["duplicate"])
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(second.json()["description"], "Build useful software")
        self.assertTrue(all(req.url.host == "mobile.example.test" for req in self.supabase.requests))

    def test_import_rejects_non_http_urls_and_embedded_credentials(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "https://person:password@example.com/job", "https://example.com/\\other"):
            response = self.request("POST", "jobs", json={"source_url": url, "title": "Engineer", "company_name": "Example", "description": "Role"})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.supabase.tables["jobs"], [])

    def test_uuid_path_validation_and_tenant_scoped_job_updates(self):
        for path in ("jobs/not-a-uuid", "resumes/not-a-uuid/download", "artifacts/not-a-uuid/download"):
            response = self.request("PATCH" if path.startswith("jobs/") else "GET", path, **({"json": {"status": "ready"}} if path.startswith("jobs/") else {}))
            self.assertEqual(response.status_code, 422)
        foreign = self.supabase.job(USER_B)
        self.assertEqual(self.request("PATCH", "jobs/" + foreign["id"], json={"status": "ready"}).status_code, 404)
        self.assertEqual(foreign["status"], "new")
        own = self.supabase.job()
        self.assertEqual(self.request("PATCH", "jobs/" + own["id"], json={"status": "ready", "user_id": USER_B}).status_code, 422)

    def test_upload_stores_uuid_path_and_downloads_original_binary(self):
        content = docx_bytes()
        response = self.upload(content)
        self.assertEqual(response.status_code, 201, response.text)
        resume = response.json()
        prefix, filename = resume["storage_path"].split("/")
        self.assertEqual(prefix, USER_A)
        UUID(filename.removesuffix(".docx"))
        self.assertEqual(resume["byte_size"], len(content))
        self.studio.extract_resume_text.assert_called_once_with(content, "resume.docx")
        download = self.request("GET", "resumes/" + resume["id"] + "/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, content)
        self.assertEqual(download.headers["content-type"], DOCX_MIME)
        self.assertIn("attachment;", download.headers["content-disposition"])

    def test_upload_rejects_bad_base64_spoofed_files_and_traversal(self):
        for changes in ({"content_base64": "!!!!"}, {"filename": "../resume.docx"}, {"filename": "resume.exe"}, {"filename": "resume.pdf"}):
            self.assertEqual(self.upload(**changes).status_code, 422)
        self.assertEqual(self.upload(b"This is not a DOCX").status_code, 422)
        self.assertEqual(self.supabase.objects, {})

    def test_unsafe_docx_archives_are_rejected_before_extraction(self):
        for extra in ({"../escape.xml": "x"}, {"word/evil.xml": '<!DOCTYPE x [<!ENTITY e "hello">]><x/>'}, {"word/bomb.xml": "x" * 100000}, {"word/vbaProject.bin": b"macro"}):
            with self.subTest(names=list(extra)):
                self.assertEqual(self.upload(docx_bytes(extra)).status_code, 422)
        self.studio.extract_resume_text.assert_not_called()

    def test_file_and_body_size_limits(self):
        self.assertEqual(self.upload(b"x" * (MAX_RESUME_BYTES + 1)).status_code, 422)
        response = self.request("POST", "resumes", headers={"Content-Length": str(MAX_BODY_BYTES + 1)}, content=b"{}")
        self.assertEqual(response.status_code, 413)
        response = self.request("POST", "chat", content=(b"x" * 200000 for _ in range(2)))
        self.assertEqual(response.status_code, 413)

    def test_parse_failures_return_safe_actionable_error(self):
        self.studio.extract_resume_text.side_effect = RuntimeError("secret parser traceback")
        response = self.upload()
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("secret", response.text)
        self.assertEqual(self.supabase.objects, {})

    def test_upload_metadata_failure_retains_journaled_storage(self):
        self.supabase.fault = lambda req: httpx.Response(500, text="secret SQL exception") if req.method == "POST" and req.url.path == "/rest/v1/resumes" else None
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.supabase.objects), 1)
        self.assertEqual(self.supabase.tables["mobile_resume_operations"][0]["state"], "upload_pending")
        self.assertNotIn("secret", response.text)

    def test_foreign_downloads_and_storage_path_poisoning_are_blocked(self):
        foreign = self.supabase.resume(USER_B)
        self.assertEqual(self.request("GET", "resumes/" + foreign["id"] + "/download").status_code, 404)
        poisoned = self.supabase.resume(storage_path=foreign["storage_path"])
        self.assertEqual(self.request("GET", "resumes/" + poisoned["id"] + "/download").status_code, 404)
        self.assertEqual(self.request("DELETE", "resumes/" + poisoned["id"]).status_code, 404)
        self.assertIn(poisoned, self.supabase.tables["resumes"])
        self.assertFalse(any(req.url.path.startswith("/storage/") for req in self.supabase.requests))

    def test_deletion_removes_record_and_object_and_explains_reupload(self):
        resume = self.supabase.resume()
        response = self.request("DELETE", "resumes/" + resume["id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["deleted"])
        self.assertIn("upload your original", response.json()["note"])
        self.assertEqual(self.supabase.tables["resumes"], [])
        self.assertEqual(self.supabase.objects, {})

    def test_failed_record_deletion_preserves_object_without_restore(self):
        resume = self.supabase.resume()
        before = dict(self.supabase.objects)
        self.supabase.fault = lambda req: httpx.Response(500) if req.method == "DELETE" and req.url.path == "/rest/v1/resumes" else None
        response = self.request("DELETE", "resumes/" + resume["id"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.supabase.objects, before)
        self.assertEqual(len(self.supabase.tables["resumes"]), 1)

    def test_application_requires_explicit_valid_status_and_owned_job(self):
        job = self.supabase.job()
        for status in (None, "saved", "applied"):
            body = {"job_id": job["id"]}
            if status is not None:
                body["status"] = status
            self.assertEqual(self.request("POST", "applications", json=body).status_code, 422)
        response = self.request("POST", "applications", json={"job_id": job["id"], "status": "submitted", "notes": "Manually recorded by user"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "submitted")
        self.assertIsNone(response.json().get("applied_at"))
        self.assertEqual(job["status"], "new")
        foreign = self.supabase.job(USER_B)
        self.assertEqual(self.request("POST", "applications", json={"job_id": foreign["id"], "status": "draft"}).status_code, 404)

    def test_answer_memory_is_scoped_and_remember_false_forgets(self):
        question = self.supabase.add("mobile_questions", job_id=None, prompt="When can you start?", status="pending", remember=False, answer=None)
        path = "questions/" + question["id"] + "/answer"
        response = self.request("POST", path, json={"answer": "Next month", "remember": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "answered")
        self.assertEqual(self.supabase.tables["mobile_answers"][0]["user_id"], USER_A)
        self.assertEqual(self.request("POST", path, json={"answer": "Two months", "remember": False}).status_code, 200)
        self.assertEqual(self.supabase.tables["mobile_answers"], [])

    def test_foreign_questions_cannot_be_answered(self):
        question = self.supabase.add("mobile_questions", USER_B, prompt="Private question", status="pending")
        response = self.request("POST", "questions/" + question["id"] + "/answer", json={"answer": "No", "remember": True})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.supabase.tables["mobile_answers"], [])

    def test_preparation_persists_artifact_provenance_and_downloads_bytes(self):
        job, resume = self.supabase.job(), self.supabase.resume()
        self.supabase.add("candidate_context", career_text="Confirmed engineering work")
        response = self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"], "variant": "sse"})
        self.assertEqual(response.status_code, 200, response.text)
        artifact = response.json()["artifacts"][0]
        self.assertEqual(artifact["model_name"], "test-model")
        self.assertTrue(artifact["storage_path"].startswith(USER_A + "/"))
        run = self.supabase.tables["model_runs"][0]
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["input_summary"]["resume_id"], resume["id"])
        self.assertEqual(run["output_summary"]["artifact_ids"], [artifact["id"]])
        self.assertNotIn("secret", run["output_summary"]["model_metadata"])
        context = self.studio.prepare_documents.call_args.args[0]
        self.assertEqual(set(context), {"profile", "career_text", "career_background", "preferences", "resume_text", "job", "answers"})
        self.assertEqual(context["career_text"], "Confirmed engineering work")
        self.assertEqual(self.request("GET", "artifacts/" + artifact["id"] + "/download").content, b"Confirmed resume text")

    def test_preparation_cannot_read_another_users_resume(self):
        job, resume = self.supabase.job(), self.supabase.resume(USER_B)
        response = self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"], "variant": "fde"})
        self.assertEqual(response.status_code, 404)
        self.studio.prepare_documents.assert_not_called()

    def test_ai_failure_is_not_success_and_never_leaks_exception(self):
        job, resume = self.supabase.job(), self.supabase.resume()
        self.studio.prepare_documents.side_effect = RuntimeError("secret provider credential or prompt")
        response = self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"], "variant": "sse"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", response.text)
        self.assertEqual(self.supabase.tables["model_runs"][0]["status"], "failed")
        self.assertEqual(self.supabase.tables["artifacts"], [])

    def test_invalid_ai_document_output_is_rejected(self):
        job, resume = self.supabase.job(), self.supabase.resume()
        self.studio.prepare_documents.return_value = [{"kind": "tailored_resume", "filename": "../bad.txt", "mime_type": "text/plain", "content": b"text"}]
        response = self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"], "variant": "sse"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.supabase.tables["artifacts"], [])

    def test_rank_persists_score_and_preserves_user_progress(self):
        job = self.supabase.job()
        response = self.request("POST", "jobs/" + job["id"] + "/rank")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["score"], 8.5)
        self.assertEqual(response.json()["status"], "matched")
        job["status"] = "applied"
        self.studio.rank_job.return_value = AuditDict(score=2, recommendation="exclude", rationale="Insufficient overlap")
        response = self.request("POST", "jobs/" + job["id"] + "/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(job["status"], "applied")

    def test_rank_rejects_nonfinite_or_out_of_range_model_scores(self):
        job = self.supabase.job()
        for score in (float("nan"), 11, -1, "8", True):
            self.studio.rank_job.return_value = {"score": score, "recommendation": "match", "rationale": "Reason"}
            self.assertEqual(self.request("POST", "jobs/" + job["id"] + "/rank").status_code, 502)
        self.assertEqual(self.supabase.tables["job_scores"], [])

    def test_chat_returns_saved_question_objects_and_confirmed_scoped_answers(self):
        job, other_job = self.supabase.job(), self.supabase.job()
        for scope in (job["id"], other_job["id"], None):
            question = self.supabase.add("mobile_questions", job_id=scope, prompt="Availability?", answer="Next month", remember=True, status="answered")
            self.supabase.add("mobile_answers", source_question_id=question["id"], question="Availability?", answer="Next month", scope="job:" + scope if scope else "profile")
        response = self.request("POST", "chat", json={"job_id": job["id"], "message": "Help me prepare", "mode": "application", "history": [{"role": "user", "content": "Earlier message"}]})
        self.assertEqual(response.status_code, 200, response.text)
        question = response.json()["questions"][0]
        self.assertEqual(question["job_id"], job["id"])
        self.assertEqual(question["status"], "pending")
        UUID(question["id"])
        context = self.studio.answer_chat.call_args.args[0]
        self.assertEqual(len(context["answers"]), 2)
        self.assertTrue(all(answer["confirmed_at"] for answer in context["answers"]))
        self.assertEqual({answer["scope"] for answer in context["answers"]}, {"profile", "job:" + job["id"]})

    def test_chat_validation_and_bounded_output(self):
        for changes in ({"history": [{"role": "system", "content": "Instruction"}]}, {"message": "x" * 8001}, {"job_id": "bad-id"}, {"mode": "admin"}):
            response = self.request("POST", "chat", json={"message": "Help", "mode": "resume", **changes})
            self.assertEqual(response.status_code, 422)
        self.studio.answer_chat.return_value = {"reply": "x" * 40001, "evidence": [], "questions": []}
        self.assertEqual(self.request("POST", "chat", json={"message": "Help", "mode": "resume"}).status_code, 502)
        self.assertEqual(self.supabase.tables["mobile_questions"], [])

    def test_upstream_timeouts_errors_and_redirects_are_safe(self):
        def timeout(req):
            raise httpx.ReadTimeout("secret request headers", request=req)
        self.supabase.fault = timeout
        response = self.request("GET", "bootstrap")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.text)
        for status in (400, 429, 500, 302):
            self.supabase.fault = lambda req, status=status: httpx.Response(status, text="secret backend details", headers={"Location": "https://outside.example"})
            response = self.request("GET", "bootstrap")
            self.assertEqual(response.status_code, 429 if status == 429 else 502)
            if status == 429:
                self.assertEqual(response.headers["retry-after"], "60")
            self.assertNotIn("secret", response.text)

    def test_rate_limits_are_per_tenant_and_health_is_exempt(self):
        self.app.state.limits.limits["requests"] = 1
        self.assertEqual(self.request("GET", "bootstrap").status_code, 200)
        response = self.request("GET", "bootstrap")
        self.assertEqual(response.status_code, 429)
        self.assertIn("retry-after", response.headers)
        self.assertEqual(self.request("GET", "bootstrap", headers={"Authorization": "Bearer session-b"}).status_code, 200)
        self.assertEqual(self.request("GET", "health").status_code, 200)

    def test_ai_limits_are_distinct_from_regular_requests(self):
        self.app.state.limits.limits["ai"] = 1
        body = {"message": "Help", "mode": "resume"}
        self.assertEqual(self.request("POST", "chat", json=body).status_code, 200)
        self.assertEqual(self.request("POST", "chat", json=body).status_code, 429)
        self.assertEqual(self.request("GET", "bootstrap").status_code, 200)

    def test_concurrency_guard_rejects_parallel_tenant_work_and_releases(self):
        entered, release = threading.Event(), threading.Event()
        def slow_chat(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test worker timed out")
            return {"reply": "Ready", "evidence": [], "questions": []}
        self.studio.answer_chat.side_effect = slow_chat

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://asgi.test", headers={"Authorization": "Bearer session-a"}) as client:
                body = {"message": "Help", "mode": "resume"}
                first = asyncio.create_task(client.post("/api/mobile/chat", json=body))
                try:
                    self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4))
                    second = await client.post("/api/mobile/chat", json=body)
                    self.assertEqual(second.status_code, 429)
                finally:
                    release.set()
                self.assertEqual((await first).status_code, 200)
                self.assertEqual(self.app.state.limits.active, set())
        asyncio.run(scenario())

    def test_isolated_asgi_import_never_loads_founder_or_sqlite(self):
        result = subprocess.run([sys.executable, "-c", "import sys; import jobagent.mobile.app; assert 'sqlite3' not in sys.modules; assert 'jobagent.api.main' not in sys.modules; assert 'jobagent.config' not in sys.modules"], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_founder_api_routes_do_not_exist_even_without_auth(self):
        self.client.headers.clear()
        for path in ("/api/profile", "/api/jobs", "/api/runs", "/api/health", "/api/applications", "/api/beta/bootstrap", "/docs", "/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.supabase.requests, [])

    def test_resume_text_is_owner_scoped_and_does_not_confirm_import(self):
        own, foreign = self.supabase.resume(), self.supabase.resume(USER_B)
        response = self.request("GET", "resumes/" + own["id"] + "/text")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"text": "Sample candidate builds reliable software systems."})
        self.assertEqual(self.supabase.tables["candidate_context"], [])
        self.assertEqual(self.request("GET", "resumes/" + foreign["id"] + "/text").status_code, 404)
        self.assertEqual(self.request("GET", "resumes/not-a-uuid/text").status_code, 422)
        self.assertEqual(self.request("GET", "resumes/" + own["id"] + "/text", headers={"Authorization": ""}).status_code, 401)
        self.studio.extract_resume_text.assert_called_once()

    def test_resume_text_extraction_and_download_are_bounded(self):
        resume = self.supabase.resume()
        self.studio.extract_resume_text.return_value = "x" * 100001
        response = self.request("GET", "resumes/" + resume["id"] + "/text")
        self.assertEqual(response.status_code, 422)
        self.supabase.objects[("resumes", resume["storage_path"])] = b"x" * (MAX_RESUME_BYTES + 1)
        self.assertEqual(self.request("GET", "resumes/" + resume["id"] + "/text").status_code, 502)
        self.studio.extract_resume_text.assert_called_once()

    def test_eight_mib_contract_accepts_resume_above_old_five_mib_cap(self):
        self.assertEqual(MAX_RESUME_BYTES, 8 * 1024 * 1024)
        self.assertGreater(MAX_BODY_BYTES, ((MAX_RESUME_BYTES + 2) // 3) * 4 + 4096)
        buffer = io.BytesIO(docx_bytes())
        with zipfile.ZipFile(buffer, "a", zipfile.ZIP_STORED) as archive:
            archive.writestr("padding.dat", b"x" * (6 * 1024 * 1024))
        response = self.upload(buffer.getvalue())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertGreater(response.json()["byte_size"], 5 * 1024 * 1024)
        self.assertEqual(self.request("GET", "bootstrap").json()["capabilities"]["max_resume_bytes"], MAX_RESUME_BYTES)

    def test_submitted_requires_nonblank_user_confirmation_notes(self):
        job = self.supabase.job()
        for notes in (None, "", "  \n  "):
            response = self.request("POST", "applications", json={"job_id": job["id"], "status": "submitted", "notes": notes})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.supabase.tables["applications"], [])
        response = self.request("POST", "applications", json={"job_id": job["id"], "status": "submitted", "notes": "I confirm that I submitted this application myself."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json().get("applied_at"))

    def test_verified_auth_email_is_used_only_in_export_context(self):
        self.supabase.auth_emails[USER_A] = {"email": "candidate-a@example.test", "email_confirmed_at": "2026-09-01T00:00:00Z"}
        job, resume = self.supabase.job(), self.supabase.resume()
        response = self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"], "variant": "sse"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.studio.prepare_documents.call_args.args[0]["profile"]["email"], "candidate-a@example.test")
        self.assertNotIn("email", self.supabase.tables["profiles"][0])
        self.assertEqual(self.request("PUT", "profile", json={"email": "spoofed@example.test"}).status_code, 422)
        self.supabase.auth_emails[USER_A] = {"email": "unconfirmed@example.test", "email_confirmed_at": None}
        response = self.request("POST", "chat", json={"message": "Help", "mode": "resume"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("email", self.studio.answer_chat.call_args.args[0]["profile"])

    def test_first_upload_becomes_default_and_rank_empty_body_uses_it(self):
        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertTrue(uploaded.json()["is_default"])
        second = self.upload(label="Second resume")
        self.assertFalse(second.json()["is_default"])
        job = self.supabase.job()
        self.studio.extract_resume_text.reset_mock()
        response = self.request("POST", "jobs/" + job["id"] + "/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Sample candidate", self.studio.rank_job.call_args.args[0]["resume_text"])
        self.studio.extract_resume_text.assert_called_once()

    def test_repeated_profile_and_memory_updates_respect_column_grants(self):
        for text in ("First confirmed facts", "Revised confirmed facts"):
            response = self.request("PUT", "profile", json={"career_text": text})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.supabase.tables["candidate_context"]), 1)
        job = self.supabase.job()
        question = self.supabase.add("mobile_questions", job_id=job["id"], prompt="Why this company?", status="pending", remember=False, answer=None)
        for answer in ("Their technical work", "Their research"):
            response = self.request("POST", "questions/" + question["id"] + "/answer", json={"answer": answer, "remember": True})
            self.assertEqual(response.status_code, 200, response.text)
        memories = self.supabase.tables["mobile_answers"]
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["scope"], "job:" + job["id"])
        self.assertEqual(memories[0]["answer"], "Their research")

    def test_old_profile_background_defaults_and_neutral_variant_capabilities(self):
        data = self.request("GET", "bootstrap").json()
        self.assertEqual(data["profile"]["career_background"], EMPTY_BACKGROUND)
        self.assertEqual(data["capabilities"]["default_document_variant"], "role_aligned")
        self.assertEqual(data["capabilities"]["document_variants"], ["role_aligned", "career_change", "sse", "fde"])
        self.assertTrue(data["capabilities"]["career_background_self_reported"])
        self.assertEqual(self.supabase.tables["candidate_context"], [])
        self.assertEqual(self.request("PUT", "profile", json={"career_background": {}}).json()["career_background"], EMPTY_BACKGROUND)

    def test_healthcare_marketing_and_trades_background_roundtrip_and_context(self):
        for profession, title, qualification in (
            ("Nursing / acute care", "Staff Nurse", {"name": "Registered nurse licence", "kind": "licence", "status": "expired", "jurisdiction": "Example jurisdiction", "expires_on": "2025-04-30", "evidence_note": "I need to renew this licence."}),
            ("Brand marketing", "Marketing Manager", {"name": "Marketing diploma", "kind": "education", "status": "current"}),
            ("Electrical trades", "Apprentice Electrician", {"name": "Electrical training", "kind": "certification", "status": "in_progress"}),
        ):
            with self.subTest(profession=profession):
                background = {"profession": profession, "experience_level": "career_change", "qualifications": [qualification]}
                expected = CareerBackground.model_validate(background).model_dump(mode="json")
                response = self.request("PUT", "profile", json={"career_background": background})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["career_background"], expected)
                self.assertEqual(self.request("GET", "bootstrap").json()["profile"]["career_background"], expected)
                self.assertEqual(self.supabase.tables["candidate_context"][0]["career_background"], expected)
                job = self.supabase.job(title=title, description="The job's actual requirements, not the profession label, drive tailoring.")
                response = self.request("POST", "jobs/" + job["id"] + "/rank", json={})
                self.assertEqual(response.status_code, 200, response.text)
                context = self.studio.rank_job.call_args.args[0]
                self.assertEqual(context["career_background"], expected)
                self.assertEqual(context["job"]["title"], title)
                self.assertNotIn("verified", json.dumps(context["career_background"]))

    def test_profile_partial_updates_preserve_omitted_career_fields_and_owner(self):
        background = {"profession": "Physiotherapy", "qualifications": [{"name": "Clinical registration", "kind": "licence", "status": "unknown"}]}
        expected = CareerBackground.model_validate(background).model_dump(mode="json")
        foreign = self.supabase.add("candidate_context", USER_B, career_text="B private", career_background={"profession": "Private"})
        original_foreign = copy.deepcopy(foreign)
        for body in (
            {"career_text": "Confirmed patient care experience", "career_background": background},
            {"display_name": "Candidate Updated"},
            {"career_text": "Updated patient care experience"},
        ):
            response = self.request("PUT", "profile", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["career_background"], expected)
        response = self.request("PUT", "profile", json={"career_background": {"profession": "Clinical educator"}})
        self.assertEqual(response.json()["career_text"], "Updated patient care experience")
        self.assertEqual(response.json()["display_name"], "Candidate Updated")
        response = self.request("PUT", "profile", json={"career_background": None})
        self.assertEqual(response.json()["career_background"], EMPTY_BACKGROUND)
        self.assertEqual(response.json()["career_text"], "Updated patient care experience")
        response = self.request("PUT", "profile", json={"career_text": None})
        self.assertEqual(response.json()["career_text"], "")
        self.assertEqual(response.json()["career_background"], EMPTY_BACKGROUND)
        self.assertEqual(foreign, original_foreign)
        patches = [req for req in self.supabase.requests if req.method == "POST" and req.url.path.endswith("/rpc/mobile_save_profile")]
        self.assertTrue(patches)
        for req in patches:
            self.assertNotIn("user_id", json.loads(req.content))
        self.assertEqual(json.loads(patches[-2].content), {"p_patch": {"career_background": None}})
        self.assertEqual(json.loads(patches[-1].content), {"p_patch": {"career_text": None}})

    def test_atomic_background_patch_preserves_context_committed_before_rpc(self):
        raced = False
        def conflict(req):
            nonlocal raced
            if not raced and req.method == "POST" and req.url.path.endswith("/rpc/mobile_save_profile"):
                raced = True
                self.supabase.add("candidate_context", career_text="Concurrent confirmed facts")
                # A disjoint prior commit is preserved by a partial RPC patch.
                # True concurrent SQL transaction behavior has separate tests.
        self.supabase.fault = conflict
        response = self.request("PUT", "profile", json={"career_background": {"profession": "Carpentry"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["career_text"], "Concurrent confirmed facts")
        self.assertEqual(response.json()["career_background"]["profession"], "Carpentry")
        self.assertEqual(len(self.supabase.tables["candidate_context"]), 1)

    def test_invalid_credential_inputs_fail_before_any_persistence(self):
        valid = {"name": "Self-reported qualification", "kind": "licence"}
        bad_qualifications = [
            {}, {**valid, "name": "  "}, {**valid, "name": "x" * 161}, {**valid, "name": 42},
            {**valid, "kind": "doctor"}, {**valid, "kind": "license"},
            {**valid, "status": "verified"}, {**valid, "verified": True},
            {**valid, "user_id": USER_B}, {**valid, "jurisdiction": "x" * 161},
            {**valid, "evidence_note": "x" * 2001}, {**valid, "evidence_note": False},
            {**valid, "expires_on": "2026-02-30"}, {**valid, "expires_on": "2026-1-01"},
            {**valid, "expires_on": "2026-01-01T00:00:00Z"}, {**valid, "expires_on": 0},
            {**valid, "expires_on": True}, {**valid, "expires_on": "0000-01-01"},
        ]
        invalid = [{"qualifications": [qualification]} for qualification in bad_qualifications] + [
            {"profession": "x" * 121}, {"profession": 123}, {"profession": None},
            {"experience_level": "expert"}, {"experience_level": None},
            {"qualifications": [valid] * 31}, {"qualifications": {}}, {"qualifications": None},
            {"verified": True}, {"user_id": USER_B}, [], "secret invalid background",
        ]
        for background in invalid:
            with self.subTest(background_type=type(background).__name__):
                response = self.request("PUT", "profile", json={"career_background": background})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("secret invalid", response.text)
        self.assertEqual(self.supabase.tables["candidate_context"], [])
        self.assertFalse(any(
            req.method in ("POST", "PATCH")
            and req.url.path != "/rest/v1/rpc/mobile_check_access"
            for req in self.supabase.requests
        ))
        self.studio.prepare_documents.assert_not_called()

    def test_qualification_bounds_unknown_status_and_iso_date_are_preserved(self):
        qualification = {"name": "Q" * 160, "kind": "other", "jurisdiction": "J" * 160, "expires_on": "2028-02-29", "evidence_note": "E" * 2000}
        response = self.request("PUT", "profile", json={"career_background": {"profession": "P" * 120, "qualifications": [qualification] * 30}})
        self.assertEqual(response.status_code, 200, response.text)
        qualifications = response.json()["career_background"]["qualifications"]
        self.assertEqual(len(qualifications), 30)
        self.assertEqual(qualifications[0]["status"], "unknown")
        self.assertEqual(qualifications[0]["expires_on"], "2028-02-29")
        for status in ("current", "expired", "in_progress", "not_held", "unknown"):
            result = self.request("PUT", "profile", json={"career_background": {"qualifications": [{"name": "Q", "kind": "licence", "status": status}]}})
            self.assertEqual(result.json()["career_background"]["qualifications"][0]["status"], status)

    def test_malformed_stored_background_is_not_silently_cleared(self):
        row = self.supabase.add("candidate_context", career_background={"qualifications": [{"name": "Licence", "kind": "licence", "verified": True}]})
        original = copy.deepcopy(row)
        self.assertEqual(self.request("GET", "bootstrap").status_code, 502)
        job = self.supabase.job()
        self.assertEqual(self.request("POST", "jobs/" + job["id"] + "/rank", json={}).status_code, 502)
        self.studio.rank_job.assert_not_called()
        self.assertEqual(row, original)
        repaired = self.request("PUT", "profile", json={"career_background": None})
        self.assertEqual(repaired.status_code, 200, repaired.text)

    def test_prepare_supports_neutral_default_career_change_and_legacy_choices(self):
        job, resume = self.supabase.job(title="Marketing Manager"), self.supabase.resume()
        for variant in (None, "role_aligned", "career_change", "sse", "fde"):
            with self.subTest(variant=variant):
                body = {"resume_id": resume["id"]}
                if variant:
                    body["variant"] = variant
                response = self.request("POST", "jobs/" + job["id"] + "/prepare", json=body)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(set(response.json()), {"artifacts", "questions"})
                self.assertEqual(response.json()["questions"], [])
                self.assertEqual(self.studio.prepare_documents.call_args.args[1], variant or "role_aligned")
        calls = self.studio.prepare_documents.call_count
        for variant in ("healthcare", "", None, 42):
            self.assertEqual(self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"], "variant": variant}).status_code, 422)
        self.assertEqual(self.studio.prepare_documents.call_count, calls)

    def test_prepare_questions_reuse_answered_rows_without_reopening(self):
        job, resume = self.supabase.job(), self.supabase.resume()
        old = self.supabase.add("mobile_questions", job_id=job["id"], prompt="What is your licence status?", answer="Not held", status="answered", remember=False)
        original = copy.deepcopy(old)
        documents = self.studio.prepare_documents.return_value
        documents.questions = ["  WHAT is your licence status? ", "What is your licence status", "Which jurisdiction?"]
        response = self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()["questions"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], original)
        self.assertEqual(rows[1]["status"], "pending")
        self.assertEqual(old, original)
        self.assertEqual(len(self.supabase.tables["mobile_questions"]), 2)
        self.assertEqual(self.supabase.tables["model_runs"][0]["output_summary"]["question_ids"], [row["id"] for row in rows])

    def test_rank_questions_are_saved_while_opportunity_response_stays_unchanged(self):
        job = self.supabase.job(title="Staff Nurse")
        result = AuditDict(score=5.0, recommendation="review", rationale="Eligibility is unknown, so review rather than exclude.")
        result.questions = ["Please confirm your registration status."]
        self.studio.rank_job.return_value = result
        response = self.request("POST", "jobs/" + job["id"] + "/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], job["id"])
        self.assertEqual(response.json()["status"], "new")
        self.assertEqual(response.json()["score"], 5.0)
        self.assertNotIn("questions", response.json())
        questions = self.request("GET", "bootstrap").json()["questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["job_id"], job["id"])
        self.assertEqual(questions[0]["status"], "pending")
        self.assertEqual(self.supabase.tables["model_runs"][0]["output_summary"]["question_ids"], [questions[0]["id"]])

    def test_missing_facts_422_has_persisted_question_rows_and_no_success(self):
        for operation in ("prepare", "rank"):
            with self.subTest(operation=operation):
                job, resume = self.supabase.job(), self.supabase.resume()
                adapter = self.studio.prepare_documents if operation == "prepare" else self.studio.rank_job
                adapter.side_effect = FakeMissingFactsError(["Confirm your training.", "Confirm your training."])
                response = self.request("POST", "jobs/" + job["id"] + "/" + operation, json={"resume_id": resume["id"]})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(set(response.json()), {"detail"})
                detail = response.json()["detail"]
                self.assertEqual(set(detail), {"message", "questions"})
                self.assertTrue(detail["message"])
                self.assertNotIn("Private studio", response.text)
                self.assertEqual(len(detail["questions"]), 1)
                question = detail["questions"][0]
                self.assertEqual(question["user_id"], USER_A)
                self.assertEqual(question["job_id"], job["id"])
                self.assertEqual(question["status"], "pending")
                self.assertIn(question, self.supabase.tables["mobile_questions"])
                run = self.supabase.tables["model_runs"][-1]
                self.assertEqual(run["status"], "failed")
                self.assertEqual(run["output_summary"], {"needs_user": True, "question_ids": [question["id"]]})
                self.assertEqual(self.supabase.tables["artifacts"], [])
                self.assertEqual(self.supabase.tables["job_scores"], [])
                self.assertFalse(any(bucket == "application-artifacts" for bucket, _ in self.supabase.objects))

    def test_answer_without_remember_resolves_prepare_and_rank_next_attempt(self):
        job, resume = self.supabase.job(), self.supabase.resume()
        prompt = "Please confirm your qualification status."
        documents = self.studio.prepare_documents.return_value
        def prepare(context, variant):
            if not context["answers"]:
                raise FakeMissingFactsError([prompt])
            return documents
        self.studio.prepare_documents.side_effect = prepare
        path = "jobs/" + job["id"] + "/prepare"
        response = self.request("POST", path, json={"resume_id": resume["id"]})
        self.assertEqual(response.status_code, 422, response.text)
        question = response.json()["detail"]["questions"][0]
        answer = self.request("POST", "questions/" + question["id"] + "/answer", json={"answer": "Training is in progress, no licence held.", "remember": False})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["status"], "answered")
        self.assertEqual(self.supabase.tables["mobile_answers"], [])
        self.assertEqual(self.request("POST", path, json={"resume_id": resume["id"]}).status_code, 200)
        self.assertEqual(self.request("POST", "jobs/" + job["id"] + "/rank", json={}).status_code, 200)
        for adapter in (self.studio.prepare_documents, self.studio.rank_job):
            context = adapter.call_args.args[0]
            self.assertEqual(len(context["answers"]), 1)
            self.assertEqual(context["answers"][0]["answer"], "Training is in progress, no licence held.")
            self.assertEqual(context["answers"][0]["scope"], "job:" + job["id"])
            self.assertTrue(context["answers"][0]["confirmed"])
        self.assertEqual(len(self.supabase.tables["mobile_questions"]), 1)
        self.assertFalse(self.supabase.tables["mobile_questions"][0]["remember"])

    def test_current_answers_are_owner_job_scoped_and_deduped_over_memory(self):
        job, other_job = self.supabase.job(), self.supabase.job()
        question = self.supabase.add("mobile_questions", job_id=job["id"], prompt="Licence status?", answer="Expired", status="answered", remember=True)
        self.supabase.add("mobile_answers", question="Licence status?", answer="Stale current claim", source_question_id=question["id"], scope="job:" + job["id"])
        self.supabase.add("mobile_answers", question="LICENCE STATUS", answer="Another stale duplicate", source_question_id=str(uuid4()), scope="job:" + job["id"])
        for owner, scope, answer in ((USER_A, other_job["id"], "OTHER JOB PRIVATE"), (USER_A, None, "PROFILE TRANSIENT"), (USER_B, job["id"], "OTHER OWNER PRIVATE")):
            self.supabase.add("mobile_questions", owner, job_id=scope, prompt="Unrelated?", answer=answer, status="answered", remember=False)
        self.supabase.add("mobile_questions", job_id=job["id"], prompt="Still pending?", answer="UNCONFIRMED", status="pending", remember=False)
        response = self.request("POST", "jobs/" + job["id"] + "/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        answers = self.studio.rank_job.call_args.args[0]["answers"]
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["answer"], "Expired")
        self.assertEqual(answers[0]["source_question_id"], question["id"])
        self.assertEqual(answers[0]["scope"], "job:" + job["id"])
        self.assertNotIn("PRIVATE", json.dumps(answers))
        self.assertNotIn("TRANSIENT", json.dumps(answers))
        # Profile-only chat may use its own unremembered answers, never job ones.
        self.assertEqual(self.request("POST", "chat", json={"message": "Help", "mode": "resume"}).status_code, 200)
        self.assertEqual([a["answer"] for a in self.studio.answer_chat.call_args.args[0]["answers"]], ["PROFILE TRANSIENT"])

    def test_question_dedupe_is_shared_by_prepare_rank_chat_but_not_jobs_or_owners(self):
        job, other = self.supabase.job(), self.supabase.job()
        resume = self.supabase.resume()
        prompt = "Confirm your training?"
        self.studio.prepare_documents.return_value.questions = [prompt]
        self.studio.rank_job.return_value.questions = ["CONFIRM  your training"]
        self.studio.answer_chat.return_value["questions"] = ["Confirm your training."]
        prepared = self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"]}).json()
        first_id = prepared["questions"][0]["id"]
        self.assertEqual(self.request("POST", "jobs/" + job["id"] + "/rank", json={}).status_code, 200)
        chatted = self.request("POST", "chat", json={"job_id": job["id"], "message": "Help", "mode": "application"})
        self.assertEqual(chatted.json()["questions"][0]["id"], first_id)
        self.assertEqual(self.request("POST", "jobs/" + other["id"] + "/rank", json={}).status_code, 200)
        self.assertEqual(self.request("POST", "chat", json={"message": "Help", "mode": "resume"}).status_code, 200)
        foreign = self.supabase.job(USER_B)
        self.assertEqual(self.request("POST", "jobs/" + foreign["id"] + "/rank", json={}, headers={"Authorization": "Bearer session-b"}).status_code, 200)
        rows = self.supabase.tables["mobile_questions"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({row["id"] for row in rows}), 4)
        self.assertEqual(len(self.request("GET", "bootstrap").json()["questions"]), 3)

    def test_question_pagination_finds_older_answered_legacy_id(self):
        job = self.supabase.job()
        for index in range(201):
            self.supabase.add("mobile_questions", job_id=job["id"], prompt="Other question " + str(index), status="pending")
        question = self.supabase.add("mobile_questions", id="ffffffff-ffff-4fff-bfff-ffffffffffff", job_id=job["id"], prompt="What is your training?", answer="Apprenticeship", status="answered", remember=False)
        self.studio.rank_job.return_value.questions = ["WHAT is your training"]
        response = self.request("POST", "jobs/" + job["id"] + "/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.supabase.tables["mobile_questions"]), 202)
        self.assertEqual(self.supabase.tables["model_runs"][0]["output_summary"]["question_ids"], [question["id"]])
        self.assertTrue(any(req.url.params.get("offset") == "200" for req in self.supabase.requests))

    def test_question_insert_race_reads_existing_row_without_reopening(self):
        job = self.supabase.job()
        def race(req):
            if req.method == "POST" and req.url.path.endswith("/mobile_questions"):
                data = json.loads(req.content)
                owner = data.pop("user_id")
                self.supabase.add("mobile_questions", owner, **{**data, "answer": "Training confirmed", "status": "answered"})
                return httpx.Response(409)
        self.supabase.fault = race
        self.studio.rank_job.return_value.questions = ["Training?"]
        response = self.request("POST", "jobs/" + job["id"] + "/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.supabase.tables["mobile_questions"]), 1)
        self.assertEqual(self.supabase.tables["mobile_questions"][0]["status"], "answered")
        self.assertFalse(any(req.method == "PATCH" and req.url.path.endswith("/mobile_questions") for req in self.supabase.requests))

    def test_invalid_studio_questions_fail_before_artifact_or_score_writes(self):
        job, resume = self.supabase.job(), self.supabase.resume()
        for invalid in (None, "One question", [42], [" "], ["?"], ["x" * 2001], ["Question"] * 9):
            for operation, adapter in (("prepare", self.studio.prepare_documents), ("rank", self.studio.rank_job)):
                with self.subTest(operation=operation, question_type=type(invalid).__name__):
                    adapter.return_value.questions = invalid
                    response = self.request("POST", "jobs/" + job["id"] + "/" + operation, json={"resume_id": resume["id"]})
                    self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.supabase.tables["artifacts"], [])
        self.assertEqual(self.supabase.tables["job_scores"], [])
        self.assertEqual(self.supabase.tables["mobile_questions"], [])
        self.assertTrue(all(run["status"] == "failed" for run in self.supabase.tables["model_runs"]))

    def test_missing_facts_question_save_failure_is_not_fake_422_success(self):
        job, resume = self.supabase.job(), self.supabase.resume()
        self.studio.prepare_documents.side_effect = FakeMissingFactsError(["Confirm your training."])
        self.supabase.fault = lambda req: httpx.Response(500, text="Private upstream") if req.method == "POST" and req.url.path.endswith("/mobile_questions") else None
        response = self.request("POST", "jobs/" + job["id"] + "/prepare", json={"resume_id": resume["id"]})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn("Private", response.text)
        self.assertEqual(self.supabase.tables["mobile_questions"], [])
        self.assertEqual(self.supabase.tables["artifacts"], [])
        self.assertEqual(self.supabase.tables["model_runs"][0]["status"], "failed")

    def test_later_audit_failure_never_deletes_reused_answered_questions(self):
        job = self.supabase.job()
        old = self.supabase.add("mobile_questions", job_id=job["id"], prompt="When can you start?", answer="Next month", status="answered", remember=False)
        original = copy.deepcopy(old)
        self.supabase.fault = lambda req: httpx.Response(500) if req.method == "PATCH" and req.url.path.endswith("/model_runs") else None
        response = self.request("POST", "chat", json={"job_id": job["id"], "message": "Help", "mode": "application"})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.supabase.tables["mobile_questions"], [original])
        self.assertFalse(any(req.method == "DELETE" and req.url.path.endswith("/mobile_questions") for req in self.supabase.requests))

    def test_long_credential_chat_preserves_complete_facts_with_bounded_output(self):
        job = self.supabase.job(title="Staff Nurse")
        note = "I completed supervised training. " * 60
        background = {"profession": "Nursing", "qualifications": [{
            "name": "Self-reported training qualification",
            "kind": "certification", "status": "in_progress", "jurisdiction": "Example jurisdiction",
            "expires_on": None, "evidence_note": note,
        }]}
        self.assertEqual(self.request("PUT", "profile", json={"career_background": background}).status_code, 200)
        # A full fact includes all status/jurisdiction/note text. Never truncate a
        # long fact to satisfy transport limits and accidentally imply it is held.
        fact = "Self-reported training qualification; kind: certification; status: in_progress; jurisdiction: Example jurisdiction; expiry: unknown; evidence note: " + note
        evidence = ("career_background.qualifications.0: " + fact).strip()
        self.assertGreater(len(evidence), 2000)
        reply = (fact + "\n") * 12
        self.assertGreater(len(reply), 20000)
        self.studio.answer_chat.return_value = AuditDict(reply=reply, evidence=[evidence], questions=[])
        response = self.request("POST", "chat", json={"job_id": job["id"], "message": "Help me review this qualification", "mode": "resume"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"reply": reply.strip(), "evidence": [evidence], "questions": []})
        self.assertEqual(self.studio.answer_chat.call_args.args[0]["career_background"]["qualifications"][0]["evidence_note"], note.strip())
        # Test exact transport edges, then each independent bound.
        self.studio.answer_chat.return_value = {"reply": "r" * 40000, "evidence": ["e" * 4000] * 30, "questions": []}
        self.assertEqual(self.request("POST", "chat", json={"message": "Help", "mode": "resume"}).status_code, 200)
        for output in (
            {"reply": "r" * 40001, "evidence": [], "questions": []},
            {"reply": "Ready", "evidence": ["e" * 4001], "questions": []},
            {"reply": "Ready", "evidence": ["Evidence"] * 31, "questions": []},
        ):
            self.studio.answer_chat.return_value = output
            response = self.request("POST", "chat", json={"message": "Help", "mode": "resume"})
            self.assertEqual(response.status_code, 502, response.text)

    def test_question_scope_metadata_mismatch_fails_closed(self):
        job, other = self.supabase.job(), self.supabase.job()
        self.studio.rank_job.return_value.questions = ["Training?"]
        def wrong_scope(req):
            if req.method == "GET" and req.url.path.endswith("/mobile_questions") and "offset" in req.url.params:
                return httpx.Response(200, json=[{"id": str(uuid4()), "user_id": USER_A, "job_id": other["id"], "prompt": "Training?", "status": "answered"}])
        self.supabase.fault = wrong_scope
        response = self.request("POST", "jobs/" + job["id"] + "/rank", json={})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.supabase.tables["job_scores"], [])

    def test_background_migration_is_additive_bounded_and_does_not_relax_rls(self):
        sql = (Path(__file__).resolve().parents[1] / "supabase/migrations/0003_profession_neutral_background.sql").read_text()
        executable = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--")).lower()
        self.assertNotRegex(executable, r"\b(?:create|alter|drop)\s+policy\b|\bdisable\s+row\s+level\s+security\b|\bsecurity\s+definer\b")
        self.assertNotRegex(executable, r"\b(?:delete|truncate|drop)\b")
        self.assertIn("set search_path = pg_catalog", executable)
        self.assertIn("add column career_background jsonb not null", executable)
        self.assertIn("check (public.mobile_career_background_is_valid(career_background))", executable)
        self.assertIn("grant insert (career_background) on public.candidate_context to authenticated", executable)
        self.assertIn("grant update (career_background) on public.candidate_context to authenticated", executable)
        self.assertIn("revoke all on function public.mobile_career_background_is_valid(jsonb) from public", executable)
        for fragment in ("octet_length(background::text) > 300000", "jsonb_array_length(background->'qualifications') > 30",
                         "char_length(background->>'profession') > 120", "char_length(qualification->>'name') > 160",
                         "char_length(qualification->>'jurisdiction') > 160", "char_length(qualification->>'evidence_note') > 2000",
                         "qualification ?& array['name', 'kind']", "invalid_datetime_format or datetime_field_overflow",
                         "'education', 'licence', 'certification', 'other'", "'current', 'expired', 'in_progress', 'not_held', 'unknown'"):
            self.assertIn(fragment, executable)

    def test_repository_columns_and_write_grants_match_actual_sql(self):
        root = Path(__file__).resolve().parents[1]
        sql = "\n".join((root / "supabase" / "migrations" / filename).read_text() for filename in ("0001_beta_multi_tenant.sql", "0002_mobile_career_workspace.sql", "0003_profession_neutral_background.sql", "0005_mobile_resume_operations.sql", "0006_mobile_artifact_operations.sql"))
        tables = {}
        for match in re.finditer(r"create table(?: if not exists)? public\.(\w+)\s*\((.*?)\n\);", sql, re.S):
            tables[match[1]] = set(re.findall(r"^  (\w+)\s+(?:uuid|text|numeric|boolean|bigint|timestamptz|jsonb)\b", match[2], re.M))
        for table, column in re.findall(r"alter table public\.(\w+)\s+add column (\w+) jsonb", sql):
            tables[table].add(column)
        for table, columns in COLUMNS.items():
            with self.subTest(table=table):
                self.assertIn(table, tables)
                self.assertFalse(set(columns.split(",")) - tables[table])
        grants = {}
        for columns, table in re.findall(r"grant update\s*\(([^)]+)\)\s*on public\.(\w+) to authenticated", sql):
            grants.setdefault(table, set()).update(column.strip() for column in columns.split(","))
        self.assertEqual(grants["candidate_context"], {"career_text", "career_background"})
        self.assertEqual(grants["mobile_answers"], {"question", "answer", "scope", "source_question_id"})
        self.assertNotIn("user_id", grants["mobile_questions"])


if __name__ == "__main__":
    unittest.main()
