from pathlib import Path
from typing import Any

from job_application_agent.discovery import (
    BoardDefinition,
    build_discovery_queries,
    configured_board_definitions,
    fetch_configured_board_jobs,
)
from job_application_agent.models import CandidateProfile, JobSource


def test_build_discovery_queries_uses_target_roles_and_locations() -> None:
    """Verify target roles become source-specific search queries."""

    profile = CandidateProfile(
        profile_id="primary",
        full_name="Jo Ann Efobi",
        email="jo@example.com",
        target_roles=("Backend Engineer", "AI Engineer"),
        preferred_locations=("Remote US",),
        remote_preference="remote",
    )

    queries = build_discovery_queries(profile)

    assert [query.text for query in queries] == [
        "Backend Engineer remote Remote US site:boards.greenhouse.io",
        "Backend Engineer remote Remote US site:jobs.lever.co",
        "AI Engineer remote Remote US site:boards.greenhouse.io",
        "AI Engineer remote Remote US site:jobs.lever.co",
    ]
    assert [query.source for query in queries] == [
        "greenhouse",
        "lever",
        "greenhouse",
        "lever",
    ]


def test_build_discovery_queries_skips_blank_target_roles() -> None:
    """Verify empty target role values do not produce search queries."""

    profile = CandidateProfile(
        profile_id="primary",
        full_name="Jo Ann Efobi",
        email="jo@example.com",
        target_roles=(" ", "Backend Engineer"),
    )

    queries = build_discovery_queries(profile, source_sites={"test": "site:test"})

    assert len(queries) == 1
    assert queries[0].text == "Backend Engineer site:test"


def test_configured_board_definitions_parse_local_board_slugs() -> None:
    """Verify configured board slugs become structured fetch definitions."""

    definitions = configured_board_definitions(
        greenhouse_boards="localmatch:Local Match,example",
        lever_sites="localexample:Local Example",
    )

    assert definitions == (
        BoardDefinition(
            source=JobSource.GREENHOUSE,
            slug="localmatch",
            company_name="Local Match",
        ),
        BoardDefinition(source=JobSource.GREENHOUSE, slug="example"),
        BoardDefinition(
            source=JobSource.LEVER,
            slug="localexample",
            company_name="Local Example",
        ),
    )


def test_configured_board_definitions_read_local_env_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Verify board settings can come from a local dotenv file."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_AGENT_GREENHOUSE_BOARDS", raising=False)
    monkeypatch.delenv("JOB_AGENT_LEVER_SITES", raising=False)
    (tmp_path / ".env.local").write_text(
        "JOB_AGENT_GREENHOUSE_BOARDS=dotenvgh:Dotenv GH\n"
        "JOB_AGENT_LEVER_SITES=dotenvlever:Dotenv Lever\n",
        encoding="utf-8",
    )

    definitions = configured_board_definitions()

    assert definitions == (
        BoardDefinition(JobSource.GREENHOUSE, "dotenvgh", "Dotenv GH"),
        BoardDefinition(JobSource.LEVER, "dotenvlever", "Dotenv Lever"),
    )


def test_fetch_configured_board_jobs_uses_greenhouse_and_lever(
    monkeypatch: Any,
) -> None:
    """Verify configured board fetching delegates to source ingestors."""

    def fake_greenhouse_json(url: str, timeout: float = 30.0) -> dict[str, Any]:
        return {
            "jobs": [
                {
                    "id": 123,
                    "title": "Backend Engineer",
                    "absolute_url": "https://boards.greenhouse.io/local/jobs/123",
                    "location": {"name": "Remote US"},
                }
            ]
        }

    def fake_lever_json(url: str, timeout: float = 30.0) -> list[dict[str, Any]]:
        return [
            {
                "id": "abc",
                "text": "Platform Engineer",
                "hostedUrl": "https://jobs.lever.co/local/abc",
                "categories": {"location": "Remote US"},
            }
        ]

    monkeypatch.setattr(
        "job_application_agent.sources.greenhouse.fetch_json", fake_greenhouse_json
    )
    monkeypatch.setattr(
        "job_application_agent.sources.lever.fetch_json", fake_lever_json
    )

    jobs = fetch_configured_board_jobs(
        (
            BoardDefinition(JobSource.GREENHOUSE, "local", "Local GH"),
            BoardDefinition(JobSource.LEVER, "local", "Local Lever"),
        )
    )

    assert [job.title for job in jobs] == ["Backend Engineer", "Platform Engineer"]
    assert [job.company for job in jobs] == ["Local GH", "Local Lever"]
