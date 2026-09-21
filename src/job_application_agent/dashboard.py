"""Service contracts for the future review dashboard."""

from __future__ import annotations

from dataclasses import dataclass

from job_application_agent.models import JobStatus
from job_application_agent.storage import ApplicationStore


@dataclass(frozen=True)
class DashboardJobRow:
    """Compact job data for a dashboard list view."""

    job_id: int
    title: str
    company: str
    location: str | None
    source: str
    status: JobStatus


@dataclass(frozen=True)
class DashboardJobDetail:
    """Detailed job data for a dashboard detail view."""

    job_id: int
    title: str
    company: str
    location: str | None
    source: str
    status: JobStatus
    application_url: str
    content: str | None


class DashboardService:
    """Small service layer for review-dashboard queries and actions."""

    def __init__(self, store: ApplicationStore) -> None:
        """Create a dashboard service.

        Args:
            store: Initialized application store.
        """

        self.store = store

    def list_jobs(self, status: JobStatus | None = None) -> list[DashboardJobRow]:
        """Return jobs for a dashboard list.

        Args:
            status: Optional status filter.

        Returns:
            Compact job rows sorted by newest first.
        """

        rows = self.store.list_jobs(status=status)
        return [
            DashboardJobRow(
                job_id=int(row["id"]),
                title=str(row["title"]),
                company=str(row["company"]),
                location=_optional_str(row["location"]),
                source=str(row["source"]),
                status=JobStatus(str(row["status"])),
            )
            for row in rows
        ]

    def get_job_detail(self, job_id: int) -> DashboardJobDetail | None:
        """Return one job for a dashboard detail view."""

        row = self.store.get_job(job_id)
        if row is None:
            return None
        return DashboardJobDetail(
            job_id=int(row["id"]),
            title=str(row["title"]),
            company=str(row["company"]),
            location=_optional_str(row["location"]),
            source=str(row["source"]),
            status=JobStatus(str(row["status"])),
            application_url=str(row["application_url"]),
            content=_optional_str(row["content"]),
        )

    def update_status(self, job_id: int, status: JobStatus) -> None:
        """Update a job status from a dashboard action."""

        self.store.update_job_status(job_id, status)
        self.store.record_event(
            "dashboard_status_updated",
            {"status": status.value},
            job_id,
        )


def _optional_str(value: object) -> str | None:
    """Return a string or None for null dashboard row values."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None
