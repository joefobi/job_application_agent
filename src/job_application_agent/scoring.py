"""Fit scoring for normalized jobs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from job_application_agent.filters import HardFilterCriteria, HardFilterService
from job_application_agent.models import JobPosting
from job_application_agent.normalization import normalize_text

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "role_match": 0.24,
    "required_skills": 0.14,
    "nice_to_haves": 0.05,
    "seniority": 0.12,
    "location": 0.16,
    "salary": 0.14,
    "work_authorization": 0.10,
    "company": 0.05,
}


@dataclass(frozen=True)
class ScoringCriteria:
    """Candidate preferences used by the deterministic fit scorer."""

    target_roles: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    nice_to_have_skills: tuple[str, ...] = ()
    target_seniority: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    remote_ok: bool = True
    minimum_salary: int | None = None
    preferred_companies: tuple[str, ...] = ()
    avoided_companies: tuple[str, ...] = ()
    authorized_work_regions: tuple[str, ...] = ()
    needs_visa_sponsorship: bool = False
    hard_filters: HardFilterCriteria = field(default_factory=HardFilterCriteria)
    weights: Mapping[str, float] = field(default_factory=lambda: DEFAULT_WEIGHTS)


@dataclass(frozen=True)
class ScoreBreakdown:
    """Weighted score with component details and human-readable rationale."""

    total: float
    components: Mapping[str, float]
    explanations: tuple[str, ...]
    rejected_by_hard_filter: bool = False


class FitScorer:
    """Score how well a normalized job matches candidate criteria."""

    def __init__(self, hard_filter_service: HardFilterService | None = None) -> None:
        """Create a fit scorer.

        Args:
            hard_filter_service: Optional hard filter implementation to reuse.
        """

        self.hard_filter_service = hard_filter_service or HardFilterService()

    def score(self, job: JobPosting, criteria: ScoringCriteria) -> ScoreBreakdown:
        """Return a 0-100 fit score and explanatory component scores.

        Args:
            job: Posting to score.
            criteria: Candidate preferences and hard constraints.

        Returns:
            Weighted score breakdown with component scores and explanations.
        """

        hard_filter_result = self.hard_filter_service.evaluate(
            job, self._merge_hard_filters(criteria)
        )
        if not hard_filter_result.passed:
            return ScoreBreakdown(
                total=0.0,
                components={},
                explanations=hard_filter_result.reasons,
                rejected_by_hard_filter=True,
            )

        components = {
            "role_match": self._role_score(job, criteria),
            "required_skills": self._skill_overlap(
                criteria.skills, self._skill_text(job)
            ),
            "nice_to_haves": self._skill_overlap(
                criteria.nice_to_have_skills, self._skill_text(job)
            ),
            "seniority": self._seniority_score(job, criteria),
            "location": self._location_score(job, criteria),
            "salary": self._salary_score(job, criteria),
            "work_authorization": self._work_authorization_score(job, criteria),
            "company": self._company_score(job, criteria),
        }
        total = sum(
            components[name] * criteria.weights.get(name, 0.0) for name in components
        )

        return ScoreBreakdown(
            total=round(total, 2),
            components={name: round(score, 2) for name, score in components.items()},
            explanations=tuple(self._explanations(job, criteria, components)),
        )

    def _merge_hard_filters(self, criteria: ScoringCriteria) -> HardFilterCriteria:
        return HardFilterCriteria(
            remote_only=criteria.hard_filters.remote_only,
            allowed_locations=criteria.hard_filters.allowed_locations,
            minimum_salary=(
                criteria.hard_filters.minimum_salary or criteria.minimum_salary
            ),
            excluded_companies=(
                criteria.hard_filters.excluded_companies or criteria.avoided_companies
            ),
            excluded_title_keywords=criteria.hard_filters.excluded_title_keywords,
            required_title_keywords=criteria.hard_filters.required_title_keywords,
            authorized_work_regions=(
                criteria.hard_filters.authorized_work_regions
                or criteria.authorized_work_regions
            ),
            needs_visa_sponsorship=(
                criteria.hard_filters.needs_visa_sponsorship
                or criteria.needs_visa_sponsorship
            ),
        )

    def _skill_overlap(self, skills: tuple[str, ...], job_text: str) -> float:
        if not skills:
            return 70.0
        matched = [skill for skill in skills if _skill_matches(skill, job_text)]
        return 100.0 * len(matched) / len(skills)

    def _seniority_score(self, job: JobPosting, criteria: ScoringCriteria) -> float:
        if not criteria.target_seniority:
            return 70.0
        if job.seniority is None:
            return 55.0
        targets = {normalize_text(item) for item in criteria.target_seniority}
        return 100.0 if job.seniority in targets else 35.0

    def _location_score(self, job: JobPosting, criteria: ScoringCriteria) -> float:
        if job.remote is True and criteria.remote_ok:
            return 100.0
        if job.remote is False and not criteria.remote_ok:
            return 75.0
        if criteria.preferred_locations:
            location = normalize_text(job.location or "")
            if any(
                normalize_text(preferred_location) in location
                for preferred_location in criteria.preferred_locations
            ):
                return 100.0
            return 45.0
        return 65.0

    def _salary_score(self, job: JobPosting, criteria: ScoringCriteria) -> float:
        if criteria.minimum_salary is None:
            return 70.0
        if job.salary_range is None:
            return 55.0
        if not job.salary_range.overlaps_minimum(criteria.minimum_salary):
            return 0.0
        if (
            job.salary_range.minimum
            and job.salary_range.minimum >= criteria.minimum_salary
        ):
            return 100.0
        return 80.0

    def _work_authorization_score(
        self, job: JobPosting, criteria: ScoringCriteria
    ) -> float:
        if (
            criteria.needs_visa_sponsorship
            and "no_visa_sponsorship" in job.work_authorization
        ):
            return 0.0
        if (
            "us_authorization_required" in job.work_authorization
            and criteria.authorized_work_regions
        ):
            authorized_regions = {
                normalize_text(region) for region in criteria.authorized_work_regions
            }
            return 100.0 if "us" in authorized_regions else 0.0
        return 80.0

    def _company_score(self, job: JobPosting, criteria: ScoringCriteria) -> float:
        company = normalize_text(job.company)
        if any(normalize_text(item) in company for item in criteria.avoided_companies):
            return 0.0
        if any(
            normalize_text(item) in company for item in criteria.preferred_companies
        ):
            return 100.0
        return 65.0

    def _explanations(
        self,
        job: JobPosting,
        criteria: ScoringCriteria,
        components: Mapping[str, float],
    ) -> list[str]:
        explanations = [
            f"Role/title match: {components['role_match']:.0f}%.",
            f"Required skill match: {components['required_skills']:.0f}%.",
            f"Location compatibility: {components['location']:.0f}%.",
            f"Salary compatibility: {components['salary']:.0f}%.",
        ]
        if criteria.target_seniority and job.seniority:
            explanations.append(f"Detected seniority: {job.seniority}.")
        if job.salary_range and job.salary_range.minimum and job.salary_range.maximum:
            explanations.append(
                "Parsed salary range: "
                f"${job.salary_range.minimum:,}-${job.salary_range.maximum:,}."
            )
        return explanations

    def _skill_text(self, job: JobPosting) -> str:
        return " ".join((job.title, job.content or "", *job.requirements))

    def _role_score(self, job: JobPosting, criteria: ScoringCriteria) -> float:
        target_roles = tuple(role for role in criteria.target_roles if role.strip())
        if not target_roles:
            return 70.0

        title = normalize_text(job.title)
        if not title:
            return 45.0

        return max(
            _target_role_score(target_role, title) for target_role in target_roles
        )


GENERIC_ROLE_TOKENS = {
    "developer",
    "engineer",
    "engineering",
    "software",
}


def _target_role_score(target_role: str, normalized_title: str) -> float:
    normalized_role = normalize_text(target_role)
    if not normalized_role:
        return 0.0
    if _phrase_matches(normalized_role, normalized_title):
        return 100.0

    title_tokens = set(_word_tokens(normalized_title))
    role_tokens = set(_word_tokens(normalized_role))
    signal_tokens = role_tokens - GENERIC_ROLE_TOKENS
    tokens_to_match = signal_tokens or role_tokens
    if not tokens_to_match:
        return 0.0

    overlap = len(tokens_to_match & title_tokens) / len(tokens_to_match)
    if overlap == 1.0:
        return 85.0
    if overlap >= 0.5:
        return 60.0
    return 25.0


def _skill_matches(skill: str, job_text: str) -> bool:
    if any(not character.isalnum() and not character.isspace() for character in skill):
        return (
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(skill)}(?![A-Za-z0-9])",
                job_text,
                re.IGNORECASE,
            )
            is not None
        )

    normalized_skill = normalize_text(skill)
    if not normalized_skill:
        return False
    normalized_text = normalize_text(job_text)
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_skill)}(?![a-z0-9])",
            normalized_text,
        )
        is not None
    )


def _word_tokens(text: str) -> tuple[str, ...]:
    """Return lowercase word tokens for deterministic role comparison."""

    return tuple(re.findall(r"[a-z0-9]+", text))


def _phrase_matches(phrase: str, text: str) -> bool:
    """Return whether a normalized phrase appears on token boundaries."""

    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
            text,
        )
        is not None
    )
