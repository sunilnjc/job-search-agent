from __future__ import annotations

import re

# Deterministic screen for work-eligibility signals in a posting, run before any
# LLM scoring. Tags:
#   worldwide  - explicitly remote from anywhere
#   sponsors   - explicitly offers sponsorship (relocation alone is not a visa offer)
#   restricted - requires local work authorization / in-country presence
#   unknown    - no clear signal (common: many source APIs truncate descriptions)

SPONSORS_PATTERNS = [
    r"visa sponsorship (?:is )?(?:available|offered|provided)",
    r"we (?:can |will )?sponsor",
    r"sponsorship (?:is )?available",
]

WORLDWIDE_PATTERNS = [
    r"remote[,\s(-]+(?:work )?(?:from )?anywhere",
    r"work from anywhere",
    r"remote \(?global\)?",
    r"remote[,\s(-]+worldwide",
    r"globally remote",
    r"anywhere in the world",
]

NO_SPONSORSHIP_PATTERNS = [
    r"\b(?:no|without) (?:visa )?sponsorship\b",
    r"\bsponsorship (?:is |will be )?(?:not available|unavailable|not offered|not provided|not supported|not possible)\b",
    r"\b(?:no|do not|don't|does not|doesn't|not able to|unable to|cannot|can't|will not|won't) (?:provide |offer )?(?:visa )?sponsor",
    r"without (?:the need for )?(?:visa )?sponsorship",
]

RESTRICTED_PATTERNS = NO_SPONSORSHIP_PATTERNS + [
    r"(?:must be|are you) (?:legally )?(?:authori[sz]ed|eligible) to work in",
    r"work authori[sz]ation (?:in|for) the (?:us|u\.s\.|united states|uk|eu)",
    r"us citizens?(?:hip)? (?:only|required)",
    r"green card",
    r"security clearance",
    r"must (?:be (?:based|located)|reside) in\b",
    r"\bremote (?:within|in)\b",
    r"\bremote \(?[a-z][a-z .-]{0,60}\s+only\b",
    r"remote \(?(?:us|u\.s\.|usa|united states|uk|eu)(?: only)?\)?",
    r"(?:us|u\.s\.|uk|eu)[- ]based (?:candidates? |applicants? )?only",
    r"right to work in the (?:us|uk|eu)",
    r"eu work permit",
]


def prohibits_sponsorship(text: str) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in NO_SPONSORSHIP_PATTERNS)


def classify(text: str) -> str:
    lowered = text.lower()
    # Retain explicit restrictions even alongside positive/negated sponsorship
    # wording. This is a review signal, not a legal determination for a candidate.
    for pattern in RESTRICTED_PATTERNS:
        if re.search(pattern, lowered):
            return "restricted"
    for pattern in SPONSORS_PATTERNS:
        if re.search(pattern, lowered):
            return "sponsors"
    for pattern in WORLDWIDE_PATTERNS:
        if re.search(pattern, lowered):
            return "worldwide"
    return "unknown"


# Fallback country inference from the free-text location, for rows fetched before
# the jobs.country column existed (only new Adzuna fetches populate it directly).
COUNTRY_KEYWORDS = {
    "US": [
        "united states", "usa", ", us", "county", "texas", "california", "new york",
        "washington", "florida", "illinois", "massachusetts", "georgia", "colorado",
        "oregon", "virginia", "pennsylvania", "ohio", "michigan", "d.c.", "boston",
        "san francisco", "seattle", "chicago", "austin", "dallas", "houston", "atlanta",
    ],
    "GB": [
        "united kingdom", "uk", "england", "scotland", "wales", "northern ireland",
        "london", "manchester", "birmingham", "edinburgh", "belfast", "glasgow",
    ],
    "DE": [
        "germany", "deutschland", "münchen", "munich", "berlin", "frankfurt", "hamburg",
        "köln", "cologne", "nordrhein", "bayern", "baden-württemberg", "hessen",
        "niedersachsen", "rheinland", "sachsen",
    ],
}


# Adzuna serves each country from its own domain, which is a far more reliable country
# signal than parsing free-text city names ("Grand Central, Manhattan", bare "US", etc.).
ADZUNA_DOMAIN_TO_COUNTRY = {
    "co.uk": "GB",
    "com": "US",
    "de": "DE",
    "nl": "NL",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "at": "AT",
    "be": "BE",
    "ch": "CH",
    "pl": "PL",
}


def infer_country(location: str, country: str | None, url: str | None = None) -> str | None:
    if country:
        return country.upper()
    if url:
        m = re.search(r"adzuna\.(co\.uk|com|de|nl|fr|es|it|at|be|ch|pl)\b", url)
        if m:
            return ADZUNA_DOMAIN_TO_COUNTRY[m.group(1)]
    loc = location.lower()
    for code, keywords in COUNTRY_KEYWORDS.items():
        if any(kw in loc for kw in keywords):
            return code
    return None


def needs_unavailable_sponsorship(
    location: str,
    country: str | None,
    remote: bool,  # noqa: ARG001 - kept for signature stability; see note below
    eligibility: str,
    blocked_countries: list[str],
    url: str | None = None,
) -> bool:
    """True for roles in countries where the candidate has no work authorization and the
    posting shows no sponsorship / remote-from-anywhere signal — not worth applying to.

    Note: a "remote" flag does NOT rescue a blocked-country job. A US or UK posting marked
    remote almost always means remote *within that country*, which still requires local
    work authorization. Only an explicit worldwide-remote or sponsorship signal (captured
    as eligibility 'worldwide'/'sponsors') keeps a blocked-country job on the board.
    """
    if eligibility in ("sponsors", "worldwide"):
        return False
    inferred = infer_country(location, country, url)
    return inferred is not None and inferred in {c.upper() for c in blocked_countries}
