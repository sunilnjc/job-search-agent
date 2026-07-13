from __future__ import annotations

import httpx

from jobagent.config import settings
from jobagent.models import JobPosting
from jobagent.sources.base import JobSource

BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


class AdzunaSource(JobSource):
    name = "adzuna"

    def search(self) -> list[JobPosting]:
        if not settings.adzuna_app_id or not settings.adzuna_app_key:
            return []

        prefs = settings.load_preferences()
        countries = prefs.get("countries", ["us"])
        titles = prefs.get("target_titles", []) or [None]

        # One query per (country, title): Adzuna's `what` requires ALL words,
        # so joining several titles into one query matches nothing.
        postings = []
        for country in countries:
            for title in titles:
                postings.extend(self._search_country(country, title))
        return postings

    def _search_country(self, country: str, what: str | None) -> list[JobPosting]:
        country_code = country.upper()
        params = {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "results_per_page": 50,
            "content-type": "application/json",
        }
        if what:
            params["what"] = what

        resp = httpx.get(BASE_URL.format(country=country), params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        postings = []
        for item in data.get("results", []):
            postings.append(
                JobPosting(
                    source=self.name,
                    external_id=str(item.get("id")),
                    title=item.get("title", ""),
                    company=(item.get("company") or {}).get("display_name", ""),
                    location=(item.get("location") or {}).get("display_name", ""),
                    remote="remote" in (item.get("title", "") + item.get("description", "")).lower(),
                    country=country_code,
                    url=item.get("redirect_url", ""),
                    description=item.get("description", ""),
                    salary=_format_salary(item),
                    posted_at=item.get("created"),
                )
            )
        return postings


def _format_salary(item: dict) -> str | None:
    lo, hi = item.get("salary_min"), item.get("salary_max")
    if lo and hi:
        return f"${lo:,.0f} - ${hi:,.0f}"
    return None
