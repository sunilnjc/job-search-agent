"""Deterministic discovery rules: synthetic facts only, no network or providers."""
from __future__ import annotations

import copy
import json
import math
import unittest
from datetime import date

from jobagent.mobile.discovery_relevance import (
    METHOD, is_role_query, matches_titles, profile_evidence, relevance, title_level,
    _requirements, _segments,
)


def profile(profession="", level="unspecified", career_text="", qualifications=None, **extra):
    return {"career_background": {"profession": profession, "experience_level": level,
        "qualifications": qualifications or []}, "career_text": career_text, **extra}


def job(title, description="Review the complete employer posting.", **changes):
    return {"title": title, "description": description, "location_text": "Toronto Canada",
        "workplace_type": "onsite", "content_truncated": False, **changes}


def qualification(name="RN licence", status="current", **changes):
    return {"name": name, "kind": "licence", "status": status, "jurisdiction": "Ontario", **changes}


class TitleRuleTests(unittest.TestCase):
    def test_backend_aliases_reordering_and_explicit_seniority(self):
        for title in ("Senior Software Engineer, Backend", "Sr. Back-End Developer",
                      "Senior Back End Engineer", "Software Developer - Backend - Senior"):
            with self.subTest(title=title):
                self.assertTrue(matches_titles(["Senior Backend Engineer"], title))
        for title in ("Junior Backend Engineer", "Backend Engineer", "Staff Backend Engineer",
                      "Senior Frontend Engineer", "Senior Product Manager", "Senior Financial Analyst",
                      "Senior Backend Engineering Manager"):
            with self.subTest(title=title):
                self.assertFalse(matches_titles(["Senior Backend Engineer"], title))

    def test_professions_are_not_collapsed_into_software_or_ambiguous_pm(self):
        for term, title in (("RN", "Registered Nurse"), ("Nursing", "Registered Nurse"),
                            ("HR Manager", "Human Resources Manager"),
                            ("FP&A Analyst", "Financial Planning and Analysis Analyst"),
                            ("Accounting", "Senior Accountant"),
                            ("Teaching", "Primary School Teacher"),
                            ("Mechanical Engineer", "Senior Mechanical Engineer"),
                            ("Product Management", "Senior Product Manager"),
                            ("Enfermera", "ENFERMERA pediátrica")):
            with self.subTest(term=term):
                self.assertTrue(matches_titles([term], title))
        for term, title in (("PM", "Product Manager"), ("PM", "Project Manager"),
                            ("Product Manager", "Program Manager, Product"),
                            ("Project Manager", "Product Manager"),
                            ("Accountant", "Account Manager"),
                            ("Mechanical Engineer", "Software Engineer"),
                            ("Nurse", "Nurse Practitioner"),
                            ("Registered Nurse", "Registered Nurse Manager")):
            with self.subTest(term=term, title=title):
                self.assertFalse(matches_titles([term], title))

    def test_or_within_group_never_drops_seniority_or_other_target_words(self):
        self.assertTrue(matches_titles(["Senior Backend Engineer", "Registered Nurse"], "Registered Nurse"))
        self.assertFalse(matches_titles(["Senior Backend Engineer", "Registered Nurse"], "Junior Backend Engineer"))
        self.assertFalse(matches_titles(["Senior Backend Engineer Python"], "Senior Backend Engineer"))
        self.assertTrue(matches_titles(["Nurse " * 39 + "Nurse."], "Registered Nurse"))

    def test_ambiguous_titles_do_not_imply_levels(self):
        self.assertIsNone(title_level("Staff Nurse"))
        self.assertIsNone(title_level("Associate Physician"))
        self.assertEqual(title_level("Associate Director, Finance"), 5)
        self.assertEqual(title_level("Staff Software Engineer"), 4)
        self.assertEqual(title_level("Intern, Mechanical Engineering"), 0)
        self.assertEqual(title_level("Entry-level Accountant"), 1)
        self.assertTrue(is_role_query("Senior Backend Engineer"))
        self.assertFalse(is_role_query("Python SQL"))


