from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from jobagent.models import JobPosting
from jobagent.sources.base import JobSource


class ManualURLSource(JobSource):
    """Fetches a single job posting page pasted in by hand.

    Used for sites like LinkedIn/Indeed whose Terms of Service prohibit bulk
    scraping — the user finds the posting themselves and hands us the URL to
    parse, one at a time.
    """

    name = "manual_url"

    def search(self) -> list[JobPosting]:
        return []

    def fetch_by_url(self, url: str) -> JobPosting:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; jobagent/0.1)"},
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title = _meta(soup, "og:title") or (soup.title.string if soup.title else "") or ""
        description = _meta(soup, "og:description") or _body_text(soup)
        site_name = _meta(soup, "og:site_name") or ""

        return JobPosting(
            source=f"manual:{site_name or _domain(url)}",
            external_id=url,
            title=title.strip(),
            company=site_name.strip(),
            location="Unknown",
            remote="remote" in description.lower(),
            url=url,
            description=description.strip(),
        )


def _meta(soup: BeautifulSoup, property_name: str) -> str:
    tag = soup.find("meta", property=property_name) or soup.find("meta", attrs={"name": property_name})
    return tag.get("content", "") if tag else ""


def _body_text(soup: BeautifulSoup) -> str:
    body = soup.find("body")
    if not body:
        return ""
    return " ".join(body.get_text(separator=" ", strip=True).split())[:5000]


def _domain(url: str) -> str:
    return url.split("//")[-1].split("/")[0]
