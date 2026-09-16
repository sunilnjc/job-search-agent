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
# A search still fetches at most eight feeds (select_public_boards clamps the
# limit); a larger catalog widens what the router can choose from, which is what
# stops a UAE/Europe search from returning the same two boards every time.
MAX_CATALOG_BOARDS = 56

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
    # Verified live on 2026-09-16: each board answered its provider's public
    # endpoint with a non-empty posting list. Slugs that 404'd or returned an
    # empty list are deliberately absent; a dead slug degrades every search it
    # is routed into. Tags are observed coverage, never an eligibility promise.
    ("greenhouse", "stripe", ("software", "product", "finance", "data", "design", "commercial", "legal"), ("US", "GB", "IE", "SG", "IN")),
    ("greenhouse", "databricks", ("software", "data", "product", "commercial", "entry"), ("US", "GB", "DE", "IN", "SG")),
    ("greenhouse", "anthropic", ("software", "data", "research", "product", "design", "legal"), ("US", "GB")),
    ("greenhouse", "datadog", ("software", "data", "product", "security", "commercial"), ("US", "FR", "IE", "ES", "JP")),
    ("greenhouse", "cloudflare", ("software", "security", "product", "data", "commercial"), ("US", "GB", "PT", "SG", "AU")),
    ("greenhouse", "coinbase", ("software", "finance", "security", "data", "product", "legal"), ("US", "GB", "IE", "IN")),
    ("greenhouse", "gitlab", ("software", "product", "security", "commercial", "operations"), ("US", "GB", "DE", "NL", "IN")),
    ("greenhouse", "affirm", ("software", "finance", "data", "product", "commercial"), ("US", "CA", "ES", "PL")),
    ("greenhouse", "brex", ("software", "finance", "product", "data", "commercial"), ("US", "CA", "IN")),
    ("greenhouse", "samsara", ("software", "data", "product", "commercial", "operations"), ("US", "GB", "MX")),
    ("greenhouse", "figma", ("design", "software", "product", "commercial"), ("US", "GB", "JP")),
    ("greenhouse", "airbnb", ("software", "product", "data", "design", "commercial", "legal"), ("US", "GB", "IE", "IN")),
    ("greenhouse", "pinterest", ("software", "product", "data", "design", "commercial"), ("US", "GB", "IE", "MX")),
    ("greenhouse", "reddit", ("software", "product", "data", "design", "commercial"), ("US", "GB", "CA", "IE")),
    ("greenhouse", "lyft", ("software", "product", "data", "design", "operations"), ("US", "CA", "MX")),
    ("greenhouse", "instacart", ("software", "product", "data", "commercial", "operations"), ("US", "CA")),
    ("greenhouse", "flexport", ("operations", "software", "product", "commercial", "finance"), ("US", "NL", "CN", "SG")),
    ("greenhouse", "twilio", ("software", "product", "data", "commercial", "security"), ("US", "GB", "IE", "IN", "SG")),
    ("greenhouse", "robinhood", ("software", "finance", "data", "product", "security"), ("US", "GB", "CA")),
    ("greenhouse", "asana", ("software", "product", "design", "commercial", "data"), ("US", "GB", "DE", "JP")),
    ("greenhouse", "dropbox", ("software", "product", "data", "design", "commercial"), ("US", "IE", "PL")),
    ("greenhouse", "sofi", ("finance", "software", "data", "product", "commercial"), ("US",)),
    ("greenhouse", "wise", ("finance", "software", "product", "data", "commercial"), ("GB", "EE", "HU", "SG", "AE")),
    ("ashby", "openai", ("software", "research", "data", "product", "design", "legal"), ("US", "GB", "IE", "JP", "SG")),
    ("ashby", "notion", ("software", "product", "design", "commercial", "data"), ("US", "GB", "IE", "JP")),
    ("ashby", "ramp", ("finance", "software", "product", "data", "commercial"), ("US", "CA", "GB")),
    ("ashby", "vanta", ("security", "software", "product", "commercial", "legal"), ("US", "GB", "IE")),
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
        # Geography must be able to reorder boards that tie on profession,
        # otherwise a UAE/Europe search keeps returning whichever boards were
        # reviewed first. Still worth less than a profession match, so it can
        # never pull an unrelated profession ahead of a relevant one.
        if overlap and countries & set(locations):
            score += 14
        # A board tagged for many professions matches everything, so it must not
        # outrank a specialist purely by breadth. Without this, boards reviewed
        # first won every tie and a later catalog entry was never reachable.
        # Breadth is only a tie-break: a board concentrated in a requested
        # country stays ahead of a broader one, so asking for Germany still
        # surfaces the German board first.
        focus = len(countries & set(locations)) / len(locations) if locations else 0
        specificity = (-round(focus * 4), -min(len(tags), 8))
        # Review order is the last resort only, so the catalog stays deterministic
        # (no rotation that makes results disappear on retry, no request data
        # retained) without letting the boards reviewed first win every tie.
        return (-score, *specificity, REVIEWED_PUBLIC_CATALOG.index(entry))

    ordered = sorted(REVIEWED_PUBLIC_CATALOG, key=priority)
    return tuple((provider, name) for provider, name, _, _ in ordered[:limit])

COVERAGE_DISCLAIMER = (
    "Discovery searches a limited sample of public employer boards, not the whole job market. "
    "Coverage varies by profession, country and employer; no results does not mean no jobs exist. "
    "Public feeds can be incomplete, stale or unavailable. Remote work is not worldwide work eligibility."
)
