"""Discovery query planning for job search sources."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ

from job_application_agent.models import CandidateProfile, JobPosting, JobSource
from job_application_agent.sources import GreenhouseIngestor, LeverIngestor

DEFAULT_SOURCE_SITES = {
    "greenhouse": "site:boards.greenhouse.io",
    "lever": "site:jobs.lever.co",
}
GREENHOUSE_BOARDS_ENV = "JOB_AGENT_GREENHOUSE_BOARDS"
LEVER_SITES_ENV = "JOB_AGENT_LEVER_SITES"


@dataclass(frozen=True)
class DiscoveryQuery:
    """A deterministic search query derived from profile preferences."""

    text: str
    target_role: str
    source: str


@dataclass(frozen=True)
class BoardDefinition:
    """A configured structured job board to fetch during discovery."""

    source: JobSource
    slug: str
    company_name: str | None = None


def build_discovery_queries(
    profile: CandidateProfile,
    *,
    source_sites: dict[str, str] | None = None,
) -> tuple[DiscoveryQuery, ...]:
    """Build source-specific search queries from candidate target roles.

    Args:
        profile: Candidate profile containing target roles and location preferences.
        source_sites: Optional mapping of source labels to search operators.

    Returns:
        Search queries ordered by target role, then source.
    """

    sites = source_sites or DEFAULT_SOURCE_SITES
    location_terms = _location_terms(profile)
    queries: list[DiscoveryQuery] = []
    for target_role in profile.target_roles:
        role = target_role.strip()
        if not role:
            continue
        for source, site_operator in sites.items():
            text = " ".join(
                part for part in (role, *location_terms, site_operator) if part.strip()
            )
            queries.append(DiscoveryQuery(text=text, target_role=role, source=source))
    return tuple(queries)


def configured_board_definitions(
    *,
    greenhouse_boards: str | None = None,
    lever_sites: str | None = None,
) -> tuple[BoardDefinition, ...]:
    """Return structured board definitions from explicit values or env vars.

    Args:
        greenhouse_boards: Comma/newline-separated Greenhouse board slugs.
        lever_sites: Comma/newline-separated Lever site slugs.

    Returns:
        Board definitions in Greenhouse, then Lever order.
    """

    greenhouse_value = (
        environ.get(GREENHOUSE_BOARDS_ENV)
        if greenhouse_boards is None
        else greenhouse_boards
    )
    lever_value = environ.get(LEVER_SITES_ENV) if lever_sites is None else lever_sites
    return (
        *_parse_board_entries(JobSource.GREENHOUSE, greenhouse_value or ""),
        *_parse_board_entries(JobSource.LEVER, lever_value or ""),
    )


def fetch_configured_board_jobs(
    definitions: tuple[BoardDefinition, ...] | None = None,
) -> tuple[JobPosting, ...]:
    """Fetch jobs from configured Greenhouse and Lever boards.

    Args:
        definitions: Optional board definitions. Defaults to environment-backed
            definitions.

    Returns:
        Normalized postings fetched from each configured board.
    """

    boards = definitions if definitions is not None else configured_board_definitions()
    jobs: list[JobPosting] = []
    for board in boards:
        if board.source == JobSource.GREENHOUSE:
            jobs.extend(
                GreenhouseIngestor(
                    board.slug, company_name=board.company_name
                ).fetch_jobs()
            )
        elif board.source == JobSource.LEVER:
            jobs.extend(
                LeverIngestor(board.slug, company_name=board.company_name).fetch_jobs()
            )
    return tuple(jobs)


def _location_terms(profile: CandidateProfile) -> tuple[str, ...]:
    """Return location search terms from profile preferences."""

    terms: list[str] = []
    if profile.remote_preference and "remote" in profile.remote_preference.casefold():
        terms.append("remote")
    terms.extend(location.strip() for location in profile.preferred_locations)
    return tuple(term for term in terms if term)


def _parse_board_entries(source: JobSource, value: str) -> tuple[BoardDefinition, ...]:
    """Parse board entries from comma/newline-separated configuration."""

    definitions: list[BoardDefinition] = []
    for item in value.replace("\n", ",").split(","):
        entry = item.strip()
        if not entry:
            continue
        slug, company_name = _split_board_entry(entry)
        definitions.append(
            BoardDefinition(source=source, slug=slug, company_name=company_name)
        )
    return tuple(definitions)


def _split_board_entry(value: str) -> tuple[str, str | None]:
    """Split a board entry into slug and optional company name."""

    if ":" not in value:
        return value, None
    slug, company_name = value.split(":", 1)
    cleaned_company = company_name.strip()
    return slug.strip(), cleaned_company or None
