# Isolated mobile public-ATS discovery

Implementation: `src/jobagent/mobile/discovery.py`.
Tests: `tests/test_mobile_discovery.py` and `tests/test_mobile_discovery_api.py`.
This module does not write jobs, read founder state, or apply to jobs. The main
app owns route wiring; the API suite exercises that real route with fake transports.

## Contract for app integration

Authenticated endpoint: `POST /api/mobile/discovery/search`.
Use the existing verified-user/access dependency. Do not accept an owner, saved
preferences, provider, board, URL, HTTP headers, bearer, or transport from the body.
Enforce an 8 KiB raw request-body cap before parsing and retain sanitized validation
errors. Bind the module's request model directly or validate it explicitly.

```json
{
  "query": "registered nurse",
  "filters": {
    "titles": ["Nurse"],
    "locations": ["Toronto"],
    "workplace_type": "any"
  },
  "limit": 20
}
```

All fields are optional. Query: at most 160 characters. Each filter list: at most
10 nonempty strings of 160 characters each. Workplace: `any`, `remote`, `hybrid`,
or `onsite`. Limit: strict integer 1–50, default 20. Extra fields are rejected.
Stored preference terms have an independent read-side compatibility bound of 240
characters (30 terms per field), not the request-filter bound of 160. This does
not change the separately owned preference-write schema or authorize body-supplied
preferences.

```python
from jobagent.mobile.discovery import (
    DiscoveryConfig, DiscoveryError, DiscoverySearchRequest, DiscoveryService,
)

# Once per worker/app lifecycle, NOT once per request:
discovery = DiscoveryService(DiscoveryConfig.from_env())

# In an authenticated async route, using the caller's bearer/RLS repository:
result = await discovery.search(
    user_id=repo.user_id,
    preferences=await repo.one("job_preferences", required=False),
    request=body,  # DiscoverySearchRequest, or an equivalent bounded dict
)
```

The preference row must contain the same `user_id` as the verified principal;
`None` is allowed for a user without preferences. Never load a service-role
preference row, accept client-owned preferences, or substitute founder settings.
Ownership mismatch returns a safe `DiscoveryError` (403) before any feed request.
Map `DiscoveryError.status_code`, `.code`, safe `str(error)`, and nonzero
`.retry_after` to the route's existing safe error envelope/Retry-After header.
Configuration/unconfigured errors are 503, invalid identity 401, bounded-input
errors 422, limits 429, and active-capacity exhaustion 503.

The JSON result has:

- `status`: `ok`, `partial`, or `unavailable`. Return 200 for ok/partial and 503
  with this safe result envelope for unavailable (all configured sources failed).
- `results`: plain-text jobs with stable `source_id`, `source`, `provider`,
  `board`, `external_id`, canonical hosted `source_url`, `title`, `company_name`,
  `location_text`, `country`, `department`, `employment_type`, `workplace_type`,
  `description`, `content_truncated`, and source/fetch timestamps.
- `sources`: one entry per configured board, including status, checked/fetched
  times, cache indicator, received/returned/dropped/unlisted/duplicate counts,
  truncation, safe error code, and retry delay where applicable.
- `partial`, `truncated`, `matched_count`, `returned_count`, `searched_at`.
  `matched_count` means provisional filter results within the bounded fetched sample, not
  all jobs available anywhere and not an AI fit score.
- `warnings`: safe plain-text coverage/geography warnings. **Render these even
  when results are empty.** Unsupported saved regions or unconfirmed posting
  geography produce `partial` rather than silently claiming a complete search.
- `persisted: false`, `eligibility_verified: false`. Every result also has
  `eligibility_status: unknown`, provisional review reasons, and match reasons.

An empty successful search is distinct from all feeds failing. A single board's
malformed rows are counted and skipped while valid rows survive. Caps and dropped
records produce partial status; failures are never silently converted to success.
No exception body, upstream header/body dump, query, preference record or user ID
is returned as diagnostics. No job or preference is written here.

### Explicit save remains separate

After the user reviews and explicitly chooses a result, the existing authenticated
job-save endpoint may receive only `source_url`, `title`, `company_name`,
`description`, and `location_text`. Do not POST during search or automatically
advance to matched/ready. `source_id` is a public discovery identity, not a saved
job UUID or tenant ownership key. Reuse the existing per-user save deduplication,
validation and eligibility-review flow. Render text as text, not trusted HTML.
`company_name_is_board_identifier: true` warns that the board slug is the label;
no branded employer name was inferred from founder display-name mappings.

## Server configuration and resource bounds

The only discovery environment variable is `MOBILE_DISCOVERY_BOARDS`, a JSON
provider-to-board-list allowlist. Example shape (synthetic identifiers; not a
claim that these are configured or live employers):

