#!/usr/bin/env python3
"""Weekly dogfood metrics for the prior ISO week. No analytics SDK.

Counts only: roles reviewed, prepared, applied, and an N/A replies stub.
Default reads a local JSON export. --sql prints the service-role query.

  .venv/bin/python scripts/weekly_dogfood_metrics.py --from-json path/to/export.json
  .venv/bin/python scripts/weekly_dogfood_metrics.py --from-json export.json --iso-week 2026-W37
  .venv/bin/python scripts/weekly_dogfood_metrics.py --sql --iso-week 2026-W37
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobagent.mobile.dogfood_metrics import (  # noqa: E402
    iso_week_bounds, parse_iso_week, prior_iso_week, report_to_csv, report_to_json,
    weekly_dogfood_report,
)


def load_export(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")
    return payload


def sql_for(year: int, week: int) -> str:
    start, _end = iso_week_bounds(year, week)
    return (
        "-- service_role only. Monday of the requested ISO week.\n"
        f"select public.mobile_operator_dogfood_week('{start.date().isoformat()}'::date);\n"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-json", type=Path, help="Local JSON with job_scores, model_runs, applications.")
    parser.add_argument("--iso-week", help="ISO week such as 2026-W37. Default: prior ISO week (UTC).")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--sql", action="store_true", help="Print service-role SQL and exit.")
    args = parser.parse_args(argv)
    try:
        year, week = parse_iso_week(args.iso_week) if args.iso_week else prior_iso_week()
    except ValueError:
        sys.stderr.write("Use an ISO week like 2026-W37.\n")
        return 2
    if args.sql:
        sys.stdout.write(sql_for(year, week))
        return 0
    if args.from_json is None:
        sys.stderr.write("Pass --from-json <file> or --sql. No network and no analytics SDK.\n")
        return 2
    try:
        payload = load_export(args.from_json)
        report = weekly_dogfood_report(
            year=year, week=week,
            job_scores=payload.get("job_scores") or [],
            model_runs=payload.get("model_runs") or [],
            applications=payload.get("applications") or [],
        )
    except (OSError, ValueError, TypeError, UnicodeError):
        sys.stderr.write("Could not read weekly export JSON. No values logged.\n")
        return 2
    sys.stdout.write(report_to_csv(report) if args.format == "csv" else report_to_json(report))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
    raise SystemExit(main())
