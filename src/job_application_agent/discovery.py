"""Discovery query planning for job search sources."""

from __future__ import annotations

from dataclasses import dataclass

from job_application_agent.models import CandidateProfile

DEFAULT_SOURCE_SITES = {
    "greenhouse": "site:boards.greenhouse.io",
    "lever": "site:jobs.lever.co",
}


@dataclass(frozen=True)
class DiscoveryQuery:
    """A deterministic search query derived from profile preferences."""

    text: str
    target_role: str
    source: str


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


def _location_terms(profile: CandidateProfile) -> tuple[str, ...]:
    """Return location search terms from profile preferences."""

    terms: list[str] = []
    if profile.remote_preference and "remote" in profile.remote_preference.casefold():
        terms.append("remote")
    terms.extend(location.strip() for location in profile.preferred_locations)
    return tuple(term for term in terms if term)