```text
MOBILE_DISCOVERY_BOARDS={"greenhouse":["clinic-board"],"lever":["school-board"],"ashby":["factory-board"]}
```

At most 8 boards total; only `greenhouse`, `lever`, and `ashby` are supported.
Board identifiers are ASCII letters/digits followed by letters/digits/underscore/
hyphen, maximum 80 characters. Case is preserved. Full URLs, domains, paths,
query strings, fragments, Unicode lookalikes, unknown providers and duplicates
are rejected. No default boards, dotenv, founder YAML, secrets, or fallback.
An empty allowlist deliberately disables discovery. Configuration changes require
rebuilding the per-worker service/restarting the app.

| Boundary | Limit/behavior |
|---|---|
| Feed origins | Fixed HTTPS `boards-api.greenhouse.io`, `api.lever.co`, `api.ashbyhq.com` only |
| Methods and request data | GET only; board identifier and fixed public parameters; no candidate data |
| Redirects/proxy/auth/cookies | Redirects blocked, `trust_env=False`, fresh uncredentialed client per GET; no cookie replay |
| TLS | Normal certificate verification; no insecure fallback |
| Network time | 2s connect, 3s read/write, 1s pool; 6s fetch-coroutine deadline; 20s aggregate wait deadline |
| Concurrency | At most 3 feed fetches and 8 active searches per service |
| Rate | 6 searches/user/minute, 60 searches/service/minute, 20 feed fetches/service/minute |
| Limiter memory | 2,048 hashed tenant buckets; full table denies new buckets rather than evicting active quotas |
| Feed size | 2 MiB streamed bytes; declared oversized length rejected before consumption |
| Compression/content | Requests identity encoding; compressed or non-JSON responses rejected, no decompression bombs |
| JSON | Duplicate keys, nonfinite numbers, invalid UTF-8, malformed envelopes rejected |
| Records | At most 300 normalized records/board; Lever requests 301 to detect truncation, no unbounded pagination |
| Result size | 1–50 results; 16,000 description characters; aggregate JSON budget below 1 MiB (UTF-8 JSON) |
| Caches | Public board data only: success 300s; failures 60s or bounded Retry-After (up to 3,600s) |
| Retries | None within a search; negative cache prevents immediate retry storms |

These are application limits, not assertions about the ATS vendors' own quotas.
Expired public success is not served as fresh data when refresh fails. Concurrent
tenants share at most one fetch per board, but filtering and eligibility annotations
are rebuilt in fresh containers for each request. No candidate results, preferences,
tokens, or saved data enter the shared public cache.

For multi-worker/replica deployment, **add a shared gateway/rate limit or operate
one worker** until a shared limiter is provided: local counters/caches reset on
restart and do not coordinate across processes. Restrict deployment egress to the
three ATS origins and retain platform connection/request limits. Authentication,
access revocation, browser CORS and request-body enforcement belong to the app
integration, not this library. No new DB migration or dependency is needed here.

## Matching and eligibility semantics

Filtering uses only stored `target_titles`, `preferred_locations`,
`preferred_regions`, `remote_preference` and `sponsorship_required`. Other columns,
including free-form work-authorization notes and minimum AI score, are ignored.
No resume, credential list, biography, embedding or model call is accepted/used.

Title terms are matched against titles; locations/regions against source location
and explicit geographic mappings; query words against title/department/description. Tokens are Unicode
normalized and case-insensitive, OR within each list, AND between stored and
request filter groups. Requests can narrow but cannot erase stored constraints.
Remote-only requires a remote workplace signal; unknown workplace is not silently
remote. Results sort by title and stable ID, not model fit or professional prestige.

Known region/country mapping is deliberately finite: `Europe` uses UN M49 Europe,
`EU`/`European Union` uses EU membership, and listed country names/codes and common
aliases (including Germany/DE/DEU, US/USA/U.S., UAE/AE) map explicitly. Europe
includes Germany and the UK, while EU excludes the UK; this is geography, never
inferred residence or work authorization. Dubai, Abu Dhabi and Sharjah are known
UAE locations. A Dubai-specific filter still requires Dubai and is not broadened
to every UAE city. Unmapped regions use literal text only and emit a warning;
this is not a worldwide geocoder or a complete country/region vocabulary.

Known disjoint countries are excluded even for remote jobs or generic "anywhere"
language. Recognized explicit clauses such as "Must reside in US" and "Remote
US only" take precedence over headquarters metadata. Remote/hybrid jobs with
unconfirmed geography may surface only with explicit provisional-review reasons
and a top-level warning; onsite jobs do not get this fallback. A user's Worldwide
preference expresses openness, not worldwide eligibility of any posting. This
bounded lexical parser does not cover every restriction, negation or contradiction.

