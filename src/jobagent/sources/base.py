from __future__ import annotations

from abc import ABC, abstractmethod

from jobagent.models import JobPosting


class JobSource(ABC):
    name: str = "base"

    @abstractmethod
    def search(self) -> list[JobPosting]:
        """Bulk-fetch postings from this source."""

    def fetch_by_url(self, url: str) -> JobPosting:
        """Fetch a single posting by URL. Override in sources that support it."""
        raise NotImplementedError(f"{self.name} does not support fetch_by_url")
