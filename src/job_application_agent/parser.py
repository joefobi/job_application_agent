"""Extract scoring fields from normalized job postings."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from job_application_agent.models import CompensationRange, JobPosting, JobSource
from job_application_agent.normalization import canonicalize_url, normalize_text

SECTION_HEADINGS = {
    "requirements": (
        "requirements",
        "qualifications",
        "what you bring",
        "what we're looking for",
        "what we are looking for",
        "about you",
    ),
    "nice_to_haves": (
        "nice to have",
        "nice-to-have",
        "preferred qualifications",
        "bonus",
        "bonus points",
    ),
}

SENIORITY_TERMS = (
    "intern",
    "junior",
    "entry",
    "mid",
    "senior",
    "staff",
    "principal",
    "lead",
    "manager",
    "director",
)

SALARY_PATTERN = re.compile(
    r"(?P<currency>\$|usd)?\s*"
    r"(?P<first>\d{2,3}(?:,\d{3})?|\d{2,3}k)"
    r"\s*(?:-|–|—|to)\s*"
    r"(?P<second>\$?\s*\d{2,3}(?:,\d{3})?|\$?\s*\d{2,3}k)",
    re.IGNORECASE,
)


class JobParser:
    """Add parser-derived fields needed by filters and fit scoring."""

    def parse(
        self,
        payload: JobPosting | Mapping[str, Any],
        *,
        source: JobSource | str | None = None,
        company: str | None = None,
        application_url: str | None = None,
    ) -> JobPosting:
        """Return a posting enriched with parser-derived fields.

        Args:
            payload: Existing normalized posting, or a structured source payload
                from an ingestion adapter/test fixture.
            source: Optional source override for mapping payloads.
            company: Optional company override for mapping payloads.
            application_url: Optional application URL override for mapping payloads.

        Returns:
            A ``JobPosting`` with salary, requirement, remote, seniority, and work
            authorization fields populated when the parser can infer them.
        """

        posting = (
            payload
            if isinstance(payload, JobPosting)
            else _posting_from_mapping(payload, source, company, application_url)
        )
        return self.enrich(posting)

    def enrich(self, posting: JobPosting) -> JobPosting:
        """Extract fit-scoring fields from a normalized posting.

        Args:
            posting: Normalized job posting from an ingestion source.

        Returns:
            A copy of ``posting`` with derived fields filled in when missing.
        """

        content = strip_html(posting.content or "")
        requirements = posting.requirements or tuple(
            _extract_section(content, "requirements")
        )
        nice_to_haves = posting.nice_to_haves or tuple(
            _extract_section(content, "nice_to_haves")
        )
        searchable_text = " ".join(
            item for item in (posting.title, posting.location or "", content) if item
        )

        return replace(
            posting,
            content=content or posting.content,
            salary_range=posting.salary_range or parse_salary(content),
            requirements=requirements,
            nice_to_haves=nice_to_haves,
            remote=(
                posting.remote
                if posting.remote is not None
                else infer_remote(searchable_text)
            ),
            seniority=posting.seniority or infer_seniority(posting.title),
            work_authorization=posting.work_authorization
            or infer_work_authorization(content),
        )


def strip_html(value: str) -> str:
    """Convert simple HTML postings into readable text.

    Args:
        value: Raw HTML or plain-text posting content.

    Returns:
        Plain text with tags removed and line breaks preserved around common
        block elements.
    """

    with_breaks = re.sub(r"(?i)<\s*(br|/p|/li|/h[1-6])\s*/?>", "\n", value)
    without_tags = re.sub(r"<[^>]+>", " ", with_breaks)
    return "\n".join(
        _collapse_whitespace(line) for line in without_tags.splitlines() if line.strip()
    )


def parse_salary(text: str) -> CompensationRange | None:
    """Parse a US annual salary range from text when present.

    Args:
        text: Posting text that may contain a salary range.

    Returns:
        A compensation range, or None when no range can be inferred.
    """

    match = SALARY_PATTERN.search(text)
    if match is None:
        return None
    return CompensationRange(
        minimum=_parse_salary_number(match.group("first")),
        maximum=_parse_salary_number(match.group("second")),
        currency="USD",
        period="year",
    )


def infer_remote(text: str) -> bool | None:
    """Infer whether a posting is remote-compatible from text.

    Args:
        text: Title, location, and posting content to inspect.

    Returns:
        True for remote roles, False for clearly on-site roles, or None when the
        text is ambiguous.
    """

    normalized = normalize_text(text)
    if any(term in normalized for term in ("remote", "work from home", "wfh")):
        return True
    if any(term in normalized for term in ("onsite", "on site", "in office")):
        return False
    return None


def infer_seniority(title: str) -> str | None:
    """Infer a coarse seniority label from a title.

    Args:
        title: Job title.

    Returns:
        A seniority label, or None when no known label appears in the title.
    """

    normalized_title = normalize_text(title)
    for term in SENIORITY_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", normalized_title):
            return term
    return None


def infer_work_authorization(text: str) -> tuple[str, ...]:
    """Infer work authorization constraints from posting text.

    Args:
        text: Posting content to inspect.

    Returns:
        Normalized authorization tags used by filters and scoring.
    """

    normalized = normalize_text(text)
    tags: list[str] = []
    if (
        "authorized to work in the us" in normalized
        or "u s work authorization" in normalized
    ):
        tags.append("us_authorization_required")
    if "visa sponsorship" in normalized and any(
        phrase in normalized
        for phrase in ("not provide", "unable to", "do not offer", "no visa")
    ):
        tags.append("no_visa_sponsorship")
    return tuple(tags)


def _posting_from_mapping(
    payload: Mapping[str, Any],
    source: JobSource | str | None,
    company: str | None,
    application_url: str | None,
) -> JobPosting:
    resolved_url = application_url or _first_string(
        payload, ("application_url", "absolute_url", "hostedUrl", "url")
    )
    if resolved_url is None:
        raise ValueError("application_url is required")

    title = _first_string(payload, ("title", "text", "name"))
    if title is None:
        raise ValueError("title is required")

    resolved_source = _coerce_source(source, resolved_url)
    resolved_company = company or _first_string(
        payload, ("company", "company_name", "department")
    )

    return JobPosting(
        source=resolved_source,
        source_job_id=_source_job_id(
            _first_string(
                payload, ("id", "job_id", "internal_job_id", "requisition_id")
            ),
            resolved_url,
        ),
        title=title,
        company=resolved_company or _company_from_url(resolved_url),
        application_url=resolved_url,
        canonical_url=canonicalize_url(resolved_url),
        location=_location_from_mapping(payload),
        content=strip_html(
            _first_string(
                payload,
                ("description", "content", "job_description", "description_html"),
            )
            or ""
        ),
        salary_range=_salary_from_mapping(payload),
        requirements=tuple(_list_field(payload, ("requirements", "qualifications"))),
        nice_to_haves=tuple(
            _list_field(payload, ("nice_to_haves", "preferred_qualifications"))
        ),
        raw_data=dict(payload),
    )


def _extract_section(text: str, section: str) -> list[str]:
    headings = SECTION_HEADINGS[section]
    all_headings = {heading for group in SECTION_HEADINGS.values() for heading in group}
    collected: list[str] = []
    in_section = False

    for line in (_collapse_whitespace(line) for line in text.splitlines()):
        normalized_line = normalize_text(line).strip(":")
        if normalized_line in headings:
            in_section = True
            continue
        if in_section and normalized_line in all_headings:
            break
        if in_section and line:
            collected.append(line.lstrip("-* ").strip())

    return collected[:12]


def _coerce_source(
    source: JobSource | str | None, application_url: str | None
) -> JobSource:
    if isinstance(source, JobSource):
        return source
    if source:
        try:
            return JobSource(normalize_text(source))
        except ValueError:
            raise ValueError(f"Unsupported job source: {source}") from None
    if application_url:
        host = urlsplit(application_url).netloc.casefold()
        if "greenhouse" in host:
            return JobSource.GREENHOUSE
        if "lever" in host:
            return JobSource.LEVER
    raise ValueError("source is required for non-Greenhouse/Lever postings")


def _first_string(payload: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _collapse_whitespace(value)
        if isinstance(value, int):
            return str(value)
    return None


def _list_field(payload: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return [_collapse_whitespace(value)]
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
            return [
                _collapse_whitespace(str(item)) for item in value if str(item).strip()
            ]
    return []


def _location_from_mapping(payload: Mapping[str, Any]) -> str:
    location = payload.get("location")
    if isinstance(location, Mapping):
        name = location.get("name")
        if isinstance(name, str) and name.strip():
            return _collapse_whitespace(name)
    if isinstance(location, str) and location.strip():
        return _collapse_whitespace(location)
    return _first_string(payload, ("location_name", "workplace")) or "Unspecified"


def _salary_from_mapping(payload: Mapping[str, Any]) -> CompensationRange | None:
    minimum = payload.get("salary_min")
    maximum = payload.get("salary_max")
    if isinstance(minimum, int) or isinstance(maximum, int):
        return CompensationRange(
            minimum=minimum if isinstance(minimum, int) else None,
            maximum=maximum if isinstance(maximum, int) else None,
        )
    salary = _first_string(payload, ("salary_range", "compensation"))
    return parse_salary(salary) if salary else None


def _company_from_url(url: str) -> str:
    host_parts = [part for part in urlsplit(url).netloc.split(".") if part]
    if host_parts:
        return host_parts[0].replace("-", " ").title()
    return "Unknown Company"


def _source_job_id(external_id: str | None, application_url: str) -> str:
    if external_id:
        return normalize_text(external_id).replace(" ", "-")
    digest = hashlib.sha256(canonicalize_url(application_url).encode()).hexdigest()
    return f"url-{digest[:16]}"


def _parse_salary_number(value: str) -> int:
    normalized = value.replace("$", "").replace(",", "").strip().casefold()
    if normalized.endswith("k"):
        return int(float(normalized[:-1]) * 1000)
    number = int(normalized)
    if number < 1000:
        return number * 1000
    return number


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.strip().split())
