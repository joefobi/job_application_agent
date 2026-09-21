"""Service contracts for the future review dashboard."""

from __future__ import annotations

from dataclasses import dataclass

from job_application_agent.models import FollowUpStatus, JobStatus
from job_application_agent.storage import ApplicationStore

DASHBOARD_EDITABLE_STATUSES = {
    JobStatus.REJECTED,
    JobStatus.SAVED,
    JobStatus.NEEDS_REVIEW,
    JobStatus.APPROVED_TO_APPLY,
    JobStatus.WITHDRAWN,
    JobStatus.CLOSED,
}


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


@dataclass(frozen=True)
class DashboardFollowUpRow:
    """Compact follow-up reminder data for the dashboard."""

    reminder_id: int
    job_id: int
    due_at: str
    status: FollowUpStatus
    kind: str
    message: str | None


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

        current = self.store.get_job(job_id)
        if current is None:
            raise ValueError(f"Unknown job ID: {job_id}")
        current_status = JobStatus(str(current["status"]))
        if status == JobStatus.APPLIED:
            raise ValueError("Use mark_applied to record submitted applications.")
        if current_status == JobStatus.APPLIED:
            raise ValueError("Dashboard actions cannot change an already applied job.")
        if status not in DASHBOARD_EDITABLE_STATUSES:
            raise ValueError(f"Dashboard cannot set job status to {status.value}.")

        self.store.update_job_status_with_event(
            job_id,
            status,
            "dashboard_status_updated",
            {"status": status.value},
            expected_current_status=current_status,
        )

    def list_follow_ups(
        self,
        *,
        status: FollowUpStatus | None = FollowUpStatus.PENDING,
        due_at_or_before: str | None = None,
    ) -> list[DashboardFollowUpRow]:
        """Return follow-up reminders for a dashboard list.

        Args:
            status: Optional reminder status filter. Pass None for all statuses.
            due_at_or_before: Optional ISO timestamp upper bound.

        Returns:
            Compact follow-up rows sorted by due date.
        """

        reminders = self.store.list_follow_ups(
            status=status,
            due_at_or_before=due_at_or_before,
        )
        return [
            DashboardFollowUpRow(
                reminder_id=reminder.reminder_id,
                job_id=reminder.job_id,
                due_at=reminder.due_at,
                status=reminder.status,
                kind=reminder.kind,
                message=reminder.message,
            )
            for reminder in reminders
        ]


def _optional_str(value: object) -> str | None:
    """Return a string or None for null dashboard row values."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None
