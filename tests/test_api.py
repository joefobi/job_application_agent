import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from job_application_agent.api import create_app


def test_api_root_and_health_routes(tmp_path: Path) -> None:
    """Verify browser-friendly API status routes exist."""

    client = TestClient(create_app(tmp_path / "agent.db"))

    root = client.get("/")
    health = client.get("/health")

    assert root.status_code == 200
    assert root.json()["status"] == "ok"
    assert root.json()["docs"] == "/docs"
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


def test_api_profile_discovery_and_dashboard(tmp_path: Path) -> None:
    """Verify the local API supports the frontend profile and dashboard flow."""

    client = TestClient(create_app(tmp_path / "agent.db"))
    headers = {"Authorization": f"Bearer {_token('primary-user')}"}
    profile = {
        "fullName": "Jo Ann Efobi",
        "email": "jo@example.com",
        "phone": "555-0100",
        "targetRoles": ["Backend Engineer"],
        "locationPreference": "Remote US",
        "salaryRange": "$150,000+",
        "workAuthorization": "US Citizen",
        "skills": ["Python", "Postgres"],
        "avoid": ["BadCo"],
    }

    saved = client.put("/api/profile", json=profile, headers=headers)
    assert saved.status_code == 200
    assert saved.json()["targetRoles"] == ["Backend Engineer"]

    run = client.post("/api/discovery/runs", headers=headers)
    assert run.status_code == 204

    dashboard = client.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["jobs"]
    assert payload["ledger"]
    assert payload["jobs"][0]["score"] is not None
    assert payload["jobs"][0]["applicationUrl"]
    assert payload["jobs"][0]["content"]

    approved_job = [
        job for job in payload["jobs"] if job["status"] == "approved_to_apply"
    ][0]
    job_id = approved_job["id"]
    direct_apply = client.patch(
        f"/api/jobs/{job_id}/status",
        json={"status": "applied"},
        headers=headers,
    )
    assert direct_apply.status_code == 409

    prepared = client.post(f"/api/jobs/{job_id}/application-run", headers=headers)
    assert prepared.status_code == 200
    preparation = prepared.json()
    assert preparation["status"] == "started_application"
    assert preparation["materials"]["coverLetterText"]
    assert preparation["form"]["provider"] == "greenhouse"
    assert preparation["form"]["stopBeforeSubmit"] is True

    confirmation = client.post(
        f"/api/jobs/{job_id}/submission-confirmations",
        headers=headers,
        json={
            "confirmationNumber": "ABC123",
            "provider": preparation["form"]["provider"],
            "resumeVersion": preparation["materials"]["resumeVersion"],
            "coverLetterVersion": preparation["materials"]["coverLetterVersion"],
        },
    )
    assert confirmation.status_code == 200

    updated_dashboard = client.get("/api/dashboard", headers=headers).json()
    updated_ledger = [
        entry for entry in updated_dashboard["ledger"] if entry["id"] == job_id
    ][0]
    assert updated_ledger["status"] == "applied"
    assert updated_ledger["appliedAt"] is not None


def test_api_isolates_local_accounts_by_bearer_identity(tmp_path: Path) -> None:
    """Verify different bearer identities do not share profile or pipeline data."""

    client = TestClient(create_app(tmp_path / "accounts.db"))
    first_headers = {"Authorization": f"Bearer {_token('first-user')}"}
    second_headers = {"Authorization": f"Bearer {_token('second-user')}"}
    profile = {
        "fullName": "First User",
        "email": "first@example.com",
        "phone": "555-0100",
        "targetRoles": ["Backend Engineer"],
        "locationPreference": "Remote US",
        "salaryRange": "$150,000+",
        "workAuthorization": "US Citizen",
        "skills": ["Python"],
        "avoid": [],
    }

    assert (
        client.put("/api/profile", json=profile, headers=first_headers).status_code
        == 200
    )
    assert client.post("/api/discovery/runs", headers=first_headers).status_code == 204

    first_dashboard = client.get("/api/dashboard", headers=first_headers).json()
    second_profile = client.get("/api/profile", headers=second_headers)
    second_dashboard = client.get("/api/dashboard", headers=second_headers).json()

    assert first_dashboard["jobs"]
    assert second_profile.status_code == 404
    assert second_dashboard["jobs"] == []


