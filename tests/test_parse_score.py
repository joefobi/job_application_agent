from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

import pytest

from job_application_agent import (
    DeduplicationService,
    ExtractedJobFacts,
    FitScorer,
    HardFilterCriteria,
    HardFilterService,
    JobParser,
    JobSource,
    LLMJobFactExtractor,
    ScoringCriteria,
)
from job_application_agent.extraction import OpenAIResponsesJsonClient, _response_json
from job_application_agent.models import JobPosting
from job_application_agent.normalization import canonicalize_url
from job_application_agent.parser import (
    infer_remote,
    parse_minimum_years_experience,
    parse_salary,
)
from job_application_agent.scoring import ROLE_RELEVANCE_COMPONENT


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


def test_parser_decodes_escaped_html_content() -> None:
    payload: dict[str, Any] = {
        "id": 12345,
        "title": "Platform Lead",
        "company": "ExampleCo",
        "absolute_url": "https://boards.greenhouse.io/example/jobs/12345",
        "content": (
            "&lt;div class=&quot;content-intro&quot;&gt;"
            "&lt;h2&gt;&lt;strong&gt;About the Company&lt;/strong&gt;&lt;/h2&gt;"
            "&lt;p&gt;Build reliable customer-facing systems.&lt;/p&gt;"
            "&lt;/div&gt;"
        ),
    }

    job = JobParser().parse(payload)

    assert job.content == (
        "About the Company\n" "Build reliable customer-facing systems."
    )
    assert "&lt;" not in job.content


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


def test_parser_extracts_minimum_years_of_experience() -> None:
    """Verify experience requirements are parsed without confusing salary ranges."""

    assert (
        parse_minimum_years_experience(
            "Qualifications: 5+ years of professional experience building APIs."
        )
        == 5
    )
    assert (
        parse_minimum_years_experience(
            "Requires 3-5 years experience with production systems."
        )
        == 3
    )
    assert (
        parse_minimum_years_experience("Requires 10-15 years. Salary range: 140k-180k.")
        is None
    )


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


def test_llm_job_fact_extractor_converts_json_to_structured_facts() -> None:
    """Verify LLM JSON output becomes structured extracted job facts."""

    job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="1",
        title="Backend Engineer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/exampleco/jobs/1",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/exampleco/jobs/1"),
        content="Build APIs.",
    )

    facts = LLMJobFactExtractor(
        _FakeJsonClient(
            {
                "minimum_years_experience": 4,
                "salary_range": {"minimum": 150000, "maximum": 180000},
                "remote_policy": "remote",
                "locations": ["United States"],
                "requires_us_work_authorization": True,
                "visa_sponsorship": "not_available",
                "required_skills": ["Python"],
                "responsibilities": ["Build backend APIs"],
                "company_description": "ExampleCo builds tools.",
                "evidence": {"minimum_years_experience": "4+ years"},
            }
        )
    ).extract(job)

    assert facts.minimum_years_experience == 4
    assert facts.salary_range is not None
    assert facts.salary_range.minimum == 150000
    assert facts.remote_policy == "remote"
    assert facts.required_skills == ("Python",)
    assert facts.company_description == "ExampleCo builds tools."


def test_openai_json_client_raises_on_request_failure(monkeypatch: Any) -> None:
    """Verify failed LLM requests flow into the extractor fallback path."""

    def fail_urlopen(request: Any, *, timeout: float) -> Any:
        """Raise a URL error for the fake HTTP request.

        Args:
            request: Ignored request object.
            timeout: Ignored timeout.

        Returns:
            This helper never returns.
        """

        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(ValueError, match="LLM request failed"):
        OpenAIResponsesJsonClient("test-key").complete_json("prompt")


def test_openai_json_client_raises_on_missing_response_text() -> None:
    """Verify unrecognized LLM response shapes do not persist empty facts."""

    with pytest.raises(ValueError, match="JSON output text"):
        _response_json({})


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
            target_roles=("Backend Engineer",),
            preferred_locations=("Remote",),
            authorized_work_regions=("US",),
        ),
    )

    assert score.rejected_by_hard_filter is False
    assert score.total > 85
    assert score.components[ROLE_RELEVANCE_COMPONENT] == score.total

    rejected = scorer.score(
        job,
        ScoringCriteria(
            skills=("Python",),
            hard_filters=HardFilterCriteria(excluded_companies=("ExampleCo",)),
        ),
    )

    assert rejected.rejected_by_hard_filter is True
    assert rejected.total == 0


def test_fit_scorer_keeps_preferred_locations_soft() -> None:
    """Verify location preferences are not treated as hard constraints."""

    job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="1",
        title="Backend Engineer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/exampleco/jobs/1",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/exampleco/jobs/1"),
        location="Austin, TX",
    )
    facts = ExtractedJobFacts(
        remote_policy="onsite",
        locations=("Austin, TX",),
        responsibilities=("Build backend APIs.",),
    )

    score = FitScorer().score(
        job,
        ScoringCriteria(
            target_roles=("Backend Engineer",),
            preferred_locations=("New York, NY",),
        ),
        facts,
    )

    assert score.rejected_by_hard_filter is False
    assert score.components[ROLE_RELEVANCE_COMPONENT] == score.total


def test_fit_scorer_uses_target_roles_without_overweighting_skills() -> None:
    """Verify role relevance is driven by target roles, not skill mentions."""

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

    assert matching_score.components[ROLE_RELEVANCE_COMPONENT] == matching_score.total
    assert (
        mismatched_score.components[ROLE_RELEVANCE_COMPONENT] == mismatched_score.total
    )
    assert matching_score.total > mismatched_score.total


def test_fit_scorer_does_not_match_role_substrings() -> None:
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
        ScoringCriteria(target_roles=("Go Engineer",)),
    )

    assert score.components[ROLE_RELEVANCE_COMPONENT] == 0


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

    assert ai_score.components[ROLE_RELEVANCE_COMPONENT] == 0
    assert ml_score.components[ROLE_RELEVANCE_COMPONENT] == 0


def test_fit_scorer_rejects_jobs_above_candidate_experience() -> None:
    """Verify years of experience is enforced as a hard matching constraint."""

    job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="1",
        title="Backend Engineer",
        company="ExampleCo",
        application_url="https://boards.greenhouse.io/example/jobs/1",
        canonical_url=canonicalize_url("https://boards.greenhouse.io/example/jobs/1"),
        minimum_years_experience=5,
    )

    facts = ExtractedJobFacts(minimum_years_experience=5)

    score = FitScorer().score(job, ScoringCriteria(years_experience=3), facts)

    assert score.rejected_by_hard_filter is True
    assert score.total == 0
    assert score.explanations == (
        "Role requires more years of experience than the candidate has.",
    )


class _FakeJsonClient:
    """Fake JSON LLM client for extraction tests."""

    def __init__(self, response: Mapping[str, Any]) -> None:
        """Create a fake client.

        Args:
            response: JSON object to return.
        """

        self.response = response

    def complete_json(self, prompt: str) -> Mapping[str, Any]:
        """Return the configured fake JSON object.

        Args:
            prompt: Ignored extraction prompt.

        Returns:
            Configured JSON object.
        """

        return self.response