class RelevanceTests(unittest.TestCase):
    def score(self, row, saved, **kwargs):
        return relevance(row, profile_evidence(saved), **kwargs)

    def test_senior_backend_outranks_junior_pm_finance_and_keyword_dump(self):
        saved = profile("Backend Engineer", "senior", "Built Python SQL services and distributed systems.")
        text = "Python SQL services distributed systems. "
        rows = [job("Junior Backend Engineer", text), job("Product Manager", text * 100),
                job("Finance Analyst", "Senior Backend Engineer. " + text * 100),
                job("Senior Software Engineer, Backend", text)]
        scores = [self.score(row, saved)["score"] for row in rows]
        self.assertGreater(scores[-1], max(scores[:-1]))
        self.assertEqual(scores[1:3], [0, 0])
        self.assertIn("career stage", " ".join(self.score(rows[0], saved)["gaps"]))

    def test_pm_finance_and_nursing_profiles_have_own_positive_role_evidence(self):
        for profession, title, unrelated in (
                ("Product Management", "Senior Product Manager", "Senior Project Manager"),
                ("Accounting", "Senior Accountant", "Senior Account Manager"),
                ("Nursing", "Registered Nurse", "Senior Software Engineer"),
                ("Mechanical Engineer", "Senior Mechanical Engineer", "Senior Backend Engineer")):
            with self.subTest(profession=profession):
                saved = profile(profession, "senior")
                good = self.score(job(title), saved)
                bad = self.score(job(unrelated, title * 100), saved)
                self.assertGreater(good["score"], bad["score"])
                self.assertIn("self-reported", " ".join(good["reasons"]))

    def test_career_change_never_turns_target_role_into_prior_experience(self):
        saved = profile("Teacher", "career_change", "Taught mathematics and coordinated projects.")
        kwargs = {"target_titles": ["Backend Engineer"]}
        junior = self.score(job("Junior Backend Engineer"), saved, **kwargs)
        senior = self.score(job("Senior Backend Engineer"), saved, **kwargs)
        self.assertGreater(junior["score"], senior["score"])
        self.assertIn("preference, not proof of experience", " ".join(junior["reasons"]))
        self.assertIn("not established", " ".join(senior["gaps"]))
        self.assertNotIn("overlaps the profession", " ".join(junior["reasons"]))

    def test_saved_career_text_can_supply_an_explicit_title_without_background(self):
        saved = profile(career_text="Role: Senior Backend Engineer\nBuilt Python services.")
        senior = self.score(job("Senior Software Engineer, Backend", "Python services."), saved)
        junior = self.score(job("Junior Backend Engineer", "Python services."), saved)
        self.assertGreater(senior["score"], junior["score"])
        self.assertIn("explicit role", " ".join(senior["reasons"]))
        for career in ("I am a Senior Backend Engineer with experience building Python services.",
                       "Senior Backend Engineer with ten years building Python SQL services."):
            with self.subTest(career=career):
                self.assertGreater(self.score(job("Senior Backend Engineer"), profile(career_text=career))["score"], 0)

    def test_aspirations_negations_imports_and_contact_are_not_positive_facts(self):
        saved = profile(career_text="I want to be a Senior Backend Engineer.\nNo Python experience.\nI am learning SQL.\nI worked as a nurse without registration.",
            resume_text="Senior Backend Engineer Python SQL", extracted_resume_text="Senior Backend Engineer",
            display_name="Private Name", email="candidate@example.test", phone="+1234567",
            base_location="US", skills=["Python"], ai_summary="Senior Backend Engineer")
        evidence = profile_evidence(saved)
        self.assertFalse(evidence.available)
        result = relevance(job("Senior Backend Engineer", "Python SQL"), evidence)
        self.assertEqual(result["score"], 0)
        for marker in ("Private", "candidate@", "+1234567", "Python", "SQL"):
            self.assertNotIn(marker, json.dumps(result))

    def test_colleagues_and_hiring_descriptions_are_not_candidate_job_titles(self):
        saved = profile(career_text="Collaborated with nurse teams.\nHiring senior backend engineers.\nSupported a Product Manager.")
        self.assertEqual(profile_evidence(saved).career_roles, ())

    def test_repeated_words_cannot_accumulate_points_and_contact_lines_are_ignored(self):
        saved = profile("Backend Engineer", "senior", "Python SQL distributed services.\nContact candidate@example.test for Senior Backend Engineer roles.")
        one = self.score(job("Senior Backend Engineer", "Python SQL distributed services."), saved)
        many = self.score(job("Senior Backend Engineer", "Python SQL distributed services. " * 100), saved)
        self.assertEqual(one, many)
        self.assertNotIn("candidate", profile_evidence(saved).career_terms)

    def test_optional_credentials_and_optional_headers_are_not_mandatory_gaps(self):
        saved = profile("Accountant", "senior")
        for wording in ("CPA preferred.", "Degree not required.", "No degree required.", "Optional qualifications\nCPA\nMBA",
                        "Requirements\nCPA preferred\nA degree is not mandatory"):
            with self.subTest(wording=wording):
                result = self.score(job("Senior Accountant", wording), saved)
                self.assertNotIn("Mandatory credential", " ".join(result["gaps"]))
                self.assertIn("Optional/preferred", " ".join(result["reasons"]))
                self.assertEqual(result["score"], self.score(job("Senior Accountant"), saved)["score"])

    def test_identity_tokens_contacts_and_oversized_descriptors_never_leak_into_reasons(self):
        saved = profile("Backend Engineer", "senior", "Jane Doe built Python services.\nPhone: +971 555 123456\n" + "x" * 4000,
                        display_name="Jane Doe", email="candidate@example.test")
        result = self.score(job("Senior Backend Engineer", "Jane Doe Python services. +971 555 123456 " + "x" * 4000), saved)
        serialized = json.dumps(result).lower()
        for private in ("jane", "doe", "971", "candidate@example", "x" * 100):
            self.assertNotIn(private, serialized)
        self.assertTrue(all(len(reason) <= 2000 for reason in result["reasons"] + result["gaps"]))

    def test_mandatory_and_optional_requirements_are_distinguished_per_clause(self):
        result = self.score(job("Registered Nurse", "Requirements\nRN licence required; BLS preferred.\nMust have five years of clinical practice."), profile("Nurse"))
        self.assertIn("Mandatory credential", " ".join(result["gaps"]))
        self.assertIn("five years", " ".join(result["gaps"]))
        self.assertIn("BLS preferred", " ".join(result["reasons"]))
        self.assertNotIn("BLS preferred", " ".join(result["gaps"]))
        self.assertNotIn("you lack", json.dumps(result))

    def test_mixed_or_alternative_credentials_remain_unresolved(self):
        result = self.score(job("Senior Accountant", "CPA preferred but a degree is required. CPA or equivalent required."),
                             profile("Accountant", qualifications=[qualification("CPA")]))
        self.assertTrue(result["review_required"])
        self.assertIn("employer acceptance remain unverified", " ".join(result["reasons"]))
        self.assertNotIn("satisfied", json.dumps(result))

    def test_licence_status_expiry_jurisdiction_and_equivalence_never_prove_eligibility(self):
        today = date(2026, 9, 15)
        for status, expires in (("expired", None), ("not_held", None), ("in_progress", None),
                                ("unknown", None), ("current", "2026-01-01")):
            with self.subTest(status=status, expires=expires):
                saved = profile("Nurse", qualifications=[qualification(status=status, expires_on=expires)])
                result = self.score(job("Registered Nurse", "RN licence required in California."), saved, today=today)
                self.assertIn("not self-reported current", " ".join(result["gaps"]))
                self.assertNotIn("self-reported current qualification", " ".join(result["reasons"]))
        saved = profile("Nurse", qualifications=[qualification(expires_on="2027-01-01")])
        result = self.score(job("Registered Nurse", "RN licence required in California."), saved, today=today)
        self.assertIn("jurisdiction, equivalence", " ".join(result["reasons"]))
        self.assertIn("provisional", " ".join(result["gaps"]))

    def test_international_remote_rights_stay_provisional_even_with_location_or_false_sponsorship(self):
        saved = profile("Backend Engineer", "senior", base_location="New York US", sponsorship_required=False,
                        work_authorization_notes="Authorized worldwide")
        result = self.score(job("Senior Backend Engineer", "Remote US only. Must be authorized to work in US. No visa sponsorship."), saved)
        self.assertTrue(result["review_required"])
        self.assertIn("profile location is not authorization", " ".join(result["gaps"]))
        self.assertNotIn("Authorized worldwide", json.dumps(result))

    def test_contract_bounds_determinism_no_mutation_or_private_identifiers(self):
        saved = profile("Backend Engineer", "senior", "Python SQL distributed systems services data.",
                        user_id="PRIVATE_OWNER", email="private@example.test")
        row = job("Senior Backend Engineer", "Python SQL distributed systems services data.\n" + "Degree required; " * 100,
                  content_truncated=True)
        before = copy.deepcopy((saved, row))
        result = self.score(row, saved, target_titles=["Backend Engineer"])
        self.assertEqual(result, self.score(row, saved, target_titles=["Backend Engineer"]))
        self.assertEqual((saved, row), before)
        self.assertEqual(set(result), {"method", "score", "reasons", "gaps", "review_required"})
        self.assertEqual(result["method"], METHOD)
        self.assertTrue(math.isfinite(result["score"]))
        self.assertLessEqual(result["score"], 100)
        self.assertGreaterEqual(result["score"], 0)
        for field in ("reasons", "gaps"):
            self.assertLessEqual(len(result[field]), 50)
            self.assertTrue(all(isinstance(x, str) and len(x) <= 2000 for x in result[field]))
        self.assertNotIn("PRIVATE_OWNER", json.dumps(result))
        self.assertNotIn("private@example", json.dumps(result))
        self.assertNotIn("%", json.dumps(result))

    def test_null_empty_and_ignored_only_profiles_have_no_default_persona(self):
        for saved in (None, {}, {"career_background": None}, {"display_name": "Candidate", "base_location": "Dubai"}):
            with self.subTest(saved=saved):
                self.assertFalse(profile_evidence(saved).available)
                self.assertEqual(self.score(job("Senior Backend Engineer"), saved)["score"], 0)

    def test_invalid_career_data_rejected_not_coerced_to_a_default_profile(self):
        for saved in ({"career_background": []}, {"career_background": {"experience_level": "expert"}},
                      {"career_text": 123}, {"career_text": "x" * 100001},
                      {"career_text": "broken\ud800"}, {"career_background": {"qualifications": [qualification()] * 31}}):
            with self.subTest(keys=list(saved)), self.assertRaises((ValueError, TypeError)):
                profile_evidence(saved)


