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

    saved = client.put("/api/profile", json=profile)
    assert saved.status_code == 200
    assert saved.json()["targetRoles"] == ["Backend Engineer"]

    run = client.post("/api/discovery/runs")
    assert run.status_code == 204

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["jobs"]
    assert payload["ledger"]
    assert payload["jobs"][0]["score"] is not None
    assert payload["jobs"][0]["applicationUrl"]
    assert payload["jobs"][0]["content"]

    job_id = payload["jobs"][0]["id"]
    applied = client.patch(f"/api/jobs/{job_id}/status", json={"status": "applied"})
    assert applied.status_code == 204

    updated_dashboard = client.get("/api/dashboard").json()
    updated_ledger = [
        entry for entry in updated_dashboard["ledger"] if entry["id"] == job_id
    ][0]
    assert updated_ledger["status"] == "applied"
    assert updated_ledger["appliedAt"] is not None


def test_api_isolates_local_accounts_by_bearer_identity(tmp_path: Path) -> None:
    """Verify different bearer identities do not share profile or pipeline data."""

    client = TestClient(create_app(tmp_path / "accounts"))
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


def test_api_discovers_new_role_after_rejected_old_role(tmp_path: Path) -> None:
    """Verify edited target roles do not mutate stale rejected synthetic jobs."""

    client = TestClient(create_app(tmp_path / "agent.db"))
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

    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/discovery/runs").status_code == 204
    first_jobs = client.get("/api/dashboard").json()["jobs"]
    first_job = [job for job in first_jobs if job["title"] == "Backend Engineer"][0]
    assert (
        client.patch(
            f"/api/jobs/{first_job['id']}/status",
            json={"status": "rejected"},
        ).status_code
        == 204
    )

    profile["targetRoles"] = ["Platform Engineer"]
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/discovery/runs").status_code == 204
    jobs = client.get("/api/dashboard").json()["jobs"]

    assert any(
        job["title"] == "Backend Engineer" and job["status"] == "rejected"
        for job in jobs
    )
    assert any(
        job["title"] == "Platform Engineer" and job["status"] == "needs_review"
        for job in jobs
    )


def test_api_requires_profile_before_discovery(tmp_path: Path) -> None:
    """Verify discovery clearly fails until a profile exists."""

    client = TestClient(create_app(tmp_path / "agent.db"))

    response = client.post("/api/discovery/runs")

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
