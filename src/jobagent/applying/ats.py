"""Identify which applicant tracking system (ATS) a job application lives on.

Knowing the ATS is the prerequisite for staged form-filling: each one has its own field
names and flow, so a handler can only be written per-platform.

Most job URLs in this project come from aggregators (Adzuna, WeWorkRemotely), which
redirect to the employer's real application page. Detection therefore works in two steps:
match the URL if it's already a known ATS, otherwise resolve the redirect chain first.
Resolution is network I/O and is kept separate from the pure matching so the matcher stays
cheap and testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx

# A normal desktop UA — several ATS and career sites reject obvious bot agents with a 403.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ATS name -> (URL pattern, HTML fingerprint). The HTML pattern catches employer career
# pages that embed the ATS in an iframe or mount it client-side, where the address bar
# never shows the ATS domain.
ATS_SIGNATURES: list[tuple[str, str, Optional[str]]] = [
    # gh_jid / gh_src are Greenhouse's own query params, carried even when a company
    # serves the board from its own domain (e.g. stripe.com/jobs/search?gh_jid=...).
    ("greenhouse", r"greenhouse\.io|boards\.greenhouse|[?&]gh_jid=|[?&]gh_src=", r"greenhouse\.io|grnhse"),
    ("lever", r"jobs\.lever\.co|//lever\.co", r"lever\.co/|leverapp"),
    ("workday", r"myworkdayjobs\.com|\.workday\.com", r"myworkdayjobs|workdayjobs"),
    ("ashby", r"jobs\.ashbyhq\.com|ashbyhq\.com", r"ashbyhq"),
    ("smartrecruiters", r"smartrecruiters\.com", r"smartrecruiters"),
    ("workable", r"apply\.workable\.com|workable\.com", r"workable\.com"),
    ("icims", r"icims\.com", r"icims"),
    ("taleo", r"taleo\.net", r"taleo"),
    ("successfactors", r"successfactors\.|jobs\.sap\.com", r"successfactors"),
    ("bamboohr", r"bamboohr\.com", r"bamboohr"),
    ("recruitee", r"recruitee\.com", r"recruitee"),
    ("personio", r"personio\.(com|de)|jobs\.personio", r"personio"),
    ("teamtailor", r"teamtailor\.com", r"teamtailor"),
    ("jobvite", r"jobvite\.com", r"jobvite"),
    ("join", r"join\.com", r"join\.com"),
]

# Aggregators that host listings but are never the place you actually apply.
AGGREGATOR_PATTERN = r"adzuna\.|weworkremotely\.com|remoteok\.com|indeed\.|linkedin\.com/jobs"


@dataclass(frozen=True)
class ATSDetection:
    """Where an application actually lives, and how confident we are."""

    ats: Optional[str]
    final_url: str
    resolved: bool          # True when we followed redirects rather than guessing from the input
    is_aggregator: bool     # final URL is still an aggregator: no real application form reached
    error: Optional[str] = None

    @property
    def supported(self) -> bool:
        return self.ats is not None


def detect_from_url(url: str) -> Optional[str]:
    """Match a URL against known ATS domains. Pure — no network."""
    for name, url_pattern, _ in ATS_SIGNATURES:
        if re.search(url_pattern, url, re.I):
            return name
    return None


def detect_from_html(html: str) -> Optional[str]:
    """Fingerprint an employer career page that embeds an ATS. Pure — no network."""
    for name, _, html_pattern in ATS_SIGNATURES:
        if html_pattern and re.search(html_pattern, html, re.I):
            return name
    return None


def is_aggregator(url: str) -> bool:
    return bool(re.search(AGGREGATOR_PATTERN, url, re.I))


def resolve(url: str, timeout: float = 8.0, fetch_html: bool = True) -> ATSDetection:
    """Follow redirects to find the real application page and identify its ATS.

    Never raises: a job whose site is down or blocks us must still be reportable, so
    failures come back as an ATSDetection carrying the error.
    """
    direct = detect_from_url(url)
    if direct:
        return ATSDetection(ats=direct, final_url=url, resolved=False, is_aggregator=False)

    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": BROWSER_UA},
        )
    except Exception as exc:  # noqa: BLE001 - network failure is data, not a crash
        return ATSDetection(
            ats=None, final_url=url, resolved=False, is_aggregator=is_aggregator(url),
            error=f"{type(exc).__name__}: {exc}"[:200],
        )

    final_url = str(response.url)
    ats = detect_from_url(final_url)
    if ats is None and fetch_html:
        ats = detect_from_html(response.text[:200_000])

    return ATSDetection(
        ats=ats,
        final_url=final_url,
        resolved=True,
        is_aggregator=is_aggregator(final_url),
        error=None if response.status_code < 400 else f"HTTP {response.status_code}",
    )
