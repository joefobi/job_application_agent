import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from job_application_agent.application_worker import ApplicationWorker
from job_application_agent.dashboard import DashboardService
from job_application_agent.decision import DecisionInput, DecisionPolicy
from job_application_agent.filters import FilterResult
from job_application_agent.form_fillers import GreenhouseFormFiller, LeverFormFiller
from job_application_agent.materials import MaterialRequest, TemplateMaterialGenerator
from job_application_agent.models import (
    CandidateProfile,
    FollowUpStatus,
    JobPosting,
    JobSource,
    JobStatus,
    ResumeVersion,
    ReusableAnswer,
)
from job_application_agent.normalization import canonicalize_url
from job_application_agent.scoring import ScoreBreakdown
from job_application_agent.storage import ApplicationLedger, ApplicationStore
from job_application_agent.tracking import (
    FollowUpTracker,
    SubmissionConfirmationRecorder,
    SubmissionConfirmationRequest,
)


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


def test_decision_policy_preserves_progressed_status() -> None:
    """Verify progressed jobs are not reclassified by later scoring."""
    result = DecisionPolicy().decide(
        DecisionInput(
            job_id=1,
            ledger_status=JobStatus.STARTED_APPLICATION,
            filter_result=FilterResult(passed=False, reasons=("Remote mismatch.",)),
            score_breakdown=ScoreBreakdown(
                total=0,
                components={},
                explanations=("Rejected by hard filter.",),
                rejected_by_hard_filter=True,
            ),
        )
    )

    assert result.target_status == JobStatus.STARTED_APPLICATION
    assert "status_already_progressed" in result.reason_codes


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


def test_dashboard_rejects_applied_status_without_submission(tmp_path: Path) -> None:
    """Verify dashboard actions cannot bypass application submission records."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    dashboard = DashboardService(store)

    with pytest.raises(ValueError, match="mark_applied"):
        dashboard.update_status(job_id, JobStatus.APPLIED)


def test_dashboard_does_not_regress_applied_jobs(tmp_path: Path) -> None:
    """Verify dashboard actions cannot regress already-applied jobs."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    store.mark_applied(job_id)
    dashboard = DashboardService(store)

    with pytest.raises(ValueError, match="already applied"):
        dashboard.update_status(job_id, JobStatus.NEEDS_REVIEW)


