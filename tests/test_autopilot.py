from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jobagent.applying.ats import ATSDetection
from jobagent.applying.autopilot import process_ready_queue
from jobagent.config import settings
from jobagent.service import slugify
from jobagent.storage import db


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "agent.db"
        self.output_dir = Path(self.temp.name) / "output"
        self.output_patch = mock.patch.object(settings, "output_dir", self.output_dir)
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)
        self.unknown_eligibility_patch = mock.patch.object(
            settings, "autopilot_include_unknown_outside_us_uk", False
        )
        self.unknown_eligibility_patch.start()
        self.addCleanup(self.unknown_eligibility_patch.stop)
        self.direct_ats_patch = mock.patch.object(settings, "autopilot_require_direct_ats", False)
        self.direct_ats_patch.start()
        self.addCleanup(self.direct_ats_patch.stop)
        db.init_db(self.db_path)

    def _seed(self, *, eligibility="sponsors", score=9, url="https://boards.greenhouse.io/acme/jobs/1"):
        with db.connection(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO jobs (source, external_id, title, company, location, remote, url, description, fetched_at, status)
                   VALUES ('test', '1', 'Senior Engineer', 'Acme', 'Remote', 1, ?, 'desc', '2026-01-01', 'drafted')""",
                (url,),
            )
            job_id = int(cur.lastrowid)
            conn.execute(
                """INSERT INTO match_scores (job_id, embedding_similarity, llm_score, eligibility, scored_at)
                   VALUES (?, .9, ?, ?, '2026-01-01')""",
                (job_id, score, eligibility),
            )
        folder = self.output_dir / slugify("Acme-Senior Engineer")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cover_letter.pdf").write_bytes(b"pdf")
        (folder / "tailored_resume.pdf").write_bytes(b"pdf")
        return job_id

    def test_only_explicitly_eligible_score_nine_jobs_enter_queue_by_default(self):
        accepted = self._seed(eligibility="worldwide", score=9)
        self._seed(eligibility="unknown", score=10, url="https://boards.greenhouse.io/unknown/jobs/2")
        self._seed(eligibility="sponsors", score=8, url="https://boards.greenhouse.io/low/jobs/3")

        results = process_ready_queue(db_path=self.db_path, resolver=lambda url: ATSDetection("greenhouse", url, False, False))

        self.assertEqual([r.job_id for r in results], [accepted])
        self.assertEqual(results[0].state, "ready_for_submission")
        with db.connection(self.db_path) as conn:
            attempt = db.get_application_attempt(conn, results[0].attempt_id)
        self.assertEqual(attempt["state"], "ready_for_submission")
        self.assertEqual(attempt["ats"], "greenhouse")

    def test_opt_in_includes_unknown_roles_outside_us_uk_but_not_us_or_uk(self):
        unknown = self._seed(eligibility="unknown", score=10)
        us = self._seed(eligibility="unknown", score=10, url="https://boards.greenhouse.io/us/jobs/2")
        uk = self._seed(eligibility="unknown", score=10, url="https://boards.greenhouse.io/uk/jobs/3")
        with db.connection(self.db_path) as conn:
            conn.execute("UPDATE jobs SET country = 'Germany' WHERE id = ?", (unknown,))
            conn.execute("UPDATE jobs SET country = 'United States' WHERE id = ?", (us,))
            conn.execute("UPDATE jobs SET country = 'United Kingdom' WHERE id = ?", (uk,))

        results = process_ready_queue(
            db_path=self.db_path,
            resolver=lambda url: ATSDetection("greenhouse", url, False, False),
        )
        self.assertEqual(results, [])

        settings.autopilot_include_unknown_outside_us_uk = True
        results = process_ready_queue(
            db_path=self.db_path,
            resolver=lambda url: ATSDetection("greenhouse", url, False, False),
        )
        self.assertEqual([r.job_id for r in results], [unknown])

    def test_unresolved_or_aggregator_application_becomes_exception(self):
        job_id = self._seed()
        results = process_ready_queue(
            db_path=self.db_path,
            resolver=lambda url: ATSDetection(None, url, True, True),
        )

        self.assertEqual(results[0].job_id, job_id)
        self.assertEqual(results[0].state, "exception")
        self.assertEqual(results[0].reason, "unsupported_or_unresolved_ats")

    def test_ready_attempt_is_not_repeated(self):
        self._seed()
        resolver = lambda url: ATSDetection("greenhouse", url, False, False)
        self.assertEqual(len(process_ready_queue(db_path=self.db_path, resolver=resolver)), 1)
        self.assertEqual(process_ready_queue(db_path=self.db_path, resolver=resolver), [])

    def test_exception_attempt_is_not_repeated_automatically(self):
        self._seed()
        resolver = lambda url: ATSDetection(None, url, True, True)
        first = process_ready_queue(db_path=self.db_path, resolver=resolver)
        self.assertEqual(first[0].state, "exception")
        self.assertEqual(process_ready_queue(db_path=self.db_path, resolver=resolver), [])

    def test_direct_ats_mode_skips_aggregator_sources(self):
        direct = self._seed(eligibility="sponsors")
        with db.connection(self.db_path) as conn:
            conn.execute("UPDATE jobs SET source = 'greenhouse:acme' WHERE id = ?", (direct,))
        aggregator = self._seed(eligibility="sponsors", url="https://example.com/2")
        settings.autopilot_require_direct_ats = True

        results = process_ready_queue(
            db_path=self.db_path,
            resolver=lambda url: ATSDetection("greenhouse", url, False, False),
        )
        self.assertEqual([result.job_id for result in results], [direct])
        self.assertNotEqual(direct, aggregator)


if __name__ == "__main__":
    unittest.main()
