"""Profession-neutral self-reported qualifications and conservative job rubrics.

No professional taxonomy, licensing authority lookup or legal determination is
encoded here. Only explicit posting text can establish a job requirement. Names
and jurisdictions require literal matches; equivalence, recognition and ambiguous
requirements remain questions for the candidate/employer.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .exports import clean_text
from .evidence import claim_allowed


class Qualification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=160)
    kind: Literal["education", "licence", "certification", "other"]
    status: Literal["current", "expired", "in_progress", "not_held", "unknown"] = "unknown"
    jurisdiction: str = Field(default="", max_length=160)
    expires_on: Optional[str] = None
    evidence_note: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Qualification name is required")
        return value

    @field_validator("expires_on")
    @classmethod
    def iso_date(cls, value):
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Use an ISO date")
            date.fromisoformat(value)
        return value


class CareerBackground(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    profession: str = Field(default="", max_length=120)
    experience_level: Literal["unspecified", "student", "entry", "mid", "senior", "career_change"] = "unspecified"
    qualifications: list[Qualification] = Field(default_factory=list, max_length=30)


def background_from_context(context: dict) -> CareerBackground:
    """Root wins, including explicit null; nested profile supports old callers."""
    profile = context.get("profile") or {}
    raw = context["career_background"] if "career_background" in context else profile.get("career_background")
    return CareerBackground.model_validate({} if raw is None else raw)


def effective_status(qualification: Qualification, today: Optional[date] = None) -> str:
    today = today or datetime.now(timezone.utc).date()
    if qualification.status in {"not_held", "unknown", "in_progress"}:
        return qualification.status
    if qualification.expires_on and date.fromisoformat(qualification.expires_on) < today:
        return "expired"
    return qualification.status


def qualification_fact(qualification: Qualification) -> str:
    """Keep credential limitations attached to its name, not independent atoms."""
    fields = [
        "Self-reported qualification (not independently verified)",
        f"name: {clean_text(qualification.name)}",
        f"kind: {qualification.kind}",
        f"status: {qualification.status}",
        f"jurisdiction: {clean_text(qualification.jurisdiction) or 'unspecified'}",
        f"expires_on: {qualification.expires_on or 'unspecified'}",
        f"computed_status: {effective_status(qualification)}",
        "status_conflict: " + ("reported current but expiry is in the past"
                               if qualification.status == "current" and effective_status(qualification) == "expired" else "none detected"),
        f"evidence_note: {clean_text(qualification.evidence_note) or 'not provided'}",
    ]
    return "; ".join(fields)


_CREDENTIAL = re.compile(
    r"\b(?:licen[cs]e[ds]?|licensure|certificat(?:e|ion)s?|certified|registration|registered|"
    r"accreditation|accredited|RN|CPA|ACCA|ACA|CIMA|CFA|BLS|ACLS|NMC|GMC|journeyman)\b", re.I)
_LICENCE = re.compile(r"\b(?:licen[cs]e[ds]?|licensure|registration|registered|RN|NMC|GMC|journeyman)\b", re.I)
_EDUCATION = re.compile(r"\b(?:degree(?!\s+of\s+(?:autonomy|freedom|ownership|responsibility|independence|flexibility|complexity)\b)|diploma|bachelor'?s?|master'?s?|doctorate|PhD|BSc|MSc|GED|apprenticeship)\b", re.I)
_HARD = re.compile(r"\b(?:requir(?:ed|ement|ements)|must|mandatory|essential|prerequisite|shall)\b", re.I)
_PREFERRED = re.compile(r"\b(?:preferred|desirable|desired|optional|advantage|nice.to.have)\b", re.I)
_NEGATED = re.compile(r"\b(?:not\s+(?:required|mandatory|essential)|no\s+(?:\w+\s+){0,6}(?:licen[cs]e|certification|degree|diploma)\s+(?:is\s+)?required)\b", re.I)
_AMBIGUOUS = re.compile(r"\b(?:and|or|unless|equivalent|equivalence|eligible|eligibility|obtain|upon|after|within|waiv\w*)\b", re.I)


def credential_text(text: str) -> bool:
    return bool(_CREDENTIAL.search(text) or _EDUCATION.search(text))


def requirement_importance(text: str) -> str:
    if _NEGATED.search(text):
        return "unknown" if _HARD.search(_NEGATED.sub("", text)) else "preferred"
    if _HARD.search(text) and not _PREFERRED.search(text):
        return "required"
    if _PREFERRED.search(text) and not _HARD.search(text):
        return "preferred"
    return "unknown"


def literal_in(needle: str, haystack: str) -> bool:
    needle = clean_text(needle)
    return bool(needle and re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", clean_text(haystack), re.I))


def job_segments(description: str) -> list[str]:
    # Keep complete sentences/lines, including negation and alternatives. Do not
    # truncate an overlong clause into an apparently unconditional requirement.
    return [clean_text(line) for line in re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z])", description) if line.strip()][:100]


class RequirementClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=1600)
    source_ids: list[str] = Field(min_length=1, max_length=1)


class RequirementAssessment(BaseModel):
    """Model-suggested rubric; every field is checked against cited data below."""
    model_config = ConfigDict(extra="forbid", strict=True)
    requirement: RequirementClaim
    category: Literal["licence", "certification", "education", "transferable_skill", "experience", "other"]
    importance: Literal["required", "preferred", "unknown"]
    credential_name: str = Field(max_length=160)
    jurisdiction: str = Field(max_length=160)
    candidate_source_ids: list[str] = Field(max_length=4)
    assessment: Literal["supported", "missing", "uncertain"]


class RubricError(ValueError):
    """Invalid/unsubstantiated model criterion, without echoing model content."""


def recalled_requirement_sources(requirements: list[RequirementAssessment], facts: list[dict]) -> list[dict]:
    """Original-posting gates omitted from a VALID rubric, not inferred matches.

    Passing [] deliberately recalls every mandatory or ambiguous credential gate.
    Callers rejecting a rubric must pass [], never its partially validated items.
    The studio ledger bounds posting segments to 100; no model text is returned.
    """
    covered = {ref for item in requirements for ref in item.requirement.source_ids}
    return [fact for fact in facts if fact["kind"] == "job"
            and fact["id"].startswith("job.requirements.") and fact["id"] not in covered
            and (requirement_importance(fact["text"]) == "required"
                 or (credential_text(fact["text"]) and requirement_importance(fact["text"]) != "preferred"))]


def rejected_rubric_questions(facts: list[dict], review_id: str, *, suspicious_job=False) -> list[str]:
    """Bounded unresolved review, retaining EVERY recalled source in <=6 groups.

    Long/large postings use labelled excerpts, not truncated assertions. The full
    statements remain in the generation snapshot. A generation-specific question
    cannot be silently resolved by an answer to an older failed comparison.
    This is a review request, never proof of support or an eligibility decision.
    """
    recalled = recalled_requirement_sources([], facts)
    if len(recalled) > 100:
        raise ValueError("Too many posting segments for bounded requirement review.")
    # Never echo a discarded/unsafe fragment in a follow-up or snapshot. Its
    # omission remains an explicit unresolved clean-posting request.
    safe = [fact for fact in recalled if claim_allowed(fact)]
    suspicious_job = suspicious_job or len(safe) != len(recalled)
    recalled = safe
    prefix = (f"The automated requirement comparison could not be validated (review {review_id}). "
              "For each posting excerpt, confirm supporting facts or state the gap; nothing is cleared by this draft: ")
    if not recalled:
        questions = [prefix + "Please review the full posting and confirm its actual mandatory requirements."]
    else:
        # Six groups leave two of the API's eight slots for other follow-ups.
        # The ledger has <=100 posting segments: at most 17 short excerpts/group.
        size = max(1, (len(recalled) + 5) // 6)
        excerpt_limit = 900 if size == 1 else 64
        questions = []
        for offset in range(0, len(recalled), size):
            excerpts = []
            for source in recalled[offset:offset + size]:
                text = source["text"]
                excerpt = text if len(text) <= excerpt_limit else text[:excerpt_limit] + "…"
                excerpts.append(f'[{source["id"]}] "{excerpt}"')
            questions.append(prefix + "; ".join(excerpts)
                             + " Review complete statements in the original posting; excerpts may be shortened.")
    if suspicious_job:
        questions.append("Some posting text was omitted or unsafe to use. Please confirm the complete requirements from a clean employer posting.")
    return questions


def validate_rubric(requirements: list[RequirementAssessment], facts: list[dict]) -> None:
    ledger = {fact["id"]: fact for fact in facts}
    seen = set()
    for item in requirements:
        ref = item.requirement.source_ids[0]
        fact = ledger.get(ref)
        if (not fact or fact["kind"] != "job" or not ref.startswith("job.requirements.")
                or item.requirement.text != fact["text"] or ref in seen):
            raise RubricError("The job rubric contains an unsupported or duplicate requirement.")
        seen.add(ref)
        if item.importance != requirement_importance(fact["text"]):
            raise RubricError("The job rubric changed the stated requirement strength.")
        is_credential = credential_text(fact["text"])
        if is_credential and item.category not in {"licence", "certification", "education"}:
            raise RubricError("A credential requirement cannot be represented as a transferable skill.")
        if item.category in {"licence", "certification", "education"}:
            if not is_credential or not literal_in(item.credential_name, fact["text"]):
                raise RubricError("The job rubric invented a credential or omitted its evidence.")
            if clean_text(item.credential_name).casefold() in {"current", "valid", "active", "required", "licence", "license", "certification", "degree", "registration"}:
                raise RubricError("A generic status or category does not identify the required qualification.")
            if item.category == "licence" and not _LICENCE.search(fact["text"]):
                raise RubricError("The job rubric invented a licence requirement.")
            if _LICENCE.search(fact["text"]) and item.category != "licence":
                raise RubricError("The job rubric changed the licence requirement type.")
            if item.category == "education" and not _EDUCATION.search(fact["text"]):
                raise RubricError("The job rubric invented an education requirement.")
            if _EDUCATION.search(fact["text"]) and not _CREDENTIAL.search(fact["text"]) and item.category != "education":
                raise RubricError("The job rubric changed the education requirement type.")
            if item.jurisdiction and not literal_in(item.jurisdiction, fact["text"]):
                raise RubricError("The job rubric invented a credential jurisdiction.")
        elif item.credential_name or item.jurisdiction:
            raise RubricError("Only credential criteria may name credentials or jurisdictions.")
        for candidate_ref in item.candidate_source_ids:
            source = ledger.get(candidate_ref)
            if not source or source["kind"] != "candidate":
                raise RubricError("The rubric cited unsupported candidate evidence.")
        if item.assessment == "supported" and not item.candidate_source_ids:
            raise RubricError("A supported criterion needs candidate evidence.")
        if not is_credential and item.assessment == "supported":
            # Exact citations are not semantic proof of transferable equivalence.
            # Ordinary transferable matches are semantic comparisons. Keep valid
            # citations and the subjective score, but downgrade the assertion;
            # do not fail a useful ranking because two true excerpts differ.
            if not any(fact["text"] == ledger[ref]["text"] for ref in item.candidate_source_ids):
                item.assessment = "uncertain"


def credential_review(
    requirements: list[RequirementAssessment], facts: list[dict], background: CareerBackground,
    *, suspicious_job=False, today: Optional[date] = None,
) -> tuple[bool, list[str], list[str]]:
    """Review unresolved gates; never turn a profession label into qualification.

    Even a literal current match is only self-reported support, not verification
    by a regulator. No legal equivalence or work authorization is inferred.
    """
    today = today or datetime.now(timezone.utc).date()
    ledger = {fact["id"]: fact for fact in facts}
    qualification_by_id = {f"career_background.qualifications.{index}": qualification
                           for index, qualification in enumerate(background.qualifications)}
    review, questions, notes = False, [], []
    covered = set()
    for item in requirements:
        ref = item.requirement.source_ids[0]
        covered.add(ref)
        if item.category not in {"licence", "certification", "education"}:
            if item.assessment != "supported":
                notes.append("Transferable experience is a fit consideration, not proof of a credential.")
                if item.importance == "required":
                    review = True
                    statement = ledger[ref]["text"]
                    quoted = f'"{statement}"' if len(statement) <= 900 else f"posting source [{ref}]"
                    notes.append(f"A mandatory requirement is missing or not yet supported by confirmed evidence [{ref}].")
                    questions.append(f"For {quoted}, what confirmed experience supports this requirement, or is this a gap?")
            continue
        statement = ledger[ref]["text"]
        if item.importance == "preferred":
            notes.append(f"The posting describes an optional/preferred credential, not a hard gate [{ref}].")
            continue
        exact = [q for q in qualification_by_id.values()
                 if clean_text(q.name).casefold() == clean_text(item.credential_name).casefold()]
        supported = []
        for candidate_ref in item.candidate_source_ids:
            q = qualification_by_id.get(candidate_ref)
            if (q and q in exact and candidate_ref in ledger and q.kind == item.category and q.status == "current"
                    and (q.expires_on is None or date.fromisoformat(q.expires_on) >= today)
                    and clean_text(q.jurisdiction).casefold() == clean_text(item.jurisdiction).casefold()
                    and (item.category != "licence" or bool(item.jurisdiction))):
                supported.append(q)
        # Conflicting duplicate records and alternative/timing requirements cannot
        # be resolved by selecting whichever evidence yields the higher score.
        conflicting = any(q.status != "current" or (q.expires_on and date.fromisoformat(q.expires_on) < today)
                          for q in exact)
        if supported and not conflicting and not _AMBIGUOUS.search(statement) and item.importance == "required":
            notes.append(f"Literal credential match is supported only by current self-reported information, not independently verified [{ref}].")
        else:
            review = True
            statuses = sorted({"expired" if q.expires_on and date.fromisoformat(q.expires_on) < today else q.status for q in exact})
            state = ", ".join(statuses) if statuses else "missing or unmatched"
            notes.append(f"Credential requirement needs review; self-reported status is {state}. Transferable skills do not satisfy this gate [{ref}].")
            quoted = f'"{statement}"' if len(statement) <= 900 else f"posting source [{ref}]"
            questions.append(f"For {quoted}, can you confirm the qualification name, current status, jurisdiction and expiry, or clarify any accepted alternative?")
    # Deterministic recall guard: omission by the model must not waive an explicit
    # licence/certification requirement in the posting.
    for fact in facts:
        if (fact["id"].startswith("job.requirements.") and credential_text(fact["text"])
                and requirement_importance(fact["text"]) != "preferred" and fact["id"] not in covered):
            review = True
            notes.append(f"A posting credential requirement has not been evaluated [{fact['id']}].")
            quoted = f'"{fact["text"]}"' if len(fact["text"]) <= 900 else f"posting source [{fact['id']}]"
            questions.append(f"Can you clarify {quoted} and confirm the qualification name, jurisdiction and required status?")
        elif (fact["id"].startswith("job.requirements.")
              and requirement_importance(fact["text"]) == "required" and fact["id"] not in covered):
            review = True
            notes.append(f"An explicit mandatory requirement has not been evaluated [{fact['id']}].")
            statement = fact["text"]
            quoted = f'"{statement}"' if len(statement) <= 900 else f'"{statement[:850]}…" (excerpt; review the full posting)'
            questions.append(f"Can you confirm whether you meet this mandatory requirement: {quoted}? Describe the supporting experience or say if it is a gap.")
    if suspicious_job:
        review = True
        notes.append("Some job text could not be used safely as requirement evidence.")
        questions.append("Can you confirm the employer's actual requirements from a clean copy of the posting?")
    return review, list(dict.fromkeys(questions))[:6], list(dict.fromkeys(notes))[:12]
