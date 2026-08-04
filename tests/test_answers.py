"""Tests for the pre-approved answers library.

These are hermetic: every case builds a small dict in memory and passes it via the
``data=`` parameter, so nothing here reads the real (gitignored) config/answers.yaml.

The property under test throughout is the module's core guarantee: a form is never filled
with a value the user did not approve. An unfilled field must resolve to "needs input" —
never to a guess, and never to a neighbouring field's value.
"""
from __future__ import annotations

import unittest

from jobagent.profile.answers import (
    NEEDS_INPUT,
    answer_for,
    approved_facts_block,
    collect,
    get_field,
    years_for_skill,
)


def sample_answers():
    """A fully filled-in answers tree. Individual tests blank out what they care about."""
    return {
        "identity": {
            "full_name": "Sunilkumar Kalabandi",
            "email": "sunil@example.com",
            "phone": "+91 90000 00000",
            "location": "Hyderabad, India",
            "linkedin": "linkedin.com/in/sunilnjc",
            "github": "github.com/sunilnjc",
            "portfolio": NEEDS_INPUT,
        },
        "work_authorization": {
            "summary": "No US work authorization; requires employer sponsorship",
            "needs_sponsorship": True,
            "authorized_countries": ["India"],
            "relocation_notes": "Open to relocating with sponsorship support",
        },
        "availability": {
            "notice_period": "30 days",
            "earliest_start_date": NEEDS_INPUT,
        },
        "compensation": {
            "current_salary": "INR 40,00,000",
            "expected_salary": "INR 60,00,000",
        },
        "experience": {
            "total_years": 9,
            "years_by_skill": {"Java": 9, "Python": 4, "Rust": NEEDS_INPUT},
        },
        "education": {"highest_degree": "B.E. Computer Science"},
    }


class QuestionRoutingTests(unittest.TestCase):
    """Representative real-world form questions must land on the right field."""

    def setUp(self):
        self.data = sample_answers()

    def assert_routes(self, question, field, value):
        answer = answer_for(question, data=self.data)
        self.assertIsNotNone(answer, f"no pattern matched {question!r}")
        self.assertEqual(answer.field, field, f"{question!r} routed to {answer.field}")
        self.assertEqual(answer.value, value)
        self.assertFalse(answer.needs_input)

    def test_legal_authorization_question_uses_work_authorization_summary(self):
        self.assert_routes(
            "Are you legally authorized to work in the US?",
            "work_authorization.summary",
            "No US work authorization; requires employer sponsorship",
        )

    def test_sponsorship_question_uses_work_authorization_summary(self):
        self.assert_routes(
            "Will you require visa sponsorship, now or in the future?",
            "work_authorization.summary",
            "No US work authorization; requires employer sponsorship",
        )

    def test_notice_period_question(self):
        self.assert_routes("What is your notice period?", "availability.notice_period", "30 days")

    def test_salary_expectations_prefer_expected_over_current(self):
        # Ordering matters: the generic word "salary" appears in both patterns, and
        # answering an expectations question with the *current* number would leak a fact
        # the user never approved for that box.
        self.assert_routes(
            "What are your salary expectations?",
            "compensation.expected_salary",
            "INR 60,00,000",
        )

    def test_expected_ctc_also_routes_to_expected_salary(self):
        self.assert_routes("Expected CTC", "compensation.expected_salary", "INR 60,00,000")

    def test_current_ctc_routes_to_current_salary(self):
        self.assert_routes("Current CTC", "compensation.current_salary", "INR 40,00,000")

    def test_linkedin_profile_url(self):
        self.assert_routes("LinkedIn profile URL", "identity.linkedin", "linkedin.com/in/sunilnjc")

    def test_unrecognised_question_returns_none(self):
        # None (rather than a best-effort match) is what tells the caller to ask the user.
        self.assertIsNone(answer_for("Describe your favourite colour", data=self.data))
        self.assertIsNone(answer_for("What is your favourite band?", data=self.data))

    def test_label_comes_from_the_matched_pattern(self):
        answer = answer_for("Do you need sponsorship?", data=self.data)
        self.assertEqual(answer.label, "Work authorization")


class NeedsInputTests(unittest.TestCase):
    """An unfilled field must report itself as unfilled — and never borrow a value."""

    def test_needs_input_sentinel_is_not_a_value(self):
        data = sample_answers()
        data["availability"]["notice_period"] = NEEDS_INPUT

        answer = answer_for("What is your notice period?", data=data)
        self.assertEqual(answer.field, "availability.notice_period")
        self.assertTrue(answer.needs_input)
        self.assertIsNone(answer.value)

    def test_empty_string_needs_input(self):
        data = sample_answers()
        data["availability"]["notice_period"] = "   "

        answer = answer_for("How much notice do you have to give?", data=data)
        self.assertTrue(answer.needs_input)
        self.assertIsNone(answer.value)

    def test_none_needs_input(self):
        data = sample_answers()
        data["availability"]["notice_period"] = None

        answer = answer_for("What is your notice period?", data=data)
        self.assertTrue(answer.needs_input)
        self.assertIsNone(answer.value)

    def test_unfilled_field_does_not_fall_back_to_a_neighbouring_field(self):
        # The regression this whole module exists to prevent: expected salary is blank, so
        # the answer must be "ask me" — not the current salary sitting right next to it.
        data = sample_answers()
        data["compensation"]["expected_salary"] = NEEDS_INPUT

        answer = answer_for("What are your salary expectations?", data=data)
        self.assertEqual(answer.field, "compensation.expected_salary")
        self.assertTrue(answer.needs_input)
        self.assertIsNone(answer.value)
        self.assertNotEqual(answer.value, data["compensation"]["current_salary"])

    def test_case_insensitive_sentinel(self):
        self.assertTrue(get_field("a.b", data={"a": {"b": "needs_input"}}).needs_input)

    def test_missing_path_needs_input(self):
        for dotted in ("availability.notice_period", "identity.nickname", "nope.nothing.here"):
            with self.subTest(dotted=dotted):
                answer = get_field(dotted, data={})
                self.assertTrue(answer.needs_input)
                self.assertIsNone(answer.value)
                self.assertEqual(answer.field, dotted)

    def test_get_field_stringifies_non_string_values(self):
        answer = get_field("experience.total_years", data=sample_answers())
        self.assertEqual(answer.value, "9")
        self.assertFalse(answer.needs_input)

    def test_get_field_does_not_walk_through_a_scalar(self):
        answer = get_field("identity.email.domain", data=sample_answers())
        self.assertTrue(answer.needs_input)


