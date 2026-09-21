"""Submission confirmation and follow-up tracking services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from job_application_agent.models import (
    FollowUpReminder,
    FollowUpStatus,
    SubmissionConfirmation,
)
from job_application_agent.storage import ApplicationStore


@dataclass(frozen=True)
class SubmissionConfirmationRequest:
    """Data captured after a real application submission."""

    job_id: int
    applied_at: str | None = None
    resume_version: str | None = None
    cover_letter_version: str | None = None
    confirmation_number: str | None = None
    confirmation_url: str | None = None
    provider: str | None = None
    notes: str | None = None


class SubmissionConfirmationRecorder:
    """Persist application submission confirmations."""

    def __init__(self, store: ApplicationStore) -> None:
        """Create a recorder.

        Args:
            store: Initialized application store.
        """

        self.store = store

    def record(self, request: SubmissionConfirmationRequest) -> SubmissionConfirmation:
        """Record a submitted application and mark its job applied.

        Args:
            request: Submission details captured from the ATS or user.

        Returns:
            Persisted submission confirmation.
        """

        return self.store.record_submission_confirmation(
            request.job_id,
            applied_at=request.applied_at,
            resume_version=request.resume_version,
            cover_letter_version=request.cover_letter_version,
            confirmation_number=request.confirmation_number,
            confirmation_url=request.confirmation_url,
            provider=request.provider,
            notes=request.notes,
        )


class FollowUpTracker:
    """Schedule and manage application follow-up reminders."""

    def __init__(self, store: ApplicationStore) -> None:
        """Create a follow-up tracker.

        Args:
            store: Initialized application store.
        """

        self.store = store

    def schedule(
        self,
        *,
        job_id: int,
        due_at: str,
        kind: str = "post_application",
        message: str | None = None,
    ) -> FollowUpReminder:
        """Schedule a follow-up reminder.

        Args:
            job_id: Database ID for the job.
            due_at: ISO timestamp when the reminder is due.
            kind: Machine-readable reminder kind.
            message: Optional reminder text.

        Returns:
            Persisted follow-up reminder.
        """

        return self.store.create_follow_up(
            job_id,
            due_at=due_at,
            kind=kind,
            message=message,
        )

    def schedule_after_submission(
        self,
        confirmation: SubmissionConfirmation,
        *,
        days_after_submission: int = 7,
        message: str | None = None,
    ) -> FollowUpReminder:
        """Schedule a default follow-up relative to submission time.

        Args:
            confirmation: Submission confirmation to follow up on.
            days_after_submission: Number of days after submission.
            message: Optional reminder text.

        Returns:
            Persisted follow-up reminder.
        """

        submitted_at = _parse_iso_datetime(confirmation.applied_at)
        due_at = (submitted_at + timedelta(days=days_after_submission)).isoformat(
            timespec="seconds"
        )
        return self.schedule(
            job_id=confirmation.job_id,
            due_at=due_at,
            message=message or "Follow up on submitted application.",
        )

    def list_due(self, *, now: str | None = None) -> list[FollowUpReminder]:
        """Return pending follow-ups due at or before now.

        Args:
            now: ISO timestamp upper bound. Defaults to current UTC time.

        Returns:
            Pending reminders due at or before the timestamp.
        """

        return self.store.list_follow_ups(
            status=FollowUpStatus.PENDING,
            due_at_or_before=now or datetime.now(tz=UTC).isoformat(timespec="seconds"),
        )

    def complete(
        self, reminder_id: int, *, completed_at: str | None = None
    ) -> FollowUpReminder:
        """Mark a follow-up reminder completed.

        Args:
            reminder_id: Follow-up reminder ID.
            completed_at: Completion timestamp. Defaults to now.

        Returns:
            Updated reminder.
        """

        return self.store.complete_follow_up(
            reminder_id,
            completed_at=completed_at,
        )


def _parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO datetime string into an aware datetime."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
