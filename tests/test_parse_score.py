from __future__ import annotations

from typing import Any

from job_application_agent import (
    DeduplicationService,
    FitScorer,
    HardFilterCriteria,
    HardFilterService,
    JobParser,
    JobSource,
    ScoringCriteria,
)
from job_application_agent.models import JobPosting
from job_application_agent.normalization import canonicalize_url


def test_parser_normalizes_structured_job() -> None:
    payload: dict[str, Any] = {
        "id": 12345,
        "title": "Senior Backend Engineer",
        "company": "ExampleCo",
        "location": {"name": "Remote US"},
        "absolute_url": "https://boards.greenhouse.io/exampleco/jobs/12345?utm_source=x",
        "content": """
            <p>Salary range: $140k - $180k</p>
            <h3>Requirements</h3>
            <ul><li>Python</li><li>Postgres</li></ul>
            <h3>Nice to have</h3>
            <ul><li>Kubernetes</li></ul>
        """,
    }

    job = JobParser().parse(payload)

    assert job.source_job_id == "12345"
    assert job.source == JobSource.GREENHOUSE
    assert job.canonical_url == "https://boards.greenhouse.io/exampleco/jobs/12345"
    assert job.salary_range is not None
    assert job.salary_range.minimum == 140_000
    assert job.salary_range.maximum == 180_000
    assert job.requirements == ("Python", "Postgres")
    assert job.nice_to_haves == ("Kubernetes",)
    assert job.remote is True
    assert job.seniority == "senior"


def test_deduplication_matches_canonical_urls_and_fingerprints() -> None:
    parser = JobParser()
    existing = parser.parse(
        {
            "id": "abc",
            "title": "Backend Engineer",
            "company": "ExampleCo",
            "location": "Remote US",
            "absolute_url": "https://jobs.lever.co/exampleco/abc?lever-source=email",
        },
        source="lever",
    )
    same_url = parser.parse(
        {
            "title": "Backend Engineer",
            "company": "ExampleCo",
            "location": "Remote US",
            "absolute_url": "https://jobs.lever.co/exampleco/abc",
        },
        source="lever",
    )
    same_fingerprint = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="999",
        title="Backend Engineer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/exampleco/jobs/999",
        canonical_url=canonicalize_url(
            "https://boards.greenhouse.io/exampleco/jobs/999"
        ),
        location="Remote US",
    )

    service = DeduplicationService()

    assert service.find_duplicates(same_url, [existing])[0].signal == "canonical_url"
    assert (
        service.find_duplicates(same_fingerprint, [existing])[0].signal == "fingerprint"
    )


def test_hard_filters_reject_clear_mismatches() -> None:
    job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="1",
        title="On-site Backend Engineer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/exampleco/jobs/1",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/exampleco/jobs/1"),
        location="Austin, TX",
        remote=False,
    )

    result = HardFilterService().evaluate(
        job,
        HardFilterCriteria(
            remote_only=True,
            allowed_locations=("New York",),
            excluded_title_keywords=("frontend",),
        ),
    )

    assert result.passed is False
    assert "Role is explicitly on-site but candidate requires remote." in result.reasons
    assert "Location is outside the allowed locations." in result.reasons


def test_fit_scorer_scores_matches_and_honors_hard_filters() -> None:
    job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="1",
        title="Senior Backend Engineer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/exampleco/jobs/1",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/exampleco/jobs/1"),
        location="Remote US",
        content="Build Python services on Postgres and AWS.",
        requirements=("Python", "Postgres", "AWS"),
        remote=True,
        seniority="senior",
    )
    scorer = FitScorer()

    score = scorer.score(
        job,
        ScoringCriteria(
            skills=("Python", "Postgres", "AWS"),
            target_seniority=("senior",),
            preferred_locations=("Remote",),
            preferred_companies=("ExampleCo",),
            authorized_work_regions=("US",),
        ),
    )

    assert score.rejected_by_hard_filter is False
    assert score.total > 85
    assert score.components["required_skills"] == 100

    rejected = scorer.score(
        job,
        ScoringCriteria(
            skills=("Python",),
            hard_filters=HardFilterCriteria(excluded_companies=("ExampleCo",)),
        ),
    )

    assert rejected.rejected_by_hard_filter is True
    assert rejected.total == 0
