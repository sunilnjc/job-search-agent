"""Document-content regressions: fake provider, inspected blocks, no file exports.

No credentials or environment files are read. Both renderers are patched, so this
suite creates neither PDF nor DOCX bytes and makes no network requests.
"""
import copy
import json
import unittest
from unittest.mock import patch

from jobagent.mobile import studio


def context():
    return {
        "profile": {"display_name": "Mira Example", "email": "mira@example.test"},
        "career_text": "Prepared monthly forecasts for a wholesale business.",
        "career_background": {"profession": "Finance", "experience_level": "senior", "qualifications": []},
        "job": {"id": "00000000-0000-4000-8000-000000000001", "title": "Finance Manager",
                "company_name": "Synthetic Forecast Group", "description": "Prepare monthly forecasts and discuss variance analysis."},
        "preferences": {}, "answers": [],
    }


class Provider:
    def __init__(self, output):
        self.output, self.calls = output, []

    @property
    def model_metadata(self):
        return {"provider": "offline", "model_name": "synthetic-selector", "status": "received",
                "prompt_version": studio.PROMPT_VERSION}

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.output)


def selection(candidate, **sections):
    _, facts = studio._context(candidate)
    ledger = {fact["id"]: fact["text"] for fact in facts}
    result = {name: [] for name in ("summary", "experience", "education", "skills", "cover_letter")}
    for name, refs in sections.items():
        result[name] = [{"text": ledger[ref], "source_ids": [ref]} for ref in refs]
    result.update(questions=[], requirements=[])
    return result


