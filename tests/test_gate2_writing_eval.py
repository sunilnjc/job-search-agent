"""Exercise the bounded synthetic evaluator with an offline provider only."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gate2_writing_eval as evaluation
from jobagent.mobile import studio
from test_studio_launch_quality import selection


class EvalHarnessTests(unittest.TestCase):
    def setUp(self):
        network = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        credentials = patch.object(studio, "MobileProvider", side_effect=AssertionError("Credentials forbidden"))
        credentials.start()
        self.addCleanup(credentials.stop)

    def result(self):
        persona = evaluation.cases()[0]
        output = selection(evaluation.context_for(persona), experience=["career_text.2", "career_text.3"], cover_letter=["career_text.2", "career_text.3"])
        class FakeProvider:
            model_metadata = {"model_name": "gpt-4.1", "input_tokens": 1, "output_tokens": 1}
            def complete(self, **kwargs):
                return json.dumps(output)
        class FakeBoundary:
            def provider(self):
                return FakeProvider()
        return evaluation.run_case(persona, FakeBoundary())

    def test_fixture_set_has_six_distinct_professions_and_no_real_contacts(self):
        personas = evaluation.cases()
        self.assertEqual([p["id"] for p in personas], list(evaluation.IDS))
        self.assertEqual(len(personas), 6)
        self.assertGreaterEqual(len({p["profession"] for p in personas}), 4)
        for persona in personas:
            self.assertTrue(persona["email"].endswith("@example.test"))
        self.assertEqual(evaluation.PER_PHASE * 2, 12)

    def test_single_case_captures_blocks_without_files_or_credentials(self):
        result = self.result()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["structural_assessment"]["source_trace_violations"], [])
        self.assertGreater(result["structural_assessment"]["distinct_posting_connections"], 0)
        self.assertNotIn("STRUCTURAL_EVALUATION_ONLY", repr(result))

    def test_assessor_detects_invented_rendered_prose_not_just_valid_packet(self):
        result = self.result()
        paragraph = result["composition"]["letter_paragraphs"][0]
        old = paragraph["text"]
        paragraph["text"] += " I managed 100 people at Imaginary Company."
        next(b for b in result["letter_blocks"] if b["text"] == old)["text"] = paragraph["text"]
        self.assertIn("untraced_paragraph_wording", evaluation.assess_record(result)["source_trace_violations"])

    def test_assessor_detects_source_drift_metrics_and_duplicate_resume(self):
        original = self.result()
        for alteration in ("metric", "duplicate", "job"):
            result = copy.deepcopy(original)
            if alteration == "metric":
                sentence = result["composition"]["letter_paragraphs"][0]["candidate_sentences"][0]
                sentence["text"] += " Saved $1000000."
            elif alteration == "duplicate":
                result["resume_blocks"].append(next(b for b in result["resume_blocks"] if b["style"] == "body"))
            else:
                result["composition"]["letter_paragraphs"][0]["job_quote"] = "New fabricated requirement."
            self.assertTrue(evaluation.assess_record(result)["source_trace_violations"])

    def test_rich_profile_keeps_depth_attribution_and_factual_caveats(self):
        result = evaluation.rich_profile_evaluation()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["rich_profile_passed"], result.get("rich_profile_checks"))
        self.assertTrue(all(result["rich_profile_checks"].values()))
        self.assertEqual(result["metadata"]["model_name"], "deterministic-test-selector")
        self.assertEqual(result["metadata"]["input_tokens"], 0)
        self.assertEqual(result["metadata"]["output_tokens"], 0)
        self.assertEqual(len(result["accomplishment_lineage"]), 10)
        self.assertEqual(len({r["employer"] for r in result["accomplishment_lineage"]}), 2)
        self.assertGreaterEqual(result["structural_assessment"]["resume_evidence_words"], 350)
        self.assertGreaterEqual(result["structural_assessment"]["letter_evidence_words"], 100)
        body = " ".join(b["text"] for b in result["resume_blocks"])
        for limitation in ("without direct reports", "human approval for production releases",
                           "operations team retained incident-command responsibility",
                           "not a claim of a production-wide cost reduction", "No Kubernetes production experience"):
            self.assertIn(limitation, body)

    def test_rich_profile_oracle_catches_deleted_or_changed_employer_attribution(self):
        persona = evaluation.rich_case()
        for change in ("employer", "metric"):
            output = evaluation.rich_test_selection(persona)
            accomplishment = next(claim for claim in output["experience"] if claim["source_ids"] == ["career_text.3"])
            if change == "employer":
                accomplishment["text"] = accomplishment["text"].replace("Fictional Harbor Payments", "Example Cedar Banking")
            else:
                accomplishment["text"] = accomplishment["text"].replace("40 minutes", "4 minutes")
            with self.subTest(change=change), patch.object(evaluation, "rich_test_selection", return_value=output):
                result = evaluation.rich_profile_evaluation()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_type"], "MissingFactsError")

    def test_rich_profile_oracle_detects_dropped_accomplishment_even_if_packet_valid(self):
        output = evaluation.rich_test_selection(evaluation.rich_case())
        output["experience"] = [claim for claim in output["experience"] if claim["source_ids"] != ["career_text.12"]]
        with patch.object(evaluation, "rich_test_selection", return_value=output):
            result = evaluation.rich_profile_evaluation()
        self.assertEqual(result["status"], "passed")  # Schema success is not quality success.
        self.assertFalse(result["rich_profile_passed"])
        self.assertFalse(result["rich_profile_checks"]["ten_substantive_accomplishments_retained_once"])


if __name__ == "__main__":
    unittest.main()
