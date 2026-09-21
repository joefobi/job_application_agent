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
from job_application_agent.parser import infer_remote, parse_salary
from job_application_agent.scoring import DEFAULT_WEIGHTS


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


def test_deduplication_fingerprints_ignore_semantic_threshold() -> None:
    existing = JobPosting(
        source=JobSource.LEVER,
        source_job_id="abc",
        title="Backend Engineer",
        company="ExampleCo",
        application_url="https://jobs.lever.co/exampleco/abc",
        canonical_url=canonicalize_url("https://jobs.lever.co/exampleco/abc"),
        location="Remote US",
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

    assert DeduplicationService(semantic_threshold=0.99).is_duplicate(
        same_fingerprint, [existing]
    )


def test_parser_handles_raw_text_and_ats_company_fallback() -> None:
    job = JobParser().parse(
        """
        AI Engineer
        Location: Remote US
        Salary range: $150k - $180k
        Requirements
        Python
        """,
        application_url="https://boards.greenhouse.io/exampleco/jobs/12345",
    )

    assert job.title == "AI Engineer"
    assert job.company == "Exampleco"
    assert job.location == "Remote US"
    assert job.salary_range is not None
    assert job.salary_range.minimum == 150_000
    assert job.requirements == ("Python",)


def test_parser_does_not_treat_hours_as_salary() -> None:
    assert parse_salary("Expected schedule is 30-40 hours per week.") is None
    assert parse_salary("Salary range: 140k-180k") is not None


def test_parser_skips_non_salary_ranges_before_salary() -> None:
    salary = parse_salary("Requires 10-15 years. Salary range: 140k-180k.")

    assert salary is not None
    assert salary.minimum == 140_000
    assert salary.maximum == 180_000


def test_parser_does_not_treat_bonus_as_salary() -> None:
    assert parse_salary("5-10 people. Annual bonus of 2k-3k.") is None
    assert parse_salary("Annual bonus of $2k-$3k.") is None


def test_parser_keeps_salary_after_nearby_equity() -> None:
    salary = parse_salary("Equity plus base salary $80k-$90k.")

    assert salary is not None
    assert salary.minimum == 80_000
    assert salary.maximum == 90_000


def test_parser_keeps_salary_before_following_bonus() -> None:
    salary = parse_salary("$140k-$180k base salary plus bonus.")

    assert salary is not None
    assert salary.minimum == 140_000
    assert salary.maximum == 180_000


def test_remote_inference_handles_negated_remote_text() -> None:
    assert infer_remote("Remote work is unavailable; this role is on-site.") is False
    assert infer_remote("Remote US role.") is True


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


def test_fit_scorer_uses_target_roles_without_overweighting_skills() -> None:
    """Verify target roles affect fit and skills do not dominate the score."""

    matching_role = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="1",
        title="Senior Backend Engineer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/exampleco/jobs/1",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/exampleco/jobs/1"),
        content="Build services.",
    )
    mismatched_role = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="2",
        title="Frontend Designer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/exampleco/jobs/2",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/exampleco/jobs/2"),
        content="Build Python services with Postgres and AWS.",
    )

    scorer = FitScorer()
    criteria = ScoringCriteria(
        target_roles=("Backend Engineer",),
        skills=("Python", "Postgres", "AWS"),
    )

    matching_score = scorer.score(matching_role, criteria)
    mismatched_score = scorer.score(mismatched_role, criteria)

    assert DEFAULT_WEIGHTS["required_skills"] == 0.14
    assert DEFAULT_WEIGHTS["role_match"] > DEFAULT_WEIGHTS["required_skills"]
    assert matching_score.components["role_match"] == 100
    assert mismatched_score.components["role_match"] == 25
    assert mismatched_score.components["required_skills"] == 100
    assert matching_score.total > mismatched_score.total


def test_fit_scorer_does_not_match_skill_substrings() -> None:
    job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="1",
        title="Developer Relations",
        company="Google",
        application_url="https://boards.greenhouse.io/google/jobs/1",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/google/jobs/1"),
        content="Work with Google Cloud customers and write C documentation.",
    )

    score = FitScorer().score(
        job,
        ScoringCriteria(skills=("Go", "C++")),
    )

    assert score.components["required_skills"] == 0


def test_fit_scorer_does_not_exact_match_role_substrings() -> None:
    """Verify short target roles do not exactly match unrelated title substrings."""

    retail_job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="1",
        title="Retail Engineer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/example/jobs/1",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/example/jobs/1"),
    )
    html_job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="2",
        title="HTML Developer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/example/jobs/2",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/example/jobs/2"),
    )

    ai_score = FitScorer().score(
        retail_job,
        ScoringCriteria(target_roles=("AI",)),
    )
    ml_score = FitScorer().score(
        html_job,
        ScoringCriteria(target_roles=("ML",)),
    )

    assert ai_score.components["role_match"] == 25
    assert ml_score.components["role_match"] == 25
