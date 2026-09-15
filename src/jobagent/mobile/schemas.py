"""Bounded native API inputs. Ownership and operational fields are server-only."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, List, Literal, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, StrictBool,
    StringConstraints, TypeAdapter, field_validator, model_validator,
)

MAX_RESUME_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 12 * 1024 * 1024
MAX_BODY_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 100_000

ShortText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]
JobStatus = Literal["new", "matched", "ready", "applied", "excluded", "archived"]
ApplicationStatus = Literal["draft", "ready", "submitted", "interviewing", "rejected", "withdrawn", "closed"]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Qualification(InputModel):
    """User-confirmed self-report, never a credential-verification assertion."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["education", "licence", "certification", "other"]
    status: Literal["current", "expired", "in_progress", "not_held", "unknown"] = "unknown"
    jurisdiction: str = Field("", max_length=160)
    expires_on: Optional[date] = None
    evidence_note: str = Field("", max_length=2000)

    @field_validator("expires_on", mode="before")
    @classmethod
    def iso_date_only(cls, value):
        if value is None or type(value) is date:
            return value
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Use an ISO calendar date")
        return date.fromisoformat(value)


class CareerBackground(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    profession: str = Field("", max_length=120)
    experience_level: Literal["unspecified", "student", "entry", "mid", "senior", "career_change"] = "unspecified"
    qualifications: List[Qualification] = Field(default_factory=list, max_length=30)


class ProfileUpdate(InputModel):
    display_name: Optional[str] = Field(None, max_length=160)
    phone: Optional[str] = Field(None, max_length=60)
    base_location: Optional[str] = Field(None, max_length=240)
    timezone: Optional[str] = Field(None, max_length=100)
    onboarding_completed_at: Optional[AwareDatetime] = None
    career_text: Optional[str] = Field(None, max_length=MAX_TEXT_CHARS)
    career_background: Optional[CareerBackground] = None


class PreferencesUpdate(InputModel):
    target_titles: List[ShortText] = Field(default_factory=list, max_length=30)
    preferred_locations: List[ShortText] = Field(default_factory=list, max_length=30)
    preferred_regions: List[ShortText] = Field(default_factory=list, max_length=30)
    remote_preference: Literal["remote_only", "hybrid", "onsite", "open"] = "open"
    sponsorship_required: StrictBool = False
    work_authorization_notes: Optional[str] = Field(None, max_length=4000)
    minimum_match_score: float = Field(7.0, ge=0, le=10, strict=True)


class JobCreate(InputModel):
    source_url: str = Field(min_length=1, max_length=2048)
    title: ShortText
    company_name: ShortText
    description: str = Field(min_length=1, max_length=80_000)
    location_text: Optional[str] = Field(None, max_length=300)

    @field_validator("source_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value) or "\\" in value:
            raise ValueError("Invalid job URL")
        parsed = TypeAdapter(HttpUrl).validate_python(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Credentials are not allowed in job URLs")
        parts = urlsplit(str(parsed))
        # Only remove known tracking parameters; keep requisition identifiers
        # such as gh_jid, jobId and every unrecognized parameter intact.
        query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                           if not key.casefold().startswith("utm_") and key.casefold() not in {"gclid", "fbclid", "msclkid"}])
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, ""))


class JobUpdate(InputModel):
    status: Optional[JobStatus] = None
    title: Optional[ShortText] = None
    company_name: Optional[ShortText] = None
    description: Optional[str] = Field(None, min_length=1, max_length=80_000)
    location_text: Optional[str] = Field(None, max_length=300)

    @model_validator(mode="after")
    def explicit_changes(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set if key != "location_text"
        ):
            raise ValueError("Supply at least one non-null field to change")
        return self


class EligibilityReview(InputModel):
    """An explicit job-scoped user declaration, never an AI/legal conclusion."""
    status: Literal["eligible", "ineligible", "unknown"]
    reason: str = Field(min_length=1, max_length=2000)
    confirmed: Literal[True]


def safe_filename(value: str) -> str:
    if (
        not value or len(value) > 180 or value in (".", "..")
        or any(char in value for char in ("/", "\\", "\x00"))
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        raise ValueError("Invalid filename")
    return value


class ResumeUpload(InputModel):
    filename: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(min_length=4, max_length=((MAX_RESUME_BYTES + 2) // 3) * 4)
    label: ShortText
    role_focus: Optional[str] = Field(None, max_length=240)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        value = safe_filename(value)
        if not value.lower().endswith((".pdf", ".docx")):
            raise ValueError("Only PDF and DOCX resumes are supported")
        return value


class ApplicationCreate(InputModel):
    job_id: UUID
    # A required value is a user's explicit record, never proof of submission.
    status: ApplicationStatus
    notes: Optional[str] = Field(None, max_length=8000)

    @model_validator(mode="after")
    def require_submission_confirmation(self) -> "ApplicationCreate":
        if self.status == "submitted" and not self.notes:
            raise ValueError("Submitted applications require user-supplied confirmation notes")
        return self


class QuestionAnswer(InputModel):
    answer: str = Field(min_length=1, max_length=8000)
    remember: StrictBool = False


class PrepareRequest(InputModel):
    resume_id: UUID
    variant: Literal["role_aligned", "career_change", "sse", "fde"] = "role_aligned"


class RankRequest(InputModel):
    resume_id: Optional[UUID] = None


class ChatTurn(InputModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ChatRequest(InputModel):
    job_id: Optional[UUID] = None
    resume_id: Optional[UUID] = None
    message: str = Field(min_length=1, max_length=6000)
    mode: Literal["application", "interview", "resume"]
    history: List[ChatTurn] = Field(default_factory=list, max_length=12)


class RankResult(InputModel):
    score: float = Field(ge=0, le=10, strict=True)
    recommendation: Literal["strong_match", "match", "review", "exclude"]
    rationale: str = Field(min_length=1, max_length=8000)


class ChatResult(InputModel):
    reply: str = Field(min_length=1, max_length=40_000)
    evidence: List[Annotated[str, StringConstraints(min_length=1, max_length=4000)]] = Field(max_length=30)
    questions: List[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]] = Field(max_length=8)


class FollowUpQuestions(InputModel):
    """Validate studio side-channel questions before any persistence."""

    questions: List[Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)]] = Field(default_factory=list, max_length=8)