class PostingSegmentationTests(unittest.TestCase):
    """Bullets shown to a customer must be whole sentences, not feed fragments."""

    def score(self, row, saved, **kwargs):
        return relevance(row, profile_evidence(saved), **kwargs)

    def test_a_bullet_broken_across_feed_lines_is_rejoined_not_shown_in_halves(self):
        # Boards emit <br> inside one bullet; each half used to be its own "gap".
        saved = profile("Backend Engineer", "senior")
        result = self.score(job("Senior Backend Engineer", "Requirements:\n"
            "Can think about how data will pass through your software\n"
            "from persistent storage through to API endpoint\n"), saved)
        gaps = " ".join(result["gaps"])
        self.assertIn("your software from persistent storage through to API endpoint", gaps)
        self.assertNotIn("Explicit requirement needs job-specific evidence review: from persistent", gaps)

    def test_section_headings_set_context_without_becoming_requirements(self):
        saved = profile("Backend Engineer", "senior")
        result = self.score(job("Senior Backend Engineer",
            "This role will be a great fit if you:\nHave hands-on experience designing data flow\n"), saved)
        serialized = json.dumps(result)
        self.assertNotIn("great fit if you", serialized)
        self.assertIn("hands-on experience designing data flow", " ".join(result["gaps"]))

    def test_colon_headings_from_real_boards_do_not_leak_as_gaps(self):
        saved = profile("Backend Engineer", "senior")
        result = self.score(job("Senior Backend Engineer", "In This Role, You Will:\n"
            "Do discovery, design, develop and maintain software.\n"
            "What You Need to Be Successful:\nSound understanding of microservice architecture\n"), saved)
        for heading in ("In This Role", "What You Need to Be Successful"):
            self.assertNotIn(heading, json.dumps(result))

    def test_generic_industry_words_are_not_presented_as_career_evidence(self):
        saved = profile("Backend Engineer", "senior", "Built Python PostgreSQL services with design reviews.")
        result = self.score(job("Senior Backend Engineer", "Python PostgreSQL services design reviews."), saved)
        overlap = next(r for r in result["reasons"] if "mentions terms" in r)
        for signal in ("postgresql", "python"):
            self.assertIn(signal, overlap)
        for noise in ("design", "reviews", "services"):
            self.assertNotIn(noise, overlap)


