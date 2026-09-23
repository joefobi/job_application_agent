"""Extract scoring fields from normalized job postings."""

from __future__ import annotations

import hashlib
import html
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
    r"(?P<currency_first>\$|usd)?\s*"
    r"(?P<first>\d{2,3}(?:,\d{3})?|\d{2,3}k)"
    r"\s*(?:-|–|—|to)\s*"
    r"(?P<currency_second>\$|usd)?\s*"
    r"(?P<second>\d{2,3}(?:,\d{3})?|\d{2,3}k)",
    re.IGNORECASE,
)

SALARY_CONTEXT_TERMS = (
    "salary",
    "compensation",
    "base pay",
    "pay range",
)

NON_SALARY_COMPENSATION_TERMS = (
    "bonus",
    "equity",
    "stock",
    "commission",
    "stipend",
)

ONSITE_TERMS = (
    "onsite",
    "on site",
    "in office",
    "office based",
    "office first",
)

NEGATED_REMOTE_TERMS = (
    "not remote",
    "non remote",
    "no remote",
    "remote work is unavailable",
    "remote unavailable",
    "remote work unavailable",
    "remote is unavailable",
    "remote option is unavailable",
)

EXPERIENCE_PATTERNS = (
    re.compile(
        r"(?:at\s+least|minimum\s+of|minimum|requires?)\s+"
        r"(?P<years>\d{1,2})\+?\s*(?:years|yrs)\b"
        r"(?=[^.]{0,80}\bexperience\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<years>\d{1,2})\+?\s*(?:years|yrs)\b"
        r"(?=[^.]{0,80}\b(?:experience|professional|industry)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<years>\d{1,2})\s*(?:-|–|—|to)\s*\d{1,2}\s*(?:years|yrs)\b"
        r"(?=[^.]{0,80}\bexperience\b)",
        re.IGNORECASE,
    ),
)


class JobParser:
    """Add parser-derived fields needed by filters and fit scoring."""

    def parse(
        self,
        payload: JobPosting | Mapping[str, Any] | str,
        *,
        source: JobSource | str | None = None,
        company: str | None = None,
        application_url: str | None = None,
    ) -> JobPosting:
        """Return a posting enriched with parser-derived fields.

        Args:
            payload: Existing normalized posting, structured source payload from an
                ingestion adapter/test fixture, or raw posting text.
            source: Optional source override for mapping payloads.
            company: Optional company override for mapping payloads.
            application_url: Optional application URL override for mapping payloads.

        Returns:
            A ``JobPosting`` with salary, requirement, remote, seniority, and work
            authorization fields populated when the parser can infer them.
        """

        if isinstance(payload, str):
            posting = _posting_from_text(payload, source, company, application_url)
        elif isinstance(payload, JobPosting):
            posting = payload
        else:
            posting = _posting_from_mapping(payload, source, company, application_url)
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
            minimum_years_experience=(
                posting.minimum_years_experience
                if posting.minimum_years_experience is not None
                else parse_minimum_years_experience(searchable_text)
            ),
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

    decoded = html.unescape(value)
    with_breaks = re.sub(r"(?i)<\s*(br|/p|/li|/h[1-6])\s*/?>", "\n", decoded)
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

    for match in SALARY_PATTERN.finditer(text):
        if _is_salary_match(text, match):
            return CompensationRange(
                minimum=_parse_salary_number(match.group("first")),
                maximum=_parse_salary_number(match.group("second")),
                currency="USD",
                period="year",
            )
    return None


def parse_minimum_years_experience(text: str) -> int | None:
    """Parse the minimum required years of experience from posting text.

    Args:
        text: Posting text that may describe an experience requirement.

    Returns:
        Minimum required years, or None when no clear requirement is found.
    """

    matches: list[int] = []
    for pattern in EXPERIENCE_PATTERNS:
        for match in pattern.finditer(text):
            matches.append(int(match.group("years")))
    if not matches:
        return None
    return min(matches)


def infer_remote(text: str) -> bool | None:
    """Infer whether a posting is remote-compatible from text.

    Args:
        text: Title, location, and posting content to inspect.

    Returns:
        True for remote roles, False for clearly on-site roles, or None when the
        text is ambiguous.
    """

    normalized = normalize_text(text)
    if any(term in normalized for term in ONSITE_TERMS + NEGATED_REMOTE_TERMS):
        return False
    if any(term in normalized for term in ("remote", "work from home", "wfh")):
        return True
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


def _posting_from_text(
    text: str,
    source: JobSource | str | None,
    company: str | None,
    application_url: str | None,
) -> JobPosting:
    if application_url is None:
        raise ValueError("application_url is required for text postings")

    content = strip_html(text)
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        raise ValueError("posting text is empty")

    return JobPosting(
        source=_coerce_source(source, application_url),
        source_job_id=_source_job_id(None, application_url),
        title=lines[0],
        company=company or _company_from_url(application_url),
        application_url=application_url,
        canonical_url=canonicalize_url(application_url),
        location=_location_from_text(lines) or "Unspecified",
        content=content,
    )


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


def _location_from_text(lines: Sequence[str]) -> str | None:
    for line in lines[1:8]:
        if "location:" in line.casefold():
            return line.split(":", maxsplit=1)[1].strip()
    return None


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
    parsed = urlsplit(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.casefold()
    if "greenhouse.io" in host and path_parts:
        return _title_from_slug(path_parts[0])
    if "lever.co" in host and path_parts:
        return _title_from_slug(path_parts[0])

    host_parts = [part for part in parsed.netloc.split(".") if part]
    if host_parts:
        return _title_from_slug(host_parts[0])
    return "Unknown Company"


def _is_salary_match(text: str, match: re.Match[str]) -> bool:
    matched_text = match.group(0).casefold()
    context_start = max(0, match.start() - 40)
    before_context = normalize_text(text[context_start : match.start()])
    after_context = normalize_text(text[match.end() : match.end() + 30])
    salary_context_position = _last_term_position(before_context, SALARY_CONTEXT_TERMS)
    non_salary_context_position = _last_term_position(
        before_context, NON_SALARY_COMPENSATION_TERMS
    )
    following_salary_context_position = _first_term_position(
        after_context, SALARY_CONTEXT_TERMS
    )
    following_non_salary_context_position = _first_term_position(
        after_context, NON_SALARY_COMPENSATION_TERMS
    )
    has_currency = "$" in matched_text or "usd" in matched_text
    if non_salary_context_position > salary_context_position:
        return False
    if has_currency and (
        salary_context_position != -1
        or following_non_salary_context_position == -1
        or (
            following_salary_context_position != -1
            and following_salary_context_position
            < following_non_salary_context_position
        )
    ):
        return True
    if salary_context_position == -1 and following_non_salary_context_position != -1:
        return False

    has_context = salary_context_position != -1
    scale_text = match.group("first").casefold() + match.group("second").casefold()
    has_salary_scale = "k" in scale_text or "," in scale_text
    return has_context and has_salary_scale


def _last_term_position(text: str, terms: Sequence[str]) -> int:
    return max((text.rfind(term) for term in terms), default=-1)


def _first_term_position(text: str, terms: Sequence[str]) -> int:
    positions = [text.find(term) for term in terms if text.find(term) != -1]
    return min(positions, default=-1)


def _title_from_slug(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").title()


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
