"""Founder launch fixes: entirely synthetic, offline, disposable SQLite only.

Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/test_founder_launch_repairs.py
Imports the audit's protected config setup, never an approved-facts/resume file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_launch_matching_probe as audit
from jobagent.matching.constraints import review_reasons
from jobagent.sources.urls import canonical_job_url

db, service, settings = audit.db, audit.service, audit.settings


class OfflineFounderCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="founder-repairs-")
        self.addCleanup(self.temp.cleanup)
        self.enter(patch.dict(os.environ, {}, clear=True))
        self.enter(patch("dotenv.load_dotenv"))
        for name in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection"):
            self.enter(patch(name, side_effect=AssertionError("Network/provider calls forbidden")))
        self.enter(patch.object(settings, "db_path", Path(self.temp.name) / "synthetic.db"))
        self.enter(patch.object(settings, "output_dir", Path(self.temp.name) / "output"))
        self.enter(patch.object(settings, "load_preferences", return_value={}))
        self.enter(patch.object(audit.answers, "load_answers", side_effect=AssertionError("Private facts forbidden")))
        self.profile = audit.Profile(raw_text="Built Python services at Synthetic Labs.", years_experience=4)
        db.init_db()

    def enter(self, context):
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    def job(self, **changes):
        return audit.JobPosting(**{**dict(source="lever:synthetic", external_id="42", title="Engineer",
            company="Synthetic Labs", location="Dubai", country="AE", remote=False,
            description="Build Python services.", url="https://jobs.lever.co/synthetic/42"), **changes})

    def seed(self, *, status="new", score=None, **changes):
        with db.connection() as conn:
            job_id = db.upsert_job(conn, self.job(**changes))
            db.set_status(conn, job_id, status)
            if score is not None:
                db.save_match_score(conn, audit.service.MatchScore(job_id=job_id, embedding_similarity=.9, llm_score=score))
        return job_id

    def match(self, preferences=None, score=9):
        rank = Mock(return_value=(score, "Synthetic score"))
        with patch.object(service, "parse_resume", return_value=self.profile), \
                patch.object(service, "embed", return_value=[1.0, 0.0]), \
                patch.object(service, "rank_job", rank), \
                patch.object(settings, "load_preferences", return_value=preferences or {}):
            service.run_match(on_progress=lambda _: None)
        return rank


class AnswerRepairTests(OfflineFounderCase):
    def choice(self, question, facts):
        return audit.answers.choice_for_question(question, ["Select...", "Yes", "No"], data=facts)

    def test_sponsorship_both_fact_values_and_polarities(self):
        for value in (True, False):
            data = {"work_authorization": {"needs_sponsorship": value}}
            self.assertEqual(self.choice("Will you require visa sponsorship?", data), "Yes" if value else "No")
            self.assertEqual(self.choice("Can you work without visa sponsorship?", data), "No" if value else "Yes")
            self.assertEqual(self.choice("Will you not require sponsorship?", data), "No" if value else "Yes")

    def test_sponsorship_unknown_summary_is_not_a_boolean(self):
        for value in (None, "NEEDS_INPUT", "maybe", 1):
            self.assertIsNone(self.choice("Do you need sponsorship?", {"work_authorization": {
                "summary": "Discuss sponsorship with the employer", "needs_sponsorship": value}}))

    def test_country_alias_and_positive_negative_authorization(self):
        data = {"work_authorization": {"authorized_countries": ["Canada", "United States"], "unauthorized_countries": ["United Kingdom"]}}
        self.assertEqual(self.choice("Are you authorized to work in Canada?", data), "Yes")
        self.assertEqual(self.choice("Are you legally authorized to work in the U.S.?", data), "Yes")
        self.assertEqual(self.choice("Do you have the right to work in the UK?", data), "No")
        self.assertIsNone(self.choice("Are you authorized to work in Serbia?", data))

    def test_missing_and_conflicting_country_facts_require_input(self):
        data = {"work_authorization": {"authorized_countries": ["Canada"], "unauthorized_countries": ["CA"]}}
        self.assertIsNone(self.choice("Are you authorized to work in Canada?", data))
        self.assertIsNone(self.choice("Are you authorized to work in the US or Canada?", data))
        self.assertIsNone(self.choice("Are you authorized to work?", data))
        self.assertIsNone(self.choice("Do you require sponsorship in the US or Canada?", {
            "work_authorization": {"needs_sponsorship": True}}))

    def test_negative_authorization_and_conditional_questions(self):
        data = {"work_authorization": {"authorized_countries": ["Canada"], "needs_sponsorship": True}}
        self.assertEqual(self.choice("Are you not authorized to work in Canada?", data), "No")
        self.assertEqual(self.choice("Are you unauthorized to work in Canada?", data), "No")
        for question in ("Are you authorized to work in Canada, the US or Japan?",
                         "Will you require sponsorship without a work visa?",
                         "Will you require sponsorship if this role moves abroad?"):
            self.assertIsNone(self.choice(question, data), question)

    def test_compound_authorization_without_sponsor_needs_both_facts(self):
        data = {"work_authorization": {"authorized_countries": ["Canada"], "needs_sponsorship": False}}
        self.assertEqual(self.choice("Are you authorized to work in Canada without sponsorship?", data), "Yes")
        self.assertIsNone(self.choice("Are you authorized to work in the US without sponsorship?", data))
        self.assertIsNone(self.choice("Are you authorized to work in Canada and need sponsorship?", data))

    def test_country_scoped_sponsorship_fact(self):
        data = {"work_authorization": {"needs_sponsorship": True, "sponsorship_by_country": {"Canada": False}}}
        self.assertEqual(self.choice("Do you require sponsorship to work in Canada?", data), "No")
        self.assertEqual(self.choice("Do you require sponsorship in Canada?", data), "No")

    def test_exact_custom_answer_precedes_every_generic_fact(self):
        question = "Do you need sponsorship?"
        data = {"work_authorization": {"needs_sponsorship": True}, "custom_answers": {audit.answers.normalize_question(question): "No"}}
        self.assertEqual(self.choice(question, data), "No")
        self.assertEqual(audit.answers.answer_for(question, data=data).value, "No")
        data["custom_answers"][audit.answers.normalize_question(question)] = "Ask the recruiter"
        self.assertIsNone(self.choice(question, data))

    def test_criminal_disclosure_relocation_travel_are_never_hardcoded(self):
        for value in ("Yes", "No"):
            cases = [("Have you been convicted of a felony?", "application", "criminal_history"),
                     ("Are you willing to travel?", "availability", "willing_to_travel"),
                     ("Are you willing to relocate?", "work_authorization", "relocation_notes"),
                     ("Are you a US person?", "application", "us_person")]
            for question, section, key in cases:
                self.assertEqual(self.choice(question, {section: {key: value}}), value)
        self.assertIsNone(self.choice("Are you willing to relocate?", {"work_authorization": {"relocation_notes": "Maybe, depending on the city"}}))

    def test_exact_option_and_custom_unrecognized_question(self):
        data = {"custom_answers": {"can weekend shifts work": "Weekdays only"}}
        self.assertEqual(audit.answers.answer_for("Can weekend shifts work", data=data).value, "Weekdays only")
        self.assertEqual(audit.answers.choice_for_question("What is your gender?", ["Prefer not to say", "Female"],
            data={"voluntary_disclosures": {"gender": "Prefer not to say"}}), "Prefer not to say")


class ConstraintRepairTests(OfflineFounderCase):
    def reasons(self, description, preferences=None, **changes):
        return review_reasons(self.profile, self.job(description=description, **changes).model_dump(), preferences or {})

    def test_relocation_never_proves_visa_sponsorship(self):
        self.assertEqual(audit.eligibility.classify("Relocation assistance is provided."), "unknown")
        self.assertEqual(audit.eligibility.classify("Visa sponsorship is offered."), "sponsors")
        for text in ("Visa sponsorship is not available.", "No visa sponsorship is available.",
                     "Work from anywhere. We cannot sponsor visas.", "Relocation package; remote India only."):
            self.assertEqual(audit.eligibility.classify(text), "restricted", text)

    def test_country_remote_restrictions_not_limited_to_us_uk(self):
        for text in ("Remote India only", "Must reside in Japan", "Remote within Brazil", "Must be based in Kenya"):
            self.assertEqual(audit.eligibility.classify(text), "restricted")

    def test_known_authorization_cannot_override_conflicting_sponsorship_need(self):
        for text in ("Visa sponsorship is not available.", "Sponsorship will be unavailable.",
                     "We cannot offer sponsorship.", "We do not sponsor visas.", "We don't offer visa sponsorship.",
                     "Sponsorship is not supported.", "Must work without the need for visa sponsorship."):
            for needed in (True, None):
                self.assertTrue(self.reasons(text, {"authorized_countries": ["Canada"],
                    "sponsorship_required": needed}, country="CA"), text)
            self.assertEqual(self.reasons(text, {"authorized_countries": ["Canada"],
                "sponsorship_required": False}, country="CA"), [], text)

    def test_mandatory_licence_missing_negative_expired(self):
        requirement = "Current RN licence in Ontario is required."
        for facts in ("Support internship.", "No current RN licence in Ontario.", "Current RN licence in Ontario is expired.",
                      "I don't hold a current RN licence in Ontario.", "Seeking current RN licence in Ontario."):
            self.profile.raw_text = facts
            self.assertTrue(self.reasons(requirement))

    def test_literal_current_licence_and_preferred_requirement_controls(self):
        self.profile.raw_text = "Current RN licence in Ontario. Delivered patient care."
        self.assertEqual(self.reasons("Current RN licence in Ontario is required."), [])
        self.profile.raw_text = "No RN licence."
        self.assertEqual(self.reasons("RN licence preferred."), [])
        self.assertEqual(self.reasons("RN licence is not required."), [])

    def test_negated_experience_cannot_hide_mandatory_licence(self):
        self.assertTrue(self.reasons("No experience, but current RN licence in Ontario required."))

    def test_insufficient_and_unknown_years_require_review(self):
        for years in (0, 4, None):
            self.profile.years_experience = years
            self.assertTrue(self.reasons("Must have 15 years of experience."))
        self.profile.years_experience = 20
        self.assertEqual(self.reasons("Must have 15 years of experience."), [])

    def test_remote_only_does_not_mean_preferred_location_is_hard(self):
        self.assertTrue(self.reasons("Daily office attendance is required.", {"remote_preference": "remote_only"}))
        self.assertEqual(self.reasons("Work from anywhere.", {"preferred_locations": ["London"]}, remote=True), [])

    def test_known_authorized_country_does_not_establish_citizenship(self):
        self.assertEqual(self.reasons("Must be authorized to work in the US.", {"authorized_countries": ["United States"]}, country="US"), [])
        self.assertTrue(self.reasons("US citizens only.", {"authorized_countries": ["United States"]}, country="US"))

    def test_high_score_soft_fit_can_still_match(self):
        job_id = self.seed(description="Python experience preferred. Work from anywhere.", remote=True)
        rank = self.match()
        self.assertEqual(rank.call_count, 1)
        with db.connection() as conn:
            self.assertEqual(db.get_job(conn, job_id)["status"], "matched")

    def test_old_incompatible_draft_is_rechecked_without_provider(self):
        job_id = self.seed(status="drafted", score=9, description="Current RN licence in Ontario is required.")
        self.assertEqual(self.match().call_count, 0)
        with db.connection() as conn:
            self.assertEqual(db.get_job(conn, job_id)["status"], "new")
            self.assertIsNone(conn.execute("SELECT llm_score FROM match_scores WHERE job_id=?", (job_id,)).fetchone()[0])

    def test_confirming_missing_requirement_can_resume_matching(self):
        job_id = self.seed(description="Current RN licence in Ontario is required.")
        self.assertEqual(self.match().call_count, 0)
        self.profile.raw_text = "Current RN licence in Ontario. Delivered patient care."
        self.assertEqual(self.match().call_count, 1)
        with db.connection() as conn:
            self.assertEqual(db.get_job(conn, job_id)["status"], "matched")


class ScoreRepairTests(OfflineFounderCase):
    def rank_response(self, raw):
        with patch.object(settings, "rank_provider", "ollama"), patch.object(audit.ollama_rank.ollama, "chat",
                return_value={"message": {"content": raw}}):
            return audit.ollama_rank.rank_job(self.profile, "Engineer", "Synthetic", "Dubai", "Build Python services.")

    def test_invalid_scores_not_coerced(self):
        for value in (True, False, "9", 9.5, 0, -1, 11, 100, None, float("nan")):
            self.assertEqual(self.rank_response(json.dumps({"score": value, "reasoning": "Synthetic"})), (None, None))

    def test_boundary_scores_and_duplicate_json_keys(self):
        for value in (1, 10):
            self.assertEqual(self.rank_response(json.dumps({"score": value, "reasoning": "Synthetic"})), (value, "Synthetic"))
        self.assertEqual(self.rank_response('{"score":1,"score":10,"reasoning":"Synthetic"}'), (None, None))

    def test_database_rejects_out_of_range_score(self):
        job_id = self.seed()
        with db.connection() as conn, self.assertRaises(ValueError):
            db.save_match_score(conn, audit.service.MatchScore(job_id=job_id, embedding_similarity=.9, llm_score=100))

    def test_service_checks_score_even_when_rank_adapter_is_replaced(self):
        job_id = self.seed()
        self.match(score=100)
        with db.connection() as conn:
            self.assertEqual(db.get_job(conn, job_id)["status"], "new")
            self.assertIsNone(conn.execute("SELECT llm_score FROM match_scores WHERE job_id=?", (job_id,)).fetchone()[0])

    def test_historical_invalid_score_never_enters_automatic_queues(self):
        job_id = self.seed(status="drafted", score=9)
        with db.connection() as conn:
            conn.execute("UPDATE match_scores SET llm_score=100 WHERE job_id=?", (job_id,))
            self.assertEqual(db.autopilot_candidates(conn, min_score=9, limit=5), [])
            self.assertEqual(db.telegram_candidates(conn, min_score=8), [])
            self.assertIsNone(db.direct_apply_candidate(conn, job_id))


class SourceAndStorageRepairTests(OfflineFounderCase):
    def test_lever_html_lists_additional_only_and_section_order(self):
        from jobagent.sources.ats_boards import lever_description
        result = lever_description({"description": "<p>Introduction</p>", "lists": [
            {"text": "Requirements", "content": "<li>RN licence required.</li><li>No sponsorship.</li>"}],
            "additional": "<p>Must reside in Canada.</p>"})
        self.assertEqual(result, "Introduction\nRequirements\nRN licence required. No sponsorship.\nMust reside in Canada.")
        self.assertEqual(lever_description({"lists": [None, {"content": "A degree is required."}]}), "A degree is required.")

    def test_refresh_invalidates_active_score_and_draft_not_user_exclusion(self):
        job_id = self.seed(status="drafted", score=9)
        with db.connection() as conn:
            db.set_excluded(conn, job_id, "User declined this employer")
            refreshed = db.upsert_job(conn, self.job(description="No visa sponsorship.", salary="CAD 90000", location="Toronto", country="CA"))
            row = db.get_job(conn, job_id)
            self.assertEqual(refreshed, job_id)
            self.assertEqual((row["description"], row["salary"], row["country"], row["status"]), ("No visa sponsorship.", "CAD 90000", "CA", "new"))
            self.assertEqual(row["excluded_reason"], "User declined this employer")
            self.assertIsNone(conn.execute("SELECT * FROM match_scores WHERE job_id=?", (job_id,)).fetchone())

    def test_unchanged_refresh_preserves_score_but_updates_source_timestamp(self):
        job_id = self.seed(status="matched", score=9)
        with db.connection() as conn:
            db.upsert_job(conn, self.job(fetched_at="2030-01-01", posted_at="2029-12-01"))
            self.assertEqual(db.get_job(conn, job_id)["fetched_at"], "2030-01-01")
            self.assertEqual(db.get_job(conn, job_id)["status"], "matched")
            self.assertEqual(conn.execute("SELECT llm_score FROM match_scores WHERE job_id=?", (job_id,)).fetchone()[0], 9)

    def test_applied_history_and_attempts_are_not_reset_by_refresh(self):
        job_id = self.seed(status="applied", score=9)
        with db.connection() as conn:
            conn.execute("INSERT INTO application_attempts(job_id,state,created_at,updated_at) VALUES (?, 'submitted','2026-01-01','2026-01-01')", (job_id,))
            db.upsert_job(conn, self.job(description="New posting facts."))
            self.assertEqual(db.get_job(conn, job_id)["status"], "applied")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM application_attempts WHERE job_id=?", (job_id,)).fetchone()[0], 1)
        self.assertEqual(self.match().call_count, 0)

    def test_tracking_variants_deduplicate_and_reconcile_without_retiring_live_job(self):
        original = self.job().url + "?utm_source=mail#apply"
        job_id = self.seed(url=original)
        with db.connection() as conn:
            self.assertEqual(db.upsert_job(conn, self.job()), job_id)
            self.assertEqual(db.reconcile_direct_source(conn, "lever:synthetic", {self.job().url}), 0)
            self.assertEqual(db.get_job(conn, job_id)["url"], original)

    def test_different_requisition_query_ids_are_not_merged(self):
        with db.connection() as conn:
            first = db.upsert_job(conn, self.job(url=self.job().url + "?requisition=1"))
            second = db.upsert_job(conn, self.job(url=self.job().url + "?requisition=2"))
            self.assertNotEqual(first, second)

    def test_unknown_sites_keep_fragment_routing_and_significant_query_bytes(self):
        self.assertNotEqual(canonical_job_url("https://example.test/#job1"), canonical_job_url("https://example.test/#job2"))
        self.assertEqual(canonical_job_url("https://example.test/jobs?id=1%2F2&sig=a+b&utm_source=mail"), "https://example.test/jobs?id=1%2F2&sig=a+b")

    def test_unknown_utm_fields_and_userinfo_are_not_canonicalized_away(self):
        self.assertNotEqual(canonical_job_url("https://example.test/jobs?utm_job_id=1"),
                            canonical_job_url("https://example.test/jobs?utm_job_id=2"))
        url = "https://Candidate@example.test/jobs?utm_source=mail"
        self.assertEqual(canonical_job_url(url), url)

    def test_refresh_can_restore_only_its_own_source_expiry_exclusion(self):
        job_id = self.seed()
        with db.connection() as conn:
            db.reconcile_direct_source(conn, "lever:synthetic", set())
            db.upsert_job(conn, self.job())
            self.assertIsNone(db.get_job(conn, job_id)["excluded_reason"])


class PrepareRepairTests(OfflineFounderCase):
    def prepare_seams(self, cover=None):
        cover = cover or Mock(return_value="Synthetic cover letter")
        self.enter(patch.object(service, "run_fetch"))
        self.enter(patch.object(service, "run_match"))
        self.enter(patch.object(service, "parse_resume", return_value=self.profile))
        self.enter(patch.object(service, "check_live_job_link", return_value=SimpleNamespace(available=True, reason=None)))
        self.enter(patch.dict(sys.modules, {
            "jobagent.drafting.cover_letter": SimpleNamespace(draft_cover_letter=cover, build_cover_letter_pdf=Mock()),
            "jobagent.drafting.resume_tailor": SimpleNamespace(draft_resume_tailoring=Mock(return_value="Synthetic notes")),
            "jobagent.drafting.gap_analysis": SimpleNamespace(analyze_gaps=Mock(return_value="Synthetic gaps")),
            "jobagent.drafting.resume_builder": SimpleNamespace(parse_tailoring_notes=lambda _: ("Synthetic summary", ["Synthetic evidence"]),
                build_tailored_resume=Mock(), build_tailored_resume_pdf=Mock()),
        }))

    def test_actual_prepare_to_draft_row_projection_and_transition(self):
        job_id = self.seed(status="matched", score=9)
        self.prepare_seams()
        self.assertEqual(service.run_prepare(on_progress=lambda _: None), {"selected": 1, "drafted": 1, "failed": 0})
        with db.connection() as conn:
            self.assertEqual(db.get_job(conn, job_id)["status"], "drafted")
        self.assertTrue((settings.output_dir / "synthetic-labs-engineer" / "resume_tailoring.md").is_file())

    def test_total_failure_is_not_success_and_diagnostics_do_not_echo_provider_text(self):
        self.seed(status="matched", score=9)
        self.prepare_seams(Mock(side_effect=ValueError("SYNTHETIC_PRIVATE_MARKER")))
        messages = []
        with self.assertRaisesRegex(RuntimeError, "all 1"):
            service.run_prepare(on_progress=messages.append)
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", " ".join(messages))
        self.assertIn("0 drafted, 1 failed", messages[-1])

    def test_partial_batch_reports_both_outcomes(self):
        self.seed(status="matched", score=9)
        self.seed(status="matched", score=8, external_id="43", url="https://jobs.lever.co/synthetic/43")
        self.prepare_seams(Mock(side_effect=[ValueError("Synthetic failure"), "Synthetic cover"]))
        self.assertEqual(service.run_prepare(on_progress=lambda _: None), {"selected": 2, "drafted": 1, "failed": 1})

    def test_empty_batch_reports_zero_without_drafting(self):
        self.prepare_seams()
        self.assertEqual(service.run_prepare(on_progress=lambda _: None), {"selected": 0, "drafted": 0, "failed": 0})


if __name__ == "__main__":
    sys.addaudithook(audit.deny_private_file_access)
    unittest.main(verbosity=2)
