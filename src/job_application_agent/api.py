"""Local REST API for the job application agent frontend."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from job_application_agent.models import (
    CandidateProfile,
    JobPosting,
    JobSource,
    JobStatus,
)
from job_application_agent.normalization import canonicalize_url
from job_application_agent.scoring import FitScorer, ScoringCriteria
from job_application_agent.storage import ApplicationLedger, ApplicationStore

DEFAULT_DATABASE_PATH = ".context/local-agent.db"
PROFILE_ID = "primary"


class FrontendProfile(BaseModel):
    """Profile shape used by the React frontend."""

    full_name: str = Field(alias="fullName")
    email: str
    phone: str = ""
    target_roles: list[str] = Field(default_factory=list, alias="targetRoles")
    location_preference: str = Field(default="", alias="locationPreference")
    salary_range: str = Field(default="", alias="salaryRange")
    work_authorization: str = Field(default="", alias="workAuthorization")
    skills: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class StatusUpdate(BaseModel):
    """Request body for dashboard status changes."""

    status: JobStatus


def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Create the local REST API application.

    Args:
        database_path: Optional SQLite path. Defaults to `.context/local-agent.db`.

    Returns:
        Configured FastAPI app.
    """

    app = FastAPI(title="Job Application Agent API")
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=False,
        allow_headers=["*"],
        allow_methods=["*"],
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    )

    @app.get("/")
    def root() -> dict[str, Any]:
        """Return a friendly API status for browser visits."""

        return {
            "status": "ok",
            "message": "Job Application Agent API is running.",
            "docs": "/docs",
            "dashboard": "/api/dashboard",
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        """Return a simple health check response."""

        return {"status": "ok"}

    @app.get("/api/profile", response_model=FrontendProfile)
    def get_profile(request: Request) -> FrontendProfile:
        """Return the saved profile for the local user."""

        store = _request_store(request, database_path)
        profile = store.get_profile(PROFILE_ID)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile has not been saved.")
        return _to_frontend_profile(profile)

    @app.put("/api/profile", response_model=FrontendProfile)
    def put_profile(request: Request, profile: FrontendProfile) -> FrontendProfile:
        """Persist the local user's profile."""

        store = _request_store(request, database_path)
        domain_profile = _to_domain_profile(profile)
        store.save_profile(domain_profile)
        return _to_frontend_profile(domain_profile)

    @app.get("/api/dashboard")
    def get_dashboard(request: Request) -> dict[str, Any]:
        """Return jobs, duplicate candidates, and application ledger rows."""

        store = _request_store(request, database_path)
        return _dashboard_payload(store)

    @app.post("/api/discovery/runs", status_code=204)
    def post_discovery_run(request: Request) -> None:
        """Run local discovery and store matching sample jobs."""

        store = _request_store(request, database_path)
        profile = store.get_profile(PROFILE_ID)
        if profile is None:
            raise HTTPException(
                status_code=409,
                detail="Save your profile before running discovery.",
            )
        ledger = ApplicationLedger(store)
        for job in _sample_jobs(profile):
            result = ledger.record_discovered_job(job)
            if result.status == JobStatus.DISCOVERED:
                score = FitScorer().score(job, _scoring_criteria(profile))
                next_status = (
                    JobStatus.NEEDS_REVIEW if score.total >= 55 else JobStatus.REJECTED
                )
                store.update_job_status(result.job_id, next_status)

    @app.patch("/api/jobs/{job_id}/status", status_code=204)
    def patch_job_status(request: Request, job_id: int, update: StatusUpdate) -> None:
        """Update one job's status from the dashboard."""

        store = _request_store(request, database_path)
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        if update.status == JobStatus.APPLIED:
            store.record_submission_confirmation(
                job_id,
                provider="local_dashboard",
                notes="Marked applied from the local dashboard.",
            )
            return
        store.update_job_status(job_id, update.status)

    return app


def _request_store(
    request: Request,
    database_path: str | Path | None,
) -> ApplicationStore:
    """Create a store scoped to the request account."""

    account_key = _account_key(request.headers.get("authorization"))
    return _store(database_path, account_key)


def _store(database_path: str | Path | None, account_key: str) -> ApplicationStore:
    """Create and initialize the SQLite store."""

    path_value: str | Path = (
        database_path or os.environ.get("JOB_AGENT_DB_PATH") or DEFAULT_DATABASE_PATH
    )
    resolved_path = _database_path(Path(path_value), account_key, database_path is None)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    store = ApplicationStore(resolved_path)
    store.initialize()
    return store


def _database_path(path: Path, account_key: str, default_path: bool) -> Path:
    """Return the SQLite path for an account."""

    safe_key = hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:16]
    if default_path:
        return path.with_name(f"{path.stem}-{safe_key}{path.suffix}")
    if path.exists() and path.is_dir():
        return path / f"local-agent-{safe_key}.db"
    if path.suffix:
        return path
    return path / f"local-agent-{safe_key}.db"