class YearsForSkillTests(unittest.TestCase):
    def setUp(self):
        self.data = sample_answers()

    def test_lookup_is_case_insensitive(self):
        answer = years_for_skill("java", data=self.data)
        self.assertEqual(answer.value, "9")
        self.assertFalse(answer.needs_input)
        # Reports the canonical key from the config, not the caller's casing.
        self.assertEqual(answer.field, "experience.years_by_skill.Java")

    def test_exact_case_still_works(self):
        self.assertEqual(years_for_skill("Python", data=self.data).value, "4")

    def test_unlisted_skill_needs_input(self):
        answer = years_for_skill("COBOL", data=self.data)
        self.assertTrue(answer.needs_input)
        self.assertIsNone(answer.value)
        self.assertEqual(answer.label, "Years of COBOL")

    def test_skill_set_to_needs_input(self):
        answer = years_for_skill("Rust", data=self.data)
        self.assertTrue(answer.needs_input)
        self.assertIsNone(answer.value)

    def test_no_skills_section_at_all(self):
        self.assertTrue(years_for_skill("Java", data={}).needs_input)


class ApprovedFactsBlockTests(unittest.TestCase):
    def setUp(self):
        self.block = approved_facts_block(data=sample_answers())

    def test_includes_filled_values(self):
        self.assertIn("identity.full_name: Sunilkumar Kalabandi", self.block)
        self.assertIn("availability.notice_period: 30 days", self.block)
        self.assertIn("experience.years_by_skill.Java: 9", self.block)
        # Lists are flattened rather than dropped.
        self.assertIn("work_authorization.authorized_countries: India", self.block)

    def test_lists_unfilled_paths_under_not_provided(self):
        self.assertIn("NOT PROVIDED", self.block)
        not_provided = self.block.split("NOT PROVIDED", 1)[1]
        for path in (
            "identity.portfolio",
            "availability.earliest_start_date",
            "experience.years_by_skill.Rust",
        ):
            with self.subTest(path=path):
                self.assertIn(path, not_provided)

    def test_does_not_leak_the_sentinel_as_an_answer(self):
        # If NEEDS_INPUT ever appears here, an LLM reading this block would happily write
        # "NEEDS_INPUT" into a form field.
        self.assertNotIn(NEEDS_INPUT, self.block)
        self.assertNotIn("- identity.portfolio:", self.block)
        self.assertNotIn("- availability.earliest_start_date:", self.block)

    def test_blank_and_none_values_are_treated_as_unfilled(self):
        block = approved_facts_block(
            data={"identity": {"full_name": "  ", "email": None, "phone": "+1 555"}}
        )
        self.assertIn("- identity.phone: +1 555", block)
        self.assertNotIn("- identity.full_name:", block)
        self.assertNotIn("- identity.email:", block)
        not_provided = block.split("NOT PROVIDED", 1)[1]
        self.assertIn("identity.full_name", not_provided)
        self.assertIn("identity.email", not_provided)

    def test_no_not_provided_section_when_everything_is_filled(self):
        block = approved_facts_block(data={"identity": {"full_name": "Sunil"}})
        self.assertIn("- identity.full_name: Sunil", block)
        self.assertNotIn("NOT PROVIDED", block)

    def test_empty_config(self):
        self.assertEqual(approved_facts_block(data={}), "(No pre-approved answers configured.)")


class CollectTests(unittest.TestCase):
    """`collect` backs GET /api/answers — same guarantee, structured for JSON."""

    def test_splits_approved_values_from_unfilled_paths(self):
        approved, needs_input = collect(data=sample_answers())

        self.assertEqual(approved["identity"]["full_name"], "Sunilkumar Kalabandi")
        self.assertEqual(approved["compensation"]["expected_salary"], "INR 60,00,000")
        self.assertEqual(approved["work_authorization"]["authorized_countries"], ["India"])
        self.assertIn("identity.portfolio", needs_input)
        self.assertIn("availability.earliest_start_date", needs_input)
        self.assertIn("experience.years_by_skill.Rust", needs_input)
        self.assertNotIn("identity.full_name", needs_input)

    def test_unfilled_values_are_null_not_the_sentinel(self):
        approved, _ = collect(data=sample_answers())
        self.assertIsNone(approved["identity"]["portfolio"])
        self.assertIsNone(approved["availability"]["earliest_start_date"])
        self.assertIsNone(approved["experience"]["years_by_skill"]["Rust"])

    def test_blank_and_none_count_as_needing_input(self):
        approved, needs_input = collect(data={"identity": {"full_name": "  ", "email": None}})
        self.assertIsNone(approved["identity"]["full_name"])
        self.assertIsNone(approved["identity"]["email"])
        self.assertEqual(sorted(needs_input), ["identity.email", "identity.full_name"])

    def test_empty_config(self):
        self.assertEqual(collect(data={}), ({}, []))


if __name__ == "__main__":
    unittest.main()
