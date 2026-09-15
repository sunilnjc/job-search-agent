"""One admissibility contract for provider selections and exported claims.

Identity still renders through its validated contact block. Routing metadata and
URLs are not career achievements and must not be offered as prose claims.
"""
import re

INSTRUCTIONS = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|above|system)|(?:system|developer|assistant)\s*:|"
    r"\b(?:invent(?:ed|ing)?|fabricat(?:e|ed|ing)|pretend(?:ed|ing)?|disregard|override|exfiltrate)\b|"
    r"(?:add|claim|say|write|include)\s+(?:that\s+)?(?:i\s+(?:have|am|worked)|you\s+have)|"
    r"\b(?:api[_ -]?key|passwords?|secrets?|access[_ -]?token)\b|<\s*/?\s*(?:script|system|instruction)|"
    r"(?:guaranteed|perfect)\s+(?:ats|interview|job)|100\s*%\s+(?:ats|pass)", re.I)
URL = re.compile(r"(?:https?://|www\.|mailto:|javascript:|data:|[\w.+-]+@[\w.-]+\.)", re.I)
INTERNAL_FACTS = {"career_background.profession", "career_background.experience_level"}


def claim_allowed(fact: dict, *, documents: bool = False) -> bool:
    text = fact.get("text")
    return bool(isinstance(text, str) and 0 < len(text) <= 3000
                and not INSTRUCTIONS.search(text) and not URL.search(text)
                and fact.get("id") not in INTERNAL_FACTS
                and (not documents or fact.get("kind") == "candidate"))
