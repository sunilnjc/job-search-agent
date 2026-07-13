from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class JobOut(BaseModel):
    id: int
    title: str
    company: str
    location: str
    remote: bool
    country: Optional[str] = None
    url: str
    description: str
    salary: Optional[str] = None
    status: str
    llm_score: Optional[int] = None
    llm_reasoning: Optional[str] = None
    embedding_similarity: Optional[float] = None
    eligibility: str = "unknown"
    excluded_reason: Optional[str] = None


class JobDetailOut(JobOut):
    cover_letter: Optional[str] = None
    resume_tailoring: Optional[str] = None
    gap_analysis: Optional[str] = None
    has_resume_docx: bool = False
    has_resume_pdf: bool = False
    has_cover_letter_pdf: bool = False


class StatusUpdate(BaseModel):
    status: str


class ExcludeUpdate(BaseModel):
    reason: str


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str


class RunTrigger(BaseModel):
    limit: Optional[int] = None
    url: Optional[str] = None


class RunOut(BaseModel):
    run_id: str
    status: str
    log: list[str]
