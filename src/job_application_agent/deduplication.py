"""Duplicate detection for normalized job postings."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from job_application_agent.models import JobPosting
from job_application_agent.normalization import make_job_fingerprint

SemanticMatcher = Callable[[JobPosting, JobPosting], float]


@dataclass(frozen=True)
class DuplicateMatch:
    """A duplicate or possible duplicate found by one matching signal."""

    existing: JobPosting
    signal: str
    confidence: float
    detail: str


class DeduplicationService:
    """Find duplicate jobs using layered deterministic and optional semantic checks."""

    def __init__(
        self,
        *,
        semantic_matcher: SemanticMatcher | None = None,
        semantic_threshold: float = 0.92,
    ) -> None:
        """Create a deduplication service.

        Args:
            semantic_matcher: Optional callback that returns a similarity score
                for two postings.
            semantic_threshold: Minimum semantic score treated as a duplicate.
        """

        self.semantic_matcher = semantic_matcher
        self.semantic_threshold = semantic_threshold

    def find_duplicates(
        self, job: JobPosting, existing_jobs: Iterable[JobPosting]
    ) -> tuple[DuplicateMatch, ...]:
        """Return duplicate matches ordered by confidence.

        Args:
            job: New posting to check.
            existing_jobs: Existing postings to compare against.

        Returns:
            Duplicate matches ordered from highest to lowest confidence.
        """

        matches: list[DuplicateMatch] = []
        for existing in existing_jobs:
            if (
                job.source == existing.source
                and job.source_job_id == existing.source_job_id
            ):
                matches.append(
                    DuplicateMatch(
                        existing=existing,
                        signal="job_id",
                        confidence=1.0,
                        detail=f"Exact ATS job id {job.source}:{job.source_job_id}",
                    )
                )
                continue

            if job.canonical_url == existing.canonical_url:
                matches.append(
                    DuplicateMatch(
                        existing=existing,
                        signal="canonical_url",
                        confidence=1.0,
                        detail=f"Same canonical URL {job.canonical_url}",
                    )
                )
                continue

            if make_job_fingerprint(
                job.company, job.title, job.location
            ) == make_job_fingerprint(
                existing.company, existing.title, existing.location
            ):
                matches.append(
                    DuplicateMatch(
                        existing=existing,
                        signal="fingerprint",
                        confidence=0.95,
                        detail="Same normalized company, title, and location",
                    )
                )
                continue

            if self.semantic_matcher is not None:
                semantic_score = self.semantic_matcher(job, existing)
                if semantic_score >= self.semantic_threshold:
                    matches.append(
                        DuplicateMatch(
                            existing=existing,
                            signal="semantic",
                            confidence=semantic_score,
                            detail="Semantic matcher marked postings as equivalent",
                        )
                    )

        return tuple(sorted(matches, key=lambda match: match.confidence, reverse=True))

    def is_duplicate(
        self, job: JobPosting, existing_jobs: Iterable[JobPosting]
    ) -> bool:
        """Return true when a high-confidence duplicate exists.

        Args:
            job: New posting to check.
            existing_jobs: Existing postings to compare against.

        Returns:
            True when any match meets the configured duplicate threshold.
        """

        return any(
            match.signal != "semantic" or match.confidence >= self.semantic_threshold
            for match in self.find_duplicates(job, existing_jobs)
        )
