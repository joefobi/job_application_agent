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


def test_api_requires_profile_before_discovery(tmp_path: Path) -> None:
    """Verify discovery clearly fails until a profile exists."""

    client = TestClient(create_app(tmp_path / "agent.db"))

    response = client.post("/api/discovery/runs")

    assert response.status_code == 409
