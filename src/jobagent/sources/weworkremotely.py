from __future__ import annotations

import feedparser
import httpx

from jobagent.models import JobPosting
from jobagent.sources.base import JobSource
from jobagent.sources.text_clean import clean_html

FEED_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


class WeWorkRemotelySource(JobSource):
    name = "weworkremotely"

    def search(self) -> list[JobPosting]:
        # Fetch with httpx (bundled certifi CA certs) — feedparser's own urllib
        # fetch fails SSL verification on python.org macOS builds.
        resp = httpx.get(FEED_URL, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        postings = []
        for entry in feed.entries:
            title = entry.get("title", "")
            company, _, role = title.partition(": ")
            postings.append(
                JobPosting(
                    source=self.name,
                    external_id=entry.get("id", entry.get("link", "")),
                    title=role or title,
                    company=company if role else "",
                    location="Remote",
                    remote=True,
                    url=entry.get("link", ""),
                    description=clean_html(entry.get("summary", "")),
                    posted_at=entry.get("published"),
                )
            )
        return postings
