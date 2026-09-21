"""Shared form filler contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from job_application_agent.materials import MaterialBundle
from job_application_agent.models import CandidateProfile, JobPosting
from job_application_agent.storage import ApplicationLedger


class FieldAction(StrEnum):
    """Planned action for an application form field."""

    FILL = "fill"
    UPLOAD = "upload"
    REVIEW = "review"


@dataclass(frozen=True)
class FormFieldPlan:
    """One planned field action for an application form."""

    field_key: str
    action: FieldAction
    value: str | None
    requires_review: bool = False


@dataclass(frozen=True)
class FormFillPlan:
    """A dry-run plan for filling an application form."""

    job_id: int
    provider: str
    application_url: str
    fields: tuple[FormFieldPlan, ...]
    stop_before_submit: bool = True


@dataclass(frozen=True)
class FormFillResult:
    """Result returned by a form filler."""

    plan: FormFillPlan
    ready_for_user_review: bool


class ApplicationFormFiller(Protocol):
    """Protocol for provider-specific form fillers."""

    def plan(
        self,
        *,
        job_id: int,
        job: JobPosting,
        profile: CandidateProfile,
        materials: MaterialBundle,
    ) -> FormFillResult:
        """Return a reviewable plan for filling an application form."""


def build_common_fields(
    *,
    profile: CandidateProfile,
    materials: MaterialBundle,
) -> tuple[FormFieldPlan, ...]:
    """Build common applicant field plans shared by ATS providers."""

    fields = [
        FormFieldPlan("full_name", FieldAction.FILL, profile.full_name),
        FormFieldPlan("email", FieldAction.FILL, profile.email),
    ]
    if profile.phone:
        fields.append(FormFieldPlan("phone", FieldAction.FILL, profile.phone))
    if profile.location:
        fields.append(FormFieldPlan("location", FieldAction.FILL, profile.location))
    if materials.resume_version:
        fields.append(
            FormFieldPlan("resume", FieldAction.UPLOAD, materials.resume_version)
        )
    fields.append(
        FormFieldPlan(
            "cover_letter",
            FieldAction.REVIEW,
            materials.cover_letter_text,
            requires_review=True,
        )
    )
    return tuple(fields)


def assert_application_preconditions(
    *, ledger: ApplicationLedger, job_id: int, job: JobPosting
) -> None:
    """Check ledger and URL prerequisites before planning a form fill."""

    ledger.assert_can_apply(job_id)
    stored = ledger.store.get_job(job_id)
    if stored is None:
        raise ValueError(f"Unknown job ID: {job_id}")
    if (
        str(stored["source"]) != job.source.value
        or str(stored["source_job_id"]) != job.source_job_id
        or str(stored["canonical_url"]) != job.canonical_url
    ):
        raise ValueError(f"Job {job_id} does not match stored job identity.")
    if not job.application_url:
        raise ValueError("Application URL is required to fill a form.")
