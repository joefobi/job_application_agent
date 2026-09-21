from pathlib import Path

import pytest

from job_application_agent.models import (
    CandidateProfile,
    JobPosting,
    JobSource,
    JobStatus,
)
from job_application_agent.sources import GreenhouseIngestor, LeverIngestor
from job_application_agent.storage import ApplicationLedger, ApplicationStore


def test_profile_round_trips_through_store(tmp_path: Path) -> None:
    """Verify candidate profile persistence preserves structured preferences."""
    store = ApplicationStore(tmp_path / "agent.db")
    store.initialize()
    profile = CandidateProfile(
        profile_id="primary",
        full_name="Jo Ann Efobi",
        email="jo@example.com",
        target_roles=("Backend Engineer", "AI Engineer"),
        preferred_locations=("Remote US",),
        remote_preference="remote",
        minimum_salary=120000,
        skills=("Python", "Postgres"),
        blocked_companies=("BadCo",),
    )

    store.save_profile(profile)

    assert store.get_profile("primary") == profile


def test_ledger_dedupes_exact_source_job(tmp_path: Path) -> None:
    """Verify repeated source jobs update the existing ledger row."""
    store = ApplicationStore(tmp_path / "agent.db")
    store.initialize()
    ledger = ApplicationLedger(store)
    job = _greenhouse_job()

    first_result = ledger.record_discovered_job(job)
    second_result = ledger.record_discovered_job(job)

    assert first_result.is_new is True
    assert first_result.status == JobStatus.DISCOVERED
    assert second_result.is_new is False
    assert second_result.job_id == first_result.job_id
    assert second_result.reason == "existing_source_or_url"


def test_ledger_flags_applied_fingerprint_duplicate(tmp_path: Path) -> None:
    """Verify applied company/title/location matches are flagged for review."""
    store = ApplicationStore(tmp_path / "agent.db")
    store.initialize()
    ledger = ApplicationLedger(store)
    applied = _greenhouse_job()
    applied_result = ledger.record_discovered_job(applied)
    store.mark_applied(applied_result.job_id, resume_version="backend-v1")

    duplicate = LeverIngestor("example", company_name="ExampleCo").parse_jobs(
        [
            {
                "id": "lever-123",
                "text": "Backend Engineer",
                "hostedUrl": "https://jobs.lever.co/example/lever-123",
                "categories": {
                    "location": "Remote US",
                    "department": "Engineering",
                    "commitment": "Full-time",
                },
            }
        ]
    )[0]
    duplicate_result = ledger.record_discovered_job(duplicate)

    assert duplicate_result.status == JobStatus.DUPLICATE_POSSIBLE
    assert duplicate_result.reason == "possible_duplicate_applied"
    with pytest.raises(ValueError, match="needs duplicate review"):
        ledger.assert_can_apply(duplicate_result.job_id)


def test_greenhouse_ingestor_parses_jobs() -> None:
    """Verify Greenhouse API payloads normalize into job postings."""
    job = _greenhouse_job()

    assert job.source == JobSource.GREENHOUSE
    assert job.source_job_id == "12345"
    assert job.title == "Backend Engineer"
    assert job.company == "ExampleCo"
    assert job.location == "Remote US"
    assert job.department == "Engineering"
    assert job.canonical_url == "https://boards.greenhouse.io/example/jobs/12345"


def test_lever_ingestor_parses_jobs() -> None:
    """Verify Lever API payloads normalize into job postings."""
    jobs = LeverIngestor("example", company_name="ExampleCo").parse_jobs(
        [
            {
                "id": "abc-123",
                "text": "AI Engineer",
                "hostedUrl": "https://jobs.lever.co/example/abc-123?lever-source=site",
                "categories": {
                    "location": "New York, NY",
                    "department": "AI",
                    "commitment": "Full-time",
                },
                "descriptionPlain": "Build useful agent systems.",
                "lists": [
                    {
                        "text": "Requirements",
                        "content": "Python, data pipelines, and product sense.",
                    }
                ],
            }
        ]
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == JobSource.LEVER
    assert job.source_job_id == "abc-123"
    assert job.title == "AI Engineer"
    assert job.company == "ExampleCo"
    assert job.location == "New York, NY"
    assert job.department == "AI"
    assert job.employment_type == "Full-time"
    assert job.canonical_url == "https://jobs.lever.co/example/abc-123"
    assert job.content is not None
    assert "Requirements" in job.content


def _greenhouse_job() -> JobPosting:
    """Return a normalized Greenhouse sample job."""

    jobs = GreenhouseIngestor("example", company_name="ExampleCo").parse_jobs(
        {
            "jobs": [
                {
                    "id": 12345,
                    "title": "Backend Engineer",
                    "absolute_url": (
                        "https://boards.greenhouse.io/example/jobs/12345"
                        "?gh_src=newsletter&utm_source=email"
                    ),
                    "location": {"name": "Remote US"},
                    "departments": [{"name": "Engineering"}],
                    "content": "Build backend systems.",
                }
            ]
        }
    )
    return jobs[0]
