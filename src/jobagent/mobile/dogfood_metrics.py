"""Weekly founder dogfood counts. No analytics SDK, no PII export.

Roles reviewed = distinct jobs with a stored assessment (job_scores) in the week.
Prepared = distinct jobs with a succeeded prepare_documents run in the week.
Applied = distinct jobs whose application status is submitted with a timestamp
in the week (applied_at when present, otherwise updated_at). Replies are not
tracked in this schema and are exported as a documented N/A stub.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional
from uuid import UUID


def prior_iso_week(today: Optional[date] = None) -> tuple[int, int]:
    current = today or datetime.now(timezone.utc).date()
    year, week, _weekday = (current - timedelta(days=7)).isocalendar()
    return int(year), int(week)


def iso_week_bounds(year: int, week: int) -> tuple[datetime, datetime]:
    start = datetime.combine(date.fromisocalendar(year, week, 1), datetime.min.time(), tzinfo=timezone.utc)
    return start, start + timedelta(days=7)


def parse_iso_week(value: str) -> tuple[int, int]:
    text = (value or "").strip().upper()
    if len(text) == 8 and text[4:6] == "-W" and text[:4].isdigit() and text[6:].isdigit():
        year, week = int(text[:4]), int(text[6:])
        if 1 <= week <= 53:
            date.fromisocalendar(year, week, 1)
            return year, week
    raise ValueError("Use an ISO week like 2026-W37")


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _in_week(value: Any, start: datetime, end: datetime) -> bool:
    stamp = _parse_time(value)
    return stamp is not None and start <= stamp < end


def _job_id(row: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    return None


def weekly_dogfood_report(
    *,
    year: int,
    week: int,
    job_scores: Iterable[Mapping[str, Any]] = (),
    model_runs: Iterable[Mapping[str, Any]] = (),
    applications: Iterable[Mapping[str, Any]] = (),
) -> dict:
    start, end = iso_week_bounds(year, week)
    reviewed, prepared, applied = set(), set(), set()
    for row in job_scores:
        job_id = _job_id(row, "job_id")
        if job_id and _in_week(row.get("created_at"), start, end):
            reviewed.add(job_id)
    for row in model_runs:
        if row.get("operation") == "rank_job" and row.get("status") == "succeeded":
            job_id = _job_id(row, "job_id")
            if job_id and _in_week(row.get("completed_at") or row.get("created_at"), start, end):
                reviewed.add(job_id)
        if row.get("operation") == "prepare_documents" and row.get("status") == "succeeded":
            job_id = _job_id(row, "job_id")
            if job_id and _in_week(row.get("completed_at") or row.get("created_at"), start, end):
                prepared.add(job_id)
    for row in applications:
        if row.get("status") != "submitted":
            continue
        job_id = _job_id(row, "job_id")
        stamp = row.get("applied_at") or row.get("updated_at")
        if job_id and _in_week(stamp, start, end):
            applied.add(job_id)
    return {
        "iso_week": f"{year}-W{week:02d}",
        "period_start": start.isoformat().replace("+00:00", "Z"),
        "period_end": end.isoformat().replace("+00:00", "Z"),
        "roles_reviewed": len(reviewed),
        "roles_prepared": len(prepared),
        "roles_applied": len(applied),
        "replies": None,
        "replies_status": "not_tracked",
        "notes": {
            "reviewed": "Distinct jobs with a job_scores row or succeeded rank_job in the ISO week.",
            "prepared": "Distinct jobs with a succeeded prepare_documents run in the ISO week.",
            "applied": "Distinct jobs with application status submitted and applied_at/updated_at in the ISO week. submitted is a user-recorded status, not proof of an employer receipt.",
            "replies": "Inbound employer replies are not stored. This field is an honest N/A stub.",
        },
    }


def report_to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def report_to_csv(report: Mapping[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["iso_week", "period_start", "period_end", "roles_reviewed", "roles_prepared", "roles_applied", "replies", "replies_status"])
    writer.writerow([
        report["iso_week"], report["period_start"], report["period_end"],
        report["roles_reviewed"], report["roles_prepared"], report["roles_applied"],
        "N/A", report["replies_status"],
    ])
    return output.getvalue()
