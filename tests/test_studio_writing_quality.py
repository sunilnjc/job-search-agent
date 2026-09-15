"""Source-exact launch writing checks; never credentials, network or exports."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from jobagent.mobile import studio
from jobagent.mobile.document_review import LEGACY_NOTICE, validated_document_review, validated_saved_document_review
from jobagent.mobile.writing import compose_letter, connection_options, letter_sentence
import test_studio_launch_quality as existing
from test_studio_launch_quality import context, selection


class SourceCompositionTests(unittest.TestCase):
    setUp = existing.StudioLaunchQualityTests.setUp
    render = existing.StudioLaunchQualityTests.render
    sections = existing.StudioLaunchQualityTests.sections

    def assert_composition_grounded(self, candidate, documents, letter):
        _, facts = studio._context(candidate)
        ledger = {f["id"]: f for f in facts}
        seen, job_seen = [], set()
        for paragraph in documents.composition["letter_paragraphs"]:
            self.assertIn(paragraph["text"], [b.text for b in letter])
            if paragraph["job_source_id"]:
                ref = paragraph["job_source_id"]
                self.assertNotIn(ref, job_seen)
                job_seen.add(ref)
                self.assertEqual(paragraph["job_quote"], ledger[ref]["text"])
                self.assertIn('“' + ledger[ref]["text"] + '”', paragraph["text"])
            for sentence in paragraph["candidate_sentences"]:
                ref = sentence["source_id"]
                seen.append(ref)
                original = ledger[ref]["text"]
                self.assertEqual(sentence["text"], letter_sentence(original)[0])
                if sentence["operation"] == "first_person_subject":
                    # Exact reverse: the only permitted operation added "I "
                    # and lowercased one initial action letter, nothing else.
                    self.assertEqual(sentence["text"][2].upper() + sentence["text"][3:], original)
                else:
                    self.assertEqual(sentence["text"], original)
                self.assertIn(sentence["text"], paragraph["text"])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertLessEqual(len(job_seen), 2)
        self.assertEqual(documents[2]["source_ids"], seen)
        self.assertEqual(validated_document_review(documents)["cover_letter_source_ids"], seen)

    def test_six_existing_personas_preserve_all_selected_facts_and_limitations(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from gate2_writing_eval import cases, context_for
        for persona in cases():
            candidate = context_for(persona)
            refs = [f"career_text.{i}" for i in range(len(persona["facts"]))]
            output = selection(candidate, experience=refs, cover_letter=refs[1:5])
            with self.subTest(persona=persona["id"]):
                documents, resume, letter, _ = self.render(candidate, output)
                self.assert_composition_grounded(candidate, documents, letter)
                originals = set(persona["facts"])
                resume_body = [b.text for b in resume if b.style == "body"]
                self.assertTrue(set(resume_body) <= originals)
                self.assertEqual(len(resume_body), len(set(resume_body)))

    def test_cover_letter_quotes_real_duty_and_retains_exact_metrics(self):
        candidate = context()
        candidate["career_text"] = "Prepared monthly forecasts for a USD 12 million operating budget.\nReduced planning time by five business days in 2024."
        output = selection(candidate, experience=["career_text.0", "career_text.1"], cover_letter=["career_text.0", "career_text.1"])
        docs, _, letter, _ = self.render(candidate, output)
        self.assert_composition_grounded(candidate, docs, letter)
        text = " ".join(b.text for b in letter)
        self.assertIn('Your posting highlights “Prepare monthly forecasts and discuss variance analysis.”', text)
        self.assertIn("I prepared monthly forecasts for a USD 12 million operating budget.", text)
        self.assertIn("I reduced planning time by five business days in 2024.", text)
        self.assertNotIn("ideal", text)
        self.assertNotIn("qualified", text)
        self.assertEqual(len(docs.composition["letter_paragraphs"]), 1)

    def test_changed_posting_changes_connections_not_candidate_history(self):
        candidate = context()
        candidate["career_text"] = "Prepared monthly forecasts for budget owners.\nLed customer interviews for a planning tool."
        output = selection(candidate, experience=["career_text.0", "career_text.1"], cover_letter=["career_text.0", "career_text.1"])
        before, resume1, letter1, _ = self.render(candidate, output)
        candidate["job"]["description"] = "Conduct customer interviews for a planning product."
        after, resume2, letter2, _ = self.render(candidate, output)
        self.assertEqual(resume1, resume2)
        self.assertNotEqual(letter1, letter2)
        self.assertEqual(before.composition["letter_paragraphs"][0]["candidate_sentences"][0]["source_id"], "career_text.0")
        self.assertEqual(after.composition["letter_paragraphs"][0]["candidate_sentences"][0]["source_id"], "career_text.1")
        self.assert_composition_grounded(candidate, after, letter2)

    def test_one_overlooked_topic_example_can_be_recalled_from_validated_resume(self):
        candidate = context()
        candidate["career_text"] = "Reduced the monthly planning cycle by two days.\nUsed Excel and Power BI. No software development experience."
        candidate["job"]["description"] = "Excel reporting required."
        output = selection(candidate, experience=["career_text.0", "career_text.1"], cover_letter=["career_text.0"])
        docs, _, letter, _ = self.render(candidate, output)
        self.assertEqual(docs.composition["recalled_letter_source_ids"], ["career_text.1"])
        self.assertEqual(docs[2]["source_ids"], ["career_text.1", "career_text.0"])
        self.assertIn("I used Excel and Power BI. No software development experience.", " ".join(b.text for b in letter))
        self.assert_composition_grounded(candidate, docs, letter)

    def test_recall_cannot_promote_unselected_or_unconfirmed_source_to_letter(self):
        candidate = context()
        candidate["career_text"] += "\nBuilt a Python service."
        candidate["resume_text"] = "Built an aerospace engine."
        candidate["job"]["description"] = "Build a Python aerospace service."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        docs, _, letter, _ = self.render(candidate, output)
        self.assertEqual(docs.composition["recalled_letter_source_ids"], [])
        self.assertNotIn("Python", " ".join(b.text for b in letter))

    def test_repeated_skills_and_generic_motivation_are_omitted_not_rewritten(self):
        candidate = context()
        bad = ["Skills: Excel Excel Excel, forecasting.", "I am pasionate about developement and learnning."]
        candidate["career_text"] += "\n" + "\n".join(bad)
        output = selection(candidate, experience=["career_text.0", "career_text.1"],
                           cover_letter=["career_text.0", "career_text.1", "career_text.2"])
        docs, resume, letter, _ = self.render(candidate, output)
        for text in bad:
            self.assertNotIn(text, " ".join(b.text for b in resume + letter))
            self.assertIn(text, [s["text"] for s in docs.review["snapshot"]["sources"]])
        self.assertEqual(docs.review["omitted_source_ids"], ["career_text.1", "career_text.2"])
        self.assertTrue(any("skills note repeats" in q for q in docs.questions))
        self.assertTrue(any("motivation statement" in q for q in docs.questions))

    def test_filtering_does_not_hide_invalid_claim_or_expired_qualification(self):
        candidate = context()
        candidate["career_text"] += "\nSkills: Excel Excel Excel."
        output = selection(candidate, experience=["career_text.0", "career_text.1"], cover_letter=["career_text.0"])
        output["experience"][1]["text"] += " I hold a CPA licence."
        with self.assertRaises(studio.MissingFactsError):
            self.render(candidate, output)
        candidate["career_background"]["qualifications"] = [{"name": "CPA", "kind": "certification", "status": "expired",
            "jurisdiction": "", "expires_on": "2020-01-01", "evidence_note": "No renewal."}]
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_background.qualifications.0"])
        with self.assertRaises(studio.MissingFactsError):
            self.render(candidate, output)

    def test_thin_source_is_short_with_actionable_followup_never_repetition(self):
        candidate = context()
        output = selection(candidate, summary=["career_text.0"], experience=["career_text.0"],
                           cover_letter=["career_text.0", "career_text.0"])
        docs, resume, letter, _ = self.render(candidate, output)
        self.assertEqual([b.text for b in resume].count(candidate["career_text"]), 1)
        self.assertEqual(len(docs.composition["letter_paragraphs"][0]["candidate_sentences"]), 1)
        self.assertTrue(any("short evidence-based draft" in q and "qualitative outcome" in q for q in docs.questions))
        self.assert_composition_grounded(candidate, docs, letter)

    def test_missing_job_connection_is_explicit_not_fabricated(self):
        candidate = context()
        candidate["job"]["description"] = "Design aerospace turbines."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        docs, _, letter, _ = self.render(candidate, output)
        self.assertTrue(all(p["job_source_id"] is None for p in docs.composition["letter_paragraphs"]))
        self.assertNotIn("turbines", " ".join(b.text for b in letter))
        self.assertTrue(any("not enough specific overlap" in q for q in docs.questions))

    def test_negative_claim_cannot_be_a_positive_topic_connection(self):
        candidate = context()
        candidate["career_text"] = "No Python or programming experience."
        candidate["job"]["description"] = "Build Python services."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        docs, _, letter, _ = self.render(candidate, output)
        self.assertTrue(all(p["job_source_id"] is None for p in docs.composition["letter_paragraphs"]))
        self.assertIn(candidate["career_text"], " ".join(b.text for b in letter))

    def test_legal_gates_never_become_positive_letter_connections(self):
        candidate = context()
        candidate["career_text"] = "Analyzed Python clinical reporting data; no nursing licence."
        for description in ("Python clinical reporting. Current nursing licence required.",
                            "Current US work authorization required for Python clinical reporting."):
            candidate["job"]["description"] = description
            _, facts = studio._context(candidate)
            hints = studio._document_evidence_hints(facts, candidate)
            for options in connection_options(facts, hints).values():
                for option in options:
                    requirement = next(f["text"] for f in facts if f["id"] == option["job_source_id"])
                    self.assertNotIn("licence", requirement)
                    self.assertNotIn("authorization", requirement)

    def test_single_employer_does_not_absorb_unattributed_metrics(self):
        candidate = context()
        candidate["career_text"] = "Analyst, Example Market, 2020-present.\nReduced reporting time by two days."
        output = selection(candidate, experience=["career_text.0", "career_text.1"], cover_letter=["career_text.1"])
        docs, resume, letter, _ = self.render(candidate, output)
        sections = self.sections(resume)
        self.assertEqual(sections["Work Experience"], [candidate["career_text"].splitlines()[0]])
        self.assertEqual(sections["Selected Career Contributions"], [candidate["career_text"].splitlines()[1]])
        self.assertNotIn("Example Market", " ".join(b.text for b in letter))
        self.assertEqual(docs.review["unattributed_source_ids"], ["career_text.1"])
        self.assertTrue(any("Which employer or project" in q for q in docs.questions))

    def test_employment_heading_cannot_be_misfiled_as_professional_summary(self):
        candidate = context()
        candidate["career_text"] = "Product Manager, Example Analytics, 2020-present.\nLed customer interviews for a planning product."
        output = selection(candidate, summary=["career_text.0"], experience=["career_text.1"], cover_letter=["career_text.1"])
        _, resume, _, _ = self.render(candidate, output)
        self.assertNotIn("Professional Summary", self.sections(resume))
        self.assertEqual(self.sections(resume)["Work Experience"], [candidate["career_text"].splitlines()[0]])

    def test_standalone_career_headline_is_not_an_accomplishment(self):
        candidate = context()
        candidate["career_text"] = "Senior Backend Engineer\nEngineer, Example Market, 2021-present.\nEngineer, Fictional Bank, 2017-2021.\nReduced reporting time by two days."
        output = selection(candidate, experience=["career_text.0", "career_text.1", "career_text.2", "career_text.3"],
                           cover_letter=["career_text.0", "career_text.3"])
        docs, resume, letter, _ = self.render(candidate, output)
        self.assertNotIn("Senior Backend Engineer", [b.text for b in resume + letter])
        self.assertEqual(self.sections(resume)["Selected Career Contributions"], ["Reduced reporting time by two days."])
        self.assertNotIn("Professional Summary", self.sections(resume))
        self.assertIn("career_text.0", docs.review["omitted_source_ids"])
        self.assertEqual(next(s["text"] for s in docs.review["snapshot"]["sources"] if s["id"] == "career_text.0"), "Senior Backend Engineer")

    def test_profile_headline_and_known_title_later_in_source_are_not_body_copy(self):
        candidate = context()
        candidate["profile"]["headline"] = "Senior Backend Engineer"
        candidate["career_text"] += "\nSenior Backend Engineer"
        candidate["preferences"]["target_titles"] = None  # Older callers may omit preference values.
        output = selection(candidate, experience=["career_text.0", "career_text.1", "profile.headline"],
                           cover_letter=["career_text.0", "profile.headline"])
        docs, resume, letter, _ = self.render(candidate, output)
        self.assertNotIn("Senior Backend Engineer", " ".join(b.text for b in resume + letter))
        self.assertEqual(set(docs.review["omitted_source_ids"]), {"profile.headline", "career_text.1"})

    def test_actual_summary_claim_is_not_removed_like_bare_role_label(self):
        candidate = context()
        candidate["profile"]["headline"] = "Built Java services for payment operations."
        output = selection(candidate, summary=["profile.headline"], experience=["career_text.0"], cover_letter=["profile.headline"])
        docs, resume, _, _ = self.render(candidate, output)
        self.assertEqual(self.sections(resume)["Professional Summary"], [candidate["profile"]["headline"]])
        self.assertNotIn("profile.headline", docs.review["omitted_source_ids"])

    def test_accomplishment_commas_and_year_are_not_employment_header(self):
        candidate = context()
        candidate["career_text"] = "Engineer, Example Market, 2021-present.\nBuilt Java services, using Kafka, in 2024 at Example Market for payment operations."
        output = selection(candidate, experience=["career_text.0", "career_text.1"], cover_letter=["career_text.1"])
        docs, resume, letter, provider = self.render(candidate, output)
        hint = provider.calls[0]["payload"]["document_evidence_hints"]["career_text.1"]
        self.assertFalse(hint["employment_header"])
        self.assertIn(candidate["career_text"].splitlines()[1], [b.text for b in resume])
        self.assertIn("I built Java services, using Kafka, in 2024 at Example Market for payment operations.", " ".join(b.text for b in letter))
        self.assertEqual(docs.review["unattributed_source_ids"], [])

    def test_degree_with_institution_and_year_is_education_not_employment(self):
        candidate = context()
        candidate["career_text"] = "BCom Accounting, Example College, 2018. ACCA studies in progress, not qualified."
        output = selection(candidate, summary=["career_text.0"], cover_letter=["career_text.0"])
        docs, resume, letter, _ = self.render(candidate, output)
        self.assertEqual(self.sections(resume)["Education"], [candidate["career_text"]])
        self.assertNotIn("Work Experience", self.sections(resume))
        self.assertNotIn("Professional Summary", self.sections(resume))
        self.assert_composition_grounded(candidate, docs, letter)

    def test_n26_autonomy_excerpt_has_noncredential_contract_with_real_degree_counterfactual(self):
        # Public excerpt supplied by main, not a claim to have retrieved a full JD.
        # The remaining description is synthetic. No hosted candidate/ATS request.
        candidate = context()
        candidate["job"].update(title="Senior Backend Engineer - Customer Risk Lifecycle", company_name="N26",
            description="A high degree of autonomy and access to cutting edge technologies.\nA Computer Science degree is required.")
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        docs, _, _, provider = self.render(candidate, output)
        contracts = provider.calls[0]["payload"]["requirement_contract"]
        self.assertEqual(contracts, [
            {"source_id": "job.requirements.0", "credential_wording": False, "importance": "unknown"},
            {"source_id": "job.requirements.1", "credential_wording": True, "importance": "required"}])
        self.assertNotIn("job.requirements.0", docs.review["requirements_needing_review"])
        self.assertIn("job.requirements.1", docs.review["requirements_needing_review"])
        self.assertTrue(any("Computer Science degree" in question for question in docs.questions))

    def test_wrong_model_education_classification_still_cannot_clear_autonomy(self):
        candidate = context()
        candidate["job"]["description"] = "A high degree of autonomy and access to cutting edge technologies."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        output["requirements"] = [{"requirement": {"text": candidate["job"]["description"], "source_ids": ["job.requirements.0"]},
            "category": "education", "importance": "unknown", "credential_name": "degree", "jurisdiction": "",
            "candidate_source_ids": ["career_text.0"], "assessment": "supported"}]
        docs, _, _, _ = self.render(candidate, output)
        self.assertEqual(docs.model_metadata["rubric_reason_code"], "rubric_contract_invalid")
        self.assertEqual(docs.model_metadata["rubric_unresolved_requirement_count"], 0)
        self.assertTrue(any("nothing is cleared" in question for question in docs.questions))

    def test_bare_role_label_is_not_a_fake_duty_connection(self):
        candidate = context()
        candidate["job"].update(title="Product Manager", description="B2B SaaS Product Manager. Customer discovery and analytics required.")
        candidate["career_text"] = "Led discovery interviews with 35 customers for a SaaS product."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        docs, _, _, _ = self.render(candidate, output)
        self.assertEqual(docs.composition["letter_paragraphs"][0]["job_quote"], "Customer discovery and analytics required.")

    def test_existing_confirmed_outcome_does_not_trigger_generic_metrics_question(self):
        candidate = context()
        candidate["career_text"] = "Reduced reporting time by 20% in 2024."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        output["questions"] = ["metrics", "eligibility"]
        docs, _, _, _ = self.render(candidate, output)
        self.assertNotIn(studio._QUESTIONS["metrics"], docs.questions)
        self.assertIn(studio._QUESTIONS["eligibility"], docs.questions)
        self.assertTrue(any("short evidence-based draft" in q for q in docs.questions))

    def test_arbitrary_composer_prose_is_rejected_even_with_valid_source_id(self):
        candidate = context()
        _, facts = studio._context(candidate)
        claim = studio._Claim(text="Prepared monthly forecasts at Google and saved $500000.", source_ids=["career_text.0"])
        with self.assertRaises(ValueError):
            compose_letter([claim], facts, studio._document_evidence_hints(facts, candidate))

    def test_narrow_grammar_does_not_rephrase_passive_or_first_person_sources(self):
        for original in ("Built by another team; reviewed by me.", "Used to support a reporting team.",
                         "I led the migration; did not manage anyone.", "Skills: SQL (coursework only).",
                         "No management responsibility."):
            self.assertEqual(letter_sentence(original), (original, "verbatim"))
        original = "Led technical reviews; did not manage direct reports."
        self.assertEqual(letter_sentence(original), ("I led technical reviews; did not manage direct reports.", "first_person_subject"))

    def test_historic_reviews_remain_readable_and_new_notice_is_truthful(self):
        candidate = context()
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        docs, _, _, _ = self.render(candidate, output)
        self.assertIn("first-person grammar", docs.review["notice"])
        legacy = copy.deepcopy(docs.review)
        legacy["notice"] = LEGACY_NOTICE
        self.assertEqual(validated_saved_document_review(legacy)["notice"], LEGACY_NOTICE)


if __name__ == "__main__":
    unittest.main()
