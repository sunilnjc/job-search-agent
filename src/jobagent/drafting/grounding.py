"""Conservative, offline grounding at founder material output/write boundaries.

This is evidence selection, not a semantic entailment model. Only complete source
units may be selected/reordered; paraphrases require a future reviewed-claim flow.
Job descriptions, user chat instructions, model-extracted fields and previously
generated materials are deliberately NOT candidate evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


GROUNDING_MESSAGE = (
    "The proposed edit contains unsupported or unverifiable wording; this edit was not saved. "
    "Use complete facts from the original resume, or confirm the missing facts in your "
    "source resume first. Job requirements and previous AI drafts are not evidence."
)


class GroundingError(ValueError):
    def __init__(self):
        super().__init__(GROUNDING_MESSAGE)


def _normalize(text: str) -> str:
    # Do not remove punctuation, negation, units, qualifiers or change case.
    return " ".join(text.split())


def source_units(resume_text: str) -> tuple[str, ...]:
    """Keep whole paragraphs/bullets; join extraction-wrapped incomplete lines.

    In particular, 'No experience with\nclinical management.' is indivisible, as
    is 'Built X. Did not lead Y.' on one source line. No substring/word-bag matches.
    Ambiguous wrapping may over-group facts; rejecting an edit is safer than
    silently detaching a qualifier or attribution.
    """
    units, pending = [], []
    bullet_context = ""
    pending_bullet = False

    def flush():
        if pending:
            unit = _normalize(" ".join(pending))
            if unit and unit not in units:
                units.append(unit)
            pending.clear()

    for raw in resume_text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            bullet_context = ""
            pending_bullet = False
            continue
        bullet = re.match(r"^(?:[-*•]|\d+[.)])\s+", line)
        if bullet:
            if pending and not pending_bullet:
                # E.g. 'Team achievements:' or 'No experience with:' scopes all
                # following bullets; do not strip that attribution/negation.
                bullet_context = _normalize(" ".join(pending))
                pending.clear()
            else:
                flush()
            line = line[bullet.end():]
            if bullet_context:
                line = bullet_context + " " + line
            pending_bullet = True
        elif not pending:
            bullet_context = ""
            pending_bullet = False
        pending.append(line)
        if re.search(r"[.!?][\"'”’)]?$", line):
            flush()
    flush()
    return tuple(units)


def source_for_prompt(resume_text: str, max_chars: int = 6000) -> str:
    """Budget by whole source units, never by cutting away the end of a fact."""
    selected = []
    used = 0
    for unit in source_units(resume_text):
        if used + len(unit) + 2 <= max_chars:
            selected.append(unit)
            used += len(unit) + 2
    return "\n\n".join(selected)


def _composed_of_units(text: str, units: tuple[str, ...]) -> bool:
    remaining = _normalize(text)
    reachable = {0}
    # Full-unit exact matches only, with whitespace separating consecutive units.
    for offset in range(len(remaining) + 1):
        if offset not in reachable:
            continue
        for unit in units:
            end = offset + len(unit)
            if remaining.startswith(unit, offset):
                if end == len(remaining):
                    return True
                if end < len(remaining) and remaining[end] == " ":
                    reachable.add(end + 1)
    return False


@dataclass(frozen=True)
class GroundingContext:
    resume_text: str
    job_title: str = ""
    job_company: str = ""

    def validate_resume(self, summary: str, highlights: list[str]) -> None:
        units = source_units(self.resume_text)
        if (not isinstance(summary, str) or not summary.strip() or len(summary) > 6000
                or not isinstance(highlights, list) or not 4 <= len(highlights) <= 6
                or any(not isinstance(item, str) or not item.strip() or len(item) > 3000 for item in highlights)):
            raise GroundingError()
        normalized = [_normalize(item) for item in highlights]
        if (len(set(normalized)) != len(normalized)
                or not _composed_of_units(summary, units)
                or any(item not in units for item in normalized)):
            raise GroundingError()

    def validate_cover_letter(self, text: str) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > 18000:
            raise GroundingError()
        # Only neutral structure is exempt from source validation. In particular,
        # no work-rights, relocation, experience or enthusiasm claims are exempt.
        framing = (
            "Dear Hiring Team,", "Dear Hiring Manager,", "Sincerely,", "Kind regards,",
            "Thank you for considering my application.",
            date.today().strftime("%d %B %Y"), date.today().isoformat(),
        )
        if self.job_title and self.job_company and not any(
                char in self.job_title + self.job_company for char in "\r\n"):
            framing += (f"I am applying for the {self.job_title} role at {self.job_company}.",)
        if not _composed_of_units(text, source_units(self.resume_text) + framing):
            raise GroundingError()


def tailoring_markdown(summary: str, highlights: list[str]) -> str:
    return ("## Tailored Professional Summary\n\n" + summary.strip()
            + "\n\n## Tailored Bullet Points\n\n"
            + "\n".join("- " + _normalize(item) for item in highlights) + "\n")
