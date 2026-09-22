"""Local REST API for the job application agent frontend."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from job_application_agent.application_worker import (
    ApplicationPreparation,
    ApplicationWorker,
)
from job_application_agent.discovery import (
    configured_board_definitions,
    fetch_configured_board_jobs,
)
from job_application_agent.models import (
    CandidateProfile,
    CompensationRange,
    JobPosting,
    JobSource,
    JobStatus,
)
from job_application_agent.normalization import canonicalize_url
from job_application_agent.scoring import FitScorer, ScoringCriteria
from job_application_agent.storage import ApplicationLedger, ApplicationStore
from job_application_agent.tracking import SubmissionConfirmationRequest

DEFAULT_DATABASE_PATH = ".context/local-agent.db"
GOOGLE_CLIENT_ID_KEYS = ("GOOGLE_CLIENT_ID", "VITE_GOOGLE_CLIENT_ID")
LOCAL_AUTH_ENV_KEY = "JOB_AGENT_ALLOW_LOCAL_AUTH"
LOCAL_ENV_FILES = (".env.local", ".env")
TRUE_VALUES = {"1", "on", "true", "yes"}
PROFILE_ID = "primary"
GoogleTokenVerifier = Callable[[str, str], Mapping[str, Any]]
JobFetcher = Callable[[CandidateProfile], Sequence[JobPosting]]


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


class SubmissionConfirmationPayload(BaseModel):
    """Request body for confirming a submitted application."""

    applied_at: str | None = Field(default=None, alias="appliedAt")
    resume_version: str | None = Field(default=None, alias="resumeVersion")
    cover_letter_version: str | None = Field(default=None, alias="coverLetterVersion")
    confirmation_number: str | None = Field(default=None, alias="confirmationNumber")
    confirmation_url: str | None = Field(default=None, alias="confirmationUrl")
    provider: str | None = None
    notes: str | None = None

    model_config = {"populate_by_name": True}


def create_app(
    database_path: str | Path | None = None,
    google_client_id: str | None = None,
    google_token_verifier: GoogleTokenVerifier | None = None,
    allow_local_auth: bool | None = None,
    job_fetcher: JobFetcher | None = None,
) -> FastAPI:
    """Create the local REST API application.

    Args:
        database_path: Optional SQLite path. Defaults to `.context/local-agent.db`.
        google_client_id: Optional Google OAuth client ID. Defaults to environment
            or local dotenv lookup.
        google_token_verifier: Optional verifier for Google ID tokens.
        allow_local_auth: Whether to accept the local development bearer token.
            Defaults to `JOB_AGENT_ALLOW_LOCAL_AUTH`.
        job_fetcher: Optional discovery fetcher override for tests.

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

        store = _request_store(
            request,
            database_path,
            google_client_id,
            google_token_verifier,
            allow_local_auth,
        )
        profile = store.get_profile(PROFILE_ID)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile has not been saved.")
        return _to_frontend_profile(profile)

    @app.put("/api/profile", response_model=FrontendProfile)
    def put_profile(request: Request, profile: FrontendProfile) -> FrontendProfile:
        """Persist the local user's profile."""

        store = _request_store(
            request,
            database_path,
            google_client_id,
            google_token_verifier,
            allow_local_auth,
        )
        domain_profile = _to_domain_profile(profile)
        store.save_profile(domain_profile)
        return _to_frontend_profile(domain_profile)

    @app.get("/api/dashboard")
    def get_dashboard(request: Request) -> dict[str, Any]:
        """Return jobs, duplicate candidates, and application ledger rows."""

        store = _request_store(
            request,
            database_path,
            google_client_id,
            google_token_verifier,
            allow_local_auth,
        )
        return _dashboard_payload(store)

    @app.post("/api/discovery/runs", status_code=204)
    def post_discovery_run(request: Request) -> None:
        """Run local discovery and store matching sample jobs."""

        store = _request_store(
            request,
            database_path,
            google_client_id,
            google_token_verifier,
            allow_local_auth,
        )
        profile = store.get_profile(PROFILE_ID)
        if profile is None:
            raise HTTPException(
                status_code=409,
                detail="Save your profile before running discovery.",
            )
        try:
            jobs = _discovery_jobs(profile, job_fetcher=job_fetcher)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        _record_discovered_jobs(store, profile, jobs)

    @app.post("/api/application-runs")
    def post_application_runs(request: Request) -> dict[str, Any]:
        """Prepare every job currently approved for application."""

        store = _request_store(
            request,
            database_path,
            google_client_id,
            google_token_verifier,
            allow_local_auth,
        )
        profile = store.get_profile(PROFILE_ID)
        if profile is None:
            raise HTTPException(
                status_code=409,
                detail="Save your profile before preparing applications.",
            )
        preparations = ApplicationWorker(store).prepare_approved_jobs(profile)
        return {
            "prepared": [
                _application_preparation_payload(preparation)
                for preparation in preparations
            ]
        }

    @app.post("/api/jobs/{job_id}/application-run")
    def post_job_application_run(request: Request, job_id: int) -> dict[str, Any]:
        """Prepare one approved job for user review before submission."""

        store = _request_store(
            request,
            database_path,
            google_client_id,
            google_token_verifier,
            allow_local_auth,
        )
        profile = store.get_profile(PROFILE_ID)
        if profile is None:
            raise HTTPException(
                status_code=409,
                detail="Save your profile before preparing applications.",
            )
        try:
            preparation = ApplicationWorker(store).prepare_job(job_id, profile)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _application_preparation_payload(preparation)

    @app.post("/api/jobs/{job_id}/submission-confirmations")
    def post_submission_confirmation(
        request: Request,
        job_id: int,
        payload: SubmissionConfirmationPayload,
    ) -> dict[str, Any]:
        """Record a confirmed submission and mark the job applied."""

        store = _request_store(
            request,
            database_path,
            google_client_id,
            google_token_verifier,
            allow_local_auth,
        )
        try:
            confirmation = ApplicationWorker(store).confirm_submission(
                SubmissionConfirmationRequest(
                    job_id=job_id,
                    applied_at=payload.applied_at,
                    resume_version=payload.resume_version,
                    cover_letter_version=payload.cover_letter_version,
                    confirmation_number=payload.confirmation_number,
                    confirmation_url=payload.confirmation_url,
                    provider=payload.provider,
                    notes=payload.notes,
                )
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "jobId": str(confirmation.job_id),
            "status": confirmation.status.value,
            "appliedAt": confirmation.applied_at,
            "resumeVersion": confirmation.resume_version,
            "coverLetterVersion": confirmation.cover_letter_version,
            "confirmationNumber": confirmation.confirmation_number,
            "confirmationUrl": confirmation.confirmation_url,
            "provider": confirmation.provider,
            "notes": confirmation.notes,
        }

    @app.patch("/api/jobs/{job_id}/status", status_code=204)
    def patch_job_status(request: Request, job_id: int, update: StatusUpdate) -> None:
        """Update one job's status from the dashboard."""

        store = _request_store(
            request,
            database_path,
            google_client_id,
            google_token_verifier,
            allow_local_auth,
        )
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        if update.status == JobStatus.APPLIED:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Use submission confirmation recording after the application "
                    "worker has started the application."
                ),
            )
        store.update_job_status(job_id, update.status)

    return app


