"""Material generation contracts for resumes, cover letters, and answers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from job_application_agent.models import CandidateProfile, JobPosting


@dataclass(frozen=True)
class MaterialRequest:
    """Inputs for generating application materials."""

    profile: CandidateProfile
    job: JobPosting
    fit_reasons: tuple[str, ...] = ()
    selected_resume_version: str | None = None


@dataclass(frozen=True)
class MaterialBundle:
    """Generated or selected materials for one job application."""

    resume_version: str | None
    cover_letter_version: str
    cover_letter_text: str
    short_answers: dict[str, str]
    requires_review: bool = True


class MaterialGenerator(Protocol):
    """Protocol for pluggable deterministic or agentic material generators."""

    def generate(self, request: MaterialRequest) -> MaterialBundle:
        """Generate materials for a job application."""


class TemplateMaterialGenerator:
    """Safe placeholder generator that uses only profile and job facts."""

    def generate(self, request: MaterialRequest) -> MaterialBundle:
        """Generate a conservative draft material bundle.

        Args:
            request: Candidate, job, and optional fit context.

        Returns:
            A review-required bundle that can be replaced by an LLM generator.
        """

        resume_version = request.selected_resume_version or _default_resume_version(
            request.profile
        )
        cover_letter = (
            f"Dear {request.job.company} hiring team,\n\n"
            f"I am interested in the {request.job.title} role. "
            f"My background includes {', '.join(request.profile.skills) or 'relevant'} "
            "experience, and I would welcome the chance to discuss how it maps to "
            "your team.\n\n"
            f"Sincerely,\n{request.profile.full_name}"
        )
        return MaterialBundle(
            resume_version=resume_version,
            cover_letter_version=f"{request.job.source.value}:{request.job.source_job_id}",
            cover_letter_text=cover_letter,
            short_answers={
                answer.question_key: answer.answer
                for answer in request.profile.reusable_answers
                if not answer.requires_review
            },
            requires_review=True,
        )


def _default_resume_version(profile: CandidateProfile) -> str | None:
    """Return the default resume version ID, if configured."""

    for resume in profile.resume_versions:
        if resume.is_default:
            return resume.version_id
    if profile.resume_versions:
        return profile.resume_versions[0].version_id
    return None
