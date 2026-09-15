"""Only bounded, enumerated rubric diagnostics may reach stored run metadata."""
import unittest
from types import SimpleNamespace

from jobagent.mobile.app import model_metadata


class MobileModelMetadataTests(unittest.TestCase):
    def test_known_recovery_diagnostic_is_preserved(self):
        diagnostic = {"rubric_reason_code": "rubric_contract_invalid",
                      "rubric_rejected_count": 2,
                      "rubric_unresolved_requirement_count": 7}
        result = model_metadata(SimpleNamespace(model_metadata=diagnostic))
        self.assertEqual({key: result[key] for key in diagnostic}, diagnostic)

    def test_unknown_codes_and_unbounded_values_are_not_logged(self):
        for code in ("private provider response", "rubric_contract_invalid"):
            for value in (True, -1, 101, "private candidate data"):
                result = model_metadata(SimpleNamespace(model_metadata={
                    "rubric_reason_code": code, "rubric_rejected_count": value,
                    "rubric_unresolved_requirement_count": value, "raw_response": "secret"}))
                self.assertNotIn("raw_response", result)
                self.assertNotIn("rubric_rejected_count", result)
                self.assertNotIn("rubric_unresolved_requirement_count", result)
                if code != "rubric_contract_invalid":
                    self.assertNotIn("rubric_reason_code", result)
