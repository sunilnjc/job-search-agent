"""Public ATS discovery, isolated from founder state and from saved mobile jobs.

Create ONE DiscoveryService per app worker; authenticate and load the caller's
RLS-scoped preference row before calling search(). Only public board snapshots
are shared/cached. Queries, preferences, filtered results and bearer tokens are
never sent upstream or stored here. No database, model, application, or file I/O.
See docs/mobile-discovery.md for the route contract and deployment boundaries.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Annotated, Callable, Literal, Mapping, Optional
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

MAX_BOARDS = 8
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_BOARD_JOBS = 300
MAX_DESCRIPTION_CHARS = 16_000
MAX_RESULTS_BYTES = 1024 * 1024
FEED_TIMEOUT_SECONDS = 6.0
SEARCH_TIMEOUT_SECONDS = 20.0
CACHE_SECONDS = 300
FAILURE_CACHE_SECONDS = 60
_BOARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z", re.ASCII)
_EXTERNAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}\Z", re.ASCII)
_PROVIDERS = frozenset({"greenhouse", "lever", "ashby"})
_ENV_KEY = "MOBILE_DISCOVERY_BOARDS"


class DiscoveryError(ValueError):
    """Safe route-facing error; never includes input, upstream body or URL."""

    def __init__(self, code: str, status_code: int, message: str, retry_after: int = 0):
        super().__init__(message)
        self.code, self.status_code, self.retry_after = code, status_code, retry_after


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


_Term = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]
_StoredPreferenceTerm = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=240)]


class DiscoveryFilters(_Input):
    titles: list[_Term] = Field(default_factory=list, max_length=10)
    locations: list[_Term] = Field(default_factory=list, max_length=10)
    workplace_type: Literal["any", "remote", "hybrid", "onsite"] = "any"


class DiscoverySearchRequest(_Input):
    query: str = Field("", max_length=160)
    filters: DiscoveryFilters = Field(default_factory=DiscoveryFilters)
    limit: int = Field(20, ge=1, le=50)


class _Preferences(_Input):
    # Stored rows can outlive the current write schema. Do not reuse the narrower
    # public request-filter bound for their independent compatibility contract.
    target_titles: list[_StoredPreferenceTerm] = Field(default_factory=list, max_length=30)
    preferred_locations: list[_StoredPreferenceTerm] = Field(default_factory=list, max_length=30)
    preferred_regions: list[_StoredPreferenceTerm] = Field(default_factory=list, max_length=30)
    remote_preference: Literal["remote_only", "hybrid", "onsite", "open"] = "open"
    sponsorship_required: Optional[bool] = None


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def _not_finite(value):
    raise ValueError("Nonfinite number")


@dataclass(frozen=True)
class Board:
    provider: str
    name: str

    def __post_init__(self):
        if not isinstance(self.provider, str) or self.provider not in _PROVIDERS or not isinstance(self.name, str) or not _BOARD.fullmatch(self.name):
            raise DiscoveryError("invalid_configuration", 503, "Discovery board configuration is invalid.")

    @property
    def key(self) -> str:
        return self.provider + ":" + self.name

    def endpoint(self) -> tuple[str, dict]:
        # Neither tenant input nor feed-provided URLs participate in these URLs.
        if self.provider == "greenhouse":
            return "https://boards-api.greenhouse.io/v1/boards/" + self.name + "/jobs", {"content": "true"}
        if self.provider == "lever":
            return "https://api.lever.co/v0/postings/" + self.name, {"mode": "json", "skip": "0", "limit": str(MAX_BOARD_JOBS + 1)}
        return "https://api.ashbyhq.com/posting-api/job-board/" + self.name, {}


@dataclass(frozen=True)
class DiscoveryConfig:
    boards: tuple[Board, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.boards, tuple) or len(self.boards) > MAX_BOARDS
                or any(not isinstance(board, Board) for board in self.boards)
                or len(set(self.boards)) != len(self.boards)):
            raise DiscoveryError("invalid_configuration", 503, "Discovery board configuration is invalid.")

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> DiscoveryConfig:
        """Read ONLY the public board allowlist. No dotenv or founder settings."""
        raw = (os.environ if environ is None else environ).get(_ENV_KEY, "{}")
        try:
            if not isinstance(raw, str) or len(raw) > 4096:
                raise ValueError("Configuration bound")
            data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_not_finite)
            if not isinstance(data, dict) or set(data) - _PROVIDERS:
                raise ValueError("Unknown provider")
            boards = []
            for provider, names in sorted(data.items()):
                if not isinstance(names, list) or len(names) > MAX_BOARDS:
                    raise ValueError("Board list bound")
                boards.extend(Board(provider, name) for name in names)
            return cls(tuple(boards))
        except (ValueError, TypeError, RecursionError):
            raise DiscoveryError("invalid_configuration", 503, "Discovery board configuration is invalid.") from None


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "li", "br", "div", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    if re.search(r"[\ud800-\udfff]", value):
        raise ValueError("Invalid Unicode text")
    parser = _PlainText()
    # Greenhouse can entity-encode the HTML. Input is bounded by feed size first.
    parser.feed(html.unescape(value))
    text = "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).strip()[:limit]


def _timestamp(value, *, milliseconds=False) -> Optional[str]:
    try:
        if milliseconds and type(value) in {int, float}:
            moment = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        elif isinstance(value, str) and len(value) <= 64:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                return None
        else:
            return None
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _workplace(explicit, location: str) -> str:
    values = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}
    if isinstance(explicit, str) and explicit.casefold() in values:
        return values[explicit.casefold()]
    signals = [kind for word, kind in ((r"remote", "remote"), (r"hybrid", "hybrid"), (r"on[ -]?site", "onsite"))
               if re.search(r"\b" + word + r"\b", location, re.I)]
    return signals[0] if len(signals) == 1 else "unknown"


def _normalize_job(board: Board, raw: dict, fetched_at: str) -> Optional[dict]:
    if not isinstance(raw, dict):
        raise ValueError("Job is not an object")
    if board.provider == "ashby" and raw.get("isListed") is False:
        return None
    if board.provider == "ashby" and raw.get("isListed") is not True:
        raise ValueError("Invalid publication marker")
    raw_id = raw.get("id")
    external_id = str(raw_id) if type(raw_id) is int and raw_id > 0 else raw_id
    if board.provider == "ashby":
        # Public Ashby schema need not supply id. Derive it only from a hosted,
        # same-board posting URL; never follow this URL or the applyUrl field.
        url = raw.get("jobUrl")
        if not isinstance(url, str) or len(url) > 2048 or re.search(r"[\s\\]", url):
            raise ValueError("Invalid source identity")
        parts = urlsplit(url)
        path = parts.path.strip("/").split("/")
        if (parts.scheme != "https" or parts.netloc != "jobs.ashbyhq.com" or len(path) != 2
                or path[0] != board.name or not _EXTERNAL_ID.fullmatch(path[1])):
            raise ValueError("Invalid source identity")
        if external_id is not None and external_id != path[1]:
            raise ValueError("Conflicting source identity")
        external_id = path[1]
    if not isinstance(external_id, str) or not _EXTERNAL_ID.fullmatch(external_id):
        raise ValueError("Missing source identity")
    if board.provider == "greenhouse" and not external_id.isdigit():
        raise ValueError("Invalid source identity")
    if board.provider == "greenhouse":
        external_id = str(int(external_id))
        if external_id == "0":
            raise ValueError("Invalid source identity")
    title = _plain(raw.get("text") if board.provider == "lever" else raw.get("title"), 161)
    if not title or len(title) > 160:
        raise ValueError("Missing or oversized title")

    location, country, department, employment, explicit = "", "", "", "", None
    published, updated, created = None, None, None
    sections = []
    if board.provider == "greenhouse":
        location = (raw.get("location") or {}).get("name", "")
        sections = [raw.get("content")]
        department = " ".join(item.get("name", "") for item in (raw.get("departments") or []) if isinstance(item, dict) and isinstance(item.get("name"), str))
        updated = _timestamp(raw.get("updated_at"))  # NOT a publication date.
        source_url = f"https://boards.greenhouse.io/{board.name}/jobs/{external_id}"
    elif board.provider == "lever":
        categories = raw.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location", "")]
        if not isinstance(locations, list) or len(locations) > 30:
            raise ValueError("Invalid locations")
        location = " | ".join(item for item in locations if isinstance(item, str))
        country, department = raw.get("country", ""), categories.get("department") or categories.get("team", "")
        employment, explicit = categories.get("commitment", ""), raw.get("workplaceType")
        sections = [raw.get("descriptionPlain") or raw.get("description")]
        lists = raw.get("lists") or []
        if not isinstance(lists, list) or len(lists) > 100:
            raise ValueError("Invalid sections")
        for section in lists:
            if not isinstance(section, dict):
                raise ValueError("Invalid section")
            sections.extend([section.get("text"), section.get("content")])
        sections.append(raw.get("additionalPlain") or raw.get("additional"))
        created = _timestamp(raw.get("createdAt"), milliseconds=True)
        source_url = f"https://jobs.lever.co/{board.name}/{external_id}"
    else:
        locations = [raw.get("location", "")]
        for item in (raw.get("secondaryLocations") or []):
            if isinstance(item, dict):
                locations.append(item.get("location", ""))
        if len(locations) > 30:
            raise ValueError("Invalid locations")
        location = " | ".join(item for item in locations if isinstance(item, str))
        address = raw.get("address") or {}
        country = (address.get("postalAddress") or address).get("addressCountry", "")
        department, employment = raw.get("department", ""), raw.get("employmentType", "")
        explicit = raw.get("workplaceType") or ("remote" if raw.get("isRemote") is True else None)
        sections = [raw.get("descriptionPlain") or raw.get("descriptionHtml")]
        published = _timestamp(raw.get("publishedAt"))
        source_url = f"https://jobs.ashbyhq.com/{board.name}/{external_id}"
    text = "\n".join(_plain(section, MAX_DESCRIPTION_CHARS + 1) for section in sections if section)
    if not text.strip():
        raise ValueError("Missing job description")
    location = _plain(location, 301)
    truncated = len(text) > MAX_DESCRIPTION_CHARS or len(location) > 300
    return {
        "source_id": "ats_" + hashlib.sha256((board.key + "\0" + external_id).encode()).hexdigest(),
        "source": board.key, "provider": board.provider, "board": board.name, "external_id": external_id,
        "source_url": source_url, "title": title, "company_name": board.name,
        "company_name_is_board_identifier": True, "location_text": location[:300],
        "country": _plain(country, 80) or None, "department": _plain(department, 160),
        "employment_type": _plain(employment, 80) or None, "workplace_type": _workplace(explicit, location),
        "description": text[:MAX_DESCRIPTION_CHARS], "content_truncated": truncated,
        "source_published_at": published, "source_updated_at": updated, "source_created_at": created,
        "fetched_at": fetched_at,
    }


def _parse_feed(board: Board, data, fetched_at: str) -> tuple[list[dict], dict]:
    items = data if board.provider == "lever" else data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("Invalid feed envelope")
    capped = len(items) > MAX_BOARD_JOBS or board.provider == "lever" and len(items) >= MAX_BOARD_JOBS + 1
    if board.provider == "greenhouse" and isinstance(data.get("meta"), dict):
        total = data["meta"].get("total")
        capped = capped or type(total) is int and total > len(items)
    found, dropped, unlisted, duplicates = {}, 0, 0, 0
    for raw in items[:MAX_BOARD_JOBS]:
        try:
            job = _normalize_job(board, raw, fetched_at)
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError):
            dropped += 1
            continue
        if job is None:
            unlisted += 1
            continue
        if job["source_id"] in found:
            duplicates += 1
            previous = found[job["source_id"]]
            # Same published identity only. Prefer the newer source update;
            # deterministic first record otherwise, never title/company merging.
            if (job["source_updated_at"] or "") <= (previous["source_updated_at"] or ""):
                continue
        found[job["source_id"]] = job
    truncated = bool(capped or any(job["content_truncated"] for job in found.values()))
    return list(found.values()), {"received_count": len(items), "returned_count": len(found),
        "dropped_count": dropped, "unlisted_count": unlisted, "duplicate_count": duplicates,
        "truncated": truncated, "status": "partial" if dropped or truncated else "ok"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+(?:[+#]+)?", unicodedata.normalize("NFKC", text).casefold()))


def _matches(terms: list[str], text: str) -> bool:
    words = _words(text)
    return not terms or any(bool(_words(term)) and _words(term) <= words for term in terms)


# Explicit geographic lookup, NEVER work-authorization evidence. Europe uses
# the UN M49 area grouping; EU is a separate, narrower membership grouping.
_EUROPE = frozenset("AL AD AT AX BY BE BA BG HR CZ DK EE FO FI FR DE GI GR GG VA HU IS IE IM IT JE LV LI LT LU MT MD MC ME NL MK NO PL PT RO RU SM RS SK SI ES SJ SE CH UA GB".split())
_EU = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split())
_COUNTRY_NAMES = dict(item.split("=", 1) for item in (
    "AL=Albania;AD=Andorra;AT=Austria;AX=Åland Islands;BY=Belarus;BE=Belgium;BA=Bosnia and Herzegovina;"
    "BG=Bulgaria;HR=Croatia;CY=Cyprus;CZ=Czechia;DK=Denmark;EE=Estonia;FO=Faroe Islands;FI=Finland;"
    "FR=France;DE=Germany;GI=Gibraltar;GR=Greece;GG=Guernsey;VA=Holy See;HU=Hungary;IS=Iceland;"
    "IE=Ireland;IM=Isle of Man;IT=Italy;JE=Jersey;LV=Latvia;LI=Liechtenstein;LT=Lithuania;LU=Luxembourg;"
    "MT=Malta;MD=Moldova;MC=Monaco;ME=Montenegro;NL=Netherlands;MK=North Macedonia;NO=Norway;"
    "PL=Poland;PT=Portugal;RO=Romania;RU=Russian Federation;SM=San Marino;RS=Serbia;SK=Slovakia;"
    "SI=Slovenia;ES=Spain;SJ=Svalbard and Jan Mayen;SE=Sweden;CH=Switzerland;UA=Ukraine;GB=United Kingdom;"
    "US=United States;AE=United Arab Emirates;CA=Canada;IN=India;AU=Australia;NZ=New Zealand;"
    "SG=Singapore;JP=Japan;CN=China;BR=Brazil;MX=Mexico;ZA=South Africa;SA=Saudi Arabia;"
    "QA=Qatar;OM=Oman;KW=Kuwait;BH=Bahrain;TR=Türkiye"
).split(";"))


def _geo_key(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


_COUNTRY_ALIASES = {_geo_key(name): code for code, name in _COUNTRY_NAMES.items()}
_COUNTRY_ALIASES.update({code.casefold(): code for code in _COUNTRY_NAMES})
_COUNTRY_ALIASES.update({"usa": "US", "u s": "US", "u s a": "US", "united states of america": "US",
    "uae": "AE", "u a e": "AE", "uk": "GB", "u k": "GB", "gbr": "GB", "great britain": "GB",
    "deu": "DE", "deutschland": "DE", "fra": "FR", "can": "CA", "aus": "AU", "ind": "IN",
    "czech republic": "CZ", "russia": "RU", "vatican city": "VA", "turkey": "TR"})
_COUNTRY_MATCHERS = tuple((re.compile(r"(?<!\w)" + re.escape(alias if len(alias) > 3 else alias.upper()) + r"(?!\w)"),
    code, len(alias) > 3) for alias, code in _COUNTRY_ALIASES.items())
_REGIONS = {"europe": _EUROPE, "eu": _EU, "european union": _EU}
_OPEN_REGIONS = {"anywhere", "worldwide", "global", "world"}


def _region_codes(term: str) -> Optional[frozenset]:
    key = _geo_key(term)
    if key in _OPEN_REGIONS:
        return frozenset()  # User's openness, NOT a worldwide-posting assertion.
    if key in _REGIONS:
        return _REGIONS[key]
    code = _COUNTRY_ALIASES.get(key)
    return frozenset({code}) if code else None


def _country_mentions(text: str) -> set[str]:
    key = _geo_key(text)
    cased_key = " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text)))
    exact = _COUNTRY_ALIASES.get(key.removeprefix("the "))
    found = {exact} if exact else set()
    for pattern, code, casefold in _COUNTRY_MATCHERS:
        # Avoid reading 'in'/'can'/'us' prose as countries. Country-code metadata
        # is parsed exactly above; short codes in mixed location text need caps.
        if pattern.search(key if casefold else cased_key):
            found.add(code)
    return found


def _posting_countries(job: dict) -> set[str]:
    location = job["location_text"]
    text = location + "\n" + job["description"]
    # Country restrictions beat generic 'anywhere' text and a headquarters
    # location. Only parse explicitly scoped clauses, never arbitrary mentions.
    text = re.sub(r"\bU\.S\.(?:A\.)?", "US", text, flags=re.I)
    restricted = set()
    for pattern in (r"\bremote\s+(?:within|in)\s+([^\n.!?;]{1,100})",
                    r"\bremote\s+([^\n.!?;]{1,100}?)\s+only\b",
                    r"\bmust\s+(?:reside|be (?:based|located))\s+in\s+([^\n.!?;]{1,100})"):
        for match in re.finditer(pattern, text, re.I):
            clause = match.group(1)
            if not re.search(r"\b(?:not|except|outside)\b", clause, re.I):
                restricted.update(_country_mentions(clause))
    if restricted:
        return restricted
    found = _country_mentions(location)
    explicit = _COUNTRY_ALIASES.get(_geo_key(job["country"] or ""))
    if explicit:
        found.add(explicit)
    # Known city -> country only. A Dubai-specific filter still requires Dubai,
    # rather than incorrectly broadening to every city in the UAE.
    if _matches(["Dubai", "Abu Dhabi", "Sharjah"], location):
        found.add("AE")
    return found


def _location_group(terms: list[str], job: dict, countries: set[str]) -> Optional[str]:
    if not terms:
        return ""
    location = job["location_text"] + " " + (job["country"] or "")
    uncertain = False
    for term in terms:
        allowed = _region_codes(term)
        if allowed is not None:
            if not allowed:
                return "Location preference is open; posting restrictions still require review."
            if countries:
                if countries & allowed:
                    return "Posting geography overlaps a requested country/region; this is not work authorization."
            elif _matches([term], location):
                return "Posting names the requested region; exact country and eligibility require review."
            else:
                uncertain = True
        elif _matches([term], location):
            return "Matches literal location/region text; no country authorization is inferred."
    if uncertain and job["workplace_type"] in {"remote", "hybrid"}:
        return "Geography is unconfirmed for this remote/hybrid posting; review the regional preference."
    return None


def _filter_reason(job: dict, preferences: _Preferences, request: DiscoverySearchRequest) -> Optional[list[str]]:
    title = job["title"]
    # OR within a preference group; AND between saved preferences and new filters.
    # Literal/token matching is profession-neutral, not an inferred skill score.
    groups = [(preferences.target_titles, title, "Matches stored target title."),
              (request.filters.titles, title, "Matches requested title filter."),
              ([request.query] if request.query else [], title + " " + job["department"] + " " + job["description"], "Matches query words.")]
    reasons = []
    for terms, text, reason in groups:
        if not _matches(terms, text):
            return None
        if terms:
            reasons.append(reason)
    countries = _posting_countries(job)
    for terms in (preferences.preferred_locations + preferences.preferred_regions, request.filters.locations):
        reason = _location_group(terms, job, countries)
        if reason is None:
            return None
        if reason:
            reasons.append(reason)
    stored_type = {"remote_only": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(preferences.remote_preference)
    for kind in (stored_type, None if request.filters.workplace_type == "any" else request.filters.workplace_type):
        if kind and job["workplace_type"] != kind:
            return None
    if stored_type or request.filters.workplace_type != "any":
        reasons.append("Matches workplace filter; this does not establish residence/work rights.")
    return reasons or ["Listed on a configured public employer board; no fit score calculated."]


def _eligibility(job: dict, preferences: _Preferences) -> dict:
    text = job["description"] + " " + job["location_text"]
    reasons = ["Discovery does not verify work authorization, licences, experience, or employer eligibility. Review before saving/applying."]
    no_sponsor = re.search(r"\b(?:no|without) (?:visa )?sponsorship|\b(?:cannot|can't|do not|don't|will not|won't|unable to) (?:offer |provide )?(?:visa )?sponsor|sponsorship (?:is )?(?:not available|not offered|unavailable)", text, re.I)
    if no_sponsor:
        reasons.append("Posting contains a sponsorship restriction" + (" that may conflict with your sponsorship preference." if preferences.sponsorship_required is True else "; confirm your job-specific position."))
    if re.search(r"must (?:reside|be (?:based|located))|citizens? only|authori[sz]ed to work|right to work|security clearance", text, re.I):
        reasons.append("Posting mentions residence, work-rights, citizenship, or clearance conditions requiring review.")
    if job["workplace_type"] in {"remote", "hybrid"}:
        reasons.append("Remote/hybrid does not mean worldwide or remove country restrictions.")
        if not _posting_countries(job):
            reasons.append("Remote country is unspecified or unrecognized; geography requires review, not worldwide eligibility.")
    if re.search(r"licen[cs]e|registration|certification|degree|diploma", text, re.I):
        reasons.append("Review qualification wording and jurisdiction; no candidate credential was checked.")
    if job["content_truncated"]:
        reasons.append("Posting text was truncated; inspect the source for complete requirements.")
    return {"status": "unknown", "provisional": True, "review_required": True,
            "independently_verified": False, "reasons": reasons}


class _Limiter:
    def __init__(self, clock: Callable, capacity: int):
        self.clock, self.capacity, self.entries, self.lock = clock, capacity, {}, threading.Lock()

    def take(self, key: str, limit: int) -> bool:
        now = self.clock()
        with self.lock:
            for old_key in list(self.entries):
                queue = self.entries[old_key]
                while queue and queue[0] <= now - 60:
                    queue.popleft()
                if not queue:
                    del self.entries[old_key]
            if key not in self.entries:
                if len(self.entries) >= self.capacity:
                    return False  # Do not evict active tenants to reset their quota.
                self.entries[key] = deque()
            if len(self.entries[key]) >= limit:
                return False
            self.entries[key].append(now)
            return True


class _FeedFailure(Exception):
    def __init__(self, code: str, *, http_status: Optional[int] = None, retry_after: int = 60):
        self.code, self.http_status, self.retry_after = code, http_status, retry_after


def _retry(value) -> int:
    return min(max(int(value), 60), 3600) if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 9 else 60


class DiscoveryService:
    """Per-worker singleton with bounded public caches and fail-closed quotas.

    transport is an offline test seam, NOT an authenticated client or a route
    argument. Production connections use fixed HTTPS hosts, TLS verification,
    no proxy environment, no redirects/cookies/bearer, and only GET requests.
    """

    def __init__(self, config: DiscoveryConfig, *, transport: Optional[httpx.AsyncBaseTransport] = None,
                 clock: Callable = time.monotonic):
        self.config, self._transport, self._clock = config, transport, clock
        self._cache = {}
        self._locks = {board: asyncio.Lock() for board in config.boards}
        self._network_slots = asyncio.Semaphore(3)
        self._tenants, self._global = _Limiter(clock, 2048), _Limiter(clock, 4)
        self._active, self._active_lock = 0, threading.Lock()

    async def _read(self, board: Board) -> tuple[list[dict], dict]:
        url, params = board.endpoint()
        # A fresh client per GET cannot replay a feed's cookies onto another
        # board. Caller authentication/client defaults can never enter here.
        async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(3.0, connect=2.0, pool=1.0),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
                headers={"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "JobPursuit-PublicDiscovery/1.0"}) as client:
            async with client.stream("GET", url, params=params) as response:
                if 300 <= response.status_code < 400:
                    raise _FeedFailure("redirect_blocked", http_status=response.status_code)
                if response.status_code != 200:
                    raise _FeedFailure("rate_limited" if response.status_code == 429 else "http_error",
                        http_status=response.status_code, retry_after=_retry(response.headers.get("Retry-After")))
                if response.headers.get("Content-Encoding", "identity").casefold() != "identity":
                    raise _FeedFailure("unsupported_encoding")  # Reject compressed bombs before decoding.
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().casefold()
                if content_type != "application/json" and not (content_type.startswith("application/") and content_type.endswith("+json")):
                    raise _FeedFailure("unsupported_content_type")
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 9 or int(length) > MAX_FEED_BYTES):
                    raise _FeedFailure("too_large")
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    if len(raw) + len(chunk) > MAX_FEED_BYTES:
                        raise _FeedFailure("too_large")
                    raw.extend(chunk)
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_not_finite)
        except (ValueError, UnicodeError, RecursionError):
            raise _FeedFailure("invalid_json") from None
        fetched_at = _now()
        try:
            jobs, source = _parse_feed(board, data, fetched_at)
        except (ValueError, TypeError, RecursionError):
            raise _FeedFailure("invalid_feed") from None
        return jobs, {**source, "source": board.key, "fetched_at": fetched_at,
                      "checked_at": fetched_at, "error_code": None, "retry_after": 0}

    @staticmethod
    def _failure(board: Board, failure: _FeedFailure) -> tuple[list, dict]:
        return [], {"source": board.key, "status": "error", "error_code": failure.code,
            "http_status": failure.http_status, "retry_after": failure.retry_after,
            "fetched_at": None, "checked_at": _now(), "truncated": False,
            "received_count": 0, "returned_count": 0, "dropped_count": 0,
            "unlisted_count": 0, "duplicate_count": 0}

    async def _board(self, board: Board) -> tuple[list, dict]:
        async with self._locks[board]:
            cached = self._cache.get(board)
            if cached and cached[0] > self._clock():
                return cached[1], {**cached[2], "cached": True}
            # Expired success is never served as if freshly available after error.
            self._cache.pop(board, None)
            async with self._network_slots:
                if not self._global.take("feed", 20):
                    jobs, source = self._failure(board, _FeedFailure("refresh_limited"))
                else:
                    try:
                        jobs, source = await asyncio.wait_for(self._read(board), FEED_TIMEOUT_SECONDS)
                    except (asyncio.TimeoutError, httpx.TimeoutException):
                        jobs, source = self._failure(board, _FeedFailure("timeout"))
                    except _FeedFailure as failure:
                        jobs, source = self._failure(board, failure)
                    except httpx.HTTPError:
                        jobs, source = self._failure(board, _FeedFailure("network_error"))
                ttl = max(FAILURE_CACHE_SECONDS, source["retry_after"]) if source["status"] == "error" else CACHE_SECONDS
                self._cache[board] = (self._clock() + ttl, jobs, source)
                return jobs, {**source, "cached": False}

    async def search(self, *, user_id: str, preferences: Optional[dict], request) -> dict:
        try:
            if not isinstance(user_id, str) or str(UUID(user_id)) != user_id:
                raise ValueError("Invalid identity")
        except (ValueError, TypeError, AttributeError):
            raise DiscoveryError("invalid_identity", 401, "A verified user identity is required.") from None
        if preferences is not None and (not isinstance(preferences, dict) or preferences.get("user_id") != user_id):
            raise DiscoveryError("preference_owner_mismatch", 403, "Preferences must belong to the authenticated user.")
        try:
            request = DiscoverySearchRequest.model_validate(request.model_dump() if isinstance(request, DiscoverySearchRequest) else request)
            # Ignore all other stored columns, especially authorization notes and
            # timestamps. Never expose user_id or a preference object in results.
            prefs = _Preferences.model_validate({key: value for key, value in (preferences or {}).items() if key in _Preferences.model_fields})
        except ValidationError:
            raise DiscoveryError("invalid_request", 422, "Discovery query or preferences exceed the supported bounds.") from None
        if not self.config.boards:
            raise DiscoveryError("not_configured", 503, "No public discovery boards are configured.")
        if not self._tenants.take(hashlib.sha256(user_id.encode()).hexdigest(), 6) or not self._global.take("search", 60):
            raise DiscoveryError("rate_limited", 429, "Discovery search limit reached. Retry later.", 60)
        with self._active_lock:
            if self._active >= 8:
                raise DiscoveryError("busy", 503, "Discovery is busy. Retry later.", 5)
            self._active += 1
        tasks = {asyncio.create_task(self._board(board)): board for board in self.config.boards}
        try:
            done, pending = await asyncio.wait(tasks, timeout=SEARCH_TIMEOUT_SECONDS)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            snapshots = [(tasks[task], task.result()) for task in done]
            snapshots.extend((tasks[task], self._failure(tasks[task], _FeedFailure("deadline_exceeded"))) for task in pending)
            sources, selected = [], {}
            warnings = []
            if any(_region_codes(term) is None for term in prefs.preferred_regions):
                warnings.append("Some saved regions have no supported country map; only literal location text can match them. Use Europe, EU, a supported country, or an explicit location.")
            for board, (jobs, source) in sorted(snapshots, key=lambda pair: pair[0].key):
                sources.append({**source, "cached": source.get("cached", False)})
                for job in jobs:
                    if (prefs.preferred_regions or prefs.preferred_locations or request.filters.locations) and not _posting_countries(job):
                        warning = "Some postings have unconfirmed geography. Remote/hybrid results are provisional review candidates, not confirmed regional or worldwide matches."
                        if warning not in warnings:
                            warnings.append(warning)
                    reasons = _filter_reason(job, prefs, request)
                    if reasons is not None:
                        # Build new containers; never attach tenant annotations to
                        # cached public records or share a mutable result object.
                        selected[job["source_id"]] = {**job, "match_reasons": reasons,
                            "eligibility_status": "unknown", "eligibility": _eligibility(job, prefs), "persisted": False}
            results, size = [], 0
            for job in sorted(selected.values(), key=lambda item: (item["title"].casefold(), item["source_id"]))[:request.limit]:
                item_size = len(json.dumps(job, ensure_ascii=False).encode("utf-8"))
                if size + item_size > MAX_RESULTS_BYTES - 32_768:
                    break
                results.append(job)
                size += item_size
            truncated = len(results) < len(selected) or any(source["truncated"] for source in sources)
            partial = bool(warnings) or truncated or any(source["status"] != "ok" for source in sources)
            unavailable = all(source["status"] == "error" for source in sources)
            return {"status": "unavailable" if unavailable else "partial" if partial else "ok",
                "results": results, "sources": sources, "partial": partial, "truncated": truncated,
                "matched_count": len(selected), "returned_count": len(results), "searched_at": _now(),
                "warnings": warnings,
                "persisted": False, "ranking": "literal_preferences_then_title", "eligibility_verified": False}
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with self._active_lock:
                self._active -= 1
