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
from urllib.parse import urljoin, urlparse

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


def choose_apply_link(page_url: str, links: list[tuple[str, str]]) -> Optional[str]:
    """Choose the likely employer Apply URL from links collected on an aggregator page.

    Prefer a recognised ATS.  Links that stay on the listing aggregator are never an
    application destination, even if their button happens to say Apply.
    """
    source_host = urlparse(page_url).netloc.lower()
    choices: list[tuple[int, str]] = []
    for href, label in links:
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        candidate = urljoin(page_url, href)
        host = urlparse(candidate).netloc.lower()
        if not host or host == source_host or is_aggregator(candidate):
            continue
        score = 0
        if detect_from_url(candidate):
            score += 100
        if re.search(r"apply|application|bewerben|solliciteren|postuler", label, re.I):
            score += 30
        if score:
            choices.append((score, candidate))
    return max(choices, default=(0, None), key=lambda item: item[0])[1]


def resolve_with_browser(url: str, timeout_ms: int = 30_000) -> ATSDetection:
    """Resolve a JavaScript-rendered listing into the employer's actual application URL.

    Aggregators commonly return HTTP 403 to a simple client while their browser page carries
    an outbound Apply button. This reads that button only; it never fills or submits a form.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ATSDetection(None, url, False, is_aggregator(url), "playwright_not_installed")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=BROWSER_UA)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(750)
                final_url = page.url
                direct = detect_from_url(final_url)
                if direct:
                    return ATSDetection(direct, final_url, True, False)
                links = page.locator("a[href]").evaluate_all(
                    "els => els.map(a => [a.href, (a.innerText || a.getAttribute('aria-label') || '').trim()])"
                )
                apply_url = choose_apply_link(final_url, [(str(href), str(label)) for href, label in links])
                if not apply_url:
                    return ATSDetection(None, final_url, True, is_aggregator(final_url), "employer_apply_link_not_found")
            finally:
                browser.close()
    except Exception as exc:  # browser/network failure is a reportable exception, not a crash
        return ATSDetection(None, url, False, is_aggregator(url), f"browser_resolution_error:{type(exc).__name__}")
    return resolve(apply_url, fetch_html=True)


def resolve_for_application(url: str) -> ATSDetection:
    """Use fast HTTP resolution first, then a browser only for blocked/aggregator pages."""
    detected = resolve(url)
    if detected.is_aggregator or detected.error:
        browser_detected = resolve_with_browser(url)
        if browser_detected.ats or not browser_detected.is_aggregator:
            return browser_detected
    return detected
