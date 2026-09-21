"""Domain models for the job application agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    """Lifecycle states for jobs and applications."""

    DISCOVERED = "discovered"
    DUPLICATE_POSSIBLE = "duplicate_possible"
    REJECTED = "rejected"
    SAVED = "saved"
    NEEDS_REVIEW = "needs_review"
    APPROVED_TO_APPLY = "approved_to_apply"
    STARTED_APPLICATION = "started_application"
    APPLIED = "applied"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"
    INTERVIEWING = "interviewing"
    CLOSED = "closed"


class JobSource(StrEnum):
    """Supported job ingestion sources."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"


@dataclass(frozen=True)
class WorkExperience:
    """A candidate work history entry."""

    company: str
    title: str
    start_date: str
    end_date: str | None = None
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True)
class Education:
    """A candidate education entry."""

    institution: str
    credential: str
    field_of_study: str | None = None
    graduation_year: int | None = None


@dataclass(frozen=True)
class ResumeVersion:
    """A stored resume version available for applications."""

    version_id: str
    label: str
    file_path: str
    is_default: bool = False


@dataclass(frozen=True)
class ReusableAnswer:
    """A reusable answer for common application questions."""

    question_key: str
    answer: str
    requires_review: bool = False


@dataclass(frozen=True)
class CandidateProfile:
    """Structured candidate facts and search preferences."""

    profile_id: str
    full_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    work_authorization: str | None = None
    target_roles: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    remote_preference: str | None = None
    minimum_salary: int | None = None
    skills: tuple[str, ...] = ()
    blocked_companies: tuple[str, ...] = ()
    blocked_industries: tuple[str, ...] = ()
    work_experience: tuple[WorkExperience, ...] = ()
    education: tuple[Education, ...] = ()
    resume_versions: tuple[ResumeVersion, ...] = ()
    reusable_answers: tuple[ReusableAnswer, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the profile."""

        return {
            "profile_id": self.profile_id,
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "location": self.location,
            "work_authorization": self.work_authorization,
            "target_roles": list(self.target_roles),
            "preferred_locations": list(self.preferred_locations),
            "remote_preference": self.remote_preference,
            "minimum_salary": self.minimum_salary,
            "skills": list(self.skills),
            "blocked_companies": list(self.blocked_companies),
            "blocked_industries": list(self.blocked_industries),
            "work_experience": [
                {
                    "company": item.company,
                    "title": item.title,
                    "start_date": item.start_date,
                    "end_date": item.end_date,
                    "highlights": list(item.highlights),
                }
                for item in self.work_experience
            ],
            "education": [
                {
                    "institution": item.institution,
                    "credential": item.credential,
                    "field_of_study": item.field_of_study,
                    "graduation_year": item.graduation_year,
                }
                for item in self.education
            ],
            "resume_versions": [
                {
                    "version_id": item.version_id,
                    "label": item.label,
                    "file_path": item.file_path,
                    "is_default": item.is_default,
                }
                for item in self.resume_versions
            ],
            "reusable_answers": [
                {
                    "question_key": item.question_key,
                    "answer": item.answer,
                    "requires_review": item.requires_review,
                }
                for item in self.reusable_answers
            ],
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> CandidateProfile:
        """Build a profile from a JSON dictionary.

        Args:
            data: Serialized profile data from storage.

        Returns:
            A reconstructed candidate profile.
        """

        return cls(
            profile_id=str(data["profile_id"]),
            full_name=str(data["full_name"]),
            email=str(data["email"]),
            phone=_optional_str(data.get("phone")),
            location=_optional_str(data.get("location")),
            work_authorization=_optional_str(data.get("work_authorization")),
            target_roles=tuple(str(item) for item in data.get("target_roles", [])),
            preferred_locations=tuple(
                str(item) for item in data.get("preferred_locations", [])
            ),
            remote_preference=_optional_str(data.get("remote_preference")),
            minimum_salary=_optional_int(data.get("minimum_salary")),
            skills=tuple(str(item) for item in data.get("skills", [])),
            blocked_companies=tuple(
                str(item) for item in data.get("blocked_companies", [])
            ),
            blocked_industries=tuple(
                str(item) for item in data.get("blocked_industries", [])
            ),
            work_experience=tuple(
                WorkExperience(
                    company=str(item["company"]),
                    title=str(item["title"]),
                    start_date=str(item["start_date"]),
                    end_date=_optional_str(item.get("end_date")),
                    highlights=tuple(
                        str(highlight) for highlight in item.get("highlights", [])
                    ),
                )
                for item in data.get("work_experience", [])
            ),
            education=tuple(
                Education(
                    institution=str(item["institution"]),
                    credential=str(item["credential"]),
                    field_of_study=_optional_str(item.get("field_of_study")),
                    graduation_year=_optional_int(item.get("graduation_year")),
                )
                for item in data.get("education", [])
            ),
            resume_versions=tuple(
                ResumeVersion(
                    version_id=str(item["version_id"]),
                    label=str(item["label"]),
                    file_path=str(item["file_path"]),
                    is_default=bool(item.get("is_default", False)),
                )
                for item in data.get("resume_versions", [])
            ),
            reusable_answers=tuple(
                ReusableAnswer(
                    question_key=str(item["question_key"]),
                    answer=str(item["answer"]),
                    requires_review=bool(item.get("requires_review", False)),
                )
                for item in data.get("reusable_answers", [])
            ),
        )


@dataclass(frozen=True)
class JobPosting:
    """Normalized job posting data from a source system."""

    source: JobSource
    source_job_id: str
    title: str
    company: str
    application_url: str
    canonical_url: str
    location: str | None = None
    department: str | None = None
    employment_type: str | None = None
    content: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LedgerResult:
    """Result of recording a discovered job in the application ledger."""

    job_id: int
    status: JobStatus
    is_new: bool
    reason: str


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""

    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _optional_str(value: object) -> str | None:
    """Return a stripped string or None for empty optional input."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    """Return an integer or None for empty optional input."""

    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(str(value))