def _request_store(
    request: Request,
    database_path: str | Path | None,
    google_client_id: str | None,
    google_token_verifier: GoogleTokenVerifier | None,
    allow_local_auth: bool | None,
) -> ApplicationStore:
    """Create a store scoped to the request account."""

    account_key = _account_key(
        request, google_client_id, google_token_verifier, allow_local_auth
    )
    return _store(database_path, account_key)


def _store(database_path: str | Path | None, account_key: str) -> ApplicationStore:
    """Create and initialize the SQLite store."""

    path_value: str | Path = (
        database_path or os.environ.get("JOB_AGENT_DB_PATH") or DEFAULT_DATABASE_PATH
    )
    resolved_path = _database_path(Path(path_value), account_key)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    store = ApplicationStore(resolved_path)
    store.initialize()
    return store


def _database_path(path: Path, account_key: str) -> Path:
    """Return the SQLite path for an account."""

    safe_key = hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:16]
    if path.exists() and path.is_dir():
        return path / f"local-agent-{safe_key}.db"
    if path.suffix:
        return path.with_name(f"{path.stem}-{safe_key}{path.suffix}")
    return path / f"local-agent-{safe_key}.db"


def _account_key(
    request: Request,
    google_client_id: str | None,
    google_token_verifier: GoogleTokenVerifier | None,
    allow_local_auth: bool | None,
) -> str:
    """Return a stable local account key from a bearer credential."""

    authorization = request.headers.get("authorization")
    if not authorization or not authorization.casefold().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in before using the API.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sign in before using the API.")
    if token == "local-dev-token":
        if _local_auth_enabled(allow_local_auth) and _is_loopback_request(request):
            return token
        raise HTTPException(status_code=401, detail="Local sign-in is disabled.")
    client_id = google_client_id or _google_client_id()
    if not client_id:
        raise HTTPException(
            status_code=401,
            detail="Configure GOOGLE_CLIENT_ID before using Google sign-in.",
        )
    try:
        claims = _verify_google_token(token, client_id, google_token_verifier)
    except ValueError as error:
        raise HTTPException(status_code=401, detail="Invalid sign-in token.") from error
    jwt_identity = _claim_identity(claims)
    if not jwt_identity:
        raise HTTPException(status_code=401, detail="Invalid sign-in token.")
    return jwt_identity


