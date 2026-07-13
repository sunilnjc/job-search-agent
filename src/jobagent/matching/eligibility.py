from __future__ import annotations

import re

# Deterministic screen for work-eligibility signals in a posting, run before any
# LLM scoring. Tags:
#   worldwide  - explicitly remote from anywhere
#   sponsors   - mentions visa sponsorship / relocation support
#   restricted - requires local work authorization / in-country presence
#   unknown    - no clear signal (common: many source APIs truncate descriptions)

SPONSORS_PATTERNS = [
    r"visa sponsorship (?:is )?(?:available|offered|provided)",
    r"we (?:can |will )?sponsor",
    r"sponsorship (?:is )?available",
    r"relocation (?:support|assistance|package)",
]

WORLDWIDE_PATTERNS = [
    r"remote[,\s(-]+(?:work )?(?:from )?anywhere",
    r"work from anywhere",
    r"remote \(?global\)?",
    r"remote[,\s(-]+worldwide",
    r"globally remote",
    r"anywhere in the world",
]

RESTRICTED_PATTERNS = [
    r"(?:must be|are you) (?:legally )?(?:authori[sz]ed|eligible) to work in",
    r"work authori[sz]ation (?:in|for) the (?:us|u\.s\.|united states|uk|eu)",
    r"(?:no|not able to|unable to|cannot|can't|will not|won't) (?:provide |offer )?sponsor",
    r"without (?:the need for )?(?:visa )?sponsorship",
    r"us citizens?(?:hip)? (?:only|required)",
    r"green card",
    r"security clearance",
    r"must (?:be (?:based|located)|reside) in (?:the )?(?:us|u\.s\.|united states|uk|eu|europe|canada)",
    r"remote \(?(?:us|u\.s\.|usa|united states|uk|eu)(?: only)?\)?",
    r"(?:us|u\.s\.|uk|eu)[- ]based (?:candidates? |applicants? )?only",
    r"right to work in the (?:us|uk|eu)",
    r"eu work permit",
]


def classify(text: str) -> str:
    lowered = text.lower()
    for pattern in SPONSORS_PATTERNS:
        if re.search(pattern, lowered):
            return "sponsors"
    for pattern in WORLDWIDE_PATTERNS:
        if re.search(pattern, lowered):
            return "worldwide"
    for pattern in RESTRICTED_PATTERNS:
        if re.search(pattern, lowered):
            return "restricted"
    return "unknown"
