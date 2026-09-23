"""Role relevance scoring for normalized jobs."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from job_application_agent.extraction import LocalJobFactExtractor
from job_application_agent.filters import HardFilterCriteria, HardFilterService
from job_application_agent.models import ExtractedJobFacts, JobPosting
from job_application_agent.normalization import normalize_text

ROLE_RELEVANCE_COMPONENT = "role_relevance"
ROLE_APPROVAL_THRESHOLD = 85.0
ROLE_REVIEW_THRESHOLD = 65.0


@dataclass(frozen=True)
class ScoringCriteria:
    """Candidate preferences used by the role relevance scorer."""

    target_roles: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    nice_to_have_skills: tuple[str, ...] = ()
    target_seniority: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    remote_ok: bool = True
    minimum_salary: int | None = None
    years_experience: int | None = None
    preferred_companies: tuple[str, ...] = ()
    avoided_companies: tuple[str, ...] = ()
    authorized_work_regions: tuple[str, ...] = ()
    needs_visa_sponsorship: bool = False
    hard_filters: HardFilterCriteria = field(default_factory=HardFilterCriteria)


@dataclass(frozen=True)
class ScoreBreakdown:
    """Score with component details and human-readable rationale."""

    total: float
    components: Mapping[str, float]
    explanations: tuple[str, ...]
    rejected_by_hard_filter: bool = False


@dataclass(frozen=True)
class RoleSimilarity:
    """Similarity score for one target role against one job."""

    target_role: str
    similarity: float


@dataclass(frozen=True)
class RoleRelevanceResult:
    """Role relevance score and supporting similarity details."""

    score: float
    matched_target_role: str | None
    similarities: tuple[RoleSimilarity, ...]
    job_text: str


class RoleEmbedder(Protocol):
    """Embed role-focused text for similarity scoring."""

    def embed(self, text: str) -> Mapping[str, float]:
        """Return an embedding-like vector for text.

        Args:
            text: Role-focused text to embed.

        Returns:
            Sparse embedding vector.
        """


class TokenRoleEmbedder:
    """Deterministic token-vector embedder for local role scoring."""

    def embed(self, text: str) -> Mapping[str, float]:
        """Return a normalized sparse token vector.

        Args:
            text: Role-focused text to embed.

        Returns:
            Sparse token vector with term-frequency weights.
        """

        tokens = _tokens(text)
        counts: Counter[str] = Counter(tokens)
        total = math.sqrt(sum(value * value for value in counts.values()))
        if total == 0:
            return {}
        return {token: count / total for token, count in counts.items()}


class RoleRelevanceScorer:
    """Score eligible jobs against candidate target roles."""

    def __init__(self, embedder: RoleEmbedder | None = None) -> None:
        """Create a role relevance scorer.

        Args:
            embedder: Optional text embedder.
        """

        self.embedder = embedder or TokenRoleEmbedder()

    def score(
        self,
        job: JobPosting,
        facts: ExtractedJobFacts,
        target_roles: tuple[str, ...],
    ) -> RoleRelevanceResult:
        """Score how closely a job matches target roles.

        Args:
            job: Normalized job posting.
            facts: Extracted role-relevance facts.
            target_roles: Candidate target role titles.

        Returns:
            Role relevance result with per-target-role similarities.
        """

        job_text = _job_role_text(job, facts)
        job_vector = self.embedder.embed(job_text)
        similarities = tuple(
            RoleSimilarity(
                target_role=target_role,
                similarity=_role_similarity(
                    target_role,
                    job_text,
                    self.embedder.embed(target_role),
                    job_vector,
                ),
            )
            for target_role in target_roles
            if target_role.strip()
        )
        if not similarities:
            return RoleRelevanceResult(
                score=70.0,
                matched_target_role=None,
                similarities=(),
                job_text=job_text,
            )
        best = max(similarities, key=lambda item: item.similarity)
        return RoleRelevanceResult(
            score=_similarity_to_score(best.similarity),
            matched_target_role=best.target_role,
            similarities=similarities,
            job_text=job_text,
        )


class FitScorer:
    """Apply hard filters, then score eligible jobs by role relevance."""

    def __init__(
        self,
        hard_filter_service: HardFilterService | None = None,
        role_scorer: RoleRelevanceScorer | None = None,
    ) -> None:
        """Create a fit scorer.

        Args:
            hard_filter_service: Optional hard filter implementation to reuse.
            role_scorer: Optional role relevance scorer to reuse.
        """

        self.hard_filter_service = hard_filter_service or HardFilterService()
        self.role_scorer = role_scorer or RoleRelevanceScorer()

    def score(
        self,
        job: JobPosting,
        criteria: ScoringCriteria,
        facts: ExtractedJobFacts | None = None,
    ) -> ScoreBreakdown:
        """Return a role relevance score for an eligible job.

        Args:
            job: Posting to score.
            criteria: Candidate preferences and hard constraints.
            facts: Extracted job facts. Local fallback extraction is used when absent.

        Returns:
            Score breakdown with role relevance details or hard-filter reasons.
        """

        extracted_facts = facts or LocalJobFactExtractor().extract(job)
        hard_filter_result = self.hard_filter_service.evaluate_facts(
            job,
            extracted_facts,
            self._merge_hard_filters(criteria),
        )
        if not hard_filter_result.passed:
            return ScoreBreakdown(
                total=0.0,
                components={},
                explanations=hard_filter_result.reasons,
                rejected_by_hard_filter=True,
            )

        relevance = self.role_scorer.score(
            job,
            extracted_facts,
            criteria.target_roles,
        )
        return ScoreBreakdown(
            total=round(relevance.score, 2),
            components={ROLE_RELEVANCE_COMPONENT: round(relevance.score, 2)},
            explanations=tuple(_role_explanations(relevance)),
        )

    def _merge_hard_filters(self, criteria: ScoringCriteria) -> HardFilterCriteria:
        """Merge explicit hard filters with profile-derived constraints.

        Args:
            criteria: Candidate scoring criteria.

        Returns:
            Deterministic hard-filter criteria.
        """

        return HardFilterCriteria(
            remote_only=criteria.hard_filters.remote_only,
            allowed_locations=criteria.hard_filters.allowed_locations,
            minimum_salary=(
                criteria.hard_filters.minimum_salary
                if criteria.hard_filters.minimum_salary is not None
                else criteria.minimum_salary
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
            years_experience=(
                criteria.hard_filters.years_experience
                if criteria.hard_filters.years_experience is not None
                else criteria.years_experience
            ),
        )


def _job_role_text(job: JobPosting, facts: ExtractedJobFacts) -> str:
    """Build role-focused text for relevance scoring.

    Args:
        job: Normalized job posting.
        facts: Extracted job facts.

    Returns:
        Text containing title, responsibilities, and requirements only.
    """

    parts = [
        f"Title: {job.title}",
        "Responsibilities:",
        *facts.responsibilities,
        "Requirements:",
        *facts.required_skills,
    ]
    return "\n".join(part for part in parts if part.strip())


def _role_explanations(relevance: RoleRelevanceResult) -> list[str]:
    """Return human-readable role relevance explanations.

    Args:
        relevance: Role relevance result.

    Returns:
        Explanation strings for the dashboard/API.
    """

    if relevance.matched_target_role is None:
        return ["No target roles configured; using neutral role relevance."]
    return [
        (
            "Matched target role "
            f"'{relevance.matched_target_role}' with role relevance "
            f"{relevance.score:.0f}%."
        )
    ]


def _tokens(text: str) -> tuple[str, ...]:
    """Return normalized role-scoring tokens.

    Args:
        text: Text to tokenize.

    Returns:
        Non-stopword tokens.
    """

    normalized = normalize_text(text)
    return tuple(
        token
        for token in re.findall(r"[a-z0-9+#.]+", normalized)
        if token not in STOPWORDS
    )


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    """Return cosine similarity for normalized sparse vectors.

    Args:
        left: Left sparse vector.
        right: Right sparse vector.

    Returns:
        Cosine similarity.
    """

    if not left or not right:
        return 0.0
    return sum(value * right.get(token, 0.0) for token, value in left.items())


def _role_similarity(
    target_role: str,
    job_text: str,
    target_vector: Mapping[str, float],
    job_vector: Mapping[str, float],
) -> float:
    """Return target-role similarity against role-focused job text.

    Args:
        target_role: Candidate target role title.
        job_text: Role-focused job text.
        target_vector: Embedded target role vector.
        job_vector: Embedded job text vector.

    Returns:
        Similarity value calibrated for role scoring.
    """

    normalized_role = normalize_text(target_role)
    normalized_job = normalize_text(job_text)
    similarity = _cosine(target_vector, job_vector)
    if normalized_role and re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_role)}(?![a-z0-9])",
        normalized_job,
    ):
        return max(similarity, 0.48)
    return similarity


def _similarity_to_score(similarity: float) -> float:
    """Convert role similarity into a 0-100 score.

    Args:
        similarity: Raw similarity value.

    Returns:
        Calibrated role relevance score.
    """

    if similarity <= 0.10:
        return 0.0
    if similarity >= 0.48:
        return 100.0
    return round(((similarity - 0.10) / 0.38) * 100.0, 2)


STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
