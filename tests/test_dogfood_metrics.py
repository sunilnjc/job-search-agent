"""Weekly dogfood counts and CLI. No network, analytics SDK, or PII logs."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jobagent.mobile.dogfood_metrics import (
    parse_iso_week, prior_iso_week, report_to_csv, weekly_dogfood_report,
)

ROOT = Path(__file__).resolve().parents[1]
USER_JOB = "11111111-1111-4111-8111-111111111111"
OTHER_JOB = "22222222-2222-4222-8222-222222222222"


class DogfoodMetricsTests(unittest.TestCase):
    def test_prior_iso_week_and_parse(self):
        self.assertEqual(parse_iso_week("2026-W37"), (2026, 37))
        with self.assertRaises(ValueError):
            parse_iso_week("2026-37")
        year, week = prior_iso_week()
        self.assertGreaterEqual(week, 1)
        self.assertGreaterEqual(year, 2020)

    def test_counts_are_distinct_jobs_and_replies_are_stubbed(self):
        report = weekly_dogfood_report(
            year=2026, week=37,
            job_scores=[
                {"job_id": USER_JOB, "created_at": "2026-09-08T12:00:00Z"},
                {"job_id": USER_JOB, "created_at": "2026-09-09T12:00:00Z"},
            ],
            model_runs=[
                {"job_id": OTHER_JOB, "operation": "rank_job", "status": "succeeded",
                 "completed_at": "2026-09-10T09:00:00Z"},
                {"job_id": USER_JOB, "operation": "prepare_documents", "status": "succeeded",
                 "completed_at": "2026-09-10T10:00:00Z"},
                {"job_id": OTHER_JOB, "operation": "prepare_documents", "status": "failed",
                 "completed_at": "2026-09-10T11:00:00Z"},
            ],
            applications=[
                {"job_id": USER_JOB, "status": "submitted", "applied_at": None,
                 "updated_at": "2026-09-11T08:00:00Z"},
                {"job_id": OTHER_JOB, "status": "draft", "updated_at": "2026-09-11T08:00:00Z"},
            ],
        )
        self.assertEqual(report["iso_week"], "2026-W37")
        self.assertEqual(report["roles_reviewed"], 2)
        self.assertEqual(report["roles_prepared"], 1)
        self.assertEqual(report["roles_applied"], 1)
        self.assertIsNone(report["replies"])
        self.assertEqual(report["replies_status"], "not_tracked")
        csv_text = report_to_csv(report)
        self.assertIn("N/A", csv_text)
        self.assertNotIn("@", json.dumps(report))

    def test_outside_week_is_excluded(self):
        report = weekly_dogfood_report(
            year=2026, week=37,
            job_scores=[{"job_id": USER_JOB, "created_at": "2026-09-01T00:00:00Z"}],
            model_runs=[], applications=[],
        )
        self.assertEqual(report["roles_reviewed"], 0)

    def test_cli_from_json_and_sql(self):
        payload = {
            "job_scores": [{"job_id": USER_JOB, "created_at": "2026-09-08T12:00:00Z"}],
            "model_runs": [],
            "applications": [],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/weekly_dogfood_metrics.py"),
                 "--from-json", str(path), "--iso-week", "2026-W37"],
                cwd=str(ROOT), capture_output=True, text=True, check=False,
                env={"PYTHONPATH": str(ROOT / "src"), "PYTHON_DOTENV_DISABLED": "1"},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["roles_reviewed"], 1)
        sql = subprocess.run(
            [sys.executable, str(ROOT / "scripts/weekly_dogfood_metrics.py"), "--sql", "--iso-week", "2026-W37"],
            cwd=str(ROOT), capture_output=True, text=True, check=False,
            env={"PYTHONPATH": str(ROOT / "src"), "PYTHON_DOTENV_DISABLED": "1"},
        )
        self.assertEqual(sql.returncode, 0, sql.stderr)
        self.assertIn("mobile_operator_dogfood_week", sql.stdout)
        self.assertIn("2026-09-07", sql.stdout)


class PacketBurnCLITests(unittest.TestCase):
    def test_cli_aggregates_fixture_json(self):
        runs = [{
            "id": "rank-1", "user_id": USER_JOB, "job_id": OTHER_JOB, "operation": "rank_job",
            "status": "succeeded", "completed_at": "2026-09-08T10:00:00Z",
            "output_summary": {"usage": {"estimated_cost_usd": 0.02, "reserved_units": 1,
                                         "input_tokens": 10, "output_tokens": 5}},
        }, {
            "id": "prep-1", "user_id": USER_JOB, "job_id": OTHER_JOB, "operation": "prepare_documents",
            "status": "succeeded", "completed_at": "2026-09-08T11:00:00Z",
            "output_summary": {"usage": {"estimated_cost_usd": 0.04, "reserved_units": 2,
                                         "input_tokens": 20, "output_tokens": 10}},
        }]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runs.json"
            path.write_text(json.dumps({"model_runs": runs}), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/packet_burn_report.py"), "--from-json", str(path)],
                cwd=str(ROOT), capture_output=True, text=True, check=False,
                env={"PYTHONPATH": str(ROOT / "src"), "PYTHON_DOTENV_DISABLED": "1"},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["summary"]["completed_packets"], 1)
        self.assertEqual(body["summary"]["mean_reserved_units"], 3)
        self.assertNotIn("sk-", result.stdout)


if __name__ == "__main__":
    unittest.main()
