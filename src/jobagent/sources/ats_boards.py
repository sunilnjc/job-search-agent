from __future__ import annotations

import httpx

from jobagent.config import settings
from jobagent.models import JobPosting
from jobagent.sources.base import JobSource
from jobagent.sources.text_clean import clean_html

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{board}"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"

# Board tokens are URL slugs, not names. Without this the company reads as "n26" or
# "remotecom" everywhere — including in generated cover letters.
BOARD_DISPLAY_NAMES = {
    "n26": "N26",
    "gitlab": "GitLab",
    "sumup": "SumUp",
    "remotecom": "Remote.com",
    "adyen": "Adyen",
    "stripe": "Stripe",
    "anthropic": "Anthropic",
    "datadog": "Datadog",
    "elastic": "Elastic",
    "canonical": "Canonical",
    "celonis": "Celonis",
    "ripple": "Ripple",
    "monzo": "Monzo",
    "speechify": "Speechify",
    "openai": "OpenAI",
    "figma": "Figma",
    "cloudflare": "Cloudflare",
    "databricks": "Databricks",
}


def display_name(board: str) -> str:
    return BOARD_DISPLAY_NAMES.get(board.lower(), board.replace("-", " ").title())


class ATSBoardsSource(JobSource):
    name = "ats_boards"

    def search(self) -> list[JobPosting]:
        prefs = settings.load_preferences()
        boards = prefs.get("ats_boards", {}) or {}

        # Kept for reconciliation: only a board that responded successfully may mark
        # missing previous listings expired. A temporary network error must never hide
        # an entire employer's board.
        self.successful_sources: set[str] = set()
        postings: list[JobPosting] = []
        for token in boards.get("greenhouse", []) or []:
            result = self._search_greenhouse(token)
            if result is not None:
                self.successful_sources.add(f"greenhouse:{token}")
                postings.extend(result)
        for token in boards.get("lever", []) or []:
            result = self._search_lever(token)
            if result is not None:
                self.successful_sources.add(f"lever:{token}")
                postings.extend(result)
        for token in boards.get("ashby", []) or []:
            result = self._search_ashby(token)
            if result is not None:
                self.successful_sources.add(f"ashby:{token}")
                postings.extend(result)
        return postings

    def _search_ashby(self, board: str) -> list[JobPosting] | None:
        """Read Ashby's public job-board feed, not the rendered careers page.

        The feed only contains currently listed jobs, making it a much more reliable
        source than an aggregator's cached application URL.
        """
        resp = httpx.get(
            ASHBY_URL.format(board=board),
            params={"includeCompensation": "true"},
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        postings = []
        for job in resp.json().get("jobs", []):
            location = job.get("location", "") or ""
            address = job.get("address") or {}
            compensation = job.get("compensation")
            postings.append(
                JobPosting(
                    source=f"ashby:{board}",
                    external_id=str(job.get("id", job.get("jobUrl", ""))),
                    title=job.get("title", ""),
                    company=display_name(board),
                    location=location,
                    remote="remote" in location.lower(),
                    url=job.get("jobUrl", ""),
                    description=clean_html(job.get("descriptionHtml", "") or job.get("descriptionPlain", "")),
                    salary=str(compensation) if compensation else None,
                    posted_at=job.get("publishedAt"),
                    country=address.get("addressCountry"),
                )
            )
        return [posting for posting in postings if posting.url and posting.title]

    def _search_greenhouse(self, board: str) -> list[JobPosting] | None:
        resp = httpx.get(
            GREENHOUSE_URL.format(board=board), params={"content": "true"}, timeout=20
        )
        if resp.status_code != 200:
            return None
        data = resp.json()

        postings = []
        for job in data.get("jobs", []):
            postings.append(
                JobPosting(
                    source=f"greenhouse:{board}",
                    external_id=str(job.get("id")),
                    title=job.get("title", ""),
                    company=display_name(board),
                    location=(job.get("location") or {}).get("name", ""),
                    remote="remote" in (job.get("location") or {}).get("name", "").lower(),
                    url=job.get("absolute_url", ""),
                    description=clean_html(job.get("content", "")),
                    posted_at=job.get("updated_at"),
                )
            )
        return postings

    def _search_lever(self, board: str) -> list[JobPosting] | None:
        resp = httpx.get(LEVER_URL.format(board=board), params={"mode": "json"}, timeout=20)
        if resp.status_code != 200:
            return None
        data = resp.json()

        postings = []
        for job in data:
            location = (job.get("categories") or {}).get("location", "")
            postings.append(
                JobPosting(
                    source=f"lever:{board}",
                    external_id=str(job.get("id")),
                    title=job.get("text", ""),
                    company=display_name(board),
                    location=location,
                    remote="remote" in location.lower(),
                    url=job.get("hostedUrl", ""),
                    description=job.get("descriptionPlain", "") or job.get("description", ""),
                    posted_at=str(job.get("createdAt", "")),
                )
            )
        return postings
