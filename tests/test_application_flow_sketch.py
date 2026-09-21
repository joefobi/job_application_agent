from pathlib import Path

import pytest

from job_application_agent.dashboard import DashboardService
from job_application_agent.decision import DecisionInput, DecisionPolicy
from job_application_agent.filters import FilterResult
from job_application_agent.form_fillers import GreenhouseFormFiller, LeverFormFiller
from job_application_agent.materials import MaterialRequest, TemplateMaterialGenerator
from job_application_agent.models import (
    CandidateProfile,
    JobPosting,
    JobSource,
    JobStatus,
    ResumeVersion,
    ReusableAnswer,
)
from job_application_agent.normalization import canonicalize_url
from job_application_agent.scoring import ScoreBreakdown
from job_application_agent.storage import ApplicationLedger, ApplicationStore


def test_decision_policy_approves_high_score() -> None:
    """Verify the sketch policy maps a strong job to approved_to_apply."""
    result = DecisionPolicy().decide(
        DecisionInput(
            job_id=1,
            ledger_status=JobStatus.DISCOVERED,
            filter_result=FilterResult(passed=True),
            score_breakdown=ScoreBreakdown(
                total=91,
                components={"skills": 91},
                explanations=("Strong match.",),
            ),
        )
    )

    assert result.target_status == JobStatus.APPROVED_TO_APPLY
    assert result.reason_codes == ("score_above_approve_threshold",)


def test_decision_policy_sends_duplicate_to_review() -> None:
    """Verify duplicate state wins over scoring."""
    result = DecisionPolicy().decide(
        DecisionInput(
            job_id=1,
            ledger_status=JobStatus.DUPLICATE_POSSIBLE,
            filter_result=FilterResult(passed=True),
            score_breakdown=ScoreBreakdown(
                total=99,
                components={"skills": 99},
                explanations=("Strong match.",),
            ),
        )
    )

    assert result.target_status == JobStatus.NEEDS_REVIEW
    assert "duplicate_possible" in result.reason_codes


def test_dashboard_lists_and_updates_jobs(tmp_path: Path) -> None:
    """Verify the dashboard sketch can list and update stored jobs."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    dashboard = DashboardService(store)

    rows = dashboard.list_jobs()
    dashboard.update_status(job_id, JobStatus.NEEDS_REVIEW)
    detail = dashboard.get_job_detail(job_id)

    assert len(rows) == 1
    assert rows[0].title == "Backend Engineer"
    assert detail is not None
    assert detail.status == JobStatus.NEEDS_REVIEW


def test_template_material_generator_uses_only_profile_facts() -> None:
    """Verify the placeholder material generator produces review-required output."""
    profile = _profile()
    job = _job(JobSource.GREENHOUSE)

    bundle = TemplateMaterialGenerator().generate(
        MaterialRequest(profile=profile, job=job, selected_resume_version=None)
    )

    assert bundle.resume_version == "backend-v1"
    assert bundle.requires_review is True
    assert "Backend Engineer" in bundle.cover_letter_text
    assert bundle.short_answers == {"linkedin_url": "https://linkedin.example/jo"}


def test_greenhouse_form_filler_returns_reviewable_plan(tmp_path: Path) -> None:
    """Verify Greenhouse filler sketch plans fields and stops before submit."""
    _store, ledger, job_id = _store_with_job(tmp_path, source=JobSource.GREENHOUSE)
    profile = _profile()
    job = _job(JobSource.GREENHOUSE)
    materials = TemplateMaterialGenerator().generate(
        MaterialRequest(profile=profile, job=job)
    )

    result = GreenhouseFormFiller(ledger).plan(
        job_id=job_id,
        job=job,
        profile=profile,
        materials=materials,
    )

    assert result.ready_for_user_review is True
    assert result.plan.provider == "greenhouse"
    assert result.plan.stop_before_submit is True


def test_lever_form_filler_rejects_wrong_provider(tmp_path: Path) -> None:
    """Verify provider-specific fillers do not accept the wrong source."""
    _store, ledger, job_id = _store_with_job(tmp_path, source=JobSource.GREENHOUSE)
    profile = _profile()
    job = _job(JobSource.GREENHOUSE)
    materials = TemplateMaterialGenerator().generate(
        MaterialRequest(profile=profile, job=job)
    )

    with pytest.raises(ValueError, match="LeverFormFiller only accepts Lever"):
        LeverFormFiller(ledger).plan(
            job_id=job_id,
            job=job,
            profile=profile,
            materials=materials,
        )


def _store_with_job(
    tmp_path: Path, source: JobSource = JobSource.GREENHOUSE
) -> tuple[ApplicationStore, ApplicationLedger, int]:
    """Create a temporary store containing one discovered job."""

    store = ApplicationStore(tmp_path / "agent.db")
    store.initialize()
    ledger = ApplicationLedger(store)
    result = ledger.record_discovered_job(_job(source))
    return store, ledger, result.job_id


def _profile() -> CandidateProfile:
    """Return a sample candidate profile."""

    return CandidateProfile(
        profile_id="primary",
        full_name="Jo Ann Efobi",
        email="jo@example.com",
        phone="555-0100",
        location="Remote US",
        skills=("Python", "Postgres"),
        resume_versions=(
            ResumeVersion(
                version_id="backend-v1",
                label="Backend resume",
                file_path="resumes/backend.pdf",
                is_default=True,
            ),
        ),
        reusable_answers=(
            ReusableAnswer(
                question_key="linkedin_url",
                answer="https://linkedin.example/jo",
                requires_review=False,
            ),
            ReusableAnswer(
                question_key="requires_sponsorship",
                answer="No",
                requires_review=True,
            ),
        ),
    )


def _job(source: JobSource) -> JobPosting:
    """Return a sample job for a provider."""

    source_job_id = "greenhouse-1" if source == JobSource.GREENHOUSE else "lever-1"
    url = (
        "https://boards.greenhouse.io/example/jobs/greenhouse-1"
        if source == JobSource.GREENHOUSE
        else "https://jobs.lever.co/example/lever-1"
    )
    return JobPosting(
        source=source,
        source_job_id=source_job_id,
        title="Backend Engineer",
        company="ExampleCo",
        location="Remote US",
        application_url=url,
        canonical_url=canonicalize_url(url),
        content="Build backend systems.",
    )
