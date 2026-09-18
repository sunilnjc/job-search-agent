"""Structured why/evidence/uncertainty shape with rationale fallbacks."""
import unittest

from jobagent.mobile.fit_explanation import (
    coerce_fit_explanation, explanation_from_entries, explanation_shape,
    fit_explanation_from_rationale, rationale_from_entries, validated_fit_explanation,
)
from jobagent.mobile.schemas import FitExplanation, RankResult


class FitExplanationTests(unittest.TestCase):
    def test_schema_accepts_additive_fields_and_legacy_rationale_only(self):
        legacy = RankResult(score=7.0, recommendation="review", rationale="Fit estimate, not an ATS score.")
        self.assertIsNone(legacy.fit_explanation)
        shaped = RankResult.model_validate({
            "score": 8.0, "recommendation": "match", "rationale": "Fit estimate, not an ATS score.",
            "fit_explanation": {
                "why": ["Fit estimate, not an ATS score."],
                "evidence": ["Confirmed information: Led a clinic team"],
                "uncertainty": ["Work eligibility is unknown, not confirmed ineligible."],
            },
        })
        self.assertEqual(set(shaped.fit_explanation.model_dump()), {"why", "evidence", "uncertainty"})

    def test_entries_preserve_rationale_and_split_buckets(self):
        entries = [
            ("why", "Fit estimate, not an ATS score or prediction of an interview."),
            ("evidence", "Confirmed information: Ran community clinics [career_text.0]"),
            ("uncertainty", "Work eligibility is unknown, not confirmed ineligible."),
        ]
        rationale = rationale_from_entries(entries)
        self.assertIn("Fit estimate", rationale)
        self.assertIn("Confirmed information", rationale)
        self.assertNotIn("career_text.0", rationale)
        shape = explanation_from_entries(entries)
        self.assertEqual(shape["why"][0][:12], "Fit estimate")
        self.assertEqual(shape["evidence"][0], "Confirmed information: Ran community clinics")
        self.assertIn("unknown", shape["uncertainty"][0])

    def test_legacy_rationale_fallback_and_invalid_json(self):
        rationale = (
            "Fit estimate, not an ATS score or prediction of an interview.\n"
            "Confirmed information: Built reports [career_text.0]\n"
            "The numeric fit estimate is unvalidated, not a pass on any requirement."
        )
        fallback = fit_explanation_from_rationale(rationale)
        self.assertTrue(fallback["why"])
        self.assertEqual(fallback["evidence"], ["Confirmed information: Built reports"])
        self.assertTrue(fallback["uncertainty"])
        self.assertNotIn("career_text", fallback["evidence"][0])
        self.assertIsNone(validated_fit_explanation("not-an-object"))
        self.assertIsNone(validated_fit_explanation({"why": [], "evidence": [], "uncertainty": []}))
        coerced = coerce_fit_explanation({"why": ["Kept"], "evidence": [], "uncertainty": [], "extra": "drop"},
                                         rationale="ignored")
        self.assertEqual(coerced["why"], ["Kept"])
        empty = coerce_fit_explanation(None, rationale=rationale)
        self.assertEqual(empty["evidence"], fallback["evidence"])

    def test_pydantic_fit_explanation_bounds(self):
        with self.assertRaises(Exception):
            FitExplanation(why=["x" * 2001], evidence=[], uncertainty=[])
        self.assertEqual(explanation_shape(["  Why  "], [], []), {"why": ["Why"], "evidence": [], "uncertainty": []})
        self.assertEqual(
            explanation_shape(["Confirmed information: Built reports [career_text.0]"], [], []),
            {"why": ["Confirmed information: Built reports"], "evidence": [], "uncertainty": []},
        )

if __name__ == "__main__":
    unittest.main()