class StudioLaunchQualityTests(unittest.TestCase):
    def setUp(self):
        network = patch("socket.socket.connect", side_effect=AssertionError("Network is forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def render(self, candidate, output, variant="role_aligned"):
        calls, provider = [], Provider(output)
        def inspect_blocks(blocks, **kwargs):
            calls.append(list(blocks))
            return b"structural-test-only"
        with patch.object(studio, "_provider", return_value=provider), \
                patch.object(studio, "render_pdf", side_effect=inspect_blocks), \
                patch.object(studio, "render_docx", side_effect=inspect_blocks):
            result = studio.prepare_documents(candidate, variant)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[2], calls[3])
        return result, calls[0], calls[2], provider

    def sections(self, blocks):
        result, heading = {}, "Identity"
        for block in blocks:
            if block.style == "heading":
                heading = block.text
            else:
                result.setdefault(heading, []).append(block.text)
        return result

    def test_flat_degree_and_project_are_not_work_even_when_model_misfiles_them(self):
        candidate = context()
        candidate["career_text"] = ("Prepared monthly forecasts for a wholesale business.\n"
                                    "BSc Accounting, Synthetic College, 2018.\n"
                                    "Personal project: built a household budget workbook.")
        output = selection(candidate, experience=["career_text.0", "career_text.1", "career_text.2"],
                           cover_letter=["career_text.0"])
        _, resume, _, _ = self.render(candidate, output)
        sections = self.sections(resume)
        self.assertEqual(sections["Work Experience"], [candidate["career_text"].splitlines()[0]])
        self.assertEqual(sections["Education"], [candidate["career_text"].splitlines()[1]])
        self.assertEqual(sections["Projects"], [candidate["career_text"].splitlines()[2]])

    def test_source_headings_keep_student_project_work_separate_and_remove_heading_facts(self):
        candidate = context()
        candidate["career_background"]["experience_level"] = "student"
        candidate["career_text"] = ("EDUCATION\nExample College - computing course, 2025.\n"
                                    "PROJECTS\nBuilt a booking prototype for coursework.\n"
                                    "SKILLS\nReact, SQLite")
        output = selection(candidate, experience=[f"career_text.{i}" for i in range(6)], cover_letter=["career_text.3"])
        result, resume, _, _ = self.render(candidate, output)
        sections = self.sections(resume)
        self.assertNotIn("Work Experience", sections)
        self.assertEqual(sections["Education"], ["Example College - computing course, 2025."])
        self.assertEqual(sections["Projects"], ["Built a booking prototype for coursework."])
        self.assertEqual(sections["Skills"], ["React, SQLite"])
        self.assertEqual(result[0]["source_ids"], ["career_text.1", "career_text.3", "career_text.5"])

    def test_career_switcher_training_and_personal_project_are_not_new_employment(self):
        candidate = context()
        candidate["career_text"] = ("Mechanical Engineer, Fictional Machinery Workshop, 2018-2025.\n"
                                    "Completed a React and Python software bootcamp in March 2026.\n"
                                    "Built a personal inventory dashboard; no professional software employment.\n"
                                    "BEng Mechanical Engineering, Example University, 2018.")
        output = selection(candidate, experience=[f"career_text.{i}" for i in range(4)], cover_letter=["career_text.2"])
        _, resume, letter, _ = self.render(candidate, output, "career_change")
        sections = self.sections(resume)
        self.assertEqual(sections["Work Experience"], [candidate["career_text"].splitlines()[0]])
        self.assertEqual(len(sections["Education"]), 2)
        self.assertEqual(sections["Projects"], [candidate["career_text"].splitlines()[2]])
        self.assertIn("no professional software employment", " ".join(b.text for b in letter))

    def test_capstone_and_nursing_registration_limitations_get_honest_headings(self):
        candidate = context()
        candidate["career_text"] = ("Capstone: transit arrival visualization using public sample data.\n"
                                    "Philippine nursing licence self-reported current. No UK NMC registration.")
        output = selection(candidate, experience=["career_text.0", "career_text.1"], cover_letter=["career_text.1"])
        _, resume, _, _ = self.render(candidate, output)
        sections = self.sections(resume)
        self.assertNotIn("Work Experience", sections)
        self.assertEqual(sections["Qualifications"], [candidate["career_text"].splitlines()[1]])

    def test_work_on_a_licence_system_does_not_become_a_personal_qualification(self):
        candidate = context()
        candidate["career_text"] = "Maintained the licence renewal reporting system for the transport team."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        _, resume, _, _ = self.render(candidate, output)
        self.assertEqual(self.sections(resume)["Work Experience"], [candidate["career_text"]])
        self.assertNotIn("Qualifications", self.sections(resume))

    def test_structured_project_skills_education_and_volunteering_use_source_labels(self):
        candidate = context()
        candidate["profile"].update(projects=["Built a budgeting tool as a personal project."],
                                    education=["Completed an accounting diploma in 2017."], skills=["Excel"])
        candidate["career_text"] = "VOLUNTEER EXPERIENCE\nMaintained the community pantry ledger."
        output = selection(candidate, experience=["profile.projects.0", "profile.education.0", "profile.skills.0", "career_text.1"],
                           cover_letter=["profile.projects.0"])
        _, resume, _, _ = self.render(candidate, output)
        self.assertEqual(set(self.sections(resume)) - {"Identity"}, {"Projects", "Education", "Skills", "Volunteer Experience"})

    def test_duplicate_source_content_appears_once_per_document(self):
        candidate = context()
        output = selection(candidate, summary=["career_text.0"], experience=["career_text.0", "career_text.0"],
                           cover_letter=["career_text.0", "career_text.0"])
        result, resume, letter, _ = self.render(candidate, output)
        self.assertEqual([block.text for block in resume].count(candidate["career_text"]), 1)
        self.assertEqual(" ".join(block.text for block in letter).count("I prepared monthly forecasts for a wholesale business."), 1)
        self.assertEqual(result[0]["source_ids"], ["career_text.0"])
        self.assertEqual(result[2]["source_ids"], ["career_text.0"])

    def test_identity_is_not_a_professional_summary_or_cover_example(self):
        candidate = context()
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        result, resume, letter, _ = self.render(candidate, output)
        self.assertNotIn("Professional Summary", self.sections(resume))
        self.assertEqual([b.text for b in letter].count("Mira Example"), 2)  # header and signature only
        # Identity is not offered to the model, so an identity citation is rejected outright.
        output["summary"] = [{"text": "Mira Example", "source_ids": ["profile.display_name"]}]
        with self.assertRaises(studio.MissingFactsError):
            self.render(candidate, output)

    def test_heading_or_identity_only_model_output_is_not_a_successful_document(self):
        for text, ref, claimed in (("SKILLS", "career_text.0", "SKILLS"),
                                   ("Prepared monthly forecasts.", "profile.display_name", "Mira Example")):
            candidate = context()
            candidate["career_text"] = text
            output = selection(candidate)
            output["experience"] = output["cover_letter"] = [{"text": claimed, "source_ids": [ref]}]
            with self.subTest(ref=ref), self.assertRaises(studio.MissingFactsError):
                self.render(candidate, output)

    def test_actual_artifact_provenance_does_not_include_other_documents_or_unused_facts(self):
        candidate = context()
        candidate["career_text"] += "\nReduced the monthly reporting cycle by two days.\nCompleted an unrelated short course."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.1"])
        result, _, _, _ = self.render(candidate, output)
        self.assertEqual(result[0]["source_ids"], ["career_text.0"])
        self.assertEqual(result[2]["source_ids"], ["career_text.1"])
        self.assertEqual(result.review["omitted_source_ids"], ["career_text.2"])
        self.assertEqual(result.review["mode"], "selected_verbatim_facts")
        self.assertIn("not a free-form rewrite", result.review["notice"])

    def test_selector_receives_literal_relevance_cues_for_actual_job_not_assumed_profession(self):
        candidate = context()
        candidate["career_text"] += "\nOrganized a community event.\nPrepared variance analysis for budget owners."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        result, _, _, provider = self.render(candidate, output)
        hints = provider.calls[0]["payload"]["document_evidence_hints"]
        self.assertGreater(hints["career_text.0"]["literal_overlap"], hints["career_text.1"]["literal_overlap"])
        self.assertIn("career_text.2", result.review["omitted_with_literal_job_overlap"])
        self.assertIn("not proof of fit", provider.calls[0]["system"])
        self.assertNotIn("finance", provider.calls[0]["system"].lower())

    def test_no_relevance_cue_strips_a_negative_or_adds_a_skill(self):
        candidate = context()
        candidate["career_text"] = "Used dashboards prepared by the analytics team; no Python programming experience."
        candidate["job"]["description"] = "Build Python forecasting pipelines."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        _, resume, letter, _ = self.render(candidate, output)
        self.assertIn(candidate["career_text"], [b.text for b in resume])
        self.assertIn("I used dashboards prepared by the analytics team; no Python programming experience.",
                      [b.text for b in letter])
        forged = copy.deepcopy(output)
        forged["experience"][0]["text"] = "Python programming experience."
        with self.assertRaises(studio.MissingFactsError):
            self.render(candidate, forged)

    def test_job_requirements_cannot_become_candidate_examples(self):
        candidate = context()
        output = selection(candidate, experience=["career_text.0"], cover_letter=["job.description"])
        with self.assertRaises(studio.MissingFactsError):
            self.render(candidate, output)

    def test_source_errors_stay_visible_instead_of_being_silently_laundered(self):
        candidate = context()
        candidate["career_text"] = "Prepared montly forecsts for a wholesale business."
        output = selection(candidate, experience=["career_text.0"], cover_letter=["career_text.0"])
        result, resume, letter, _ = self.render(candidate, output)
        self.assertIn(candidate["career_text"], [b.text for b in resume])
        self.assertIn("I prepared montly forecsts for a wholesale business.", " ".join(b.text for b in letter))
        self.assertIn("source wording", result.review["notice"])

    def test_full_qualification_status_cannot_be_hidden_under_skills(self):
        candidate = context()
        candidate["career_background"]["qualifications"] = [{"name": "CIMA", "kind": "certification",
            "status": "in_progress", "jurisdiction": "", "expires_on": None, "evidence_note": "Completed the initial module."}]
        output = selection(candidate, experience=["career_text.0"], skills=["career_background.qualifications.0"],
                           cover_letter=["career_text.0"])
        _, resume, _, _ = self.render(candidate, output)
        sections = self.sections(resume)
        self.assertNotIn("Skills", sections)
        self.assertIn("status: in_progress", sections["Qualifications"][0])
        candidate["career_background"]["qualifications"][0]["status"] = "not_held"
        output = selection(candidate, experience=["career_text.0"], skills=["career_background.qualifications.0"],
                           cover_letter=["career_text.0"])
        with self.assertRaises(studio.MissingFactsError):
            self.render(candidate, output)

    def test_source_employer_and_dates_are_not_inferred_or_reordered(self):
        candidate = context()
        candidate["career_text"] = "Analyst, Synthetic Shop, 2020-2023.\nAnalyst, Example Mart, 2017-2020.\nPrepared inventory reports."
        output = selection(candidate, experience=["career_text.0", "career_text.1", "career_text.2"], cover_letter=["career_text.2"])
        result, resume, _, _ = self.render(candidate, output)
        self.assertEqual(self.sections(resume)["Work Experience"], candidate["career_text"].splitlines()[:2])
        self.assertEqual(self.sections(resume)["Selected Career Contributions"], ["Prepared inventory reports."])
        self.assertEqual(result.review["unattributed_source_ids"], ["career_text.2"])

    def test_career_change_projects_precede_confirmed_old_profession_without_relabeling(self):
        candidate = context()
        candidate["profile"]["projects"] = ["Personal project: React appointment planner."]
        output = selection(candidate, experience=["career_text.0", "profile.projects.0"], cover_letter=["profile.projects.0"])
        _, resume, letter, _ = self.render(candidate, output, "career_change")
        headings = [block.text for block in resume if block.style == "heading"]
        self.assertLess(headings.index("Projects"), headings.index("Work Experience"))
        self.assertNotIn("Software Engineer", " ".join(b.text for b in resume))
        self.assertIn("I am applying for the Finance Manager role at Synthetic Forecast Group.", " ".join(b.text for b in letter))


if __name__ == "__main__":
    unittest.main()
