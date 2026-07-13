from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class JobPosting(BaseModel):
    id: Optional[int] = None
    source: str
    external_id: str
    title: str
    company: str
    location: str
    remote: bool = False
    country: Optional[str] = None
    url: str
    description: str
    salary: Optional[str] = None
    posted_at: Optional[str] = None
    fetched_at: str = ""


class MatchScore(BaseModel):
    job_id: int
    embedding_similarity: float
    llm_score: Optional[int] = None
    llm_reasoning: Optional[str] = None
    eligibility: str = "unknown"
    scored_at: str = ""


class Profile(BaseModel):
    raw_text: str
    skills: list[str] = []
    titles: list[str] = []
    years_experience: Optional[int] = None
    summary: Optional[str] = None


STATUSES = [
    "new",
    "matched",
    "drafted",
    "applied",
    "interviewing",
    "rejected",
    "offer",
]


def now_iso() -> str:
    return datetime.utcnow().isoformat()
