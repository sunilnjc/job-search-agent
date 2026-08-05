"""Safe, auditable preparation worker for Ready job applications.

The worker intentionally stops at ``ready_for_submission``.  A browser ATS handler may fill
only approved facts, but a real application is marked submitted only after its confirmation
page is observed.  CAPTCHA, OTP, unfamiliar questions, missing documents and eligibility
ambiguity all become explicit exceptions for Telegram to report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from jobagent.applying import ats
from jobagent.config import settings
from jobagent.profile.resume_parser import parse_resume
from jobagent.service import draft_job, slugify
from jobagent.storage import db


@dataclass(frozen=True)
class AutopilotResult:
    job_id: int
    title: str
    company: str
    state: str
    reason: Optional[str] = None
    attempt_id: Optional[int] = None
    final_url: Optional[str] = None


def document_paths(job: Mapping[str, Any]) -> tuple[Path, Path]:
    folder = settings.output_dir / slugify(f"{job['company']}-{job['title']}")
    return folder / "cover_letter.pdf", folder / "tailored_resume.pdf"


def _result(job: Mapping[str, Any], state: str, **kwargs: Any) -> AutopilotResult:
    return AutopilotResult(job_id=int(job["id"]), title=str(job["title"]), company=str(job["company"]), state=state, **kwargs)


def process_job(
    conn,
    job: Mapping[str, Any],
    *,
    resolver: Callable[[str], ats.ATSDetection] = ats.resolve_for_application,
    profile: Any = None,
) -> AutopilotResult:
    """Prepare a single strict-eligibility job, preserving every outcome in SQLite."""
    attempt_id = db.create_application_attempt(conn, int(job["id"]))
    db.update_application_attempt(conn, attempt_id, state="preparing")
    cover_letter, resume = document_paths(job)

    try:
        if not (cover_letter.is_file() and resume.is_file()):
            draft_job(conn, job, profile if profile is not None else parse_resume(), on_progress=lambda _: None)
        if not (cover_letter.is_file() and resume.is_file()):
            reason = "document_generation_incomplete"
            db.update_application_attempt(conn, attempt_id, state="exception", reason=reason)
            return _result(job, "exception", reason=reason, attempt_id=attempt_id)

        detected = resolver(str(job["url"]))
        metadata = json.dumps(
            {"resolved": detected.resolved, "is_aggregator": detected.is_aggregator}, sort_keys=True
        )
        if detected.error:
            reason = (
                "employer_apply_link_not_found"
                if detected.error == "employer_apply_link_not_found"
                else "application_page_unavailable"
            )
            db.update_application_attempt(
                conn, attempt_id, state="exception", ats=detected.ats,
                final_url=detected.final_url, reason=reason, details_json=metadata,
            )
            return _result(job, "exception", reason=reason, attempt_id=attempt_id, final_url=detected.final_url)
        if detected.is_aggregator or not detected.supported:
            db.update_application_attempt(
                conn, attempt_id, state="exception", ats=detected.ats,
                final_url=detected.final_url, reason="unsupported_or_unresolved_ats", details_json=metadata,
            )
            return _result(job, "exception", reason="unsupported_or_unresolved_ats", attempt_id=attempt_id, final_url=detected.final_url)

        # A platform-specific browser handler will continue from this recorded checkpoint.
        # Never treat a prepared packet as a submitted application.
        db.update_application_attempt(
            conn, attempt_id, state="ready_for_submission", ats=detected.ats,
            final_url=detected.final_url, details_json=metadata,
        )
        return _result(job, "ready_for_submission", attempt_id=attempt_id, final_url=detected.final_url)
    except Exception as exc:  # record failure; a single job must not stop the rest of the queue
        reason = f"preparation_error:{type(exc).__name__}"
        db.update_application_attempt(conn, attempt_id, state="failed", reason=reason)
        return _result(job, "failed", reason=reason, attempt_id=attempt_id)


def process_ready_queue(
    *,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
    resolver: Callable[[str], ats.ATSDetection] = ats.resolve_for_application,
    profile: Any = None,
) -> list[AutopilotResult]:
    """Process a bounded batch of explicitly eligible drafted roles.

    The query, attempt creation, and each job's state change use the same local database;
    callers can safely run this repeatedly without duplicating a prepared application.
    """
    db.init_db(db_path)
    batch_size = settings.autopilot_batch_size if limit is None else limit
    with db.connection(db_path) as conn:
        jobs = db.autopilot_candidates(
            conn,
            settings.autopilot_min_score,
            batch_size,
            include_unknown_outside_us_uk=settings.autopilot_include_unknown_outside_us_uk,
            require_direct_ats=settings.autopilot_require_direct_ats,
        )
        return [process_job(conn, job, resolver=resolver, profile=profile) for job in jobs]


def submit_attempt(attempt_id: int, db_path: Optional[Path] = None) -> AutopilotResult:
    """Perform the confirmed submission step for a prepared, supported application."""
    from jobagent.applying.greenhouse import submit as submit_greenhouse

    with db.connection(db_path) as conn:
        attempt = db.get_application_attempt_job(conn, attempt_id)
        if not attempt:
            raise ValueError(f"No application attempt {attempt_id}")
        if attempt["state"] != "ready_for_submission":
            return _result(attempt, "exception", reason="attempt_not_ready", attempt_id=attempt_id)
        if attempt["ats"] != "greenhouse":
            return _result(attempt, "exception", reason="ats_handler_not_available", attempt_id=attempt_id)
        cover, resume = document_paths(attempt)
        outcome = submit_greenhouse(attempt, resume, cover)
        db.update_application_attempt(
            conn, attempt_id, state=outcome.state, reason=outcome.reason, submitted=outcome.state == "submitted"
        )
        if outcome.state == "submitted":
            from jobagent.tracking import pipeline

            pipeline.transition(conn, int(attempt["job_id"]), "applied")
        return _result(attempt, outcome.state, reason=outcome.reason, attempt_id=attempt_id, final_url=attempt["final_url"])
