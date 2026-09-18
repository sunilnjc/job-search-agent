"""Additive why / evidence / uncertainty shape for ranking and assessments.

The model still selects verbatim source_facts; this module only structures the
server-built rationale. Old clients keep `rationale`. New clients may read
`fit_explanation`. Invalid stored JSON is ignored and rebuilt from rationale.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional, Sequence

_MAX_ITEMS = 16
_MAX_CHARS = 2000
_SOURCE_CITATION = re.compile(
    r"\s*\[(?:[a-z][\w]*(?:\.[a-z\d_]+)*)(?:,\s*(?:[a-z][\w]*(?:\.[a-z\d_]+)*))*\]",
    re.I,
)
_EVIDENCE_PREFIXES = (
    "Job posting:",
    "Confirmed information:",
    "Additional complete evidence",
)
_UNCERTAINTY_MARKERS = (
    "unvalidated",
    "could not be validated",
    "needs review",
    "follow-up",
    "provisional",
    "unknown",
    "ineligible",
    "conflict",
    "not independently verified",
    "confirm career",
    "resolve the returned",
    "mandatory requirement",
    "not been evaluated",
    "requirement comparison was discarded",
    "hard constraint",
    "do you want to keep",
)


def _clean_line(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(_SOURCE_CITATION.sub("", value).split()).strip()
    if not text or len(text) > _MAX_CHARS:
        return text[:_MAX_CHARS] if text else None
    return text


def _clean_list(values: Any, *, limit: int = _MAX_ITEMS) -> list[str]:
    if not isinstance(values, list):
        return []
    items = []
    for value in values[:limit]:
        text = _clean_line(value)
        if text and text not in items:
            items.append(text)
    return items


def explanation_shape(why: Iterable[str] = (), evidence: Iterable[str] = (),
                      uncertainty: Iterable[str] = ()) -> dict:
    return {
        "why": _clean_list(list(why)),
        "evidence": _clean_list(list(evidence)),
        "uncertainty": _clean_list(list(uncertainty)),
    }


def rationale_from_entries(entries: Sequence[tuple[str, str]]) -> str:
    lines = []
    total = 0
    for _bucket, text in entries:
        cleaned = _clean_line(text)
        if not cleaned:
            continue
        if total + len(cleaned) + 1 > 8000:
            break
        lines.append(cleaned)
        total += len(cleaned) + 1
    return "\n".join(lines)


def explanation_from_entries(entries: Sequence[tuple[str, str]]) -> dict:
    buckets = {"why": [], "evidence": [], "uncertainty": []}
    for bucket, text in entries:
        if bucket not in buckets:
            bucket = "why"
        cleaned = _clean_line(text)
        if cleaned:
            buckets[bucket].append(cleaned)
    return explanation_shape(buckets["why"], buckets["evidence"], buckets["uncertainty"])


def is_evidence_line(text: str) -> bool:
    return text.startswith(_EVIDENCE_PREFIXES)


def is_uncertainty_line(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _UNCERTAINTY_MARKERS)


def fit_explanation_from_rationale(rationale: Any) -> dict:
    """Backward-compatible split of a legacy concatenated rationale."""
    if not isinstance(rationale, str) or not rationale.strip():
        return explanation_shape()
    why, evidence, uncertainty = [], [], []
    for raw in rationale.splitlines():
        text = _clean_line(raw)
        if not text:
            continue
        if is_evidence_line(text):
            evidence.append(text)
        elif is_uncertainty_line(text):
            uncertainty.append(text)
        else:
            why.append(text)
    return explanation_shape(why, evidence, uncertainty)


def validated_fit_explanation(value: Any) -> Optional[dict]:
    if not isinstance(value, Mapping):
        return None
    extra = set(value) - {"why", "evidence", "uncertainty"}
    if extra:
        # Additive unknown keys are ignored, never treated as a parse failure.
        value = {key: value[key] for key in ("why", "evidence", "uncertainty") if key in value}
    shape = explanation_shape(value.get("why"), value.get("evidence"), value.get("uncertainty"))
    if not any(shape.values()):
        return None
    return shape


def coerce_fit_explanation(value: Any, *, rationale: Any = None) -> dict:
    structured = validated_fit_explanation(value)
    if structured:
        return structured
    return fit_explanation_from_rationale(rationale)
