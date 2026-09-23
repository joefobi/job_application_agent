"""Hard eligibility filters for jobs that should not proceed to scoring."""

from __future__ import annotations

from dataclasses import dataclass

from job_application_agent.extraction import LocalJobFactExtractor
from job_application_agent.models import ExtractedJobFacts, JobPosting
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
    years_experience: int | None = None


@dataclass(frozen=True)
class FilterResult:
    """Result of applying hard filters."""

    passed: bool
    reasons: tuple[str, ...] = ()


class HardFilterService:
    """Reject jobs that clearly violate non-negotiable constraints."""

    def evaluate(self, job: JobPosting, criteria: HardFilterCriteria) -> FilterResult:
        """Apply hard filters to a job using locally inferred facts.

        Args:
            job: Posting to evaluate.
            criteria: Candidate constraints that must be satisfied.

        Returns:
            Filter result with pass/fail status and rejection reasons.
        """

        return self.evaluate_facts(
            job,
            LocalJobFactExtractor().extract(job),
            criteria,
        )

    def evaluate_facts(
        self,
        job: JobPosting,
        facts: ExtractedJobFacts,
        criteria: HardFilterCriteria,
    ) -> FilterResult:
        """Apply hard filters to extracted job facts.

        Args:
            job: Posting identity and company/title metadata.
            facts: LLM-extracted hard-filter facts.
            criteria: Candidate constraints that must be satisfied.

        Returns:
            Filter result with pass/fail status and rejection reasons.
        """

        reasons: list[str] = []
        title = normalize_text(job.title)
        company = normalize_text(job.company)
        locations = tuple(
            normalize_text(location)
            for location in (*facts.locations, job.location or "")
            if location.strip()
        )

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

        if criteria.remote_only and facts.remote_policy == "onsite":
            reasons.append("Role is explicitly on-site but candidate requires remote.")

        if criteria.allowed_locations and facts.remote_policy != "remote":
            allowed = tuple(normalize_text(item) for item in criteria.allowed_locations)
            if locations and not any(
                allowed_location in location
                for allowed_location in allowed
                for location in locations
            ):
                reasons.append("Location is outside the allowed locations.")

        if facts.salary_range and not facts.salary_range.overlaps_minimum(
            criteria.minimum_salary
        ):
            reasons.append("Salary range is below the candidate minimum.")

        if (
            criteria.needs_visa_sponsorship
            and facts.visa_sponsorship == "not_available"
        ):
            reasons.append("Role states that visa sponsorship is unavailable.")

        if (
            facts.requires_us_work_authorization is True
            and criteria.authorized_work_regions
            and "us"
            not in {
                normalize_text(region) for region in criteria.authorized_work_regions
            }
        ):
            reasons.append("Role requires US work authorization.")

        if (
            criteria.years_experience is not None
            and facts.minimum_years_experience is not None
            and facts.minimum_years_experience > criteria.years_experience
        ):
            reasons.append(
                "Role requires more years of experience than the candidate has."
            )

        return FilterResult(passed=not reasons, reasons=tuple(reasons))
