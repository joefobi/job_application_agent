"""Greenhouse job board ingestion."""

from __future__ import annotations

from typing import Any

from job_application_agent.models import JobPosting, JobSource
from job_application_agent.normalization import canonicalize_url
from job_application_agent.sources.http import fetch_json


class GreenhouseIngestor:
    """Fetch and normalize jobs from the Greenhouse board API."""

    def __init__(self, board_token: str, company_name: str | None = None) -> None:
        """Create a Greenhouse ingestor.

        Args:
            board_token: Greenhouse board token from the board URL.
            company_name: Optional company override when API metadata is sparse.
        """

        self.board_token = board_token
        self.company_name = company_name or board_token

    @property
    def jobs_url(self) -> str:
        """Return the Greenhouse jobs API URL."""

        return (
            "https://boards-api.greenhouse.io/v1/boards/"
            f"{self.board_token}/jobs?content=true"
        )

    def fetch_jobs(self) -> list[JobPosting]:
        """Fetch and normalize all jobs from the configured board."""

        payload = fetch_json(self.jobs_url)
        if not isinstance(payload, dict):
            raise ValueError("Greenhouse response must be a JSON object.")
        return self.parse_jobs(payload)

    def parse_jobs(self, payload: dict[str, Any]) -> list[JobPosting]:
        """Parse a Greenhouse jobs API response.

        Args:
            payload: Decoded Greenhouse API response.

        Returns:
            Normalized job postings.
        """

        jobs = payload.get("jobs", [])
        if not isinstance(jobs, list):
            raise ValueError("Greenhouse payload field 'jobs' must be a list.")
        return [self._parse_job(job) for job in jobs if isinstance(job, dict)]

    def _parse_job(self, job: dict[str, Any]) -> JobPosting:
        """Parse one Greenhouse job object."""

        source_job_id = str(job["id"])
        application_url = str(job.get("absolute_url") or job.get("url") or "")
        if not application_url:
            application_url = (
                f"https://boards.greenhouse.io/{self.board_token}/jobs/{source_job_id}"
            )
        location = _greenhouse_location(job.get("location"))
        department = _greenhouse_department(job.get("departments"))
        return JobPosting(
            source=JobSource.GREENHOUSE,
            source_job_id=source_job_id,
            title=str(job["title"]),
            company=self.company_name,
            location=location,
            department=department,
            application_url=application_url,
            canonical_url=canonicalize_url(application_url),
            content=_optional_str(job.get("content")),
            raw_data=job,
        )


def _greenhouse_location(value: object) -> str | None:
    """Extract a Greenhouse location name."""

    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if name else None
    return None


def _greenhouse_department(value: object) -> str | None:
    """Extract the first Greenhouse department name."""

    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if not isinstance(first, dict):
        return None
    name = first.get("name")
    return str(name) if name else None


def _optional_str(value: object) -> str | None:
    """Return a string or None for empty values."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None
