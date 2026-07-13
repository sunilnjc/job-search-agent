from __future__ import annotations

import httpx

from jobagent.config import settings
from jobagent.models import JobPosting
from jobagent.sources.base import JobSource
from jobagent.sources.text_clean import clean_html

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{board}"


class ATSBoardsSource(JobSource):
    name = "ats_boards"

    def search(self) -> list[JobPosting]:
        prefs = settings.load_preferences()
        boards = prefs.get("ats_boards", {}) or {}

        postings: list[JobPosting] = []
        for token in boards.get("greenhouse", []) or []:
            postings.extend(self._search_greenhouse(token))
        for token in boards.get("lever", []) or []:
            postings.extend(self._search_lever(token))
        return postings

    def _search_greenhouse(self, board: str) -> list[JobPosting]:
        resp = httpx.get(
            GREENHOUSE_URL.format(board=board), params={"content": "true"}, timeout=20
        )
        if resp.status_code != 200:
            return []
        data = resp.json()

        postings = []
        for job in data.get("jobs", []):
            postings.append(
                JobPosting(
                    source=f"greenhouse:{board}",
                    external_id=str(job.get("id")),
                    title=job.get("title", ""),
                    company=board,
                    location=(job.get("location") or {}).get("name", ""),
                    remote="remote" in (job.get("location") or {}).get("name", "").lower(),
                    url=job.get("absolute_url", ""),
                    description=clean_html(job.get("content", "")),
                    posted_at=job.get("updated_at"),
                )
            )
        return postings

    def _search_lever(self, board: str) -> list[JobPosting]:
        resp = httpx.get(LEVER_URL.format(board=board), params={"mode": "json"}, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json()

        postings = []
        for job in data:
            location = (job.get("categories") or {}).get("location", "")
            postings.append(
                JobPosting(
                    source=f"lever:{board}",
                    external_id=str(job.get("id")),
                    title=job.get("text", ""),
                    company=board,
                    location=location,
                    remote="remote" in location.lower(),
                    url=job.get("hostedUrl", ""),
                    description=job.get("descriptionPlain", "") or job.get("description", ""),
                    posted_at=str(job.get("createdAt", "")),
                )
            )
        return postings
