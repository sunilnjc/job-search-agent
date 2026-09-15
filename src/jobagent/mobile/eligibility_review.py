"""Job-version-scoped self-reported eligibility, separate from ordinary Q&A."""
from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid5

from pydantic import ValidationError

from .schemas import EligibilityReview

REVIEW_QUESTION = "Job-specific eligibility review (self-reported)"


def job_fingerprint(job: dict) -> str:
    facts = {key: job.get(key) for key in (
        "source_url", "title", "company_name", "description", "location_text",
        "workplace_type", "employment_type",
    )}
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def review_id(user_id: str, job_id: str) -> str:
    return str(uuid5(UUID(user_id), "eligibility-review:" + job_id))


def decode_review(row: dict | None, job: dict, user_id: str) -> dict | None:
    if not row or row.get("id") != review_id(user_id, job["id"]) or (
        row.get("scope") != "job:" + job["id"] or row.get("question") != REVIEW_QUESTION
        or not row.get("confirmed_at")
    ):
        return None
    try:
        payload = json.loads(row["answer"])
        if payload.pop("job_fingerprint") != job_fingerprint(job):
            return None
        review = EligibilityReview.model_validate(payload)
    except (KeyError, TypeError, ValueError, ValidationError):
        return None
    return {**review.model_dump(), "confirmed_at": row["confirmed_at"],
            "provenance": "user_self_report", "independently_verified": False}
