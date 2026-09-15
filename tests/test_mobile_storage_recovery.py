"""Synthetic storage saga fault matrix. No sockets, paid calls or founder data.

Run with PYTHONPATH=src:tests and the existing .venv. SQL checks inspect migration
text only; the fake models owner-scoped REST/Storage, not a deployed database.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from jobagent.mobile.app import create_app
from jobagent.mobile.repository import MobileRepository
from jobagent.mobile.artifact_storage import persist_artifact, recover_artifact, artifact_operations
from test_launch_privacy_probe import OfflineCase, CommitThenTimeoutSupabase
from test_mobile_api import SETTINGS, USER_A, USER_B, docx_bytes, fake_studio


class StorageRecoveryTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.remote = CommitThenTimeoutSupabase()
        self.studio = fake_studio()
        self.source = docx_bytes()
        self.make_client()

    def make_client(self):
        self.app = create_app(settings=SETTINGS, transport=httpx.MockTransport(self.remote), studio=self.studio)
        self.client = self.stack.enter_context(TestClient(self.app, raise_server_exceptions=False))
        self.client.headers["Authorization"] = "Bearer session-a"

    def upload(self, *, key=None, content=None, **changes):
        body = {"filename": "resume.docx", "label": "Synthetic storage test", "content_base64": base64.b64encode(content or self.source).decode(), **changes}
        return self.client.post("/api/mobile/resumes", json=body, headers={"Idempotency-Key": key} if key else {})

    def recover(self, item_id):
        return self.client.post(f"/api/mobile/resumes/{item_id}/recover")

    def delete(self, item_id):
        return self.client.delete(f"/api/mobile/resumes/{item_id}")

    def test_missing_journal_fails_before_storage_mutation(self):
        self.remote.fault = lambda req: httpx.Response(404) if "mobile_resume_operations" in req.url.path else None
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.remote.objects, {})
        self.assertFalse(any(req.url.path.startswith("/storage/") for req in self.remote.requests))

    def test_journal_insert_before_commit_failure_changes_no_bytes(self):
        self.remote.fault = lambda req: httpx.Response(503) if req.method == "POST" and req.url.path.endswith("mobile_resume_operations") else None
        self.assertEqual(self.upload().status_code, 503)
        self.assertEqual(self.remote.objects, {})
        self.remote.fault = None
        self.assertEqual(self.upload().status_code, 201)

    def test_journal_insert_and_finalize_ack_loss_are_reconciled(self):
        self.remote.after_commit = lambda req: req.url.path.endswith("mobile_resume_operations") and req.method in {"POST", "PATCH"}
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(self.remote.tables["mobile_resume_operations"][0]["state"], "ready")

    def test_upload_storage_unavailable_has_truthful_missing_copy_error_and_retry(self):
        self.remote.fault = lambda req: httpx.Response(503) if req.method == "POST" and req.url.path.startswith("/storage/") else None
        response = self.upload()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "resume_upload_bytes_required")
        self.assertEqual(self.remote.objects, {})
        self.assertEqual(len(self.remote.tables["mobile_resume_operations"]), 1)
        self.remote.fault = None
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(len(self.remote.tables["mobile_resume_operations"]), 1)

    def test_unknown_metadata_commit_preserves_bytes_across_new_app_then_recovers(self):
        self.remote.after_commit = lambda req: req.method == "POST" and req.url.path == "/rest/v1/resumes"
        self.remote.fault = lambda req: httpx.Response(503) if req.method == "GET" and req.url.path == "/rest/v1/resumes" and self.remote.tables["resumes"] else None
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        item_id = self.remote.tables["mobile_resume_operations"][0]["id"]
        self.assertEqual(len(self.remote.objects), 1)
        self.assertFalse(any(req.method == "DELETE" for req in self.remote.requests))
        self.remote.after_commit = self.remote.fault = None
        self.make_client()  # all server process-local state is discarded
        self.assertEqual(self.recover(item_id).status_code, 200)
        self.assertEqual(self.remote.tables["mobile_resume_operations"][0]["state"], "ready")
        self.studio.prepare_documents.assert_not_called()
        self.studio.rank_job.assert_not_called()

    def test_replay_is_stable_and_new_key_conflicts_cannot_overwrite(self):
        key = str(uuid4())
        first = self.upload(key=key)
        self.assertEqual(first.status_code, 201)
        second = self.upload(key=key)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["id"], second.json()["id"])
        before = dict(self.remote.objects)
        self.assertEqual(self.upload(key=key, label="Changed intent").status_code, 409)
        self.assertEqual(self.upload(key=key, content=docx_bytes({"extra.xml": "different"})).status_code, 409)
        self.assertEqual(self.remote.objects, before)
        self.assertEqual(len(self.remote.tables["resumes"]), 1)
        self.assertEqual(self.upload(key="not-a-uuid").status_code, 422)

    def test_foreign_recovery_and_operation_listing_never_expose_owner_rows(self):
        self.remote.fault = lambda req: httpx.Response(503) if req.method == "POST" and req.url.path == "/rest/v1/resumes" else None
        self.upload()
        item_id = self.remote.tables["mobile_resume_operations"][0]["id"]
        self.client.headers["Authorization"] = "Bearer session-b"
        self.assertEqual(self.recover(item_id).status_code, 404)
        self.assertEqual(self.client.get("/api/mobile/resume-operations").json(), [])
        self.assertEqual(len(self.remote.objects), 1)

    def test_corrupt_existing_bytes_not_overwritten_or_deleted(self):
        self.remote.fault = lambda req: httpx.Response(503) if req.method == "POST" and req.url.path == "/rest/v1/resumes" else None
        self.upload()
        item_id = self.remote.tables["mobile_resume_operations"][0]["id"]
        path = next(iter(self.remote.objects))
        self.remote.objects[path] = b"Different synthetic bytes"
        self.remote.fault = None
        self.assertEqual(self.recover(item_id).status_code, 409)
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(self.remote.objects[path], b"Different synthetic bytes")

    def test_failed_deletion_then_retry_never_requires_reupload(self):
        row = self.upload().json()
        original = dict(self.remote.objects)
        self.remote.fault = lambda req: httpx.Response(500) if req.method == "DELETE" and req.url.path == "/rest/v1/resumes" else None
        response = self.delete(row["id"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.remote.objects, original)
        self.assertEqual(self.remote.tables["mobile_resume_operations"][0]["state"], "delete_pending")
        self.remote.fault = None
        self.make_client()
        self.assertEqual(self.delete(row["id"]).status_code, 200)
        self.assertEqual(self.remote.objects, {})
        self.assertEqual(self.remote.tables["resumes"], [])

    def test_storage_delete_failure_retains_discoverable_intent_and_retries(self):
        row = self.upload().json()
        self.remote.fault = lambda req: httpx.Response(500) if req.method == "DELETE" and req.url.path.startswith("/storage/") else None
        response = self.delete(row["id"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.remote.tables["resumes"], [])
        self.assertEqual(len(self.remote.objects), 1)
        self.assertEqual(self.client.get("/api/mobile/resume-operations").json()[0]["state"], "delete_pending")
        self.remote.fault = None
        self.make_client()
        self.assertEqual(self.recover(row["id"]).status_code, 200)
        self.assertEqual(self.remote.objects, {})

    def test_delete_ack_loss_on_each_service_reconciles_and_repeats(self):
        row = self.upload().json()
        self.remote.after_commit = lambda req: req.method in {"DELETE", "PATCH"}
        self.assertEqual(self.delete(row["id"]).status_code, 200)
        self.assertEqual(self.delete(row["id"]).status_code, 200)
        self.assertEqual(self.remote.objects, {})
        self.assertEqual(self.remote.tables["mobile_resume_operations"][0]["state"], "deleted")

    def test_delete_unknown_metadata_ack_never_touches_bytes(self):
        row = self.upload().json()
        self.remote.after_commit = lambda req: req.method == "DELETE" and req.url.path == "/rest/v1/resumes"
        self.remote.fault = lambda req: httpx.Response(503) if req.method == "GET" and req.url.path == "/rest/v1/resumes" and not self.remote.tables["resumes"] else None
        self.assertEqual(self.delete(row["id"]).status_code, 503)
        self.assertEqual(len(self.remote.objects), 1)
        self.remote.fault = self.remote.after_commit = None
        self.make_client()
        self.assertEqual(self.delete(row["id"]).status_code, 200)

    def test_deleted_upload_key_is_terminal_new_key_allows_intentional_reupload(self):
        key = str(uuid4())
        row = self.upload(key=key).json()
        self.assertEqual(self.delete(row["id"]).status_code, 200)
        self.assertEqual(self.upload(key=key).status_code, 409)
        self.assertEqual(self.upload(key=str(uuid4())).status_code, 201)

    def test_legacy_resume_with_null_mime_is_still_deletable(self):
        row = self.remote.resume(mime_type=None)
        self.assertEqual(self.delete(row["id"]).status_code, 200)
        self.assertNotIn(("resumes", row["storage_path"]), self.remote.objects)

    def test_storage_upload_and_delete_throttling_preserve_retry_guidance(self):
        self.remote.fault = lambda req: httpx.Response(429, headers={"Retry-After": "37"}) if req.method == "POST" and req.url.path.startswith("/storage/") else None
        response = self.upload()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "37")
        self.remote.fault = None
        row = self.upload().json()
        self.remote.fault = lambda req: httpx.Response(429, headers={"Retry-After": "37"}) if req.method == "DELETE" and req.url.path.startswith("/storage/") else None
        response = self.delete(row["id"])
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "37")
        self.assertEqual(len(self.remote.objects), 1)

    def test_recovery_storage_reads_bypass_cache(self):
        self.upload()
        reads = [req for req in self.remote.requests if req.method == "GET" and req.url.path.startswith("/storage/")]
        self.assertTrue(reads)
        self.assertTrue(all(req.url.params.get("reconcile_nonce") and "no-cache" in req.headers["cache-control"] for req in reads))
        self.assertEqual(len({req.url.params["reconcile_nonce"] for req in reads}), len(reads))

    def test_non_missing_400_and_permission_failures_do_not_authorize_delete(self):
        for status, payload in ((400, {"statusCode": "404", "code": "AccessDenied"}), (403, {"statusCode": "404", "code": "NoSuchKey"})):
            with self.subTest(status=status):
                row = self.remote.resume()
                self.remote.fault = lambda req: httpx.Response(status, json=payload) if req.method == "GET" and req.url.path.startswith("/storage/") else None
                response = self.delete(row["id"])
                self.assertIn(response.status_code, (403, 503))
                self.assertIn(("resumes", row["storage_path"]), self.remote.objects)
                self.remote.fault = None

    def test_retry_after_is_bounded_and_untrusted_headers_not_forwarded(self):
        for raw, expected in (("999999999", "3600"), ("0", "1"), ("garbage", "60"), ("Thu, 01 Jan 2099 00:00:00 GMT", "60")):
            self.remote.fault = lambda req: httpx.Response(429, headers={"Retry-After": raw, "Secret-Diagnostic": "not forwarded"})
            response = self.client.get("/api/mobile/bootstrap")
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], expected)
            self.assertNotIn("secret-diagnostic", response.headers)


class ArtifactRecoveryTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.remote = CommitThenTimeoutSupabase()
        item_id = str(uuid4())
        self.content = b"Synthetic confirmed artifact content"
        self.metadata = {"id": item_id, "job_id": self.remote.job()["id"], "resume_id": self.remote.resume()["id"],
                         "kind": "tailored_resume", "filename": "resume.txt", "mime_type": "text/plain", "byte_size": len(self.content),
                         "storage_path": USER_A + "/" + item_id + ".txt", "model_provider": "fixture", "model_name": "offline"}

    def call(self, function, **kwargs):
        async def invoke():
            async with httpx.AsyncClient(base_url=SETTINGS.url, headers={"apikey": SETTINGS.publishable_key, "Authorization": "Bearer session-a"}, transport=httpx.MockTransport(self.remote)) as client:
                return await function(MobileRepository(client, USER_A), **kwargs)
        return asyncio.run(invoke())

    def persist(self):
        return self.call(persist_artifact, metadata=self.metadata, content=self.content)

    def test_lost_artifact_storage_and_metadata_acks_preserve_bytes(self):
        self.remote.after_commit = lambda req: req.method == "POST"
        row = self.persist()
        self.assertEqual(row["id"], self.metadata["id"])
        self.assertEqual(self.remote.objects["application-artifacts", self.metadata["storage_path"]], self.content)
        self.assertEqual(self.remote.tables["mobile_artifact_operations"][0]["state"], "ready")
        self.assertFalse(any(req.method == "DELETE" for req in self.remote.requests))

    def test_missing_artifact_journal_never_writes_bytes(self):
        original = dict(self.remote.objects)
        self.remote.fault = lambda req: httpx.Response(404) if req.url.path.endswith("mobile_artifact_operations") else None
        with self.assertRaises(HTTPException) as caught:
            self.persist()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.remote.objects, original)
        self.assertFalse(any(req.url.path.startswith("/storage/") for req in self.remote.requests))

    def test_artifact_missing_bytes_requires_original_content_not_fake_recovery(self):
        self.remote.fault = lambda req: httpx.Response(503) if req.method == "POST" and req.url.path.startswith("/storage/") else None
        for action in (self.persist, lambda: self.call(recover_artifact, artifact_id=self.metadata["id"])):
            with self.assertRaises(HTTPException) as caught:
                action()
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(caught.exception.detail["code"], "artifact_upload_bytes_required")
        self.assertEqual(self.remote.tables["artifacts"], [])
        self.assertEqual(self.call(artifact_operations)[0]["id"], self.metadata["id"])
        self.remote.fault = None
        self.assertEqual(self.persist()["id"], self.metadata["id"])

    def test_artifact_unknown_metadata_commit_preserves_bytes_until_recovery(self):
        self.remote.after_commit = lambda req: req.method == "POST" and req.url.path == "/rest/v1/artifacts"
        self.remote.fault = lambda req: httpx.Response(503) if req.method == "GET" and req.url.path == "/rest/v1/artifacts" and self.remote.tables["artifacts"] else None
        with self.assertRaises(HTTPException) as caught:
            self.persist()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(len(self.remote.tables["artifacts"]), 1)
        self.assertEqual(self.remote.objects["application-artifacts", self.metadata["storage_path"]], self.content)
        self.assertFalse(any(req.method == "DELETE" for req in self.remote.requests))
        self.remote.after_commit = self.remote.fault = None
        self.assertEqual(self.call(recover_artifact, artifact_id=self.metadata["id"])["id"], self.metadata["id"])
        self.assertEqual(len(self.remote.tables["artifacts"]), 1)

    def test_artifact_corrupt_bytes_are_not_overwritten_or_deleted(self):
        self.persist()
        path = ("application-artifacts", self.metadata["storage_path"])
        self.remote.objects[path] = b"Changed synthetic bytes"
        for action in (self.persist, lambda: self.call(recover_artifact, artifact_id=self.metadata["id"])):
            with self.assertRaises(HTTPException) as caught:
                action()
            self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.remote.objects[path], b"Changed synthetic bytes")
        self.assertFalse(any(req.method == "DELETE" for req in self.remote.requests))

    def test_artifact_foreign_path_refused_before_journal_or_storage_mutation(self):
        self.metadata["storage_path"] = USER_B + "/" + self.metadata["id"] + ".txt"
        original = dict(self.remote.objects)
        with self.assertRaises(HTTPException) as caught:
            self.persist()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.remote.objects, original)
        self.assertFalse(any(req.method in {"POST", "PATCH", "DELETE"} for req in self.remote.requests))

    def test_metadata_failure_recovery_without_provider_or_original_content(self):
        self.remote.fault = lambda req: httpx.Response(500) if req.method == "POST" and req.url.path == "/rest/v1/artifacts" else None
        with self.assertRaises(HTTPException) as caught:
            self.persist()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail["operation_id"], self.metadata["id"])
        self.assertEqual(self.call(artifact_operations)[0]["id"], self.metadata["id"])
        self.remote.fault = None
        recovered = self.call(recover_artifact, artifact_id=self.metadata["id"])
        self.assertEqual(recovered["id"], self.metadata["id"])
        self.assertEqual(self.remote.objects["application-artifacts", self.metadata["storage_path"]], self.content)

    def test_artifact_ready_transition_failure_keeps_committed_row_and_bytes(self):
        self.remote.fault = lambda req: httpx.Response(500) if req.method == "PATCH" and req.url.path.endswith("mobile_artifact_operations") else None
        with self.assertRaises(HTTPException) as caught:
            self.persist()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(len(self.remote.tables["artifacts"]), 1)
        self.assertIn(("application-artifacts", self.metadata["storage_path"]), self.remote.objects)
        self.remote.fault = None
        self.assertEqual(self.call(recover_artifact, artifact_id=self.metadata["id"])["id"], self.metadata["id"])

    def test_pending_artifact_recovers_after_source_resume_is_deleted(self):
        self.remote.fault = lambda req: httpx.Response(500) if req.method == "POST" and req.url.path == "/rest/v1/artifacts" else None
        with self.assertRaises(HTTPException):
            self.persist()
        self.remote.tables["resumes"] = []
        self.remote.fault = None
        recovered = self.call(recover_artifact, artifact_id=self.metadata["id"])
        self.assertIsNone(recovered["resume_id"])
        self.assertEqual(self.remote.objects["application-artifacts", self.metadata["storage_path"]], self.content)

    def test_artifact_replay_same_id_is_idempotent_and_mismatch_refused(self):
        first = self.persist()
        self.assertEqual(self.persist()["id"], first["id"])
        self.assertEqual(len(self.remote.tables["artifacts"]), 1)
        self.content = b"Different content"
        self.metadata["byte_size"] = len(self.content)
        with self.assertRaises(HTTPException) as caught:
            self.persist()
        self.assertEqual(caught.exception.status_code, 409)

    def test_migration_has_owner_policies_immutable_intents_no_seed_or_delete(self):
        root = Path(__file__).resolve().parents[1] / "supabase/migrations"
        for filename, table in (("0005_mobile_resume_operations.sql", "mobile_resume_operations"), ("0006_mobile_artifact_operations.sql", "mobile_artifact_operations")):
            sql = (root / filename).read_text().lower()
            self.assertIn(f"alter table public.{table} enable row level security", sql)
            self.assertIn("with check ((select auth.uid()) = user_id)", sql)
            self.assertIn("primary key (id, user_id)", sql)
            self.assertIn("grant update (state)", sql)
            self.assertNotIn("grant delete", sql)
            self.assertIn("octet_length", sql)
            self.assertIn("is distinct from", sql)
