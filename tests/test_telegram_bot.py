from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jobagent.config import settings
from jobagent.service import slugify
from jobagent.storage import db
from jobagent.telegram_bot import TelegramBot, build_job_card, job_document_paths


class TelegramCardTests(unittest.TestCase):
    def test_job_card_is_html_safe_and_has_private_actions(self):
        message, keyboard = build_job_card(
            {
                "id": 42,
                "title": "Senior <Engineer>",
                "company": "Example & Co",
                "location": "Remote",
                "url": "https://example.com/job/42",
                "status": "drafted",
                "llm_score": 9,
                "llm_reasoning": "Strong Java & platform fit",
                "eligibility": "sponsors",
            },
            "http://100.1.2.3:8842",
        )

        self.assertIn("Senior &lt;Engineer&gt;", message)
        self.assertIn("Example &amp; Co", message)
        self.assertEqual(keyboard["inline_keyboard"][0][0]["url"], "http://100.1.2.3:8842?job=42")
        self.assertEqual(keyboard["inline_keyboard"][1][0]["callback_data"], "applied:42")
        self.assertEqual(keyboard["inline_keyboard"][1][1]["callback_data"], "exclude:42")


class TelegramCandidateQueryTests(unittest.TestCase):
    """Exercise db.telegram_candidates against a throwaway SQLite file.

    The real jobagent.db is never touched: every call passes an explicit db_path inside a
    TemporaryDirectory.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test-jobagent.db"
        db.init_db(self.db_path)
        self.assertNotEqual(self.db_path, settings.db_path)

    def _seed(
        self,
        conn,
        *,
        title="Staff Engineer",
        company="Acme",
        status="drafted",
        eligibility="unknown",
        llm_score=9,
        excluded_reason=None,
        similarity=0.5,
        notified=False,
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO jobs (source, external_id, title, company, location, remote, url,
                              description, salary, posted_at, fetched_at, status, excluded_reason)
            VALUES ('test', ?, ?, ?, 'Remote', 1, ?, 'desc', NULL, NULL, '2026-01-01', ?, ?)
            """,
            (
                f"{company}-{title}-{eligibility}-{status}-{llm_score}",
                title,
                company,
                f"https://example.com/{company}/{title}/{eligibility}/{status}/{llm_score}".replace(" ", "-"),
                status,
                excluded_reason,
            ),
        )
        job_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO match_scores (job_id, embedding_similarity, llm_score, llm_reasoning,
                                      eligibility, scored_at)
            VALUES (?, ?, ?, 'because', ?, '2026-01-01')
            """,
            (job_id, similarity, llm_score, eligibility),
        )
        if notified:
            db.mark_telegram_notified(conn, job_id)
        return job_id

    def _candidate_ids(self, conn, **kwargs):
        kwargs.setdefault("min_score", 8)
        kwargs.setdefault("limit", 10)
        return [int(row["id"]) for row in db.telegram_candidates(conn, **kwargs)]

    def test_unknown_eligibility_is_returned(self):
        """~99% of scored rows are tagged 'unknown'; excluding them surfaced nothing."""
        with db.connection(self.db_path) as conn:
            job_id = self._seed(conn, eligibility="unknown")
            self.assertIn(job_id, self._candidate_ids(conn))

    def test_restricted_and_no_sponsorship_are_excluded(self):
        with db.connection(self.db_path) as conn:
            restricted = self._seed(conn, company="Restricted", eligibility="restricted")
            no_sponsorship = self._seed(conn, company="NoSponsor", eligibility="no-sponsorship")
            worldwide = self._seed(conn, company="Worldwide", eligibility="worldwide")

            ids = self._candidate_ids(conn)
            self.assertIn(worldwide, ids)
            self.assertNotIn(restricted, ids)
            self.assertNotIn(no_sponsorship, ids)

    def test_excluded_jobs_are_never_surfaced(self):
        with db.connection(self.db_path) as conn:
            excluded = self._seed(conn, company="Excluded", excluded_reason="Needs US work authorization")
            self.assertNotIn(excluded, self._candidate_ids(conn))

    def test_only_drafted_and_matched_statuses_are_returned(self):
        with db.connection(self.db_path) as conn:
            drafted = self._seed(conn, company="Drafted", status="drafted")
            matched = self._seed(conn, company="Matched", status="matched")
            new = self._seed(conn, company="New", status="new")
            applied = self._seed(conn, company="Applied", status="applied")

            ids = self._candidate_ids(conn)
            self.assertIn(drafted, ids)
            self.assertIn(matched, ids)
            self.assertNotIn(new, ids)
            self.assertNotIn(applied, ids)

    def test_scores_below_min_score_are_excluded(self):
        with db.connection(self.db_path) as conn:
            low = self._seed(conn, company="Low", llm_score=7)
            at_threshold = self._seed(conn, company="AtThreshold", llm_score=8)

            ids = self._candidate_ids(conn, min_score=8)
            self.assertIn(at_threshold, ids)
            self.assertNotIn(low, ids)

    def test_sponsoring_jobs_outrank_unknown_jobs_at_the_same_score(self):
        with db.connection(self.db_path) as conn:
            unknown = self._seed(conn, company="Unknown", eligibility="unknown", llm_score=9)
            sponsors = self._seed(conn, company="Sponsors", eligibility="sponsors", llm_score=9)

            self.assertEqual(self._candidate_ids(conn), [sponsors, unknown])

    def test_only_unnotified_skips_already_sent_jobs(self):
        with db.connection(self.db_path) as conn:
            seen = self._seed(conn, company="Seen", notified=True)
            unseen = self._seed(conn, company="Unseen")

            ids = self._candidate_ids(conn, only_unnotified=True)
            self.assertEqual(ids, [unseen])
            self.assertIn(seen, self._candidate_ids(conn, only_unnotified=False))


def _offline_bot(test_case):
    """A TelegramBot that can never reach the network, with every send recorded."""
    bot = TelegramBot(token="test-token", allowed_chat_id="123", dashboard_url="http://x", min_score=8)
    test_case.addCleanup(bot.close)
    sent: list = []

    def record_send_text(chat_id, text, reply_markup=None):
        sent.append((chat_id, text, reply_markup))

    for name, replacement in (
        ("send_text", record_send_text),
        ("_request", mock.Mock(side_effect=AssertionError("no network calls in tests"))),
        ("send_document", mock.Mock(side_effect=AssertionError("no network calls in tests"))),
    ):
        patcher = mock.patch.object(bot, name, replacement)
        patcher.start()
        test_case.addCleanup(patcher.stop)
    return bot, sent


class HandleMessageTests(unittest.TestCase):
    def test_non_text_message_is_ignored_without_crashing(self):
        """A sticker/photo has no "text"; splitting an empty string used to raise IndexError."""
        bot, sent = _offline_bot(self)

        bot.handle_message({"chat": {"id": 123}, "sticker": {"file_id": "abc"}})

        self.assertEqual(sent, [])

    def test_blank_text_message_is_ignored(self):
        bot, sent = _offline_bot(self)

        bot.handle_message({"chat": {"id": 123}, "text": "   "})

        self.assertEqual(sent, [])

    def test_unknown_command_still_replies(self):
        bot, sent = _offline_bot(self)

        bot.handle_message({"chat": {"id": 123}, "text": "hello"})

        self.assertEqual(len(sent), 1)
        self.assertIn("/today", sent[0][1])

    def test_today_uses_the_unnotified_queue_but_matches_does_not(self):
        bot, _ = _offline_bot(self)
        with mock.patch.object(bot, "send_matches", return_value=1) as send_matches:
            bot.handle_message({"chat": {"id": 123}, "text": "/today"})
        self.assertEqual(send_matches.call_args.kwargs["only_unnotified"], True)

        with mock.patch.object(bot, "send_matches", return_value=1) as send_matches:
            bot.handle_message({"chat": {"id": 123}, "text": "/matches"})
        self.assertEqual(send_matches.call_args.kwargs, {})


class JobDocumentTests(unittest.TestCase):
    ROW = {
        "id": 7,
        "title": "Staff Engineer",
        "company": "Acme Corp",
        "location": "Remote",
        "url": "https://example.com/job/7",
        "status": "drafted",
        "llm_score": 9,
        "llm_reasoning": "Great fit",
        "eligibility": "unknown",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output_dir = Path(self._tmp.name) / "output"
        patcher = mock.patch.object(settings, "output_dir", self.output_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.job_dir = self.output_dir / slugify(f"{self.ROW['company']}-{self.ROW['title']}")

    def _write_documents(self):
        self.job_dir.mkdir(parents=True, exist_ok=True)
        (self.job_dir / "cover_letter.pdf").write_bytes(b"%PDF-1.4 cover")
        (self.job_dir / "tailored_resume.pdf").write_bytes(b"%PDF-1.4 resume")

    def _docs_buttons(self, keyboard):
        return [
            button
            for row in keyboard["inline_keyboard"]
            for button in row
            if button.get("callback_data", "").startswith("docs:")
        ]

    def test_no_documents_returns_empty_list(self):
        self.assertEqual(job_document_paths(self.ROW), [])

    def test_documents_are_returned_in_reading_order(self):
        self._write_documents()

        paths = job_document_paths(self.ROW)

        self.assertEqual(
            paths,
            [
                (self.job_dir / "cover_letter.pdf", "Cover letter"),
                (self.job_dir / "tailored_resume.pdf", "Tailored resume"),
            ],
        )

    def test_partial_documents_only_report_what_exists(self):
        self.job_dir.mkdir(parents=True, exist_ok=True)
        (self.job_dir / "cover_letter.pdf").write_bytes(b"%PDF-1.4 cover")

        self.assertEqual(job_document_paths(self.ROW), [(self.job_dir / "cover_letter.pdf", "Cover letter")])

    def test_card_omits_docs_button_when_no_documents_exist(self):
        _, keyboard = build_job_card(self.ROW, "http://dash")

        self.assertEqual(self._docs_buttons(keyboard), [])

    def test_card_includes_docs_button_when_documents_exist(self):
        self._write_documents()

        _, keyboard = build_job_card(self.ROW, "http://dash")

        buttons = self._docs_buttons(keyboard)
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["callback_data"], "docs:7")
        self.assertIn("Send documents", buttons[0]["text"])


if __name__ == "__main__":
    unittest.main()
