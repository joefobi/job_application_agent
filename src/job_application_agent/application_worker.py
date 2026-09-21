"""Application worker orchestration for approved jobs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from typing import Protocol, cast

from job_application_agent.form_fillers import GreenhouseFormFiller, LeverFormFiller
from job_application_agent.form_fillers.base import FormFillResult
from job_application_agent.materials import (
    MaterialBundle,
    MaterialGenerator,
    MaterialRequest,
    TemplateMaterialGenerator,
)
from job_application_agent.models import (
    CandidateProfile,
    JobPosting,
    JobSource,
    JobStatus,
    SubmissionConfirmation,
    utc_now_iso,
)
from job_application_agent.storage import ApplicationLedger, ApplicationStore
from job_application_agent.tracking import (
    SubmissionConfirmationRecorder,
    SubmissionConfirmationRequest,
)


class ApplicationFormPlanner(Protocol):
    """Protocol for provider-specific application form planners."""

    def plan(
        self,
        *,
        job_id: int,
        job: JobPosting,
        profile: CandidateProfile,
        materials: MaterialBundle,
    ) -> FormFillResult:
        """Return a reviewable form-fill result."""


@dataclass(frozen=True)
class ApplicationPreparation:
    """Reviewable output from preparing an approved job application."""

    job_id: int
    status: JobStatus
    materials: MaterialBundle
    form_result: FormFillResult
    requires_user_approval: bool


class ApplicationWorker:
    """Prepare approved jobs for application without marking them applied."""

    def __init__(
        self,
        store: ApplicationStore,
        *,
        material_generator: MaterialGenerator | None = None,
        form_planners: dict[JobSource, ApplicationFormPlanner] | None = None,
    ) -> None:
        """Create an application worker.

        Args:
            store: Initialized application store.
            material_generator: Optional material generator implementation.
            form_planners: Optional provider-specific form planners.
        """

        self.store = store
        self.ledger = ApplicationLedger(store)
        self.material_generator = material_generator or TemplateMaterialGenerator()
        self.form_planners = form_planners or {
            JobSource.GREENHOUSE: GreenhouseFormFiller(self.ledger),
            JobSource.LEVER: LeverFormFiller(self.ledger),
        }

    def approved_job_ids(self) -> tuple[int, ...]:
        """Return job IDs currently approved for application.

        Returns:
            Approved job IDs ordered the same way as the store list.
        """

        return tuple(
            int(row["id"])
            for row in self.store.list_jobs(status=JobStatus.APPROVED_TO_APPLY)
        )

    def prepare_approved_jobs(
        self,
        profile: CandidateProfile,
        *,
        limit: int | None = None,
    ) -> tuple[ApplicationPreparation, ...]:
        """Prepare approved jobs for user review.

        Args:
            profile: Candidate profile used to tailor materials and fill fields.
            limit: Optional maximum number of approved jobs to prepare.

        Returns:
            Prepared application plans.
        """

        job_ids = self.approved_job_ids()
        if limit is not None:
            job_ids = job_ids[:limit]
        return tuple(self.prepare_job(job_id, profile) for job_id in job_ids)

    def prepare_next(self, profile: CandidateProfile) -> ApplicationPreparation | None:
        """Prepare the next approved job, if one exists."""

        job_ids = self.approved_job_ids()
        if not job_ids:
            return None
        return self.prepare_job(job_ids[0], profile)

    def prepare_job(
        self,
        job_id: int,
        profile: CandidateProfile,
    ) -> ApplicationPreparation:
        """Generate materials and a reviewable form-fill plan for one job.

        Args:
            job_id: Approved job ID to prepare.
            profile: Candidate profile used for truthful materials and fields.

        Returns:
            Application preparation result.
        """

        row = self.store.get_job(job_id)
        if row is None:
            raise ValueError(f"Unknown job ID: {job_id}")
        status = JobStatus(str(row["status"]))
        if status not in {
            JobStatus.APPROVED_TO_APPLY,
            JobStatus.STARTED_APPLICATION,
        }:
            raise ValueError(
                f"Job {job_id} must be approved_to_apply or started_application "
                "before the worker runs."
            )

        job = _job_from_row(row)
        materials, form_result = self._build_preparation(job_id, job, profile)
        if status == JobStatus.APPROVED_TO_APPLY:
            self._commit_preparation(job_id, job, materials, form_result)
        return ApplicationPreparation(
            job_id=job_id,
            status=JobStatus.STARTED_APPLICATION,
            materials=materials,
            form_result=form_result,
            requires_user_approval=(
                materials.requires_review
                or form_result.ready_for_user_review
                or form_result.plan.stop_before_submit
                or any(field.requires_review for field in form_result.plan.fields)
            ),
        )

    def confirm_submission(
        self,
        request: SubmissionConfirmationRequest,
    ) -> SubmissionConfirmation:
        """Record a confirmed submission and only then mark the job applied.

        Args:
            request: Confirmed submission details from the user or submitter.

        Returns:
            Durable submission confirmation.
        """

        row = self.store.get_job(request.job_id)
        if row is None:
            raise ValueError(f"Unknown job ID: {request.job_id}")
        status = JobStatus(str(row["status"]))
        if status != JobStatus.STARTED_APPLICATION:
            raise ValueError(
                "Submission can only be confirmed after the application worker "
                "starts the application."
            )
        confirmation_request = (
            request
            if request.provider is not None
            else replace(request, provider=str(row["source"]))
        )
        return SubmissionConfirmationRecorder(self.store).record(confirmation_request)

    def _build_preparation(
        self,
        job_id: int,
        job: JobPosting,
        profile: CandidateProfile,
    ) -> tuple[MaterialBundle, FormFillResult]:
        """Build materials and form plan before durable state changes.

        Args:
            job_id: Job ID being prepared.
            job: Stored job posting.
            profile: Candidate profile used to tailor application content.

        Returns:
            Generated materials and a reviewable form-fill result.
        """

        planner = self.form_planners.get(job.source)
        if planner is None:
            raise ValueError(f"No form planner is configured for {job.source.value}.")

        materials = self.material_generator.generate(
            MaterialRequest(profile=profile, job=job)
        )
        form_result = planner.plan(
            job_id=job_id,
            job=job,
            profile=profile,
            materials=materials,
        )
        return materials, form_result

    def _commit_preparation(
        self,
        job_id: int,
        job: JobPosting,
        materials: MaterialBundle,
        form_result: FormFillResult,
    ) -> None:
        """Commit preparation events and status transition atomically.

        Args:
            job_id: Approved job ID being prepared.
            job: Stored job posting.
            materials: Generated materials for the application.
            form_result: Planned form-fill result.

        Returns:
            None.
        """

        now = utc_now_iso()
        with self.store.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.STARTED_APPLICATION.value,
                    now,
                    job_id,
                    JobStatus.APPROVED_TO_APPLY.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"Job {job_id} no longer has status "
                    f"{JobStatus.APPROVED_TO_APPLY.value}."
                )
            _record_event(
                connection,
                job_id,
                "application_materials_created",
                {
                    "resume_version": materials.resume_version,
                    "cover_letter_version": materials.cover_letter_version,
                    "requires_review": materials.requires_review,
                },
                now,
            )
            _record_event(
                connection,
                job_id,
                "application_form_planned",
                {
                    "application_url": form_result.plan.application_url,
                    "field_count": len(form_result.plan.fields),
                    "provider": form_result.plan.provider,
                    "stop_before_submit": form_result.plan.stop_before_submit,
                },
                now,
            )
            _record_event(
                connection,
                job_id,
                "application_worker_started",
                {"source": job.source.value},
                now,
            )


def _job_from_row(row: sqlite3.Row) -> JobPosting:
    """Build a job posting model from a SQLite row."""

    raw_data = _raw_json(row["raw_json"])
    return JobPosting(
        source=JobSource(str(row["source"])),
        source_job_id=str(row["source_job_id"]),
        title=str(row["title"]),
        company=str(row["company"]),
        location=_optional_str(row["location"]),
        department=_optional_str(row["department"]),
        employment_type=_optional_str(row["employment_type"]),
        application_url=str(row["application_url"]),
        canonical_url=str(row["canonical_url"]),
        content=_optional_str(row["content"]),
        raw_data=raw_data,
    )


def _record_event(
    connection: sqlite3.Connection,
    job_id: int,
    event_type: str,
    details: dict[str, object],
    created_at: str,
) -> None:
    """Record one job event on an existing transaction.

    Args:
        connection: SQLite connection with an active transaction.
        job_id: Job ID for the event.
        event_type: Machine-readable event type.
        details: JSON-serializable event payload.
        created_at: Timestamp shared by the transaction.

    Returns:
        None.
    """

    connection.execute(
        """
        INSERT INTO job_events (job_id, event_type, details_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (job_id, event_type, json.dumps(details, sort_keys=True), created_at),
    )


def _raw_json(value: object) -> dict[str, object]:
    """Return row raw_json as a dictionary."""

    if value is None:
        return {}
    data = json.loads(str(value))
    if not isinstance(data, dict):
        return {}
    return cast(dict[str, object], data)


def _optional_str(value: object) -> str | None:
    """Return a stripped string or None."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None
