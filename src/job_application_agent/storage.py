"""SQLite storage and application ledger services."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from job_application_agent.models import (
    CandidateProfile,
    JobPosting,
    JobStatus,
    LedgerResult,
    utc_now_iso,
)
from job_application_agent.normalization import make_job_fingerprint


class ApplicationStore:
    """SQLite-backed store for profiles, jobs, applications, and events."""

    def __init__(self, database_path: str | Path) -> None:
        """Create a store for a SQLite database path.

        Args:
            database_path: Filesystem path for the SQLite database. Use
                `":memory:"` for an in-memory store.
        """

        self.database_path = str(database_path)

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection.

        Returns:
            A SQLite connection with row dictionaries enabled.
        """

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create the database schema when it does not already exist."""

        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    profile_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_job_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT,
                    department TEXT,
                    employment_type TEXT,
                    application_url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    content TEXT,
                    raw_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source, source_job_id),
                    UNIQUE(canonical_url)
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint
                ON jobs(fingerprint);

                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    applied_at TEXT,
                    resume_version TEXT,
                    cover_letter_version TEXT,
                    confirmation_number TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                """
            )

    def save_profile(self, profile: CandidateProfile) -> None:
        """Insert or update a candidate profile.

        Args:
            profile: Candidate profile to store.
        """

        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO candidate_profiles (
                    profile_id, data_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.profile_id,
                    json.dumps(profile.to_json_dict(), sort_keys=True),
                    now,
                    now,
                ),
            )

    def get_profile(self, profile_id: str) -> CandidateProfile | None:
        """Return a stored profile by ID.

        Args:
            profile_id: Profile identifier.

        Returns:
            The profile, or None when it has not been stored.
        """

        with self.connect() as connection:
            row = connection.execute(
                "SELECT data_json FROM candidate_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()

        if row is None:
            return None
        data = json.loads(str(row["data_json"]))
        if not isinstance(data, dict):
            raise ValueError("Stored profile JSON must be an object.")
        return CandidateProfile.from_json_dict(data)

    def find_job_by_identity(self, job: JobPosting) -> sqlite3.Row | None:
        """Find an existing job by exact source ID or canonical URL.

        Args:
            job: Job posting to check.

        Returns:
            A matching job row, or None.
        """

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE (source = ? AND source_job_id = ?)
                   OR canonical_url = ?
                ORDER BY id
                LIMIT 1
                """,
                (job.source.value, job.source_job_id, job.canonical_url),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def find_applied_by_fingerprint(self, job: JobPosting) -> sqlite3.Row | None:
        """Find an applied job with the same company/title/location fingerprint.

        Args:
            job: Job posting to check.

        Returns:
            A matching applied job row, or None.
        """

        fingerprint = make_job_fingerprint(job.company, job.title, job.location)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT jobs.*
                FROM jobs
                JOIN applications ON applications.job_id = jobs.id
                WHERE jobs.fingerprint = ?
                  AND applications.status = ?
                ORDER BY jobs.id
                LIMIT 1
                """,
                (fingerprint, JobStatus.APPLIED.value),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def upsert_discovered_job(self, job: JobPosting, status: JobStatus) -> int:
        """Insert or update a discovered job.

        Args:
            job: Normalized job posting.
            status: Status to write when the job is newly created.

        Returns:
            The database ID for the job.
        """

        now = utc_now_iso()
        fingerprint = make_job_fingerprint(job.company, job.title, job.location)
        raw_json = json.dumps(job.raw_data, sort_keys=True)
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, status FROM jobs
                WHERE (source = ? AND source_job_id = ?)
                   OR canonical_url = ?
                ORDER BY id
                LIMIT 1
                """,
                (job.source.value, job.source_job_id, job.canonical_url),
            ).fetchone()
            if existing is not None:
                job_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE jobs
                    SET title = ?,
                        company = ?,
                        location = ?,
                        department = ?,
                        employment_type = ?,
                        application_url = ?,
                        canonical_url = ?,
                        fingerprint = ?,
                        content = ?,
                        raw_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        job.title,
                        job.company,
                        job.location,
                        job.department,
                        job.employment_type,
                        job.application_url,
                        job.canonical_url,
                        fingerprint,
                        job.content,
                        raw_json,
                        now,
                        job_id,
                    ),
                )
                return job_id

            cursor = connection.execute(
                """
                INSERT INTO jobs (
                    source,
                    source_job_id,
                    title,
                    company,
                    location,
                    department,
                    employment_type,
                    application_url,
                    canonical_url,
                    fingerprint,
                    content,
                    raw_json,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.source.value,
                    job.source_job_id,
                    job.title,
                    job.company,
                    job.location,
                    job.department,
                    job.employment_type,
                    job.application_url,
                    job.canonical_url,
                    fingerprint,
                    job.content,
                    raw_json,
                    status.value,
                    now,
                    now,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a job ID.")
            return cursor.lastrowid

    def update_job_status(self, job_id: int, status: JobStatus) -> None:
        """Update the current status of a job.

        Args:
            job_id: Database ID for the job.
            status: New job lifecycle status.
        """

        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, utc_now_iso(), job_id),
            )

    def update_job_status_with_event(
        self,
        job_id: int,
        status: JobStatus,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        """Update a job status and append its audit event atomically.

        Args:
            job_id: Database ID for the job.
            status: New job lifecycle status.
            event_type: Machine-readable audit event name.
            details: Event payload.
        """

        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, job_id),
            )
            connection.execute(
                """
                INSERT INTO job_events (job_id, event_type, details_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, event_type, json.dumps(details, sort_keys=True), now),
            )

    def mark_applied(
        self,
        job_id: int,
        *,
        resume_version: str | None = None,
        cover_letter_version: str | None = None,
        confirmation_number: str | None = None,
        notes: str | None = None,
    ) -> None:
        """Record a confirmed application submission.

        Args:
            job_id: Database ID for the job.
            resume_version: Resume version submitted, if known.
            cover_letter_version: Cover letter version submitted, if known.
            confirmation_number: Confirmation number from the ATS, if any.
            notes: Additional submission notes.
        """

        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO applications (
                    job_id,
                    status,
                    applied_at,
                    resume_version,
                    cover_letter_version,
                    confirmation_number,
                    notes,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    applied_at = excluded.applied_at,
                    resume_version = excluded.resume_version,
                    cover_letter_version = excluded.cover_letter_version,
                    confirmation_number = excluded.confirmation_number,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    JobStatus.APPLIED.value,
                    now,
                    resume_version,
                    cover_letter_version,
                    confirmation_number,
                    notes,
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (JobStatus.APPLIED.value, now, job_id),
            )

    def record_event(
        self, event_type: str, details: dict[str, Any], job_id: int | None = None
    ) -> None:
        """Append an audit event.

        Args:
            event_type: Machine-readable event name.
            details: Event payload.
            job_id: Optional job ID associated with the event.
        """

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO job_events (job_id, event_type, details_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    job_id,
                    event_type,
                    json.dumps(details, sort_keys=True),
                    utc_now_iso(),
                ),
            )

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        """Return a job row by database ID."""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def list_jobs(self, status: JobStatus | None = None) -> list[sqlite3.Row]:
        """Return stored jobs, optionally filtered by status.

        Args:
            status: Optional lifecycle status to filter by.

        Returns:
            Job rows sorted by newest first.
        """

        with self.connect() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY id DESC",
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC",
                    (status.value,),
                ).fetchall()
        return [cast(sqlite3.Row, row) for row in rows]


class ApplicationLedger:
    """High-level ledger operations for ingestion and duplicate prevention."""

    def __init__(self, store: ApplicationStore) -> None:
        """Create a ledger over an initialized application store."""

        self.store = store

    def record_discovered_job(self, job: JobPosting) -> LedgerResult:
        """Record a discovered job and run deterministic duplicate checks.

        Args:
            job: Normalized job posting from an ingestion source.

        Returns:
            A result containing the persisted job ID and dedupe outcome.
        """

        existing = self.store.find_job_by_identity(job)
        if existing is not None:
            job_id = self.store.upsert_discovered_job(
                job, JobStatus(str(existing["status"]))
            )
            status = JobStatus(str(existing["status"]))
            reason = "existing_source_or_url"
            if status == JobStatus.APPLIED:
                reason = "already_applied"
            self.store.record_event(
                "job_seen_again",
                {"reason": reason, "source": job.source.value},
                job_id,
            )
            return LedgerResult(
                job_id=job_id,
                status=status,
                is_new=False,
                reason=reason,
            )

        applied_duplicate = self.store.find_applied_by_fingerprint(job)
        status = (
            JobStatus.DUPLICATE_POSSIBLE
            if applied_duplicate is not None
            else JobStatus.DISCOVERED
        )
        job_id = self.store.upsert_discovered_job(job, status)
        reason = "possible_duplicate_applied" if applied_duplicate else "new_job"
        self.store.record_event(
            "job_discovered",
            {"reason": reason, "source": job.source.value},
            job_id,
        )
        return LedgerResult(
            job_id=job_id,
            status=status,
            is_new=True,
            reason=reason,
        )

    def assert_can_apply(self, job_id: int) -> None:
        """Raise when a job cannot proceed to application.

        Args:
            job_id: Database ID for the job to check.

        Raises:
            ValueError: If the job is missing, already applied, or a possible
                duplicate that requires review.
        """

        row = self.store.get_job(job_id)
        if row is None:
            raise ValueError(f"Unknown job ID: {job_id}")
        status = JobStatus(str(row["status"]))
        if status == JobStatus.APPLIED:
            raise ValueError(f"Job {job_id} was already applied to.")
        if status == JobStatus.DUPLICATE_POSSIBLE:
            raise ValueError(f"Job {job_id} needs duplicate review before applying.")
