"""Local, bounded lexical relevance from explicitly saved career self-reports.

No provider, file, database, résumé import, identity field, or authorization note
is a source of career evidence. Points order a board sample, not probabilities,
credential verification, or eligibility decisions. The caller owns tenant checks.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from .evidence import INSTRUCTIONS
from .schemas import CareerBackground, MAX_TEXT_CHARS

METHOD = "profile_rules_v1"

# Deliberately finite, unambiguous title aliases. PM is NOT expanded: product,
# project and program management are different roles. No credential equivalence.
_ALIASES = (
    (r"\bback[ -]?end\b", "backend"),
    (r"\bfront[ -]?end\b", "frontend"),
    (r"\bfull[ -]?stack\b", "fullstack"),
    (r"\bsr\.?\b", "senior"), (r"\bjr\.?\b", "junior"),
    (r"\brn\b", "registered nurse"),
    (r"\bnursing\b", "nurse"), (r"\bteaching\b", "teacher"),
    (r"\baccounting\b", "accountant"),
    (r"\bfinance analyst\b", "financial analyst"),
    (r"\b(product|project|program) management\b", r"\1 manager"),
    (r"\bhr\b", "human resources"),
    (r"\bfp\s*&\s*a\b", "financial planning analysis"),
    (r"\bfinancial planning (?:and|&) analysis\b", "financial planning analysis"),
    (r"\bsoftware develop(?:er|ment engineer)\b", "software engineer"),
    (r"\b(?:backend|frontend|fullstack) developer\b", lambda m: m[0].split()[0] + " software engineer"),
    (r"\b(?:backend|frontend|fullstack) engineer\b", lambda m: m[0].split()[0] + " software engineer"),
)
_ROLE_NOUNS = frozenset("engineer developer nurse teacher educator accountant analyst designer recruiter counsel lawyer therapist physician pharmacist technician consultant manager director supervisor coordinator specialist researcher officer associate mechanic electrician plumber chef cook driver architect assistant backend frontend fullstack".split())
_LEVEL_WORDS = frozenset("senior junior sr jr entry mid intern internship graduate principal lead".split())
_STOP = frozenset("a an and are as at be been by can for from has have i in into is it my of on or our that the their this to was were will with work worked working experience experienced years year skills using built including team teams responsibilities requirements required preferred knowledge ability strong good excellent candidate candidates role job company you your we us they seeking looking".split())
# Words that appear in almost every posting in a field, so sharing them with a
# posting tells the reader nothing. Listing "design, reviews, services" as the
# evidence a role was surfaced reads as filler and invites justified mistrust.
# These are excluded from the shown overlap only; they remain ordinary text for
# title matching, requirement wording and credential checks.
_GENERIC = frozenset("""
software engineer engineering developer development develop developing design designs designing
review reviews reviewing service services system systems platform platforms product products project projects
data code coding build building built deliver delivery deliverables tool tools technical technology technologies
business customer customers client clients user users stakeholder stakeholders process processes solution solutions
support supporting maintain maintaining implement implementing collaborate collaboration communication
environment environments quality best practices practice modern scalable robust complex cross functional
""".split())
_UNSUPPORTED = re.compile(r"\b(?:no|not|never|without|lack\w*|unknown|unconfirmed|unverified|aspir\w*|wish\w*|want\w*|hope\w*|interested|learning|studying|plan(?:ning)?|seeking|pursu\w*|transition\w*|switch\w*)\b", re.I)
_CREDENTIAL = re.compile(r"\b(?:licen[cs](?:e[ds]?|ure)|registration|certificat(?:e|ion)s?|certified|degree(?!\s+of\s+(?:autonomy|freedom|ownership|responsibility|independence|flexibility|complexity)\b)|diploma|bachelor'?s?|master'?s?|phd|rn|cpa|acca|cfa|bls|acls|nmc)\b", re.I)
_HARD = re.compile(r"\b(?:required|requirements?|must|mandatory|essential|prerequisite)\b", re.I)
_OPTIONAL = re.compile(r"\b(?:preferred|desirable|desired|optional|nice.to.have|a plus|bonus|not (?:required|mandatory|essential)|no (?:\w+ ){0,4}(?:degree|licen[cs]e|certification|diploma) (?:is )?required)\b", re.I)
_WORK_RIGHTS = re.compile(r"\b(?:visa|sponsor\w*|work (?:rights|permit)|right to work|authori[sz]ed to work|citizen\w*|must reside|must be (?:based|located))\b", re.I)
_CONTACT = re.compile(r"\b(?:phone|mobile|telephone|email|contact)\s*:|\+?\d[\d ()-]{6,}\d", re.I)
_LINK = re.compile(r"https?://|www\.|mailto:|javascript:|data:", re.I)


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+(?:[+#]+)?", unicodedata.normalize("NFKC", text).casefold()))


def _canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    for pattern, replacement in _ALIASES:
        text = re.sub(pattern, replacement, text)
    return text


def title_level(title: str) -> Optional[int]:
    """Only explicit title levels; never infer years or a managerial credential."""
    text = _canonical(title)
    tokens = words(text)
    if tokens & {"chief", "head", "director", "vp"}:
        return 5
    if tokens & {"intern", "internship"}:
        return 0
    if tokens & {"junior", "graduate"} or re.search(r"\bentry[ -]level\b", text):
        return 1
    if tokens & {"principal", "lead"} or "staff" in tokens and "software" in tokens:
        return 4
    if "senior" in tokens:
        return 3
    if re.search(r"\bmid[ -]level\b", text):
        return 2
    # Associate is not globally entry-level (e.g. associate director/physician).
    # Staff nurse is not globally staff-level software engineering.
    return None


def _role_words(title: str) -> set[str]:
    canonical = re.sub(r"\b(entry|mid)[ -]level\b", r"\1", _canonical(title))
    tokens = words(canonical) - _LEVEL_WORDS - {"a", "an", "and", "of", "the"}
    if "software" in tokens:
        tokens.discard("staff")
    return tokens


def _role_match(term: str, title: str) -> bool:
    wanted, actual = _role_words(term), _role_words(title)
    if not wanted or not wanted <= actual:
        return False
    # A business-domain word in a different occupational title is not that
    # occupation: accounting integrations designers are not accountants, and
    # product marketing managers are not product managers. Keep explicit target
    # specialization; these guards narrow matches, never discard user tokens.
    if wanted & {"accountant", "finance", "financial"} and not wanted & {"engineer", "designer", "developer"}:
        if actual & {"engineer", "designer", "developer"}:
            return False
    if {"product", "manager"} <= wanted and "marketing" not in wanted:
        if re.search(r"\bproduct marketing\b", _canonical(title)):
            return False
    # Function words in a title must not turn a different profession into a
    # match: "Program Manager, Product" is not a Product Manager synonym.
    for group in ({"product", "project", "program"}, {"backend", "frontend"}):
        selected = wanted & group
        if selected and actual & group and not actual & group <= selected:
            return False
    # A registered nurse title does not establish advanced-practice alignment.
    if "nurse" in wanted and "practitioner" not in wanted and "practitioner" in actual:
        return False
    if not wanted & {"manager", "director", "head", "chief", "vp"} and actual & {"manager", "director", "head", "chief", "vp"}:
        return False
    return True


def matches_titles(terms: list[str], title: str) -> bool:
    """OR within a title group; an explicit requested level is never dropped."""
    return not terms or any(_role_match(term, title) and
        (title_level(term) is None or title_level(term) == title_level(title)) for term in terms)


def is_role_query(query: str) -> bool:
    return bool(words(_canonical(query)) & _ROLE_NOUNS)


def is_open_role_title(title: str) -> bool:
    """Exclude explicitly labeled talent pools, not infer expiry or live status."""
    return not bool(re.search(r"\b(?:future opportunit\w*|talent (?:pool|community|network)|open call|general application|spontaneous application|expression of interest|register (?:your )?interest)\b|don't see (?:the|a) (?:perfect|right) (?:fit|role)", title, re.I))


def discovery_interests(titles: list[str], *, profession: str = "", level: str = "") -> frozenset[str]:
    """Coarse local source routing only; NEVER title aliases or fit evidence.

    Target roles override a previous profession for career changers. Generic or
    ambiguous words (PM, engineer, manager) do not default to software. These
    tags cannot make a posting pass matches_titles or establish qualifications.
    """
    text = " ".join(_canonical(title) for title in (titles or [profession]))
    tokens = words(text)
    interests = set()
    for family, pattern in (
        ("software", r"\b(?:software|backend|frontend|fullstack|devops|site reliability|ios|android|platform engineer)\b"),
        ("product", r"\bproduct (?:manager|management|owner)\b"),
        ("finance", r"\b(?:finance|financial|accountant|bookkeep\w*|tax|treasury|investment|fp&a|banker|banking)\b"),
        ("data", r"\b(?:data|machine learning|ml engineer|ai engineer|applied scientist)\b"),
        ("security", r"\b(?:cybersecurity|security|soc|siem)\b"),
        ("nursing", r"\b(?:nurse|midwife|midwifery)\b"),
        ("healthcare", r"\b(?:physician|doctor|pharmac\w*|therapist|clinical|medical|care coordinator)\b"),
        ("education", r"\b(?:teacher|educator|teaching|curriculum|school)\b"),
        ("design", r"\b(?:designer|design|ux|ui)\b"),
        ("commercial", r"\b(?:sales|marketing|account executive|account manager|customer success|customer support|communications|recruit\w*|human resources)\b"),
        ("manufacturing", r"\b(?:mechanical|manufactur\w*|plant|warehouse|electrician|industrial)\b"),
        ("operations", r"\b(?:operations|project manager|program manager|logistics|coordinator|supply chain|administrat\w*)\b"),
        ("legal", r"\b(?:legal|counsel|lawyer|compliance)\b"),
        ("research", r"\b(?:research|researcher|scientist)\b"),
    ):
        if re.search(pattern, text):
            interests.add(family)
    if tokens & {"junior", "intern", "internship", "graduate", "entry"} or level in {"entry", "student", "career_change"}:
        interests.add("entry")
    return frozenset(interests)


def _safe_fact(text: str) -> bool:
    # Reject @ conservatively without an unanchored email regex, which can take
    # quadratic time on a maximum-length word with no valid email suffix.
    return bool(text.strip() and "@" not in text and not INSTRUCTIONS.search(text)
                and not _LINK.search(text) and not _CONTACT.search(text)
                and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", text))


@dataclass(frozen=True)
class ProfileEvidence:
    profession: str = ""
    level: str = "unspecified"
    career_terms: frozenset[str] = frozenset()
    career_roles: tuple[str, ...] = ()
    qualifications: tuple = ()

    @property
    def available(self) -> bool:
        return bool(self.profession or self.level != "unspecified" or self.career_terms or self.qualifications)


def profile_evidence(profile: Optional[dict]) -> ProfileEvidence:
    """Accept only the saved ProfileUpdate career fields, not extracted previews.

    Validation errors deliberately have no user values in the route-facing error.
    Extra profile fields (contact, location, résumé, model output) are ignored.
    """
    if profile is None:
        return ProfileEvidence()
    raw_background = profile.get("career_background")
    background = CareerBackground.model_validate({} if raw_background is None else raw_background)
    career = profile.get("career_text")
    if career is None:
        career = ""
    if not isinstance(career, str) or len(career) > MAX_TEXT_CHARS or re.search(r"[\ud800-\udfff]", career):
        raise ValueError("Invalid saved career text")
    terms, roles = set(), []
    # Identity fields are only a redaction denylist, never positive evidence.
    private_terms = set()
    for key in ("display_name", "phone", "email"):
        value = profile.get(key)
        if isinstance(value, str):
            private_terms.update(words(value[:320]))
    # Negative, prospective, instruction-like and URL-containing clauses cannot
    # supply positive evidence. Saved material remains a user self-report.
    clauses = (clause for line in career.splitlines() if _safe_fact(line)
               for clause in re.split(r"[;.!?]+", line))
    for clause in clauses:
        if not _safe_fact(clause) or _UNSUPPORTED.search(clause):
            continue
        terms.update(term for term in words(clause) - _STOP - private_terms if len(term) <= 40 and any(c.isalpha() for c in term))
        match = re.match(r"\s*(?:(?:i (?:am|was)|(?:i )?work(?:ed)? as)\s+|(?:current |previous )?(?:role|title|profession):\s*)(.+)", clause, re.I)
        role = re.split(r"\s+(?:with|at|for)\s+", match[1] if match else clause.strip(), maxsplit=1, flags=re.I)[0]
        if (match or len(words(role)) <= 8) and is_role_query(role) and len(role) <= 160:
            # Do not convert "collaborated with nurses" into nursing experience.
            if not re.search(r"\b(?:collaborat\w*|support\w*|hiring|hire|recruiting|managed|led|assisted|help\w*|built|developed)\b", role, re.I):
                roles.append(role)
    profession = background.profession
    if not _safe_fact(profession) or _UNSUPPORTED.search(profession):
        profession = ""
    qualifications = tuple(q for q in background.qualifications if _safe_fact(q.name))
    return ProfileEvidence(profession, background.experience_level, frozenset(terms), tuple(roles[:30]), qualifications)


def _segments(description: str):
    """Yield whole posting bullets, rejoining lines a feed broke mid-sentence.

    Boards emit <br> inside a single bullet, so splitting on newlines shatters
    one requirement into fragments ("...data will pass through your software" /
    "from persistent storage through to API endpoint") and each fragment is then
    shown as its own requirement. A line opening lower-case continues the
    previous one; a blank line always ends a bullet.
    """
    buffer = ""
    for raw in description.splitlines():
        line = " ".join(raw.split()).strip(" \t-•*·")
        if not line:
            if buffer:
                yield buffer
            buffer = ""
            continue
        if buffer and line[:1].islower():
            buffer += " " + line
            continue
        if buffer:
            yield buffer
        buffer = line
    if buffer:
        yield buffer


# A section heading introduces the bullets after it; it is never itself a
# requirement. Feeds write these both bare ("Requirements") and colon-terminated
# ("What You Need to Be Successful:", "This role will be a great fit if you:"),
# and the colon form used to survive as a gap because the colon was stripped
# before the heading was recognized.
_REQUIRED_HEADING = re.compile(r"(?:(?:minimum|basic|mandatory|essential) )?(?:requirements|qualifications|what you bring|what you'?l*l? need|what you need to be successful|who you are|background(?: (?:&|and) skills)?|skills|(?:this )?role will be a great fit if you)", re.I)
_OPTIONAL_HEADING = re.compile(r"(?:(?:preferred|optional|desirable)(?: qualifications| requirements)?|nice.to.have(?:s)?|bonus(?: points)?)", re.I)
_NEUTRAL_HEADING = re.compile(r"(?:responsibilities|benefits|about us|about the (?:role|team|opportunity)|what we offer|what you'?l*l? do|in this role(?:, you will)?|traits|perks)", re.I)
# Each heading pattern is a single group, so alternating them cannot change the
# precedence of the branches inside any one of them.
_ANY_HEADING = re.compile("|".join((_REQUIRED_HEADING.pattern, _OPTIONAL_HEADING.pattern, _NEUTRAL_HEADING.pattern)), re.I)
# Split only at a semicolon or a sentence end followed by a new sentence, so an
# abbreviation or a decimal inside one bullet cannot cut it in half.
_CLAUSES = re.compile(r";\s*|(?<=[.!?])\s+(?=[A-Z])")


def _requirements(description: str):
    context = "unknown"
    for segment in _segments(description):
        heading = segment[:-1].strip() if segment.endswith(":") else segment
        if segment.endswith(":") or _ANY_HEADING.fullmatch(heading):
            context = ("required" if _REQUIRED_HEADING.fullmatch(heading)
                       else "optional" if _OPTIONAL_HEADING.fullmatch(heading)
                       else "unknown")
            continue
        for raw in _CLAUSES.split(segment):
            clause = " ".join(raw.split()).strip(" :-•")
            if clause:
                yield from _classify(clause, context)


def _classify(clause: str, context: str):
    """Importance of one clause; wording in the clause outranks its section."""
    optional = _OPTIONAL.search(clause)
    hard = _HARD.search(_OPTIONAL.sub("", clause))
    importance = "unknown" if optional and hard else "optional" if optional else "required" if hard else context
    if importance != "unknown" or _CREDENTIAL.search(clause):
        yield clause[:240], importance


def relevance(job: dict, evidence: ProfileEvidence, *, target_titles: Optional[list[str]] = None,
              today: Optional[date] = None) -> dict:
    """A bounded explanation, without reading/mutating a cached job or profile."""
    reasons, gaps, score = [], [], 0
    if not evidence.available:
        return {"method": METHOD, "score": 0, "reasons": ["No confirmed career evidence supplied; only discovery filters were applied."],
                "gaps": ["Role fit, qualifications and work eligibility need review."], "review_required": True}
    title, description = job["title"], job["description"]
    targets = target_titles or []
    target_match = bool(targets and matches_titles(targets, title))
    profession_match = bool(evidence.profession and _role_match(evidence.profession, title))
    career_match = any(_role_match(role, title) for role in evidence.career_roles)
    aligned = target_match or profession_match or career_match
    if target_match:
        score += 40
        reasons.append("Title matches a saved target role; a target is a preference, not proof of experience.")
    if profession_match:
        score += 25
        reasons.append("Title overlaps the profession in your confirmed career background (self-reported).")
    elif career_match:
        score += 20
        reasons.append("Title overlaps an explicit role in your confirmed career text (self-reported).")
    else:
        gaps.append("No direct title alignment was found in confirmed career experience; review role-specific or transferable evidence.")
    job_level = title_level(title)
    candidate_level = {"student": 0, "entry": 1, "mid": 2, "senior": 3}.get(evidence.level)
    if candidate_level is None and evidence.level == "unspecified":
        candidate_level = title_level(evidence.profession)
        if candidate_level is None:
            levels = {title_level(role) for role in evidence.career_roles} - {None}
            candidate_level = next(iter(levels)) if len(levels) == 1 else None
    if evidence.level == "career_change":
        gaps.append("Career change is self-reported; target-profession experience and seniority are not established.")
        if aligned and job_level in {0, 1}:
            score += 8
            reasons.append("Posting explicitly names an entry/intern level for career-change review; this does not establish qualification.")
        elif aligned and job_level is not None and job_level >= 3:
            score -= 15
            gaps.append("Posting names a senior/leadership level; confirm experience in this target profession.")
    elif candidate_level is not None and job_level is not None and aligned:
        if candidate_level == job_level:
            score += 20
            reasons.append("Explicit title level aligns with your self-reported career stage/title.")
        else:
            score -= min(30, 10 * abs(candidate_level - job_level))
            gaps.append("Explicit posting level differs from your self-reported career stage/title.")
    elif job_level is None:
        gaps.append("Posting title does not establish seniority; years and responsibilities require review.")
    # Description repetition cannot overpower role/level evidence; it contributes
    # at most twelve points and never turns an unrelated title into a role match.
    overlap = sorted(evidence.career_terms & (words(description) - _STOP) - _GENERIC)
    if overlap and (aligned or not (evidence.profession or evidence.career_roles)):
        score += 2 * min(len(overlap), 6)
        reasons.append("Posting also mentions terms in your confirmed career text: " + ", ".join(overlap[:6]) + ". This is wording overlap, not verified proficiency.")
    today = today or datetime.now(timezone.utc).date()
    seen = set()
    for clause, importance in _requirements(description):
        if clause in seen:
            continue
        seen.add(clause)
        if len(seen) > 8:
            gaps.append("Additional posting requirements need source review; the explanation is bounded.")
            break
        if not _safe_fact(clause):
            gaps.append("Some posting wording cannot be used as evidence; review the source.")
            continue
        if importance == "optional":
            reasons.append("Optional/preferred wording, not a mandatory gap: " + clause)
            continue
        if _CREDENTIAL.search(clause):
            named = [q for q in evidence.qualifications if words(q.name) <= words(clause)]
            current = [q for q in named if q.status == "current" and (q.expires_on is None or q.expires_on >= today)]
            if current:
                reasons.append("Posting names a self-reported current qualification; jurisdiction, equivalence and employer acceptance remain unverified: " + clause)
            elif named:
                gaps.append("Named qualification is not self-reported current or its saved expiry has passed: " + clause)
            else:
                gaps.append(("Mandatory credential wording needs confirmed evidence review: " if importance == "required" else "Credential wording has unclear importance; review: ") + clause)
        elif importance == "required":
            gaps.append("Explicit requirement needs job-specific evidence review: " + clause)
        else:
            gaps.append("Mixed/unclear requirement wording needs review: " + clause)
    if _WORK_RIGHTS.search(description + " " + job.get("location_text", "")):
        gaps.append("Posting work-rights/residence/sponsorship conditions need job-specific review; profile location is not authorization.")
    if job.get("content_truncated"):
        gaps.append("Posting text is truncated; review the full source requirements.")
    gaps.append("Eligibility and qualifications remain provisional and are not independently verified.")
    return {"method": METHOD, "score": min(100, max(0, score)), "reasons": reasons or ["No positive role evidence was established by these rules."],
            "gaps": gaps, "review_required": True}
