"""Timestamp parsing for values produced by PostgreSQL/PostgREST.

PostgreSQL trims trailing zeros from fractional seconds, so a timestamp can
arrive with any number of digits (``...:09.23869+00:00``). Python 3.9's
``datetime.fromisoformat`` accepts only exactly 3 or 6 digits and rejects a
trailing ``Z``, so parsing such a value raises ValueError on the deployed
runtime while succeeding on 3.11+. Normalize before parsing.
"""
from __future__ import annotations

import re
from datetime import datetime

# Fractional seconds only: anchored to the seconds field, never a UTC offset.
_FRACTION = re.compile(r"(?<=:\d\d)\.(\d{1,9})")
_SHORT_OFFSET = re.compile(r"([+-]\d{2})$")


def parse_moment(value: str) -> datetime:
    """Parse an ISO 8601 timestamp, tolerating database precision variants.

    Raises ValueError for anything that is not a usable timestamp, exactly as
    ``datetime.fromisoformat`` does, so existing callers keep their handling.
    """
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    text = value.strip()
    if text[-1:] in {"Z", "z"}:
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda match: "." + (match.group(1) + "000000")[:6], text, count=1)
    text = _SHORT_OFFSET.sub(r"\1:00", text)
    return datetime.fromisoformat(text)
