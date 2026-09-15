#!/usr/bin/env python3
"""Synthetic local FastAPI journey, hermetic unless BOTH live flags are supplied.

Default (no credentials, network or paid calls):
  .venv/bin/python scripts/mobile_journey_check.py
Approved key reuse, at most two real model requests, no automatic retry:
  .venv/bin/python scripts/mobile_journey_check.py --execute-live-ai --confirm-synthetic-data

No hosted Supabase, real profiles, emails, URL fetches or job submissions.
Live mode consumes existing API credits; offline mode makes no paid calls.
The existing MobileProvider alone loads the approved ignored .env. Its existing
mobile model, prompts, schema, official endpoint and zero SDK retries are unchanged.
Do not use pytest/unittest flags to opt into paid tests: live execution exists here
only. --save-artifacts writes downloaded synthetic bytes into a NEW temp folder.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import tempfile
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

MAX_LIVE_CALLS = 2  # rank + prepare; deliberately no automatic retries
SAFE_METADATA = ("provider", "model_name", "prompt_version", "status", "input_tokens", "output_tokens")


def output_diagnostics(raw, payload):
    """Booleans/counts only: no prompt, source text, model prose or exceptions.

    Revalidate a separate parsed object for diagnosis; return the original raw
    response unchanged so the production studio still decides what is accepted.
    """
    from jobagent.mobile import studio

    result = {}
    try:
        output_type = studio._RankOutput if payload["operation"] == "rank" else studio._DocumentOutput
        output = output_type.model_validate_json(raw)
        result["schema_valid"] = True
        claims = output.claims if payload["operation"] == "rank" else (
            output.summary + output.experience + output.education + output.skills + output.cover_letter)
        ledger = {fact["id"]: fact for fact in payload["source_facts"]}
        result["claim_count"] = len(claims)
        result["unknown_source_count"] = sum(ref not in ledger for claim in claims for ref in claim.source_ids)
        result["nonverbatim_claim_count"] = sum(not any(ledger.get(ref, {}).get("text") == claim.text for ref in claim.source_ids) for claim in claims)
        result["substantive_candidate_claim_count"] = sum(any(studio._substantive_candidate_fact(ledger[ref]) for ref in claim.source_ids if ref in ledger) for claim in claims)
        try:
            studio._validate_claims(claims, payload["source_facts"], candidate_only=payload["operation"] == "documents")
            result["claims_valid"] = True
        except studio.MissingFactsError:
            result["claims_valid"] = False
        try:
            studio.validate_rubric(output.requirements, payload["source_facts"])
            result["rubric_valid"] = True
        except studio.RubricError:
            result["rubric_valid"] = False
    except Exception:
        result["diagnostic_incomplete"] = True
    return result


class LiveBoundary:
    """Observe real adapter metadata and restrict httpx egress without mocking AI."""

    def __init__(self):
        self.calls = []
        self.http_requests = 0
        self.stack = ExitStack()

    def __enter__(self):
        import httpx
        from test_mobile_journey import JourneyFailure

        original_send = httpx.HTTPTransport.handle_request

        def send(transport, request):
            if (request.method != "POST" or str(request.url) != "https://api.openai.com/v1/chat/completions"
                    or self.http_requests >= MAX_LIVE_CALLS):
                raise JourneyFailure("live HTTP boundary refused request")
            self.http_requests += 1
            return original_send(transport, request)

        async def deny_async(*args, **kwargs):
            raise JourneyFailure("real asynchronous network forbidden")

        # Supabase uses MockTransport; all non-mocked HTTP must be this one OpenAI
        # endpoint. Ignore machine proxies so synthetic inputs go only to OpenAI.
        self.stack.enter_context(patch.dict(os.environ, {
            name: "" for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        }))
        self.stack.enter_context(patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*", "OPENAI_LOG": "error"}))
        self.stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", send))
        self.stack.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", deny_async))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def provider(self):
        from jobagent.mobile import studio
        from test_mobile_journey import JourneyFailure

        # The sole credential-loading path. Do not load dotenv, Settings, keys,
        # candidate libraries or alternate providers in this script.
        adapter = studio.MobileProvider()
        if adapter.provider != "openai":
            raise JourneyFailure("existing mobile provider is not OpenAI")
        boundary = self

        class ObservedProvider:
            @property
            def model_metadata(self):
                return adapter.model_metadata

            def complete(self, **kwargs):
                operation = kwargs.get("payload", {}).get("operation")
                if operation not in {"rank", "documents"} or len(boundary.calls) >= MAX_LIVE_CALLS:
                    raise JourneyFailure("live model call budget refused")
                event = {"operation": operation}
                boundary.calls.append(event)
                try:
                    raw = adapter.complete(**kwargs)
                    event["diagnostics"] = output_diagnostics(raw, kwargs["payload"])
                    return raw
                finally:
                    metadata = adapter.model_metadata
                    event["metadata"] = {key: metadata[key] for key in SAFE_METADATA if key in metadata}

        return ObservedProvider()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-live-ai", action="store_true", help="Permit two paid OpenAI calls using the existing mobile adapter/key")
    parser.add_argument("--confirm-synthetic-data", action="store_true", help="Confirm only this script's fixed synthetic inputs may leave the machine")
    parser.add_argument("--save-artifacts", action="store_true", help="Save downloaded synthetic artifacts to a new temporary directory for visual QA")
    args = parser.parse_args(argv)
    if args.execute_live_ai != args.confirm_synthetic_data:
        print(json.dumps({"status": "refused", "reason": "Live AI requires both --execute-live-ai and --confirm-synthetic-data; no calls made."}))
        return 2

    # Silence dependency logs and stdout/stderr, including any unexpected raw SDK
    # failure. Only our allowlisted report leaves this boundary, even on failure.
    previous_logging = logging.root.manager.disable
    logging.disable(sys.maxsize)
    report = {"status": "failed", "stage": "setup", "failed_checks": ["unexpected setup failure"]}
    live = None
    journey = None
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), ExitStack() as stack:
            from test_mobile_journey import DeterministicProvider, Journey, JourneyFailure

            if args.execute_live_ai:
                live = stack.enter_context(LiveBoundary())
                factory = live.provider
            else:
                provider = DeterministicProvider()
                factory = lambda: provider
            journey = stack.enter_context(Journey(factory, live=args.execute_live_ai))
            try:
                report = journey.run()
            except JourneyFailure:
                report = journey.report()
                if not report["failed_checks"]:
                    report["failed_checks"] = ["journey boundary failure"]
            except Exception:
                report = journey.report()
                report["failed_checks"].append("unexpected journey failure; details suppressed")
            if args.save_artifacts and journey.downloads:
                directory = Path(tempfile.mkdtemp(prefix="mobile-journey-"))
                for name, content in journey.downloads.items():
                    (directory / name).write_bytes(content)
                report["artifact_directory"] = str(directory)
    except Exception:
        # Never print exception repr/str/traceback: may contain SDK request data.
        report["status"] = "failed"
    finally:
        logging.disable(previous_logging)
    report["live_model_call_limit"] = MAX_LIVE_CALLS
    report["live_model_calls"] = live.calls if live else []
    report["real_openai_http_requests"] = live.http_requests if live else 0
    if live:
        report["reported_token_totals"] = {
            key: sum(event.get("metadata", {}).get(key, 0) for event in live.calls)
            for key in ("input_tokens", "output_tokens")
        }
        report["calls_with_unavailable_usage"] = sum(
            any(key not in event.get("metadata", {}) for key in ("input_tokens", "output_tokens"))
            for event in live.calls
        )
    if report.get("artifact_directory"):
        try:
            report_path = Path(report["artifact_directory"]) / "verification.json"
            report["report_file"] = str(report_path)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        except Exception:
            report["status"] = "failed"
            report.setdefault("failed_checks", []).append("could not save verification report")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
