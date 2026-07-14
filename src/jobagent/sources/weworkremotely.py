from __future__ import annotations

import feedparser
import httpx

from jobagent.models import JobPosting
from jobagent.sources.base import JobSource
from jobagent.sources.text_clean import clean_html

# WeWorkRemotely has no public API; these per-category RSS feeds are the public
# access method (a WWR subscription is for email digests/site features, not data).
# We pull every engineering-relevant category, not just "programming", to widen
# coverage — the DB dedups by URL, so overlap between feeds is harmless.
FEED_SLUGS = [
    "remote-programming-jobs",
    "remote-full-stack-programming-jobs",
    "remote-back-end-programming-jobs",
    "remote-front-end-programming-jobs",
    "remote-devops-sysadmin-jobs",
    "remote-product-jobs",
]
FEED_URL = "https://weworkremotely.com/categories/{slug}.rss"


class WeWorkRemotelySource(JobSource):
    name = "weworkremotely"

    def search(self) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_urls: set[str] = set()
        for slug in FEED_SLUGS:
            for posting in self._fetch_feed(slug):
                if posting.url and posting.url not in seen_urls:
                    seen_urls.add(posting.url)
                    postings.append(posting)
        return postings

    def _fetch_feed(self, slug: str) -> list[JobPosting]:
        # Fetch with httpx (bundled certifi CA certs) — feedparser's own urllib
        # fetch fails SSL verification on python.org macOS builds. One category
        # feed failing shouldn't lose the others.
        try:
            resp = httpx.get(FEED_URL.format(slug=slug), timeout=20, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError:
            return []

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
