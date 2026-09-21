"""Hard eligibility filters for jobs that should not proceed to scoring."""

from __future__ import annotations

from dataclasses import dataclass

from job_application_agent.models import JobPosting
from job_application_agent.normalization import normalize_text


@dataclass(frozen=True)
class HardFilterCriteria:
    """Candidate constraints that should be enforced deterministically."""

    remote_only: bool = False
    allowed_locations: tuple[str, ...] = ()
    minimum_salary: int | None = None
    excluded_companies: tuple[str, ...] = ()
    excluded_title_keywords: tuple[str, ...] = ()
    required_title_keywords: tuple[str, ...] = ()
    authorized_work_regions: tuple[str, ...] = ()
    needs_visa_sponsorship: bool = False


@dataclass(frozen=True)
class FilterResult:
    """Result of applying hard filters."""

    passed: bool
    reasons: tuple[str, ...] = ()


class HardFilterService:
    """Reject jobs that clearly violate non-negotiable constraints."""

    def evaluate(self, job: JobPosting, criteria: HardFilterCriteria) -> FilterResult:
        reasons: list[str] = []
        title = normalize_text(job.title)
        company = normalize_text(job.company)
        location = normalize_text(job.location or "")

        if any(
            normalize_text(blocked_company) in company
            for blocked_company in criteria.excluded_companies
        ):
            reasons.append("Company is on the excluded company list.")

        blocked_keywords = [
            keyword
            for keyword in criteria.excluded_title_keywords
            if normalize_text(keyword) in title
        ]
        if blocked_keywords:
            reasons.append(
                "Title contains excluded keyword(s): "
                + ", ".join(sorted(blocked_keywords))
                + "."
            )

        if criteria.required_title_keywords and not any(
            normalize_text(keyword) in title
            for keyword in criteria.required_title_keywords
        ):
            reasons.append("Title does not contain any required keyword.")

        if criteria.remote_only and job.remote is False:
            reasons.append("Role is explicitly on-site but candidate requires remote.")

        if criteria.allowed_locations and job.remote is not True:
            allowed = tuple(normalize_text(item) for item in criteria.allowed_locations)
            if not any(item in location for item in allowed):
                reasons.append("Location is outside the allowed locations.")

        if job.salary_range and not job.salary_range.overlaps_minimum(
            criteria.minimum_salary
        ):
            reasons.append("Salary range is below the candidate minimum.")

        if (
            criteria.needs_visa_sponsorship
            and "no_visa_sponsorship" in job.work_authorization
        ):
            reasons.append("Role states that visa sponsorship is unavailable.")

        if (
            "us_authorization_required" in job.work_authorization
            and criteria.authorized_work_regions
            and "us"
            not in {
                normalize_text(region) for region in criteria.authorized_work_regions
            }
        ):
            reasons.append("Role requires US work authorization.")

        return FilterResult(passed=not reasons, reasons=tuple(reasons))
