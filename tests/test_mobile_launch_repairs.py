"""Positive acceptance for repaired launch boundaries; all transport is fake."""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch
import httpx

from jobagent.mobile import studio
from jobagent.mobile.eligibility_review import REVIEW_QUESTION
from jobagent.mobile.selections import selection_schema, materialize_selection
from test_mobile_journey import Journey, DeterministicProvider, JOB
from test_mobile_api import USER_A


class MobileLaunchRepairTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.provider = DeterministicProvider()
        self.journey = self.stack.enter_context(Journey(lambda: self.provider, live=False))

    def test_explicit_eligibility_resolves_and_edit_invalidates_review(self):
        self.journey.setup_inputs()
        job_id = self.journey.job["id"]
        path = f"jobs/{job_id}"
        first = self.journey.request("POST", path + "/rank", json={})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "new")
        response = self.journey.request("POST", path + "/eligibility", json={
            "status": "eligible", "reason": "I hold current work permission for this exact location.", "confirmed": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["eligibility_review"]["provenance"], "user_self_report")
        self.assertFalse(response.json()["eligibility_review"]["independently_verified"])
        self.journey.supabase.tables["job_scores"][0]["created_at"] = "2026-08-01T00:00:00Z"
        ranked = self.journey.request("POST", path + "/rank", json={})
        self.assertEqual(ranked.status_code, 200, ranked.text)
        self.assertEqual(ranked.json()["status"], "matched")
        self.assertIn("self-report", ranked.json()["rationale"])
        changed = self.journey.request("PATCH", path, json={"location_text": "Toronto"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["status"], "new")
        detail = self.journey.request("GET", path).json()
        self.assertEqual(detail["eligibility_status"], "unknown")
        self.assertIsNone(detail["eligibility_review"])
        self.assertEqual(len(self.journey.supabase.tables["job_scores"]), 3)  # history retained

    def test_review_cannot_be_implicit_or_apply_to_another_job_or_user(self):
        self.journey.setup_inputs()
        path = "jobs/" + self.journey.job["id"] + "/eligibility"
        for body in ({"status": "eligible", "reason": "yes"},
                     {"status": "eligible", "reason": "yes", "confirmed": False},
                     {"status": "eligible", "reason": "", "confirmed": True}):
            self.assertEqual(self.journey.request("POST", path, json=body).status_code, 422)
        body = {"status": "eligible", "reason": "Explicit fixture declaration.", "confirmed": True}
        self.assertEqual(self.journey.request("POST", path, json=body, actor="b").status_code, 404)

    def test_legacy_bookmark_enriched_and_tracking_url_deduplicated(self):
        bookmark = self.journey.supabase.job(**JOB)
        bookmark["description"] = None
        self.journey.supabase.tables["jobs"][0]["description"] = None
        response = self.journey.request("POST", "jobs", json={**JOB, "source_url": JOB["source_url"] + "?utm_source=mail#apply"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["duplicate"])
        self.assertEqual(response.json()["description"], JOB["description"])
        self.assertEqual(len(self.journey.supabase.tables["jobs"]), 1)

    def test_bootstrap_summaries_exclude_large_descriptions_detail_preserves_them(self):
        for index in range(110):
            self.journey.supabase.job(description="x" * 80_000, source_url=f"https://example.test/job/{index}")
        response = self.journey.request("GET", "bootstrap")
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(len(response.json()["jobs"]), 110)
        self.assertTrue(all(row["description"] is None for row in response.json()["jobs"]))
        row_id = response.json()["jobs"][0]["id"]
        detail = self.journey.request("GET", "jobs/" + row_id)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["description"]), 80_000)

    def test_prose_schema_never_offers_contacts_or_internal_metadata(self):
        context = {"profile": {"display_name": "Alex", "email": "alex@example.test"},
                   "career_text": "Built reliable reporting tools.",
                   "career_background": {"profession": "Backend Engineer", "experience_level": "mid", "qualifications": []},
                   "job": JOB, "preferences": {}, "answers": [], "resume_text": ""}
        payload, facts = studio._context(context)
        for operation, model in (("rank", studio._RankOutput), ("documents", studio._DocumentOutput)):
            payload["operation"] = operation
            schema = selection_schema(model.model_json_schema(), payload)
            allowed = schema["$defs"]["SelectedFactID"]["enum"]
            for ref in ("profile.email", "career_background.profession", "career_background.experience_level"):
                self.assertNotIn(ref, allowed)
                with self.assertRaises(ValueError):
                    materialize_selection(json.dumps({"claims": [{"source_ids": [ref]}]}), payload, model.model_json_schema())
            # Every offered ID is valid when materialized, not merely known.
            ledger = {fact["id"]: fact for fact in facts}
            for ref in allowed:
                studio._validate_claims([studio._Claim(text=ledger[ref]["text"], source_ids=[ref])], facts, candidate_only=operation == "documents")

    def test_saved_rank_survives_activity_log_failure_without_false_failure(self):
        self.journey.setup_inputs()
        def fail_log(request):
            if request.method == "PATCH" and request.url.path == "/rest/v1/model_runs" and json.loads(request.content).get("status") == "succeeded":
                return httpx.Response(500, text="Synthetic unavailable activity log")
        self.journey.supabase.fault = fail_log
        response = self.journey.request("POST", f"jobs/{self.journey.job['id']}/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["operation_status"], "saved_sync_pending")
        self.assertTrue(response.json()["warnings"])
        self.assertEqual(len(self.journey.supabase.tables["job_scores"]), 1)
        self.assertEqual(self.provider.operations, ["rank"])
        self.assertNotEqual(self.journey.supabase.tables["model_runs"][0]["status"], "failed")

    def test_rank_reconciles_lost_insert_ack_without_second_score_or_ai_call(self):
        self.journey.setup_inputs()
        def lose_ack(request):
            if request.method == "POST" and request.url.path == "/rest/v1/job_scores":
                self.journey.supabase.add("job_scores", **json.loads(request.content))
                return httpx.Response(504)
        self.journey.supabase.fault = lose_ack
        response = self.journey.request("POST", f"jobs/{self.journey.job['id']}/rank", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["operation_status"], "saved_sync_pending")
        self.assertEqual(len(self.journey.supabase.tables["job_scores"]), 1)
        self.assertEqual(self.provider.operations, ["rank"])
        self.assertNotEqual(self.journey.supabase.tables["model_runs"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
