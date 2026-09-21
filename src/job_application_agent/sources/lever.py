"""Lever job board ingestion."""

from __future__ import annotations

from typing import Any

from job_application_agent.models import JobPosting, JobSource
from job_application_agent.normalization import canonicalize_url
from job_application_agent.sources.http import fetch_json


class LeverIngestor:
    """Fetch and normalize jobs from the Lever postings API."""

    def __init__(self, site: str, company_name: str | None = None) -> None:
        """Create a Lever ingestor.

        Args:
            site: Lever site slug from postings URLs.
            company_name: Optional company override when API metadata is sparse.
        """

        self.site = site
        self.company_name = company_name or site

    @property
    def jobs_url(self) -> str:
        """Return the Lever postings API URL."""

        return f"https://api.lever.co/v0/postings/{self.site}?mode=json"

    def fetch_jobs(self) -> list[JobPosting]:
        """Fetch and normalize all jobs from the configured Lever site."""

        payload = fetch_json(self.jobs_url)
        if not isinstance(payload, list):
            raise ValueError("Lever response must be a JSON list.")
        return self.parse_jobs(payload)

    def parse_jobs(self, payload: list[Any]) -> list[JobPosting]:
        """Parse a Lever postings API response.

        Args:
            payload: Decoded Lever API response.

        Returns:
            Normalized job postings.
        """

        return [self._parse_job(job) for job in payload if isinstance(job, dict)]

    def _parse_job(self, job: dict[str, Any]) -> JobPosting:
        """Parse one Lever posting object."""

        source_job_id = str(job["id"])
        application_url = str(job.get("hostedUrl") or job.get("applyUrl") or "")
        if not application_url:
            application_url = f"https://jobs.lever.co/{self.site}/{source_job_id}"
        categories = job.get("categories")
        location = None
        department = None
        employment_type = None
        if isinstance(categories, dict):
            location = _optional_str(categories.get("location"))
            department = _optional_str(categories.get("department"))
            employment_type = _optional_str(categories.get("commitment"))
        return JobPosting(
            source=JobSource.LEVER,
            source_job_id=source_job_id,
            title=str(job["text"]),
            company=self.company_name,
            location=location,
            department=department,
            employment_type=employment_type,
            application_url=application_url,
            canonical_url=canonicalize_url(application_url),
            content=_lever_content(job),
            raw_data=job,
        )


def _lever_content(job: dict[str, Any]) -> str | None:
    """Extract useful text content from a Lever posting."""

    parts: list[str] = []
    description = _optional_str(job.get("descriptionPlain"))
    if description:
        parts.append(description)
    lists = job.get("lists")
    if isinstance(lists, list):
        for section in lists:
            if not isinstance(section, dict):
                continue
            heading = _optional_str(section.get("text"))
            content = _optional_str(section.get("content"))
            if heading and content:
                parts.append(f"{heading}\n{content}")
            elif content:
                parts.append(content)
    return "\n\n".join(parts) or None


def _optional_str(value: object) -> str | None:
    """Return a string or None for empty values."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None