def _verify_google_token(
    token: str,
    google_client_id: str,
    google_token_verifier: GoogleTokenVerifier | None,
) -> Mapping[str, Any]:
    """Verify a Google ID token and return its claims.

    Args:
        token: Google ID token from the frontend.
        google_client_id: Expected OAuth client ID audience.
        google_token_verifier: Optional verifier override for tests.

    Returns:
        Verified token claims. A ValueError is propagated when verification
        fails.
    """

    verifier = google_token_verifier or _default_google_token_verifier
    return verifier(token, google_client_id)


def _default_google_token_verifier(
    token: str, google_client_id: str
) -> Mapping[str, Any]:
    """Verify a Google ID token using Google's public certs."""

    try:
        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), google_client_id
        )  # type: ignore[no-untyped-call]
    except (GoogleAuthError, ValueError) as error:
        raise ValueError("Invalid Google ID token.") from error
    if not isinstance(claims, Mapping):
        raise ValueError("Google ID token claims must be an object.")
    return claims


def _claim_identity(claims: Mapping[str, Any]) -> str | None:
    """Return the stable user identity from verified token claims."""

    identity = claims.get("sub") or claims.get("email")
    return str(identity) if identity else None


def _local_auth_enabled(allow_local_auth: bool | None) -> bool:
    """Return whether fixed-token local authentication is explicitly enabled."""

    if allow_local_auth is not None:
        return allow_local_auth
    return os.environ.get(LOCAL_AUTH_ENV_KEY, "").casefold() in TRUE_VALUES


def _is_loopback_request(request: Request) -> bool:
    """Return whether the request came from a loopback client address."""

    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return request.client.host == "localhost"


def _google_client_id() -> str | None:
    """Return the configured Google OAuth client ID for token validation."""

    for key in GOOGLE_CLIENT_ID_KEYS:
        value = os.environ.get(key)
        if value:
            return value
    for path in LOCAL_ENV_FILES:
        value = _dotenv_value(Path(path), GOOGLE_CLIENT_ID_KEYS)
        if value:
            return value
    return None