class LaunchClosureRequirementsSegmentationTests(unittest.TestCase):
    """Heading/list boundaries must survive lowercase bullets after a heading."""

    def test_lowercase_bullets_after_requirements_heading_stay_separate(self):
        description = "Requirements:\n- python\n- kafka"
        self.assertEqual(list(_segments(description)), ["Requirements:", "python", "kafka"])
        self.assertEqual(list(_requirements(description)),
                         [("python", "required"), ("kafka", "required")])

    def test_mixed_case_bullets_preferred_section_and_credentials(self):
        description = (
            "Requirements:\n"
            "- Python experience\n"
            "- Kafka streaming\n"
            "Preferred:\n"
            "- CPA preferred\n"
            "- AWS certification is a bonus\n"
            "1. current RN licence\n"
        )
        self.assertEqual(
            list(_segments(description)),
            ["Requirements:", "Python experience", "Kafka streaming",
             "Preferred:", "CPA preferred", "AWS certification is a bonus",
             "current RN licence"],
        )
        reqs = list(_requirements(description))
        self.assertIn(("Python experience", "required"), reqs)
        self.assertIn(("Kafka streaming", "required"), reqs)
        optional = [clause for clause, importance in reqs if importance == "optional"]
        self.assertTrue(any("CPA" in clause for clause in optional))
        self.assertTrue(any("AWS" in clause or "certification" in clause.lower() for clause in optional))
        self.assertTrue(any("RN" in clause for clause, _ in reqs))

    def test_genuine_wrapped_lowercase_continuation_still_rejoins(self):
        description = (
            "Requirements:\n"
            "Can think about how data will pass through your software\n"
            "from persistent storage through to API endpoint\n"
        )
        segments = list(_segments(description))
        self.assertEqual(
            segments,
            ["Requirements:",
             "Can think about how data will pass through your software "
             "from persistent storage through to API endpoint"],
        )
        self.assertEqual(
            list(_requirements(description)),
            [("Can think about how data will pass through your software "
              "from persistent storage through to API endpoint", "required")],
        )


if __name__ == "__main__":
    unittest.main()
