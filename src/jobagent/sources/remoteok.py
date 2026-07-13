from __future__ import annotations

import httpx

from jobagent.models import JobPosting
from jobagent.sources.base import JobSource
from jobagent.sources.text_clean import clean_html

API_URL = "https://remoteok.com/api"


class RemoteOKSource(JobSource):
    name = "remoteok"

    def search(self) -> list[JobPosting]:
        resp = httpx.get(
            API_URL,
            headers={"User-Agent": "jobagent/0.1 (personal job search tool)"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        postings = []
        for item in data:
            # The first item in RemoteOK's response is a legal/metadata blob, not a job.
            if "id" not in item or "position" not in item:
                continue
            postings.append(
                JobPosting(
                    source=self.name,
                    external_id=str(item["id"]),
                    title=item.get("position", ""),
                    company=item.get("company", ""),
                    location=item.get("location") or "Remote",
                    remote=True,
                    url=item.get("url", f"https://remoteok.com/remote-jobs/{item['id']}"),
                    description=clean_html(item.get("description", "")),
                    salary=_format_salary(item),
                    posted_at=item.get("date"),
                )
            )
        return postings


def _format_salary(item: dict) -> str | None:
    lo, hi = item.get("salary_min"), item.get("salary_max")
    if lo and hi:
        return f"${lo:,} - ${hi:,}"
    return None