def _dotenv_value(path: Path, keys: tuple[str, ...]) -> str | None:
    """Return the first configured value for any key from a local dotenv file."""

    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() in keys:
            return value.strip().strip("\"'")
    return None


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


def _application_preparation_payload(
    preparation: ApplicationPreparation,
) -> dict[str, Any]:
    """Return an application preparation payload for the frontend."""

    plan = preparation.form_result.plan
    return {
        "jobId": str(preparation.job_id),
        "status": preparation.status.value,
        "requiresUserApproval": preparation.requires_user_approval,
        "materials": {
            "resumeVersion": preparation.materials.resume_version,
            "coverLetterVersion": preparation.materials.cover_letter_version,
            "coverLetterText": preparation.materials.cover_letter_text,
            "shortAnswers": preparation.materials.short_answers,
            "requiresReview": preparation.materials.requires_review,
        },
        "form": {
            "provider": plan.provider,
            "applicationUrl": plan.application_url,
            "stopBeforeSubmit": plan.stop_before_submit,
            "readyForUserReview": preparation.form_result.ready_for_user_review,
            "fields": [
                {
                    "fieldKey": field.field_key,
                    "action": field.action.value,
                    "value": field.value,
                    "requiresReview": field.requires_review,
                }
                for field in plan.fields
            ],
        },
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


def _discovery_jobs(
    profile: CandidateProfile,
    *,
    job_fetcher: JobFetcher | None,
) -> tuple[JobPosting, ...]:
    """Return jobs for a discovery run from configured boards or samples."""

    if job_fetcher is not None:
        return tuple(job_fetcher(profile))
    if configured_board_definitions():
        return fetch_configured_board_jobs()
    return _sample_jobs(profile)


def _record_discovered_jobs(
    store: ApplicationStore,
    profile: CandidateProfile,
    jobs: tuple[JobPosting, ...],
) -> None:
    """Persist discovered jobs and assign their first-review statuses."""

    ledger = ApplicationLedger(store)
    scorer = FitScorer()
    criteria = _scoring_criteria(profile)
    for job in jobs:
        result = ledger.record_discovered_job(job)
        if result.status == JobStatus.DISCOVERED:
            score = scorer.score(job, criteria)
            store.update_job_status(result.job_id, _next_discovery_status(score.total))


def _next_discovery_status(score: float) -> JobStatus:
    """Return the initial pipeline status for a fit score."""

    if score >= 85:
        return JobStatus.APPROVED_TO_APPLY
    if score >= 55:
        return JobStatus.NEEDS_REVIEW
    return JobStatus.REJECTED


def _sample_jobs(profile: CandidateProfile) -> tuple[JobPosting, ...]:
    """Return local discovered jobs derived from the saved profile."""

    roles = profile.target_roles or ("Software Engineer",)
    skills = ", ".join(profile.skills[:3]) or "Python, data systems"
    location = profile.location or "Remote US"
    jobs: list[JobPosting] = []
    for index, role in enumerate(roles[:4], start=1):
        role_identity = _role_identity(role)
        company = f"Local Match {index}"
        url = f"https://boards.greenhouse.io/localmatch/jobs/{role_identity}"
        jobs.append(
            JobPosting(
                source=JobSource.GREENHOUSE,
                source_job_id=f"local-{role_identity}",
                title=role,
                company=company,
                location=location,
                application_url=url,
                canonical_url=canonicalize_url(url),
                content=f"Work on production systems using {skills}.",
                requirements=tuple(profile.skills[:3]),
                remote="remote" in location.casefold(),
                salary_range=(
                    CompensationRange(
                        minimum=profile.minimum_salary,
                        maximum=profile.minimum_salary + 30_000,
                    )
                    if profile.minimum_salary is not None
                    else None
                ),
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


def _role_identity(value: str) -> str:
    """Return a collision-resistant identity for a local synthetic job role."""

    normalized = " ".join(value.casefold().split())
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "role"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


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
