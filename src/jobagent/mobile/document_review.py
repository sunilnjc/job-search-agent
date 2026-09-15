"""Bounded private generation-time document evidence; never reread live profiles.

The studio validates factual claims before rendering. This module validates the
review transport/provenance contract, not the truth of a candidate's assertions.
Persist only behind model_runs owner RLS. Do not put this source text in logs.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from uuid import UUID

VERSION = "document-review-v1"
MODE = "selected_verbatim_facts"
LEGACY_NOTICE = "This draft selects and organizes confirmed facts; it does not rewrite them or verify them independently. Review omissions and source wording before sharing."
NOTICE = "This draft selects confirmed facts; letter sentences may add first-person grammar and quote the posting. This is source-based composition, not a free-form rewrite or independent verification. Review omissions and source wording before sharing."
MAX_REVIEW_BYTES = 256_000
MAX_SOURCES = 400
HEADINGS = {"Professional Summary", "Work Experience", "Education", "Skills", "Projects",
            "Qualifications", "Languages", "Volunteer Experience", "Selected Career Contributions"}
_SOURCE = re.compile(
    r"(?:career_text\.\d+|answers\.\d+|career_background\.qualifications\.\d+|"
    r"profile\.(?:headline|summary|skills|experience|employment|education|certifications|projects|achievements|languages)"
    r"(?:\.(?:\d+|[a-z_]{1,40}))*|job\.(?:title|company_name|location_text|workplace_type|employment_type|requirements\.\d+))\Z")
_PRIVATE_PARTS = {"password", "secret", "token", "api_key", "access_token", "refresh_token", "authorization",
                  "entitlement", "user_id", "id", "created_at", "updated_at"}
_KEYS = {"version", "mode", "notice", "snapshot", "resume_sections", "cover_letter_source_ids",
         "omitted_source_ids", "omitted_with_literal_job_overlap", "requirements_needing_review", "unattributed_source_ids"}


class DocumentReviewError(ValueError):
    """Safe diagnostic; deliberately does not contain source text or identifiers."""


def _fail():
    raise DocumentReviewError("The saved document evidence is incomplete or inconsistent. Review the source draft before sharing.")


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def source_snapshot(sources: list[dict], generation_id: str, captured_at: str) -> dict:
    """Copy the very ledger used for this generation, not a later source lookup."""
    snapshot = {"generation_id": generation_id, "captured_at": captured_at,
                "sources": [{"id": source["id"], "kind": source["kind"], "text": source["text"]} for source in sources]}
    return {**snapshot, "fingerprint": _fingerprint(snapshot)}


def _ids(value, *, maximum=MAX_SOURCES) -> list[str]:
    if (not isinstance(value, list) or len(value) > maximum
            or any(not isinstance(ref, str) or len(ref) > 180 or not _SOURCE.fullmatch(ref) for ref in value)
            or len(set(value)) != len(value)):
        _fail()
    return value


def validated_saved_document_review(value) -> dict:
    """Validate an already owner-scoped stored review for a read-only endpoint.

    Raises on malformed data; callers must not represent that as an empty/all-clear
    review. This cannot establish ownership; the route/repository must do that.
    """
    if not isinstance(value, dict) or set(value) != _KEYS:
        _fail()
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_REVIEW_BYTES:
            _fail()
        review = json.loads(encoded)  # detached; no shared mutable source objects
    except (TypeError, ValueError, RecursionError):
        _fail()
    if review["version"] != VERSION or review["mode"] != MODE or review["notice"] not in (NOTICE, LEGACY_NOTICE):
        _fail()
    snapshot = review["snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != {"generation_id", "captured_at", "sources", "fingerprint"}:
        _fail()
    try:
        generation = UUID(snapshot["generation_id"])
        captured = datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))
        if str(generation) != snapshot["generation_id"] or generation.version != 4 or captured.utcoffset() != timedelta(0):
            _fail()
    except (TypeError, ValueError, AttributeError):
        _fail()
    sources = snapshot["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        _fail()
    ledger = {}
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"id", "kind", "text"}:
            _fail()
        ref = _ids([source["id"]])[0]
        text = source["text"]
        if (ref in ledger or any(part in _PRIVATE_PARTS for part in ref.split("."))
                or source["kind"] != ("job" if ref.startswith("job.") else "candidate")
                or not isinstance(text, str) or not text.strip() or len(text) > 3000
                or any(ord(char) < 32 for char in text)):
            _fail()
        ledger[ref] = source
    core = {key: snapshot[key] for key in ("generation_id", "captured_at", "sources")}
    if snapshot["fingerprint"] != _fingerprint(core):
        _fail()
    sections = review["resume_sections"]
    if not isinstance(sections, dict) or not sections or not set(sections) <= HEADINGS:
        _fail()
    resume_ids = []
    for refs in sections.values():
        refs = _ids(refs, maximum=28)
        if not refs:
            _fail()
        resume_ids.extend(refs)
    if len(resume_ids) > 28 or len(set(resume_ids)) != len(resume_ids):
        _fail()
    letter_ids = _ids(review["cover_letter_source_ids"], maximum=4)
    omitted = _ids(review["omitted_source_ids"])
    overlap = _ids(review["omitted_with_literal_job_overlap"])
    # Includes deterministic recall from all 100 bounded posting segments, not
    # only the model's at-most-12 rubric selections.
    requirements = _ids(review["requirements_needing_review"], maximum=100)
    unattributed = _ids(review["unattributed_source_ids"], maximum=32)
    candidates = {ref for ref, source in ledger.items() if source["kind"] == "candidate"}
    selected = set(resume_ids + letter_ids)
    if (not letter_ids or not selected <= candidates or not set(omitted) <= candidates
            or selected & set(omitted) or selected | set(omitted) != candidates
            or not set(overlap) <= set(omitted)
            or not set(unattributed) <= selected
            or any(ref not in ledger or not ref.startswith("job.requirements.") for ref in requirements)):
        _fail()
    return review


def validated_document_review(raw_documents) -> dict | None:
    """Validate against the actual just-generated packet before any persistence.

    None is legacy/unavailable, never an inferred reconstruction. A present invalid
    review raises. Do not pass only today's context or validated artifact metadata:
    the raw StudioDocuments carries generation IDs and rendered source mappings.
    """
    value = getattr(raw_documents, "review", None)
    if value is None or value == {}:
        return None
    review = validated_saved_document_review(value)
    snapshot = review["snapshot"]
    expected = {
        "tailored_resume": [ref for refs in review["resume_sections"].values() for ref in refs],
        "cover_letter": review["cover_letter_source_ids"],
    }
    if not isinstance(raw_documents, list) or not 2 <= len(raw_documents) <= 4:
        _fail()
    seen = set()
    for document in raw_documents:
        if not isinstance(document, dict) or document.get("kind") not in expected:
            _fail()
        if (document.get("version_id") != snapshot["generation_id"]
                or document.get("created_at") != snapshot["captured_at"]
                or document.get("source_ids") != expected[document["kind"]]):
            _fail()
        seen.add(document["kind"])
    if seen != set(expected):
        _fail()
    return review
