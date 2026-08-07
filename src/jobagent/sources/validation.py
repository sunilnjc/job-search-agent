"""Small, respectful live-link validation for direct employer listings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass(frozen=True)
class LinkCheck:
    # None means an employer's anti-bot layer prevented a respectful automated check.
    # It is deliberately distinct from False (expired/unavailable).
    available: Optional[bool]
    final_url: str
    reason: str | None = None


_UNAVAILABLE_MARKERS = (
    "job is no longer available",
    "this job is no longer available",
    "position is no longer available",
    "job posting is no longer available",
    "job has expired",
    "position has been filled",
    "job not found",
)


def check_live_job_link(url: str) -> LinkCheck:
    """Confirm a direct job link still resolves before creating application material.

    This intentionally makes one ordinary GET request only; it does not crawl a site,
    evade protections, or treat an authentication/CAPTCHA page as an application form.
    """
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": "SunilJobSearchAgent/1.0 (personal application tracker)"},
        )
    except httpx.HTTPError as exc:
        return LinkCheck(None, url, f"Could not verify automatically: {type(exc).__name__}")
    if response.status_code in {401, 403, 429}:
        return LinkCheck(None, str(response.url), f"Automated check blocked by the job site (HTTP {response.status_code}); open it manually")
    if response.status_code in {404, 410}:
        return LinkCheck(False, str(response.url), f"Direct job page returned HTTP {response.status_code}")
    if response.status_code >= 400:
        return LinkCheck(None, str(response.url), f"Could not verify automatically (HTTP {response.status_code})")
    text = response.text[:200_000].lower()
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return LinkCheck(False, str(response.url), "Direct job page says the role is unavailable or expired")
    return LinkCheck(True, str(response.url))
