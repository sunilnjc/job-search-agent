"""Offline regression coverage for the real provider's evidence-ID protocol."""
import copy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from jobagent.mobile import studio
from jobagent.mobile.selections import materialize_selection, selection_schema


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"operation": "documents", "source_facts": [
            {"id": "career_text.0", "kind": "candidate", "text": "Built reporting services; did not own the entire platform."},
            {"id": "job.requirements.0", "kind": "job", "text": "A degree is not required."},
        ]}
        self.schema = studio._DocumentOutput.model_json_schema()
        self.wire = {"summary": [{"source_ids": ["career_text.0"]}], "experience": [],
                     "education": [], "skills": [], "cover_letter": [{"source_ids": ["career_text.0"]}],
                     "questions": [], "requirements": [{
                         "requirement": {"source_ids": ["job.requirements.0"]},
                         "category": "education", "credential_name": "degree", "jurisdiction": "",
                         "candidate_source_ids": [], "assessment": "uncertain"}]}

    def test_wire_schema_selects_only_known_scoped_ids_without_factual_text(self):
        schema = selection_schema(self.schema, self.payload)
        definitions = schema["$defs"]
        self.assertEqual(definitions["SelectedFactID"]["enum"], ["career_text.0"])
        self.assertEqual(definitions["RequirementFactID"]["enum"], ["job.requirements.0"])
        self.assertEqual(set(definitions["_Claim"]["properties"]), {"source_ids"})
        self.assertEqual(set(definitions["RequirementClaim"]["properties"]), {"source_ids"})
        self.assertNotIn("importance", definitions["RequirementAssessment"]["properties"])
        self.assertIn("text", self.schema["$defs"]["_Claim"]["properties"])

    def test_materialization_preserves_whole_facts_negations_and_requirement_strength(self):
        result = json.loads(materialize_selection(json.dumps(self.wire), self.payload, self.schema))
        self.assertEqual(result["summary"][0]["text"], self.payload["source_facts"][0]["text"])
        self.assertEqual(result["requirements"][0]["requirement"]["text"], "A degree is not required.")
        self.assertEqual(result["requirements"][0]["importance"], "preferred")
        studio._DocumentOutput.model_validate(result)
        self.assertNotIn("text", self.wire["summary"][0])

    def test_model_cannot_supply_rewritten_text_or_unknown_or_wrong_kind_reference(self):
        for replacement in (
            {"text": "Owned the entire platform.", "source_ids": ["career_text.0"]},
            {"source_ids": ["invented.source"]}, {"source_ids": ["job.requirements.0"]},
            {"source_ids": []}, {"source_ids": ["career_text.0", "career_text.0"]},
            {"source_ids": [False]}, {"source_ids": ["__no_evidence_available__"]},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                value = copy.deepcopy(self.wire)
                value["summary"] = [replacement]
                materialize_selection(json.dumps(value), self.payload, self.schema)

    def test_requirement_cannot_select_candidate_fact_or_override_strength(self):
        for change in ({"requirement": {"source_ids": ["career_text.0"]}}, {"importance": "required"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                value = copy.deepcopy(self.wire)
                value["requirements"][0].update(change)
                materialize_selection(json.dumps(value), self.payload, self.schema)

    def test_duplicate_keys_and_overlong_output_rejected_before_materialization(self):
        for raw in ('{"summary": [], "summary": []}', " " * 64_001, '[]'):
            with self.assertRaises(ValueError):
                materialize_selection(raw, self.payload, self.schema)

    def test_openai_adapter_uses_selection_schema_and_returns_validatable_full_claims(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4.1-2025-04-14", usage=SimpleNamespace(prompt_tokens=30, completion_tokens=10),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                refusal=None, content=json.dumps(self.wire)))])
        config = SimpleNamespace(settings=SimpleNamespace(openai_api_key="", anthropic_api_key=""))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-key"}, clear=True), patch.dict("sys.modules", {
            "jobagent.config": config, "openai": SimpleNamespace(OpenAI=MagicMock(return_value=client))
        }):
            result = studio._complete(self.payload, studio._DocumentOutput)
        self.assertEqual(result.summary[0].text, self.payload["source_facts"][0]["text"])
        sent = client.chat.completions.create.call_args.kwargs["response_format"]["json_schema"]["schema"]
        self.assertNotIn("text", sent["$defs"]["_Claim"]["properties"])
        self.assertEqual(studio.get_model_metadata()["input_tokens"], 30)


if __name__ == "__main__":
    unittest.main()
