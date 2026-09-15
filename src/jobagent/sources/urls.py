"""Conservative identity comparison; preserve the original application URL."""
from urllib.parse import unquote_plus, urlsplit, urlunsplit

_TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id"}


def canonical_job_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return url
    if parts.scheme not in {"http", "https"} or not host or parts.username is not None:
        return url
    ats = any(host == domain or host.endswith("." + domain) for domain in (
        "lever.co", "greenhouse.io", "ashbyhq.com"))
    query = []
    for pair in parts.query.split("&"):
        key = unquote_plus(pair.split("=", 1)[0]).casefold()
        if key in _TRACKING_KEYS or (ats and key in {"gh_src", "lever-source", "lever-origin"}):
            continue
        query.append(pair)
    # Unknown sites can use fragments as requisition IDs (hash-based routing).
    # Preserve significant query bytes/order, IDs and signatures everywhere.
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/",
                       "&".join(query), "" if ats else parts.fragment))
