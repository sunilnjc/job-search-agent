"""Positive offline acceptance for repaired launch boundaries A04/A06/A11.

Require description enrichment, explicit scoped eligibility self-report (never
implicit verification from free text), and the founder drafter's source field.
Historical observation/evidence files remain unchanged; these are new assertions
against the repaired implementation, not proof of hosted or browser behavior.

Run only this file:
  .venv/bin/python -B -m unittest discover -s tests -p test_launch_cross_client_probe.py -v

Journey(live=False) supplies FakeSupabase/MockTransport, blocks socket/DNS calls
and live provider construction, and injects the deterministic provider. All
candidate documents and persistence are synthetic and in memory. The founder
service is parsed as source only; it is never imported or executed.
"""
from __future__ import annotations

import ast
import copy
import sqlite3
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from jobagent.mobile import app as mobile_api
from jobagent.mobile.repository import COLUMNS
from test_mobile_journey import DeterministicProvider, JOB, Journey


ROOT = Path(__file__).resolve().parents[1]


class LaunchCrossClientAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.provider = DeterministicProvider()
        self.journey = self.stack.enter_context(
            Journey(lambda: self.provider, live=False)
        )

    def test_a04_web_description_field_and_duplicate_import_enrich_legacy_bookmark(self):
        """Explicit supplied text completes a bookmark without duplicating it."""
        # Source-level UI contract only, not browser execution. Legacy rows can
        # still lack description even though the repaired web form requires it.
        web_source = (ROOT / "web/src/beta/BetaApp.tsx").read_text()
        add_form = web_source.split("function AddJobForm(", 1)[1].split(
            "function ProfileSummary(", 1
        )[0]
        self.assertIn("Full job description", add_form)
        self.assertRegex(add_form, r'<textarea\b[^>]*\brequired\b[^>]*value=\{description\}')
        self.assertIn("setDescription(event.target.value)", add_form)
        self.assertIn("!description.trim()", add_form)
        self.assertIn('mobileRequest<BetaJob>(userId, "/jobs"', add_form)
        self.assertIn('method: "POST"', add_form)
        self.assertIn("description: description.trim()", add_form)
        bookmark = self.journey.supabase.job(
            source_url=JOB["source_url"], title=JOB["title"],
            company_name=JOB["company_name"], location_text=JOB["location_text"],
            description=None, workplace_type="unknown", eligibility_status="unknown",
        )
        response = self.journey.request("POST", "jobs", json=JOB)
        self.assertEqual(response.status_code, 200, response.text)
        duplicate = response.json()
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["id"], bookmark["id"])
        self.assertEqual(duplicate["description"], JOB["description"])
        saved = self.journey.supabase.tables["jobs"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["description"], JOB["description"])
        detail = self.journey.request("GET", "jobs/" + bookmark["id"])
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["description"], JOB["description"])
        # A duplicate import must not silently replace text already reviewed.
        replay = self.journey.request("POST", "jobs", json={**JOB, "description": "Different supplied posting text."})
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["id"], bookmark["id"])
        self.assertEqual(replay.json()["description"], JOB["description"])
        self.assertEqual(len(self.journey.supabase.tables["jobs"]), 1)
        self.assertEqual(self.provider.operations, [])

    def test_a06_free_text_stays_insufficient_explicit_self_report_resolves_review(self):
        """Self-report is job-scoped and never presented as independent verification."""
        self.journey.setup_inputs()  # Existing synthetic DOCX/profile fixture.
        contexts = []
        build_context = mobile_api.build_context

        async def capture_context(*args, **kwargs):
            context = await build_context(*args, **kwargs)
            contexts.append(copy.deepcopy(context))
            return context

        self.stack.enter_context(patch.object(
            mobile_api, "build_context", side_effect=capture_context
        ))
        job_id = self.journey.job["id"]
        rank_path = f"jobs/{job_id}/rank"
        self.assertEqual(self.journey.request("POST", rank_path, json={}).status_code, 200)
        question = next(
            row for row in self.journey.supabase.tables["mobile_questions"]
            if row["job_id"] == job_id and "authorized" in row["prompt"]
        )
        answer = "For this synthetic UK role, I report current authorization to work."
        response = self.journey.request(
            "POST", f"questions/{question['id']}/answer",
            json={"answer": answer, "remember": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "answered")
        response = self.journey.request("POST", rank_path, json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(contexts), 2)
        self.assertNotIn("eligibility_confirmed", COLUMNS["jobs"].split(","))
        for context in contexts:
            self.assertIs(context["job"]["eligibility_confirmed"], False)
            self.assertIsNone(context["job"]["eligibility_review"])
        self.assertTrue(any(
            row["answer"] == answer and row["scope"] == "job:" + job_id
            for row in contexts[-1]["answers"]
        ))
        scores = self.journey.supabase.tables["job_scores"]
        self.assertEqual(len(scores), 2)
        self.assertEqual([row["recommendation"] for row in scores], ["review", "review"])
        self.assertEqual(response.json()["status"], "new")
        self.assertEqual(response.json()["score"], 8.0)
        self.assertIn("Work eligibility is unknown", response.json()["rationale"])
        saved_question = next(
            row for row in self.journey.supabase.tables["mobile_questions"]
            if row["id"] == question["id"]
        )
        self.assertEqual(saved_question["status"], "answered")
        self.assertFalse(any(
            row["prompt"] == question["prompt"] and row["status"] == "pending"
            for row in self.journey.supabase.tables["mobile_questions"]
        ))
        # Missing explicit consent must not create the structured review.
        denied = self.journey.request("POST", f"jobs/{job_id}/eligibility", json={
            "status": "eligible", "reason": answer, "confirmed": False,
        })
        self.assertEqual(denied.status_code, 422, denied.text)
        detail = self.journey.request("GET", f"jobs/{job_id}")
        self.assertIsNone(detail.json()["eligibility_review"])
        confirmed = self.journey.request("POST", f"jobs/{job_id}/eligibility", json={
            "status": "eligible", "reason": answer, "confirmed": True,
        })
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        review = confirmed.json()["eligibility_review"]
        self.assertEqual(confirmed.json()["eligibility_status"], "eligible")
        self.assertEqual(review["provenance"], "user_self_report")
        self.assertIs(review["independently_verified"], False)
        self.assertIs(review["confirmed"], True)
        self.assertEqual(review["reason"], answer)
        self.assertTrue(review["confirmed_at"])
        # Give prior fixture scores an unambiguous historical sort key.
        for score in scores:
            score["created_at"] = "2026-08-01T00:00:00Z"
        ranked = self.journey.request("POST", rank_path, json={})
        self.assertEqual(ranked.status_code, 200, ranked.text)
        self.assertEqual(ranked.json()["status"], "matched")
        self.assertEqual(ranked.json()["score"], 8.0)
        self.assertIn("self-report", ranked.json()["rationale"])
        self.assertEqual(len(contexts), 3)
        self.assertIs(contexts[-1]["job"]["eligibility_confirmed"], True)
        self.assertIs(contexts[-1]["job"]["eligibility_review"]["independently_verified"], False)
        self.assertEqual([row["recommendation"] for row in scores], ["review", "review", "strong_match"])
        self.assertEqual(self.provider.operations, ["rank", "rank", "rank"])

    def test_a11_prepare_projection_contains_required_source(self):
        """Execute the actual selected SQL on synthetic, in-memory rows only."""
        # Other suites may already have loaded these modules during discovery.
        # This probe must neither import them nor replace an existing module.
        guarded_modules = {
            name: sys.modules.get(name)
            for name in ("jobagent.service", "jobagent.config")
        }
        tree = ast.parse((ROOT / "src/jobagent/service.py").read_text())
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        queries = [
            node.args[0].value for node in ast.walk(functions["run_prepare"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute" and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and "FROM jobs j JOIN match_scores m" in node.args[0].value
        ]
        self.assertEqual(len(queries), 1)
        self.assertTrue(any(
            isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and node.value.id == "job" and isinstance(node.slice, ast.Constant)
            and node.slice.value == "source"
            for node in ast.walk(functions["draft_job"])
        ))
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE jobs (
                id INTEGER, source TEXT, title TEXT, company TEXT, location TEXT,
                description TEXT, status TEXT, excluded_reason TEXT
            );
            CREATE TABLE match_scores (
                job_id INTEGER, llm_score INTEGER, embedding_similarity REAL
            );
            INSERT INTO jobs VALUES (
                1, 'greenhouse:synthetic', 'Synthetic role', 'Example Test',
                'Test city', 'Synthetic description', 'matched', NULL
            );
            INSERT INTO match_scores VALUES (1, 9, 0.8);
        """)
        row = connection.execute(queries[0], (1,)).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue({"id", "source", "title", "company", "location", "description"}.issubset(row.keys()))
        self.assertEqual(row["source"], "greenhouse:synthetic")
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["title"], "Synthetic role")
        self.assertEqual(row["company"], "Example Test")
        self.assertEqual(row["location"], "Test city")
        self.assertEqual(row["description"], "Synthetic description")
        for name, previous in guarded_modules.items():
            self.assertIs(sys.modules.get(name), previous)
        self.assertEqual(self.provider.operations, [])


if __name__ == "__main__":
    unittest.main()