def test_api_rejects_missing_identity_for_protected_routes(tmp_path: Path) -> None:
    """Verify profile and pipeline data cannot use a shared anonymous store."""

    client = TestClient(create_app(tmp_path / "accounts"))
    profile = {
        "fullName": "Jo Ann Efobi",
        "email": "jo@example.com",
        "phone": "555-0100",
        "targetRoles": ["Backend Engineer"],
        "locationPreference": "Remote US",
        "salaryRange": "$150,000+",
        "workAuthorization": "US Citizen",
        "skills": ["Python"],
        "avoid": [],
    }

    assert client.get("/api/profile").status_code == 401
    assert client.put("/api/profile", json=profile).status_code == 401
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/discovery/runs").status_code == 401
    assert client.post("/api/application-runs").status_code == 401
    assert client.post("/api/jobs/1/application-run").status_code == 401
    assert (
        client.post("/api/jobs/1/submission-confirmations", json={}).status_code == 401
    )
    assert (
        client.patch("/api/jobs/1/status", json={"status": "rejected"}).status_code
        == 401
    )


def test_api_discovers_new_role_after_rejected_old_role(tmp_path: Path) -> None:
    """Verify edited target roles do not mutate stale rejected synthetic jobs."""

    client = TestClient(create_app(tmp_path / "agent.db"))
    headers = {"Authorization": f"Bearer {_token('role-edit-user')}"}
    profile = {
        "fullName": "Jo Ann Efobi",
        "email": "jo@example.com",
        "phone": "555-0100",
        "targetRoles": ["Backend Engineer"],
        "locationPreference": "Remote US",
        "salaryRange": "$150,000+",
        "workAuthorization": "US Citizen",
        "skills": ["Python"],
        "avoid": [],
    }

    assert client.put("/api/profile", json=profile, headers=headers).status_code == 200
    assert client.post("/api/discovery/runs", headers=headers).status_code == 204
    first_jobs = client.get("/api/dashboard", headers=headers).json()["jobs"]
    first_job = [job for job in first_jobs if job["title"] == "Backend Engineer"][0]
    assert (
        client.patch(
            f"/api/jobs/{first_job['id']}/status",
            json={"status": "rejected"},
            headers=headers,
        ).status_code
        == 204
    )

    profile["targetRoles"] = ["Platform Engineer"]
    assert client.put("/api/profile", json=profile, headers=headers).status_code == 200
    assert client.post("/api/discovery/runs", headers=headers).status_code == 204
    jobs = client.get("/api/dashboard", headers=headers).json()["jobs"]

    assert any(
        job["title"] == "Backend Engineer" and job["status"] == "rejected"
        for job in jobs
    )
    assert any(
        job["title"] == "Platform Engineer" and job["status"] == "approved_to_apply"
        for job in jobs
    )


def test_api_keeps_punctuated_target_roles_distinct(tmp_path: Path) -> None:
    """Verify roles like C++ and C# do not collapse into one synthetic job."""

    client = TestClient(create_app(tmp_path / "agent.db"))
    headers = {"Authorization": f"Bearer {_token('punctuation-user')}"}
    profile = {
        "fullName": "Jo Ann Efobi",
        "email": "jo@example.com",
        "phone": "555-0100",
        "targetRoles": ["C++ Engineer", "C# Engineer"],
        "locationPreference": "Remote US",
        "salaryRange": "$150,000+",
        "workAuthorization": "US Citizen",
        "skills": ["C++", "C#"],
        "avoid": [],
    }

    assert client.put("/api/profile", json=profile, headers=headers).status_code == 200
    assert client.post("/api/discovery/runs", headers=headers).status_code == 204

    jobs = client.get("/api/dashboard", headers=headers).json()["jobs"]

    assert any(job["title"] == "C++ Engineer" for job in jobs)
    assert any(job["title"] == "C# Engineer" for job in jobs)


def test_api_requires_profile_before_discovery(tmp_path: Path) -> None:
    """Verify discovery clearly fails until a profile exists."""

    client = TestClient(create_app(tmp_path / "agent.db"))
    headers = {"Authorization": f"Bearer {_token('empty-user')}"}

    response = client.post("/api/discovery/runs", headers=headers)

    assert response.status_code == 409


def _token(subject: str) -> str:
    """Return an unsigned JWT-like token for local API tests."""

    header = _base64_json({"alg": "none"})
    payload = _base64_json({"sub": subject, "email": f"{subject}@example.com"})
    return f"{header}.{payload}."


def _base64_json(value: dict[str, str]) -> str:
    """Return unpadded base64url JSON."""

    encoded = base64.urlsafe_b64encode(json.dumps(value).encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")