def _account_key(authorization: str | None) -> str:
    """Return a stable local account key from a bearer credential."""

    if not authorization or not authorization.casefold().startswith("bearer "):
        return "anonymous"
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return "anonymous"
    jwt_identity = _jwt_identity(token)
    return jwt_identity or token


def _jwt_identity(token: str) -> str | None:
    """Extract an identity claim from an unsigned local JWT payload."""

    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    identity = claims.get("sub") or claims.get("email")
    return str(identity) if identity else None


def _to_domain_profile(profile: FrontendProfile) -> CandidateProfile:
    """Convert a frontend profile into the domain model."""

    return CandidateProfile(
        profile_id=PROFILE_ID,
        full_name=profile.full_name,
        email=profile.email,
        phone=profile.phone or None,
        location=profile.location_preference or None,
        work_authorization=profile.work_authorization or None,
        target_roles=tuple(_clean_list(profile.target_roles)),
        preferred_locations=tuple(
            _clean_list(_split_profile_text(profile.location_preference))
        ),
        remote_preference=(
            "remote"
            if "remote" in profile.location_preference.casefold()
            else profile.location_preference or None
        ),
        minimum_salary=_minimum_salary(profile.salary_range),
        skills=tuple(_clean_list(profile.skills)),
        blocked_companies=tuple(_clean_list(profile.avoid)),
        blocked_industries=tuple(_clean_list(profile.avoid)),
    )


def _to_frontend_profile(profile: CandidateProfile) -> FrontendProfile:
    """Convert a domain profile into the frontend profile shape."""

    return FrontendProfile(
        fullName=profile.full_name,
        email=profile.email,
        phone=profile.phone or "",
        targetRoles=list(profile.target_roles),
        locationPreference=profile.location or "",
        salaryRange=_salary_label(profile.minimum_salary),
        workAuthorization=profile.work_authorization or "",
        skills=list(profile.skills),
        avoid=list(profile.blocked_companies or profile.blocked_industries),
    )


def _dashboard_payload(store: ApplicationStore) -> dict[str, Any]:
    """Build the frontend dashboard response from stored rows."""

    profile = store.get_profile(PROFILE_ID)
    jobs = store.list_jobs()
    scored_jobs = [_job_payload(row, profile) for row in jobs]
    return {
        "jobs": scored_jobs,
        "duplicates": [
            _duplicate_payload(row)
            for row in jobs
            if str(row["status"]) == JobStatus.DUPLICATE_POSSIBLE.value
        ],
        "ledger": [_ledger_payload(row, store) for row in jobs],
    }


def _job_payload(
    row: sqlite3.Row,
    profile: CandidateProfile | None,
) -> dict[str, Any]:
    """Return a pipeline job payload."""

    return {
        "applicationUrl": str(row["application_url"]),
        "content": row["content"],
        "id": str(row["id"]),
        "title": str(row["title"]),
        "company": str(row["company"]),
        "location": row["location"] or "",
        "status": str(row["status"]),
        "score": _score_row(row, profile),
    }


