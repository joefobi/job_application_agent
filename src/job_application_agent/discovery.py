"""Discovery query planning for job search sources."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError, as_completed
from dataclasses import dataclass
from os import environ
from pathlib import Path

from job_application_agent.models import CandidateProfile, JobPosting, JobSource
from job_application_agent.sources import GreenhouseIngestor, LeverIngestor

DEFAULT_SOURCE_SITES = {
    "greenhouse": "site:boards.greenhouse.io",
    "lever": "site:jobs.lever.co",
}
GREENHOUSE_BOARDS_ENV = "JOB_AGENT_GREENHOUSE_BOARDS"
LEVER_SITES_ENV = "JOB_AGENT_LEVER_SITES"
LOCAL_ENV_FILES = (".env.local", ".env")
DEFAULT_BOARD_FETCH_DEADLINE = 30.0
DEFAULT_BOARD_FETCH_TIMEOUT = 10.0
DEFAULT_BOARD_FETCH_WORKERS = 4


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

    greenhouse_value = _config_value(GREENHOUSE_BOARDS_ENV, greenhouse_boards)
    lever_value = _config_value(LEVER_SITES_ENV, lever_sites)
    return (
        *_parse_board_entries(JobSource.GREENHOUSE, greenhouse_value or ""),
        *_parse_board_entries(JobSource.LEVER, lever_value or ""),
    )


def fetch_configured_board_jobs(
    definitions: tuple[BoardDefinition, ...] | None = None,
    *,
    deadline: float = DEFAULT_BOARD_FETCH_DEADLINE,
    max_workers: int = DEFAULT_BOARD_FETCH_WORKERS,
    timeout: float = DEFAULT_BOARD_FETCH_TIMEOUT,
) -> tuple[JobPosting, ...]:
    """Fetch jobs from configured Greenhouse and Lever boards.

    Args:
        definitions: Optional board definitions. Defaults to environment-backed
            definitions.
        deadline: Maximum seconds to wait for the board batch.
        max_workers: Maximum number of board fetches to run concurrently.
        timeout: Per-board HTTP timeout in seconds.

    Returns:
        Normalized postings fetched from each available configured board.
    """

    boards = definitions if definitions is not None else configured_board_definitions()
    if not boards:
        return ()

    worker_count = max(1, min(max_workers, len(boards)))
    executor = ThreadPoolExecutor(max_workers=worker_count)
    future_to_index: dict[Future[tuple[JobPosting, ...]], int] = {
        executor.submit(_fetch_board_jobs, board, timeout): index
        for index, board in enumerate(boards)
    }
    results: dict[int, tuple[JobPosting, ...]] = {}
    try:
        for future in as_completed(future_to_index, timeout=deadline):
            index = future_to_index[future]
            try:
                results[index] = future.result()
            except Exception:
                results[index] = ()
    except TimeoutError:
        pass
    finally:
        for future in future_to_index:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    return tuple(job for index in sorted(results) for job in results[index])


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
        if not slug:
            raise ValueError(f"{source.value} board slug cannot be empty.")
        definitions.append(
            BoardDefinition(source=source, slug=slug, company_name=company_name)
        )
    return tuple(definitions)


def _fetch_board_jobs(board: BoardDefinition, timeout: float) -> tuple[JobPosting, ...]:
    """Fetch jobs for one configured board."""

    if board.source == JobSource.GREENHOUSE:
        return tuple(
            GreenhouseIngestor(
                board.slug, company_name=board.company_name, timeout=timeout
            ).fetch_jobs()
        )
    if board.source == JobSource.LEVER:
        return tuple(
            LeverIngestor(
                board.slug, company_name=board.company_name, timeout=timeout
            ).fetch_jobs()
        )
    return ()


def _config_value(key: str, explicit_value: str | None) -> str:
    """Return explicit, environment, or local dotenv configuration."""

    if explicit_value is not None:
        return explicit_value
    value = environ.get(key)
    if value is not None:
        return value
    for path in LOCAL_ENV_FILES:
        value = _dotenv_value(Path(path), key)
        if value is not None:
            return value
    return ""


def _dotenv_value(path: Path, key: str) -> str | None:
    """Return a dotenv value for a key when a local env file exists."""

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
        current_key, value = stripped.split("=", 1)
        if current_key.strip() == key:
            return value.strip().strip("\"'")
    return None


def _split_board_entry(value: str) -> tuple[str, str | None]:
    """Split a board entry into slug and optional company name."""

    if ":" not in value:
        return value, None
    slug, company_name = value.split(":", 1)
    cleaned_company = company_name.strip()
    return slug.strip(), cleaned_company or None
