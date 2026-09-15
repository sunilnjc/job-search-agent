"""Conservative founder auto-advancement gates, independent of model scores.

Unknown/unsupported mandatory facts cause review, not an assertion of legal
ineligibility. Literal self-reported evidence is not independent verification.
Soft preferences and merely absent sponsorship wording are not rejection rules.
"""
from __future__ import annotations

import re

from jobagent.matching.eligibility import classify, infer_country, prohibits_sponsorship
from jobagent.models import Profile
from jobagent.profile.answers import country_key

_HARD = re.compile(r"\b(?:must|required|mandatory|essential|prerequisite|shall)\b", re.I)
_CREDENTIAL = re.compile(r"\b(?:licen[cs](?:e[ds]?|ure|ing)|registration|certifications?|certified|degrees?|diplomas?|RN|NMC|CPA|PhD)\b", re.I)
_NEGATIVE = re.compile(r"\b(?:no|not|never|without|don't|doesn't|cannot|can't|expired|lapsed|revoked|suspended|pending|studying|in.progress|not.held|applied|seeking|obtain|planning|unconfirmed|lack\w*)\b", re.I)
_EXECUTIVE = re.compile(r"\b(?:chief\b.{0,35}\bofficer|CEO|COO|CFO|CTO|vice president|VP|executive leadership)\b", re.I)


def _segments(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n+|(?<=[.!?])\s+", text) if part.strip()]


def _required(statement: str) -> bool:
    # A clearly negated requirement is soft. Mixed clauses remain reviewable.
    for clause in re.split(r";|\b(?:but|however|yet)\b", statement, flags=re.I):
        remaining = re.sub(r"\b(?:not (?:required|mandatory|essential)|no .{0,60}? required)\b", "", clause, flags=re.I)
        if _HARD.search(remaining):
            return True
    return False


def _credential_supported(statement: str, facts: list[str]) -> bool:
    # Only a complete literal requirement phrase qualifies automatically. Do not
    # infer licence equivalence, status, jurisdiction or degree from a job title.
    phrase = re.sub(r"^(?:candidates? |applicants? )?(?:must|shall) (?:have|hold|possess)\s+", "", statement, flags=re.I)
    phrase = re.sub(r"\s+(?:is |are )?(?:required|mandatory|essential|a prerequisite)[.!]?$", "", phrase, flags=re.I)
    phrase = re.sub(r"^(?:a|an|the)\s+", "", phrase, flags=re.I).strip(" .!").casefold()
    if not phrase or _HARD.search(phrase):
        return False
    relevant = [fact for fact in facts if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", fact, re.I)]
    return bool(relevant) and not any(_NEGATIVE.search(fact) for fact in relevant)


def review_reasons(profile: Profile, job, preferences: dict) -> list[str]:
    """Return unresolved hard requirements; a nonempty list forbids auto-match."""
    text = " ".join(str(job[key] or "") for key in ("title", "location", "description"))
    facts = _segments(profile.raw_text)
    reasons = []
    if classify(text) == "restricted":
        # Country/work-right facts must be explicit, not inferred from residence.
        country = infer_country(job["location"], job["country"], job["url"])
        authorized = preferences.get("authorized_countries", [])
        known_country = bool(country and isinstance(authorized, list) and any(
            isinstance(item, str) and country_key(item) == country_key(country) for item in authorized))
        citizenship_or_clearance = re.search(r"\b(?:citizens?|citizenship|green card|security clearance)\b", text, re.I)
        # Existing authorization does not establish citizenship, clearance, local
        # residence, or lack of sponsorship requirements.
        residence = re.search(r"must (?:be (?:based|located)|reside) in|\bremote\b", text, re.I)
        no_sponsor = prohibits_sponsorship(text)
        if (not known_country or citizenship_or_clearance or residence or
                (no_sponsor and preferences.get("sponsorship_required") is not False)):
            reasons.append("Confirm this posting's explicit work-rights, sponsorship and residence restrictions.")
    if preferences.get("remote_preference") == "remote_only" and not job["remote"]:
        reasons.append("Remote-only preference is not supported by this posting's workplace information.")
    statements = _segments(job["description"] or "")
    for statement in statements:
        if not _required(statement):
            continue
        if _CREDENTIAL.search(statement) and not _credential_supported(statement, facts):
            reasons.append("Confirm the mandatory qualification, current status and jurisdiction: " + statement[:800])
        years = re.search(r"\b(\d+)\s*\+?\s*(?:years?|yrs?)\b", statement, re.I)
        if years and (profile.years_experience is None or profile.years_experience < int(years.group(1))):
            reasons.append("Confirmed experience does not establish the mandatory years: " + statement[:800])
        if re.search(r"\b(?:leadership|management|manage\w*|executive)\b", statement, re.I) and any(
                re.search(r"\bno (?:people |executive |leadership |management|clinical leadership)|did not manage", fact, re.I) for fact in facts):
            reasons.append("The mandatory leadership requirement conflicts with the candidate's stated background.")
    if _EXECUTIVE.search(job["title"] or "") and not any(_EXECUTIVE.search(fact) and not _NEGATIVE.search(fact) for fact in facts):
        reasons.append("Confirm executive-level experience before automatically matching an executive role.")
    return list(dict.fromkeys(reasons))
