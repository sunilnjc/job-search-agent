"""Provider wire format: select evidence IDs; never regenerate factual text.

The studio's ordinary claim/rubric validators still run after materialization.
This is not a semantic truth check and does not confer verified qualifications.
"""
from __future__ import annotations

import copy
import json

from .professions import requirement_importance
from .evidence import claim_allowed


def selection_schema(schema: dict, payload: dict) -> dict:
    result = copy.deepcopy(schema)
    definitions = result.get("$defs", {})
    if "_Claim" not in definitions:
        return result
    facts = payload.get("source_facts", [])
    candidate = [f["id"] for f in facts if claim_allowed(f, documents=True)]
    selected = candidate if payload.get("operation") == "documents" else [f["id"] for f in facts if claim_allowed(f)]
    requirements = [f["id"] for f in facts if f["kind"] == "job" and f["id"].startswith("job.requirements.") and claim_allowed(f)]
    for name, identifiers in (("SelectedFactID", selected), ("CandidateFactID", candidate),
                              ("RequirementFactID", requirements)):
        # JSON Schema enums cannot be empty. The sentinel can never materialize;
        # optional empty arrays remain the appropriate response for no evidence.
        definitions[name] = {"type": "string", "enum": identifiers or ["__no_evidence_available__"]}
    for name, reference in (("_Claim", "SelectedFactID"), ("RequirementClaim", "RequirementFactID")):
        if name not in definitions:
            continue
        definition = definitions[name]
        definition["properties"].pop("text", None)
        definition["required"] = ["source_ids"]
        definition["properties"]["source_ids"]["items"] = {"$ref": "#/$defs/" + reference}
    if "RequirementAssessment" in definitions:
        rubric = definitions["RequirementAssessment"]
        # Requirement strength is a deterministic policy, not a model guess.
        rubric["properties"].pop("importance", None)
        rubric["required"] = list(rubric["properties"])
        rubric["properties"]["candidate_source_ids"]["items"] = {"$ref": "#/$defs/CandidateFactID"}
    return result


def materialize_selection(raw: str, payload: dict, schema: dict) -> str:
    if "_Claim" not in schema.get("$defs", {}):
        return raw
    if not isinstance(raw, str) or len(raw) > 64_000:
        raise ValueError("Invalid selection response")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate selection key")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(value, dict):
        raise ValueError("Invalid selection object")
    ledger = {fact["id"]: fact for fact in payload.get("source_facts", [])}

    def claim(item, *, requirement=False):
        if not isinstance(item, dict) or set(item) != {"source_ids"}:
            raise ValueError("Only evidence selections are accepted")
        refs = item["source_ids"]
        if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], str):
            raise ValueError("Select exactly one evidence ID")
        fact = ledger.get(refs[0])
        if not fact or (requirement and (fact["kind"] != "job" or not refs[0].startswith("job.requirements."))):
            raise ValueError("Unknown evidence selection")
        if not requirement and payload.get("operation") == "documents" and fact["kind"] != "candidate":
            raise ValueError("Documents need candidate evidence")
        if not claim_allowed(fact, documents=not requirement and payload.get("operation") == "documents"):
            raise ValueError("This evidence is context only, not a permitted prose claim")
        return {"text": fact["text"], "source_ids": refs}

    for key in ("claims", "summary", "experience", "education", "skills", "cover_letter"):
        if key in value:
            if not isinstance(value[key], list):
                raise ValueError("Invalid claim array")
            value[key] = [claim(item) for item in value[key]]
    if "requirements" in value:
        if not isinstance(value["requirements"], list):
            raise ValueError("Invalid requirement array")
        for item in value["requirements"]:
            if not isinstance(item, dict) or "importance" in item or "requirement" not in item:
                raise ValueError("Invalid requirement selection")
            item["requirement"] = claim(item["requirement"], requirement=True)
            item["importance"] = requirement_importance(item["requirement"]["text"])
    return json.dumps(value, ensure_ascii=False)
