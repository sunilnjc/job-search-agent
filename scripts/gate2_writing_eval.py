"""Six paired synthetic writing cases via the existing bounded LiveBoundary.

Two single-use phases, six requests each, no retries. Both use identical existing
fixtures and the configured GPT-4.1 adapter. Rendering is captured as structural
blocks; main can render these later without another provider call. No Auth,
database, employer requests, email, real candidate input or PDF/DOCX creation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "scripts")]
import mobile_journey_check as harness
from jobagent.mobile import studio
from jobagent.mobile.document_review import validated_document_review

IDS = ("P01", "P02", "P03", "P04", "P06", "P07")
PER_PHASE = 6
OUTPUT = ROOT / "docs/testing/evidence/gate2-writing-20260915"


def cases():
    fixtures = json.loads((ROOT / "tests/fixtures/resumes/personas.json").read_text())
    selected = {p["id"]: p for p in fixtures if p["id"] in IDS}
    if set(selected) != set(IDS):
        raise ValueError("Missing synthetic persona fixture")
    return [selected[key] for key in IDS]


def context_for(persona):
    return {
        "profile": {"display_name": persona["name"], "email": persona["email"],
                    "phone": persona["phone"], "base_location": persona["location"]},
        "career_text": "\n".join(persona["facts"]), "resume_text": "", "answers": [],
        "career_background": {"profession": persona["profession"], "experience_level": persona["career_stage"], "qualifications": []},
        "preferences": {"target_titles": persona["roles"], "preferred_locations": persona["locations"],
                        "remote_preference": persona["remote"], "sponsorship_required": persona["sponsorship"],
                        "work_authorization_notes": persona["constraints"]},
        "job": {**persona["job"], "id": "00000000-0000-4000-8000-" + persona["id"][1:].zfill(12)},
    }


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rich_case():
    return json.loads((ROOT / "tests/fixtures/resumes/rich_senior_backend.json").read_text())


def rich_test_selection(persona):
    """An explicit test oracle, not a model response or quality judgment.

    Deliberately misfiles the headline and repeats one accomplishment in summary
    to challenge presentation/duplication; all ten actual accomplishments and
    both dated employment rows are selected once for the experience input.
    """
    contract = persona["test_contract"]
    def claims(indices):
        return [{"text": persona["facts"][index], "source_ids": [f"career_text.{index}"]} for index in indices]
    experience = sorted(contract["employment_indices"] + contract["accomplishment_indices"])
    return {"summary": claims([contract["headline_index"], contract["accomplishment_indices"][0]]),
            "experience": claims(experience), "education": claims(contract["education_indices"]),
            "skills": claims(contract["skills_indices"]), "cover_letter": claims(contract["letter_indices"]),
            "requirements": [], "questions": []}


def rich_profile_evaluation():
    """Offline rich-profile structural test with credentials/sockets forbidden."""
    persona = rich_case()
    selected = rich_test_selection(persona)
    class TestSelectionProvider:
        model_metadata = {"provider": "offline_deterministic_test_selection", "model_name": "deterministic-test-selector",
                          "input_tokens": 0, "output_tokens": 0, "prompt_version": studio.PROMPT_VERSION}
        def complete(self, **kwargs):
            return json.dumps(selected)
    class TestBoundary:
        def provider(self):
            return TestSelectionProvider()
    with patch("socket.socket.connect", side_effect=AssertionError("Offline evaluation forbids network")), \
            patch.object(studio, "MobileProvider", side_effect=AssertionError("Offline evaluation forbids credentials")):
        record = run_case(persona, TestBoundary(), offline=True)
    record["evaluation_kind"] = "offline_deterministic_selection_not_model_acceptance"
    if record["status"] != "passed":
        return record
    contract = persona["test_contract"]
    body = [b["text"] for b in record["resume_blocks"] if b["style"] == "body"]
    indices = contract["accomplishment_indices"]
    accomplishments = [persona["facts"][i] for i in indices]
    owners = contract["accomplishment_employers"]
    lineage = [{"source_id": f"career_text.{i}", "employer": owners[str(i)],
                "exact_occurrences_in_resume": body.count(persona["facts"][i]),
                "attribution_present_in_same_fact": owners[str(i)] in persona["facts"][i]}
               for i in indices]
    qualifications = [persona["facts"][i] for i in contract["education_indices"]]
    employment = [persona["facts"][i] for i in contract["employment_indices"]]
    sections = record["review"]["resume_sections"]
    checks = {
        "ten_substantive_accomplishments_retained_once": len(indices) == 10 and all(body.count(f) == 1 for f in accomplishments),
        "two_employment_rows_retained_once": len(employment) == 2 and all(body.count(f) == 1 for f in employment),
        "all_employers_explicit_in_each_accomplishment": all(row["attribution_present_in_same_fact"] for row in lineage),
        "education_retained_once": all(body.count(f) == 1 for f in qualifications),
        "skills_backed_by_named_accomplishments": all(any(skill in fact for fact in accomplishments) for skill in contract["backed_skills"]),
        "headline_not_body_copy": persona["facts"][contract["headline_index"]] not in body,
        "headline_omission_visible": f"career_text.{contract['headline_index']}" in record["review"]["omitted_source_ids"],
        "no_false_unattributed_warning": record["review"]["unattributed_source_ids"] == [],
        "no_empty_or_generic_summary": "Professional Summary" not in sections,
        "no_false_sparse_warning": not record["structural_assessment"]["short_evidence_warning"],
        "no_source_trace_violations": record["structural_assessment"]["source_trace_violations"] == [],
        "resume_evidence_at_least_350_words": record["structural_assessment"]["resume_evidence_words"] >= 350,
        "letter_has_four_distinct_source_examples": record["structural_assessment"]["letter_facts"] == 4,
        "letter_has_actual_posting_context": record["structural_assessment"]["distinct_posting_connections"] >= 1,
    }
    record["rich_profile_checks"] = checks
    record["accomplishment_lineage"] = lineage
    record["rich_profile_passed"] = all(checks.values())
    return record


def save_rich_profile_evaluation():
    record = rich_profile_evaluation()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {"phase": "rich_profile_offline", "provider_requests": 0, "real_http_requests": 0,
              "fixture_sha256": digest(rich_case()),
              "studio_sha256": hashlib.sha256((ROOT / "src/jobagent/mobile/studio.py").read_bytes()).hexdigest(),
              "composition_sha256": hashlib.sha256((ROOT / "src/jobagent/mobile/writing.py").read_bytes()).hexdigest(),
              "warning": "Deterministic selection proves source retention/composition, not live model selection or premium prose quality.",
              "records": [record]}
    (OUTPUT / "rich-profile-offline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"phase": report["phase"], "provider_requests": 0,
                      "passed": record.get("rich_profile_passed", False),
                      "assessment": record["structural_assessment"], "checks": record.get("rich_profile_checks", {})}))
    return 0 if record.get("rich_profile_passed") else 1


def assess_record(record):
    """Deterministic coverage/provenance metrics, NOT an LLM quality rubber stamp.

    Human review must still assess clarity and usefulness. A 200/validated packet
    does not by itself constitute adequate career evidence or premium writing.
    """
    if record.get("status") != "passed":
        return {"packet_validated": False}
    review = record["review"]
    ledger = {source["id"]: source for source in review["snapshot"]["sources"]}
    resume_ids = [ref for refs in review["resume_sections"].values() for ref in refs]
    letter_ids = review["cover_letter_source_ids"]
    resume_text = [block["text"] for block in record["resume_blocks"] if block["style"] == "body"]
    letter_text = [block["text"] for block in record["letter_blocks"]]
    composition = record.get("composition", {})
    paragraphs = composition.get("letter_paragraphs", [])
    checked, quotes, violations = [], [], []
    if paragraphs:
        from jobagent.mobile.writing import letter_sentence
        for paragraph in paragraphs:
            if paragraph["text"] not in letter_text:
                violations.append("paragraph_not_rendered")
            quote = paragraph["job_quote"]
            if quote is not None:
                job_ref = paragraph["job_source_id"]
                if job_ref not in ledger or ledger[job_ref]["text"] != quote:
                    violations.append("unsupported_job_quote")
                quotes.append(job_ref)
            expected = f'Your posting highlights “{quote}” ' if quote is not None else ""
            for sentence in paragraph["candidate_sentences"]:
                ref = sentence["source_id"]
                checked.append(ref)
                source = ledger.get(ref, {}).get("text", "")
                if (sentence["text"], sentence["operation"]) != letter_sentence(source):
                    violations.append("unsupported_candidate_transformation")
                if expected and not expected.endswith(" "):
                    expected += " " if expected.endswith((".", "!", "?", ";")) else ". "
                expected += sentence["text"]
            if expected != paragraph["text"]:
                violations.append("untraced_paragraph_wording")
    else:
        for ref in letter_ids:
            checked.append(ref)
            if ledger[ref]["text"] not in letter_text:
                violations.append("original_fact_not_rendered")
    if checked != letter_ids:
        violations.append("letter_source_order_mismatch")
    if resume_text != [ledger[ref]["text"] for ref in resume_ids]:
        violations.append("resume_body_not_exact_sources")
    if len(checked) != len(set(checked)) or len(resume_text) != len(set(resume_text)):
        violations.append("repeated_candidate_evidence")
    return {
        "packet_validated": True, "source_trace_violations": violations,
        "resume_facts": len(resume_ids), "letter_facts": len(letter_ids),
        "resume_evidence_words": sum(len(ledger[ref]["text"].split()) for ref in resume_ids),
        "letter_evidence_words": sum(len(ledger[ref]["text"].split()) for ref in letter_ids),
        "distinct_posting_connections": len(set(quotes)),
        "omitted_facts": review["omitted_source_ids"],
        "unattributed_facts_flagged": review["unattributed_source_ids"],
        "followup_count": len(record["questions"]),
        "short_evidence_warning": any("short evidence-based draft" in question for question in record["questions"]),
    }


def run_case(persona, boundary, *, offline=False):
    record = {"persona": persona["id"], "status": "not_started", "source": context_for(persona)}
    captured, selected = [], {}
    started = time.monotonic()
    provider = boundary.provider()  # Only existing adapter loads approved credentials.
    if not offline and provider.model_metadata.get("model_name") not in {"gpt-4.1", "gpt-4.1-2025-04-14"}:
        raise ValueError("Configured model differs from the approved stable GPT-4.1")

    class Capture:
        @property
        def model_metadata(self):
            return provider.model_metadata

        def complete(self, **kwargs):
            raw = provider.complete(**kwargs)
            selected.update(json.loads(raw))  # Fixed synthetic input, no transport fields.
            return raw

    def blocks_only(blocks, **kwargs):
        captured.append([{"text": b.text, "style": b.style, "href": b.href} for b in blocks])
        return b"STRUCTURAL_EVALUATION_ONLY_NOT_A_DOCUMENT"

    try:
        with patch.object(studio, "_provider", return_value=Capture()), \
                patch.object(studio, "render_pdf", side_effect=blocks_only), \
                patch.object(studio, "render_docx", side_effect=blocks_only):
            documents = studio.prepare_documents(record["source"],
                "career_change" if persona["career_stage"] == "career_change" else "role_aligned")
        if len(captured) != 4 or captured[0] != captured[1] or captured[2] != captured[3]:
            raise ValueError("Renderer block parity failed")
        record.update(status="passed", review=validated_document_review(documents),
                      questions=documents.questions, metadata=documents.model_metadata,
                      resume_blocks=captured[0], letter_blocks=captured[2],
                      composition=getattr(documents, "composition", {}))
    except Exception as error:
        record.update(status="failed", error_type=type(error).__name__,
                      metadata=studio.get_model_metadata(), questions=getattr(error, "questions", []))
    record["selected_output"] = selected
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    record["structural_assessment"] = assess_record(record)
    return record


def replay_captured():
    """Recompose the actual paid selections after deterministic repairs, for $0.

    This is explicitly not another live model evaluation. Keep both paid reports
    immutable so original defects and the exact twelve-request record stay visible.
    """
    original = json.loads((OUTPUT / "after.json").read_text())
    fixtures = cases()
    if original["fixture_sha256"] != digest(fixtures) or len(original["records"]) != len(fixtures):
        raise ValueError("Captured responses do not match the current synthetic fixtures")
    records = []
    for persona, previous in zip(fixtures, original["records"]):
        if persona["id"] != previous["persona"] or previous["status"] != "passed":
            raise ValueError("Missing successful captured selection")
        class CapturedProvider:
            model_metadata = {"provider": "offline_captured_selection", "model_name": "gpt-4.1-2025-04-14",
                              "input_tokens": 0, "output_tokens": 0, "prompt_version": studio.PROMPT_VERSION}
            def complete(self, **kwargs):
                return json.dumps(previous["selected_output"])
        class CapturedBoundary:
            def provider(self):
                return CapturedProvider()
        record = run_case(persona, CapturedBoundary())
        record["original_paid_metadata"] = previous["metadata"]
        records.append(record)
    report = {"phase": "offline_replay", "basis": "after.json actual paid selections, no retry or model call",
              "fixture_sha256": digest(fixtures), "provider_requests": 0, "real_http_requests": 0,
              "studio_sha256": hashlib.sha256((ROOT / "src/jobagent/mobile/studio.py").read_bytes()).hexdigest(),
              "composition_sha256": hashlib.sha256((ROOT / "src/jobagent/mobile/writing.py").read_bytes()).hexdigest(),
              "records": records}
    (OUTPUT / "final-replay.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    before = json.loads((OUTPUT / "before.json").read_text())
    if before["fixture_sha256"] != digest(fixtures) or len(before["records"]) != len(records):
        raise ValueError("Before/after comparison requires identical fixture sets")
    pairs = [{"persona": row["persona"], "before": assess_record(old), "paid_after": assess_record(paid),
              "final_composition": assess_record(row)}
             for old, paid, row in zip(before["records"], original["records"], records)]
    (OUTPUT / "comparison.json").write_text(json.dumps({"pairs": pairs,
        "provider_requests": before["provider_requests"] + original["provider_requests"],
        "real_http_requests": before["real_http_requests"] + original["real_http_requests"],
        "input_tokens": before["usage"]["input_tokens"] + original["usage"]["input_tokens"],
        "output_tokens": before["usage"]["output_tokens"] + original["usage"]["output_tokens"],
        "estimated_upper_cost_usd": round(before["usage"]["estimated_upper_cost_usd"] + original["usage"]["estimated_upper_cost_usd"], 6),
        "warning": "Structural checks are not an independent semantic or prose-quality score."}, indent=2) + "\n")
    print(json.dumps({"phase": "offline_replay", "provider_requests": 0,
                      "passed": sum(row["status"] == "passed" for row in records),
                      "source_trace_violations": [row["structural_assessment"].get("source_trace_violations") for row in records]}))
    return 0 if all(row["status"] == "passed" and not row["structural_assessment"]["source_trace_violations"] for row in records) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after", "replay", "rich-offline"))
    parser.add_argument("--execute-live-ai", action="store_true")
    parser.add_argument("--confirm-synthetic-data", action="store_true")
    args = parser.parse_args()
    if args.phase == "replay":
        return replay_captured()
    if args.phase == "rich-offline":
        return save_rich_profile_evaluation()
    if not (args.execute_live_ai and args.confirm_synthetic_data):
        parser.error("Both live-AI and synthetic-data flags are required; no requests made.")
    fixtures = cases()
    fixture_hash = digest(fixtures)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.phase == "after":
        before = json.loads((OUTPUT / "before.json").read_text())
        if before["fixture_sha256"] != fixture_hash:
            parser.error("Fixtures changed; refuse an invalid before/after comparison.")
    # An exclusive durable phase reservation prevents accidental paid reruns,
    # including after a crash. Each phase reserves six of the twelve calls.
    with (OUTPUT / (args.phase + ".reserved.json")).open("x") as reservation:
        json.dump({"max_provider_requests": PER_PHASE, "fixture_sha256": fixture_hash}, reservation)
    report = {"phase": args.phase, "fixture_sha256": fixture_hash,
              "started_at": datetime.now(timezone.utc).isoformat(), "records": [],
              "boundary": "synthetic fixtures; existing OpenAI adapter; structural blocks only",
              "max_provider_requests": PER_PHASE, "max_mission_requests": 12,
              "studio_sha256": hashlib.sha256((ROOT / "src/jobagent/mobile/studio.py").read_bytes()).hexdigest(),
              "composition_sha256": hashlib.sha256((ROOT / "src/jobagent/mobile/writing.py").read_bytes()).hexdigest(),
              "pricing": {"input_per_million_usd": 2.0, "output_per_million_usd": 8.0,
                          "source": "https://developers.openai.com/api/docs/models/gpt-4.1",
                          "cost_kind": "uncached upper estimate, not invoice; no cache discount assumed"}}
    logging.disable(logging.CRITICAL)
    with patch.object(harness, "MAX_LIVE_CALLS", PER_PHASE), harness.LiveBoundary() as boundary:
        for persona in fixtures:
            # Suppress SDK/library output; only allowlisted report fields are printed.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                record = run_case(persona, boundary)
            report["records"].append(record)
            report["provider_requests"] = len(boundary.calls)
            report["real_http_requests"] = boundary.http_requests
            report["calls"] = boundary.calls
            input_tokens = sum(call.get("metadata", {}).get("input_tokens", 0) for call in boundary.calls)
            output_tokens = sum(call.get("metadata", {}).get("output_tokens", 0) for call in boundary.calls)
            report["usage"] = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                               "estimated_upper_cost_usd": round((2 * input_tokens + 8 * output_tokens) / 1_000_000, 6)}
            (OUTPUT / (args.phase + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"phase": args.phase, "persona": persona["id"], "status": record["status"],
                              "provider_requests": len(boundary.calls), "seconds": record["elapsed_seconds"]}), flush=True)
    return 0 if len(report["records"]) == 6 and all(row["status"] == "passed" for row in report["records"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