def _ledger_payload(row: sqlite3.Row, store: ApplicationStore) -> dict[str, Any]:
    """Return an application ledger payload."""

    application = _application_for_job(int(row["id"]), store)
    return {
        "id": str(row["id"]),
        "company": str(row["company"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "appliedAt": application["applied_at"] if application is not None else None,
        "resumeVersion": (
            application["resume_version"] if application is not None else None
        ),
        "confirmation": (
            application["confirmation_number"] if application is not None else None
        ),
    }


def _duplicate_payload(row: sqlite3.Row) -> dict[str, Any]:
    """Return a duplicate-review payload."""

    return {
        "id": str(row["id"]),
        "reason": "Possible duplicate of an applied job",
        "confidence": 0.85,
        "jobs": [
            {
                "label": "candidate",
                "title": str(row["title"]),
                "company": str(row["company"]),
                "location": row["location"] or "",
                "discoveredAt": "",
                "identity": str(row["canonical_url"]),
            }
        ],
    }


def _application_for_job(
    job_id: int,
    store: ApplicationStore,
) -> sqlite3.Row | None:
    """Return the application row for a job, when one exists."""

    with store.connect() as connection:
        row = connection.execute(
            "SELECT * FROM applications WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return cast(sqlite3.Row | None, row)


def _score_row(row: sqlite3.Row, profile: CandidateProfile | None) -> int | None:
    """Score a stored job against the saved profile."""

    if profile is None:
        return None
    job = _job_from_row(row)
    score = FitScorer().score(job, _scoring_criteria(profile))
    return round(score.total)


def _job_from_row(row: sqlite3.Row) -> JobPosting:
    """Build a job posting model from a SQLite row."""

    return JobPosting(
        source=JobSource(str(row["source"])),
        source_job_id=str(row["source_job_id"]),
        title=str(row["title"]),
        company=str(row["company"]),
        location=row["location"],
        department=row["department"],
        employment_type=row["employment_type"],
        application_url=str(row["application_url"]),
        canonical_url=str(row["canonical_url"]),
        content=row["content"],
    )


def _scoring_criteria(profile: CandidateProfile) -> ScoringCriteria:
    """Build fit-scoring criteria from the saved profile."""

    return ScoringCriteria(
        target_roles=profile.target_roles,
        skills=profile.skills,
        preferred_locations=profile.preferred_locations,
        remote_ok=profile.remote_preference != "onsite",
        minimum_salary=profile.minimum_salary,
        avoided_companies=profile.blocked_companies,
        authorized_work_regions=_authorized_regions(profile.work_authorization),
    )


def _sample_jobs(profile: CandidateProfile) -> tuple[JobPosting, ...]:
    """Return local discovered jobs derived from the saved profile."""

    roles = profile.target_roles or ("Software Engineer",)
    skills = ", ".join(profile.skills[:3]) or "Python, data systems"
    location = profile.location or "Remote US"
    jobs: list[JobPosting] = []
    for index, role in enumerate(roles[:4], start=1):
        role_slug = _slug(role)
        company = f"Local Match {index}"
        url = f"https://boards.greenhouse.io/localmatch/jobs/{role_slug}"
        jobs.append(
            JobPosting(
                source=JobSource.GREENHOUSE,
                source_job_id=f"local-{role_slug}",
                title=role,
                company=company,
                location=location,
                application_url=url,
                canonical_url=canonicalize_url(url),
                content=f"Work on production systems using {skills}.",
                requirements=tuple(profile.skills[:3]),
                remote="remote" in location.casefold(),
                raw_data={"local_seed": True, "role": role},
            )
        )

    mismatch_url = "https://jobs.lever.co/localexample/frontend-designer"
    jobs.append(
        JobPosting(
            source=JobSource.LEVER,
            source_job_id="local-mismatch",
            title="Frontend Designer",
            company="Local Example",
            location=location,
            application_url=mismatch_url,
            canonical_url=canonicalize_url(mismatch_url),
            content=f"Uses {skills}, but the title is intentionally less relevant.",
            requirements=tuple(profile.skills[:3]),
            remote="remote" in location.casefold(),
            raw_data={"local_seed": True, "role": "Frontend Designer"},
        )
    )
    return tuple(jobs)


def _clean_list(items: list[str] | tuple[str, ...]) -> list[str]:
    """Return non-empty stripped values."""

    return [item.strip() for item in items if item.strip()]


def _slug(value: str) -> str:
    """Return a stable slug for local synthetic job identities."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "role"


def _split_profile_text(value: str) -> list[str]:
    """Split comma/newline profile text into values."""

    return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]


def _minimum_salary(value: str) -> int | None:
    """Parse the first salary-like number from profile text."""

    match = re.search(r"(\d[\d,]*)\s*k?", value.casefold())
    if match is None:
        return None
    amount = int(match.group(1).replace(",", ""))
    return amount * 1000 if amount < 1000 else amount


def _salary_label(minimum_salary: int | None) -> str:
    """Return a simple salary label for the frontend."""

    if minimum_salary is None:
        return ""
    return f"${minimum_salary:,}+"


def _authorized_regions(work_authorization: str | None) -> tuple[str, ...]:
    """Infer authorized work regions from free text."""

    if not work_authorization:
        return ()
    return ("US",) if "us" in work_authorization.casefold() else ()


app = create_app()
