from job_application_agent.discovery import build_discovery_queries
from job_application_agent.models import CandidateProfile


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
