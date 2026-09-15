"""Reviewed, credential-free public-board sample; never a candidate/founder feed.

Consumed only when MOBILE_DISCOVERY_BOARDS is absent. A larger finite catalog
is routed locally by profession; each search still fetches at most eight feeds.
Review procedure, observations and limitations: docs/discovery-source-catalog.md.
"""

CATALOG_REVIEWED_ON = "2026-09-15"
DEFAULT_PUBLIC_BOARDS = (
    ("greenhouse", "khanacademy"),
    ("greenhouse", "givewell"),
    ("greenhouse", "mavenclinic"),
    ("greenhouse", "parsleyhealth"),
    ("greenhouse", "ruggable"),
    ("greenhouse", "donorschoose"),
    ("lever", "ro"),
    ("ashby", "wealthsimple"),
)

# Routing tags describe observed board coverage, NOT an employer eligibility
# promise. No candidate text is incorporated into a URL or sent to an employer.
# Keep the catalog finite to bound the per-worker public cache and lock table.
MAX_CATALOG_BOARDS = 24

# provider, board, observed profession tags, observed country codes
REVIEWED_PUBLIC_CATALOG = (
    ("greenhouse", "khanacademy", ("education", "operations", "commercial"), ("US", "IN")),
    ("greenhouse", "givewell", ("research", "operations", "commercial", "legal"), ("US", "GB")),
    ("greenhouse", "mavenclinic", ("healthcare", "product", "software", "design", "commercial"), ("US", "GB")),
    ("greenhouse", "parsleyhealth", ("healthcare", "product", "operations"), ("US",)),
    ("greenhouse", "ruggable", ("manufacturing", "operations", "commercial"), ("US",)),
    ("greenhouse", "donorschoose", ("finance", "education", "commercial", "research"), ("US",)),
    ("lever", "ro", ("nursing", "healthcare", "software", "operations"), ("US",)),
    ("ashby", "wealthsimple", ("finance", "software", "data", "design", "entry", "product"), ("CA",)),
    ("greenhouse", "n26", ("software", "product", "finance", "security", "entry", "commercial"), ("DE", "ES")),
    ("greenhouse", "monzo", ("software", "product", "finance", "data", "entry"), ("GB",)),
    ("greenhouse", "gocardless", ("software", "product", "data", "entry", "commercial", "legal"), ("GB", "LV", "PT")),
    ("greenhouse", "mercury", ("software", "product", "finance", "security", "design", "commercial"), ("US", "CA")),
    ("greenhouse", "doximity", ("product", "finance", "software", "data", "commercial"), ("US",)),
    ("greenhouse", "duolingo", ("software", "product", "data", "design", "education", "commercial"), ("US", "CN", "JP")),
    ("lever", "malt", ("software", "finance", "entry", "commercial", "operations"), ("FR", "GB", "DE", "BE", "ES", "AE")),
    ("ashby", "linear", ("software", "product", "design", "commercial", "operations"), ("US", "GB")),
    ("ashby", "9fin", ("software", "product", "finance", "data", "commercial", "legal"), ("GB", "US")),
    ("greenhouse", "welbehealth", ("nursing", "healthcare", "operations"), ("US",)),
    ("lever", "includedhealth", ("nursing", "healthcare", "finance", "software"), ("US",)),
    ("greenhouse", "akunacapital", ("software", "data", "finance", "entry"), ("US", "CN", "SG")),
)


def select_public_boards(*, interests: frozenset[str], countries: frozenset[str],
                         limit: int = 8) -> tuple[tuple[str, str], ...]:
    """Stable routing, not relaxation or ranking of jobs; no I/O or profile cache.

    Profession overlap dominates a weak geographic routing hint. Entry-level
    tags break ties among relevant professions, not substitute other professions.
    With no recognized interests, keep the historically reviewed broad sample.
    Each selected posting still passes every original hard filter afterwards.
    """
    if type(limit) is not int or limit < 0:
        raise ValueError("Invalid catalog selection bound")
    limit = min(limit, 8)
    if not interests:
        return DEFAULT_PUBLIC_BOARDS[:limit]
    families = interests - {"entry"}

    def priority(entry):
        provider, name, tags, locations = entry
        overlap = len(families & set(tags))
        score = 20 * overlap
        if overlap and "entry" in interests and "entry" in tags:
            score += 12
        if overlap and countries & set(locations):
            score += 4
        # Listed order is reviewed and deterministic; no rotation that makes
        # zero results disappear on retry, and no request-specific data retained.
        return -score, REVIEWED_PUBLIC_CATALOG.index(entry)

    ordered = sorted(REVIEWED_PUBLIC_CATALOG, key=priority)
    return tuple((provider, name) for provider, name, _, _ in ordered[:limit])

COVERAGE_DISCLAIMER = (
    "Discovery searches a limited sample of public employer boards, not the whole job market. "
    "Coverage varies by profession, country and employer; no results does not mean no jobs exist. "
    "Public feeds can be incomplete, stale or unavailable. Remote work is not worldwide work eligibility."
)