This intentionally supports profession-neutral discovery (nursing,
teaching, manufacturing, engineering, etc.), not profession synonym expansion,
licence equivalence or credential verification. A user changing
profession/location should update stored preferences first. Broad missing
preferences allow browsing the configured board sample. Coverage depends entirely
on the operator's board selection; these APIs are not global search engines.

All results remain eligibility-unknown. Sponsorship restrictions conflicting with
a stated sponsorship need, detected residence/work-right conditions, qualifications,
remote-country ambiguity, and truncation are explained provisionally. Missing
sponsorship text never becomes proof of eligibility or automatic exclusion.
Lexical warnings do not comprehensively parse legal requirements. Model/prompt-
injection resistance is not claimed: hostile descriptions remain untrusted text,
with scripts/markup removed, and are never executed or sent to a model here.

Stable identity hashes provider + exact board + provider posting ID; Greenhouse
numeric IDs are normalized. Ashby IDs may be derived from a validated same-board
hosted URL. IDs are unaffected by tracking query parameters. Same-identity
duplicates prefer the newer source update, otherwise the first record; distinct
boards/IDs are not merged by employer/title. There is no historical DB merge.

## Official source documentation verified

Reviewed 2026-09-15 via the primary sources below; no application endpoints used.

- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html): public
  GET listings at `/v1/boards/{board_token}/jobs`, with `content=true` for full
  descriptions. The posting `id` is the public identity. `updated_at` is retained
  as `source_updated_at`, never relabelled as publication time.
- [Lever Postings API](https://github.com/lever/postings-api): JSON listings at
  `/v0/postings/{site}`, using `mode=json`, `skip`, and `limit`. Description,
  requirement lists and additional content are preserved. This implementation
  supports the global instance only, not Lever EU. Optional `createdAt`, if supplied,
  is retained separately; absent publication timestamps stay null.
- [Ashby Public Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api):
  `/posting-api/job-board/{job_board_name}` supplies hosted URLs, workplace data
  and `publishedAt` (last publication). Only explicit `isListed=true` is admitted;
  false is intentionally unlisted, and missing/malformed markers are dropped.
  Compensation and apply endpoints are not requested.
- [UN M49 geographic regions](https://unstats.un.org/unsd/methodology/m49/):
  reference for the explicit Europe country/area grouping (not work-right rules).
- [European Commission EU country list](https://employment-social-affairs.ec.europa.eu/policies-and-activities/moving-working-europe/eu-social-security-coordination/frequently-asked-questions/faq-social-security-where-do-these-rules-apply_en):
  reference for the separate EU-27 grouping; no legal eligibility inference.

`fetched_at` is retrieval time only. Cached results preserve their original fetch
time. A public listing can close between discovery and explicit save/application;
no application availability guarantee is made.

## Runtime evidence and remaining verification

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -p 'test_mobile_discovery*.py' -v
```

Latest focused execution: **89 tests passed (63 module + 26 API), 2.222s,
zero skips or expected failures**. The suites exercise the real module and app
route with all external I/O mocked.
These execute the real normalization/filter/cache/limiter/HTTP streaming code using
`httpx.MockTransport`, synthetic postings and blocked sockets. Coverage includes
three ATS formats, multiple professions/tenants, preference ownership, no candidate
egress, independent result containers, unsafe URLs/config, redirects, cookie/proxy
isolation, source timestamps, stable deduplication, dropped/unlisted rows, malformed
JSON, oversized/encoded feeds, byte/record limits, partial/all failures, deadlines,
cancellation, concurrency, caching, request/feed rate limits, 240-character stored
preference compatibility versus 160-character request filters, Europe/Germany/US,
EU/UK, UAE/city aliases, unsupported regions and remote-country uncertainty.

The API tests cover verified/private/member gates, per-owner preferences,
read-only database/storage snapshots, no model calls, injected owner/URL fields,
partial-200 versus unavailable-503, exact/chunked/encoded body caps, request quotas
and same-loop lazy singleton initialization. Their fixture wraps and enters the
app's real lifespan before installing the fake-transport service, preserving the
privacy logging hook (startup handlers alone are suppressed by custom lifespans).

This is not a deployed-route, live-feed, PostgreSQL/RLS or production-load test.
The frontend must display `warnings` and provisional eligibility; UI verification
belongs to main. No live public-board smoke is claimed. No paid/model or
application call, founder database/config/resume read, or real-user mutation was
performed for this module.

Discovery needs no migration. These focused suites do not claim SQL runtime proof;
the main task owns merged real-PostgreSQL results, including migration 0010 and
privacy snapshot/auth/storage controls.
