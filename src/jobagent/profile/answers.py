"""Pre-approved answers for job application forms.

Everything here exists to make one guarantee: an application form is never filled with a
value the user did not approve. A field the user hasn't filled in resolves to
``NEEDS_INPUT`` and the caller must ask them — it is never inferred from adjacent facts,
and never left to an LLM to improvise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

import yaml

from jobagent.config import settings

NEEDS_INPUT = "NEEDS_INPUT"


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().upper() == NEEDS_INPUT
    return False


@dataclass(frozen=True)
class Answer:
    """A resolved answer. `value` is None when the user still has to supply it."""

    field: str
    value: Optional[str]
    label: str

    @property
    def needs_input(self) -> bool:
        return self.value is None


# Question patterns → dotted path in answers.yaml. Ordered: the first match wins, so put
# the more specific patterns first (expected salary before generic salary, etc.).
QUESTION_PATTERNS: list[tuple[str, str, str]] = [
    (r"criminal|convict|felony|offen[cs]e", "application.criminal_history", "Criminal history"),
    (r"legal claim|lawsuit|litigation|legal action", "application.legal_claims", "Legal claims"),
    (r"acknowledg|confirm.*(privacy|policy|notice)|consent", "application.legal_acknowledgement", "Acknowledgement"),
    (r"visa|sponsor|work permit|right to work|authori[sz]ed to work|work authori", "work_authorization.summary", "Work authorization"),
    (r"relocat", "work_authorization.relocation_notes", "Relocation"),
    (r"notice period|how (much|long).*notice|resign", "availability.notice_period", "Notice period"),
    (r"start date|when can you (start|join)|availab(le|ility) to start", "availability.earliest_start_date", "Earliest start date"),
    (r"travel", "availability.willing_to_travel", "Willingness to travel"),
    (r"expected (salary|compensation|ctc)|salary expectation|desired (salary|compensation)", "compensation.expected_salary", "Expected salary"),
    (r"current (salary|compensation|ctc)", "compensation.current_salary", "Current salary"),
    (r"years? of experience|how many years|total experience", "experience.total_years", "Total years of experience"),
    (r"highest (degree|qualification)|education level", "education.highest_degree", "Highest degree"),
    (r"university|college|institution|where did you study", "education.institution", "Institution"),
    (r"graduat", "education.graduation_year", "Graduation year"),
    (r"linkedin", "identity.linkedin", "LinkedIn"),
    (r"github", "identity.github", "GitHub"),
    (r"portfolio|personal (website|site)", "identity.portfolio", "Portfolio"),
    (r"phone|mobile|contact number", "identity.phone", "Phone"),
    (r"e-?mail", "identity.email", "Email"),
    (r"full name|your name|legal name", "identity.full_name", "Full name"),
    (r"where are you (based|located)|where.*based|current location|city", "identity.location", "Location"),
    (r"gender", "voluntary_disclosures.gender", "Gender"),
    (r"ethnic|race", "voluntary_disclosures.ethnicity", "Ethnicity"),
    (r"veteran", "voluntary_disclosures.veteran_status", "Veteran status"),
    (r"disabilit", "voluntary_disclosures.disability_status", "Disability status"),
    (r"why (do you want|are you interested)|why this (role|company)", "narratives.why_this_role", "Why this role"),
    (r"greatest strength|your strength", "narratives.greatest_strength", "Greatest strength"),
    (r"reason for leaving|why (are you )?leaving", "narratives.reason_for_leaving", "Reason for leaving"),
]


def load_answers() -> dict:
    path = settings.answers_path
    if not path.exists():
        return {}
    with open(path) as handle:
        return yaml.safe_load(handle) or {}


def _lookup(data: dict, dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def get_field(dotted: str, data: Optional[dict] = None) -> Answer:
    data = load_answers() if data is None else data
    raw = _lookup(data, dotted)
    label = dotted.rsplit(".", 1)[-1].replace("_", " ").capitalize()
    if _is_missing(raw):
        return Answer(field=dotted, value=None, label=label)
    return Answer(field=dotted, value=str(raw), label=label)


def answer_for(question: str, data: Optional[dict] = None) -> Optional[Answer]:
    """Resolve a form question to a pre-approved answer.

    Returns None when no pattern matches (an unrecognised question — the caller should ask
    the user), or an Answer with ``needs_input`` set when the field exists but is unfilled.
    Deliberately conservative: a near-miss must not be answered with a neighbouring fact.
    """
    data = load_answers() if data is None else data
    text = question.lower()
    for pattern, dotted, label in QUESTION_PATTERNS:
        if re.search(pattern, text):
            answer = get_field(dotted, data)
            # A reusable policy can safely fill an otherwise-unset salary/work-authorisation
            # field, while a role-specific value in the established profile remains preferred.
            fallback = {
                "work_authorization.summary": "application.work_authorization",
                "compensation.expected_salary": "application.compensation",
                "compensation.current_salary": "application.compensation",
            }.get(dotted)
            if answer.needs_input and fallback:
                policy_answer = get_field(fallback, data)
                if not policy_answer.needs_input:
                    answer = policy_answer
            return Answer(field=answer.field, value=answer.value, label=label)
    return None


def years_for_skill(skill: str, data: Optional[dict] = None) -> Answer:
    data = load_answers() if data is None else data
    skills = _lookup(data, "experience.years_by_skill") or {}
    match = next((k for k in skills if k.lower() == skill.lower()), None)
    label = f"Years of {skill}"
    if match is None or _is_missing(skills.get(match)):
        return Answer(field=f"experience.years_by_skill.{skill}", value=None, label=label)
    return Answer(field=f"experience.years_by_skill.{match}", value=str(skills[match]), label=label)


def _iter_leaves(node: Any, path: str = ""):
    """Yield ``(dotted_path, value)`` for every leaf. Dicts recurse; lists are leaves."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_leaves(value, f"{path}.{key}" if path else key)
    else:
        yield path, node


def collect(data: Optional[dict] = None):
    """Split the answers into ``(approved, needs_input)`` for structured consumers.

    ``approved`` mirrors the config tree with every unfilled leaf replaced by ``None``, so
    a caller can never render the ``NEEDS_INPUT`` sentinel as though it were a real answer;
    ``needs_input`` is the list of dotted paths the user still has to supply.
    """
    data = load_answers() if data is None else data
    approved: dict = {}
    needs_input: list[str] = []
    if not isinstance(data, dict):
        return approved, needs_input

    for path, value in _iter_leaves(data):
        missing = not isinstance(value, list) and _is_missing(value)
        if missing:
            needs_input.append(path)
        parts = path.split(".")
        node = approved
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None if missing else value

    return approved, needs_input


def approved_facts_block(data: Optional[dict] = None) -> str:
    """Flatten the filled-in answers for use as LLM context.

    Only approved values are included; unfilled fields are listed separately so the model
    asks about them instead of inventing them.
    """
    data = load_answers() if data is None else data
    if not data:
        return "(No pre-approved answers configured.)"

    filled: list[str] = []
    missing: list[str] = []

    for path, value in _iter_leaves(data):
        if isinstance(value, list):
            if value:
                filled.append(f"- {path}: {', '.join(str(v) for v in value)}")
        elif _is_missing(value):
            missing.append(path)
        else:
            filled.append(f"- {path}: {value}")

    lines = ["APPROVED FACTS (use these verbatim when a form or question asks for them):"]
    lines.extend(filled or ["- (none configured)"])
    if missing:
        lines.append(
            "\nNOT PROVIDED — you must ask Sunil for these rather than guessing: "
            + ", ".join(missing)
        )
    return "\n".join(lines)