def test_status_update_rejects_stale_current_status(tmp_path: Path) -> None:
    """Verify atomic status updates cannot overwrite a concurrent apply."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    store.mark_applied(job_id)

    with pytest.raises(ValueError, match="no longer has status"):
        store.update_job_status_with_event(
            job_id,
            JobStatus.NEEDS_REVIEW,
            "dashboard_status_updated",
            {"status": JobStatus.NEEDS_REVIEW.value},
            expected_current_status=JobStatus.DISCOVERED,
        )

    row = store.get_job(job_id)
    assert row is not None
    assert JobStatus(str(row["status"])) == JobStatus.APPLIED


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


def test_form_filler_rejects_mismatched_stored_job_identity(tmp_path: Path) -> None:
    """Verify form plans cannot pair one stored job ID with another posting."""
    _store, ledger, job_id = _store_with_job(tmp_path, source=JobSource.GREENHOUSE)
    profile = _profile()
    other_job = JobPosting(
        source=JobSource.GREENHOUSE,
        source_job_id="greenhouse-2",
        title="Backend Engineer",
        company="ExampleCo",
        location="Remote US",
        application_url="https://boards.greenhouse.io/example/jobs/greenhouse-2",
        canonical_url=canonicalize_url(
            "https://boards.greenhouse.io/example/jobs/greenhouse-2"
        ),
        content="Build backend systems.",
    )
    materials = TemplateMaterialGenerator().generate(
        MaterialRequest(profile=profile, job=other_job)
    )

    with pytest.raises(ValueError, match="does not match stored job"):
        GreenhouseFormFiller(ledger).plan(
            job_id=job_id,
            job=other_job,
            profile=profile,
            materials=materials,
        )


def test_application_worker_prepares_approved_job_before_submission(
    tmp_path: Path,
) -> None:
    """Verify approved jobs move through material and form planning first."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    profile = _profile()
    DashboardService(store).update_status(job_id, JobStatus.APPROVED_TO_APPLY)
    worker = ApplicationWorker(store)

    assert worker.approved_job_ids() == (job_id,)

    preparation = worker.prepare_next(profile)

    assert preparation is not None
    assert preparation.status == JobStatus.STARTED_APPLICATION
    assert preparation.materials.resume_version == "backend-v1"
    assert "Backend Engineer" in preparation.materials.cover_letter_text
    assert preparation.form_result.plan.provider == "greenhouse"
    assert preparation.form_result.plan.stop_before_submit is True
    assert preparation.requires_user_approval is True
    row = store.get_job(job_id)
    assert row is not None
    assert JobStatus(str(row["status"])) == JobStatus.STARTED_APPLICATION
    with store.connect() as connection:
        application = connection.execute(
            "SELECT * FROM applications WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert application is None


def test_application_worker_marks_applied_only_after_confirmation(
    tmp_path: Path,
) -> None:
    """Verify submission confirmation is required before marking applied."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    profile = _profile()
    worker = ApplicationWorker(store)

    with pytest.raises(ValueError, match="worker starts"):
        worker.confirm_submission(
            SubmissionConfirmationRequest(
                job_id=job_id,
                confirmation_number="ABC123",
                provider="greenhouse",
            )
        )

    DashboardService(store).update_status(job_id, JobStatus.APPROVED_TO_APPLY)
    preparation = worker.prepare_job(job_id, profile)
    confirmation = worker.confirm_submission(
        SubmissionConfirmationRequest(
            job_id=job_id,
            applied_at="2026-09-21T10:30:00+00:00",
            resume_version=preparation.materials.resume_version,
            cover_letter_version=preparation.materials.cover_letter_version,
            confirmation_number="ABC123",
            provider=preparation.form_result.plan.provider,
        )
    )

    row = store.get_job(job_id)
    assert row is not None
    assert confirmation.status == JobStatus.APPLIED
    assert JobStatus(str(row["status"])) == JobStatus.APPLIED


def test_submission_confirmation_recorder_marks_job_applied(tmp_path: Path) -> None:
    """Verify submitted applications are durably recorded with confirmation data."""
    store, _ledger, job_id = _store_with_job(tmp_path)

    confirmation = SubmissionConfirmationRecorder(store).record(
        SubmissionConfirmationRequest(
            job_id=job_id,
            applied_at="2026-09-21T10:30:00+00:00",
            resume_version="backend-v1",
            cover_letter_version="exampleco-2026-09-21",
            confirmation_number="ABC123",
            confirmation_url="https://boards.greenhouse.io/example/applications/ABC123",
            provider="greenhouse",
            notes="Submitted through Greenhouse.",
        )
    )

    job_row = store.get_job(job_id)
    assert confirmation.status == JobStatus.APPLIED
    assert confirmation.confirmation_number == "ABC123"
    assert job_row is not None
    assert JobStatus(str(job_row["status"])) == JobStatus.APPLIED
    with store.connect() as connection:
        row = connection.execute(
            "SELECT * FROM applications WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert row is not None
    assert str(row["provider"]) == "greenhouse"
    assert str(row["confirmation_url"]).endswith("/ABC123")


def test_submission_confirmation_retry_preserves_original_evidence(
    tmp_path: Path,
) -> None:
    """Verify repeated confirmation recording does not erase original evidence."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    recorder = SubmissionConfirmationRecorder(store)

    original = recorder.record(
        SubmissionConfirmationRequest(
            job_id=job_id,
            applied_at="2026-09-21T10:30:00+00:00",
            confirmation_number="ABC123",
            provider="greenhouse",
        )
    )
    retry = recorder.record(
        SubmissionConfirmationRequest(
            job_id=job_id,
            applied_at="2026-09-22T10:30:00+00:00",
            confirmation_number=None,
            provider=None,
        )
    )

    assert retry == original
    with store.connect() as connection:
        application_count = connection.execute(
            "SELECT COUNT(*) AS count FROM applications WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        event_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM job_events
            WHERE job_id = ? AND event_type = ?
            """,
            (job_id, "application_submission_confirmed"),
        ).fetchone()
    assert application_count is not None
    assert event_count is not None
    assert int(application_count["count"]) == 1
    assert int(event_count["count"]) == 1


def test_follow_up_tracker_schedules_lists_and_completes_reminders(
    tmp_path: Path,
) -> None:
    """Verify submitted applications can get follow-up reminders."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    confirmation = SubmissionConfirmationRecorder(store).record(
        SubmissionConfirmationRequest(
            job_id=job_id,
            applied_at="2026-09-21T10:30:00+00:00",
            provider="greenhouse",
        )
    )
    tracker = FollowUpTracker(store)

    reminder = tracker.schedule_after_submission(confirmation)
    due = tracker.list_due(now="2026-09-28T10:30:00+00:00")
    dashboard_rows = DashboardService(store).list_follow_ups(
        due_at_or_before="2026-09-28T10:30:00+00:00"
    )
    completed = tracker.complete(
        reminder.reminder_id,
        completed_at="2026-09-28T11:00:00+00:00",
    )

    assert reminder.due_at == "2026-09-28T10:30:00+00:00"
    assert due == [reminder]
    assert len(dashboard_rows) == 1
    assert dashboard_rows[0].reminder_id == reminder.reminder_id
    assert completed.status == FollowUpStatus.COMPLETED
    assert completed.completed_at == "2026-09-28T11:00:00+00:00"
    assert tracker.list_due(now="2026-09-29T00:00:00+00:00") == []


def test_follow_up_due_listing_normalizes_timezone_offsets(tmp_path: Path) -> None:
    """Verify due reminder filtering compares normalized instants, not strings."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    tracker = FollowUpTracker(store)
    tracker.schedule(
        job_id=job_id,
        due_at="2026-09-28T09:00:00-04:00",
        message="Actually due at 13:00 UTC.",
    )

    assert tracker.list_due(now="2026-09-28T12:00:00+00:00") == []
    assert len(tracker.list_due(now="2026-09-28T13:00:00+00:00")) == 1


def test_follow_up_completion_is_idempotent(tmp_path: Path) -> None:
    """Verify repeated completion preserves original completion history."""
    store, _ledger, job_id = _store_with_job(tmp_path)
    tracker = FollowUpTracker(store)
    reminder = tracker.schedule(
        job_id=job_id,
        due_at="2026-09-28T10:30:00+00:00",
    )

    first = tracker.complete(
        reminder.reminder_id,
        completed_at="2026-09-28T11:00:00+00:00",
    )
    second = tracker.complete(
        reminder.reminder_id,
        completed_at="2026-09-29T11:00:00+00:00",
    )

    assert second == first
    with store.connect() as connection:
        event_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM job_events
            WHERE job_id = ? AND event_type = ?
            """,
            (job_id, "follow_up_completed"),
        ).fetchone()
    assert event_count is not None
    assert int(event_count["count"]) == 1


def test_schema_upgrade_tolerates_duplicate_column_race(tmp_path: Path) -> None:
    """Verify duplicate-column races do not break store initialization."""
    store = ApplicationStore(tmp_path / "agent.db")
    connection = Mock()
    pragma_cursor = Mock()
    pragma_cursor.fetchall.return_value = []
    connection.execute.side_effect = [
        pragma_cursor,
        sqlite3.OperationalError("duplicate column name: provider"),
    ]

    store._ensure_column(connection, "applications", "provider", "TEXT")


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
