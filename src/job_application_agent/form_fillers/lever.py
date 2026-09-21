"""Lever form filler planning."""

from __future__ import annotations

from job_application_agent.form_fillers.base import (
    FormFillPlan,
    FormFillResult,
    assert_application_preconditions,
    build_common_fields,
)
from job_application_agent.materials import MaterialBundle
from job_application_agent.models import CandidateProfile, JobPosting, JobSource
from job_application_agent.storage import ApplicationLedger


class LeverFormFiller:
    """Dry-run planner for Lever application forms."""

    def __init__(self, ledger: ApplicationLedger) -> None:
        """Create a Lever form filler."""

        self.ledger = ledger

    def plan(
        self,
        *,
        job_id: int,
        job: JobPosting,
        profile: CandidateProfile,
        materials: MaterialBundle,
    ) -> FormFillResult:
        """Return a reviewable Lever form fill plan."""

        if job.source != JobSource.LEVER:
            raise ValueError("LeverFormFiller only accepts Lever jobs.")
        assert_application_preconditions(ledger=self.ledger, job_id=job_id, job=job)
        return FormFillResult(
            plan=FormFillPlan(
                job_id=job_id,
                provider=JobSource.LEVER.value,
                application_url=job.application_url,
                fields=build_common_fields(profile=profile, materials=materials),
            ),
            ready_for_user_review=True,
        )
