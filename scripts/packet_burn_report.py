#!/usr/bin/env python3
"""Founder/operator packet burn report. No live provider calls or Stripe charges.

Default is a local JSON/CSV aggregation of already-persisted model_runs. Use
--from-json with an export that contains only model_runs rows (no resume text).
Optional --sql prints the service-role SQL. Live REST fetch is opt-in and never
logs credentials.

  .venv/bin/python scripts/packet_burn_report.py --from-json path/to/model_runs.json
  .venv/bin/python scripts/packet_burn_report.py --sql
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobagent.mobile.ai_usage import packet_burn_from_runs, summarize_packet_burn  # noqa: E402

SQL = """-- service_role only; returns mean estimated USD and reserved units per completed packet.
select public.mobile_operator_packet_burn();
"""


def load_runs(path: Path) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("model_runs") or payload.get("runs") or payload.get("rows")
    if not isinstance(payload, list):
        raise ValueError("JSON must be a list of model_runs or an object with model_runs")
    return [row for row in payload if isinstance(row, dict)]


def to_csv(summary: dict, packets: list) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["completed_packets", "incomplete_packets", "metered_packets",
                     "mean_estimated_cost_usd", "mean_reserved_units", "cost_basis"])
    writer.writerow([
        summary["completed_packets"], summary["incomplete_packets"], summary["metered_packets"],
        summary["mean_estimated_cost_usd"], summary["mean_reserved_units"], summary["cost_basis"],
    ])
    writer.writerow([])
    writer.writerow(["prepare_run_id", "assessment_run_id", "incomplete", "estimated_cost_usd",
                     "reserved_units", "input_tokens", "output_tokens"])
    for packet in packets:
        writer.writerow([
            packet.get("prepare_run_id"), packet.get("assessment_run_id"), packet.get("incomplete"),
            packet.get("estimated_cost_usd"), packet.get("reserved_units"),
            packet.get("input_tokens"), packet.get("output_tokens"),
        ])
    return output.getvalue()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-json", type=Path, help="Local model_runs JSON. No network.")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--sql", action="store_true", help="Print service-role SQL and exit.")
    args = parser.parse_args(argv)
    if args.sql:
        sys.stdout.write(SQL)
        return 0
    if args.from_json is None:
        sys.stderr.write("Pass --from-json <file> or --sql. Live provider calls are not made.\n")
        return 2
    try:
        packets = packet_burn_from_runs(load_runs(args.from_json))
    except (OSError, ValueError, TypeError, UnicodeError):
        sys.stderr.write("Could not read model_runs JSON. No values logged.\n")
        return 2
    summary = summarize_packet_burn(packets)
    payload = {"summary": summary, "packets": packets}
    if args.format == "csv":
        sys.stdout.write(to_csv(summary, packets))
    else:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
    raise SystemExit(main())
