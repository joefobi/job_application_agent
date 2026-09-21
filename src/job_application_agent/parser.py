"""Job parser for turning source payloads or posting text into normalized jobs."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
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
    """Deterministic parser for structured ATS payloads and raw posting text."""

    def parse(
        self,
        payload: Mapping[str, Any] | str,
        *,
        source: JobSource | str | None = None,
        company: str | None = None,
        application_url: str | None = None,
    ) -> JobPosting:
        """Parse a raw posting into a ``JobPosting``.

        ``payload`` may be a mapping from an ingestion adapter or raw posting
        HTML/text. Explicit keyword arguments take precedence over payload fields.
        """

        if isinstance(payload, str):
            return self._parse_text(
                payload,
                source=self._coerce_source(source, application_url),
                company=company,
                application_url=application_url,
            )

        resolved_url = application_url or self._first_string(
            payload, ("application_url", "absolute_url", "hostedUrl", "url")
        )
        if resolved_url is None:
            raise ValueError("application_url is required")

        resolved_source = self._coerce_source(source, resolved_url)
        resolved_company = company or self._first_string(
            payload, ("company", "company_name", "department")
        )
        if resolved_company is None:
            resolved_company = self._company_from_url(resolved_url)

        title = self._first_string(payload, ("title", "text", "name"))
        if title is None:
            raise ValueError("title is required")

        description = self._description_from_mapping(payload)
        location = self._location_from_mapping(payload)
        external_id = self._first_string(
            payload, ("id", "job_id", "internal_job_id", "requisition_id")
        )
        requirements = self._list_field(payload, ("requirements", "qualifications"))
        nice_to_haves = self._list_field(
            payload, ("nice_to_haves", "preferred_qualifications")
        )

        if not requirements:
            requirements = self._extract_section(description, "requirements")
        if not nice_to_haves:
            nice_to_haves = self._extract_section(description, "nice_to_haves")

        return JobPosting(
            source=resolved_source,
            source_job_id=self._source_job_id(external_id, resolved_url),
            title=title,
            company=resolved_company,
            application_url=resolved_url,
            canonical_url=canonicalize_url(resolved_url),
            location=location,
            content=description,
            salary_range=self._salary_from_mapping(payload)
            or parse_salary(description),
            requirements=tuple(requirements),
            nice_to_haves=tuple(nice_to_haves),
            remote=self._remote_from_text(" ".join((title, location, description))),
            seniority=infer_seniority(title),
            work_authorization=self._work_authorization_from_text(description),
            raw_data=dict(payload),
        )

    def _parse_text(
        self,
        posting: str,
        *,
        source: JobSource,
        company: str | None,
        application_url: str | None,
    ) -> JobPosting:
        text = strip_html(posting)
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise ValueError("posting text is empty")
        if application_url is None:
            raise ValueError("application_url is required for text postings")
        resolved_company = company or self._company_from_url(application_url)
        title = lines[0]
        location = next(
            (
                line.removeprefix("Location:").strip()
                for line in lines[1:8]
                if "location:" in line.casefold()
            ),
            "Unspecified",
        )

        return JobPosting(
            source=source,
            source_job_id=self._source_job_id(None, application_url),
            title=title,
            company=resolved_company,
            application_url=application_url,
            canonical_url=canonicalize_url(application_url),
            location=location,
            content=text,
            salary_range=parse_salary(text),
            requirements=tuple(self._extract_section(text, "requirements")),
            nice_to_haves=tuple(self._extract_section(text, "nice_to_haves")),
            remote=self._remote_from_text(text),
            seniority=infer_seniority(title),
            work_authorization=self._work_authorization_from_text(text),
        )

    def _coerce_source(
        self, source: JobSource | str | None, application_url: str | None
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

    def _first_string(
        self, payload: Mapping[str, Any], keys: Sequence[str]
    ) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return collapse_whitespace(value)
            if isinstance(value, int):
                return str(value)
        return None

    def _list_field(self, payload: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return [collapse_whitespace(value)]
            if isinstance(value, Iterable) and not isinstance(
                value, (str, bytes, Mapping)
            ):
                return [
                    collapse_whitespace(str(item))
                    for item in value
                    if str(item).strip()
                ]
        return []

    def _description_from_mapping(self, payload: Mapping[str, Any]) -> str:
        value = self._first_string(
            payload, ("description", "content", "job_description", "description_html")
        )
        return strip_html(value or "")

    def _location_from_mapping(self, payload: Mapping[str, Any]) -> str:
        location = payload.get("location")
        if isinstance(location, Mapping):
            name = location.get("name")
            if isinstance(name, str) and name.strip():
                return collapse_whitespace(name)
        if isinstance(location, str) and location.strip():
            return collapse_whitespace(location)
        return (
            self._first_string(payload, ("location_name", "workplace")) or "Unspecified"
        )

    def _salary_from_mapping(
        self, payload: Mapping[str, Any]
    ) -> CompensationRange | None:
        minimum = payload.get("salary_min")
        maximum = payload.get("salary_max")
        if isinstance(minimum, int) or isinstance(maximum, int):
            return CompensationRange(
                minimum=minimum if isinstance(minimum, int) else None,
                maximum=maximum if isinstance(maximum, int) else None,
            )
        salary = self._first_string(payload, ("salary_range", "compensation"))
        return parse_salary(salary) if salary else None

    def _extract_section(self, text: str, section: str) -> list[str]:
        normalized_headings = SECTION_HEADINGS[section]
        lines = [collapse_whitespace(line) for line in text.splitlines()]
        collected: list[str] = []
        in_section = False
        for line in lines:
            lower_line = normalize_text(line).strip(":")
            if lower_line in normalized_headings:
                in_section = True
                continue
            if in_section and lower_line in {
                heading
                for headings in SECTION_HEADINGS.values()
                for heading in headings
            }:
                break
            if in_section:
                if not line:
                    if collected:
                        break
                    continue
                cleaned = line.lstrip("-*• ").strip()
                if cleaned:
                    collected.append(cleaned)
        return collected[:12]

    def _remote_from_text(self, text: str) -> bool | None:
        normalized = normalize_text(text)
        if any(term in normalized for term in ("remote", "work from home", "wfh")):
            return True
        if any(term in normalized for term in ("onsite", "on-site", "in office")):
            return False
        return None

    def _work_authorization_from_text(self, text: str) -> tuple[str, ...]:
        normalized = normalize_text(text)
        tags: list[str] = []
        if (
            "authorized to work in the us" in normalized
            or "u.s. work authorization" in normalized
        ):
            tags.append("us_authorization_required")
        if "visa sponsorship" in normalized and any(
            phrase in normalized
            for phrase in ("not provide", "unable to", "do not offer", "no visa")
        ):
            tags.append("no_visa_sponsorship")
        return tuple(tags)

    def _company_from_url(self, url: str) -> str:
        host_parts = [part for part in urlsplit(url).netloc.split(".") if part]
        if host_parts:
            return host_parts[0].replace("-", " ").title()
        return "Unknown Company"

    def _source_job_id(self, external_id: str | None, application_url: str) -> str:
        if external_id:
            return normalize_text(external_id).replace(" ", "-")
        digest = hashlib.sha256(canonicalize_url(application_url).encode()).hexdigest()
        return f"url-{digest[:16]}"


def strip_html(value: str) -> str:
    """Convert simple HTML postings into readable text."""

    with_breaks = re.sub(r"(?i)<\s*(br|/p|/li|/h[1-6])\s*/?>", "\n", value)
    without_tags = re.sub(r"<[^>]+>", " ", with_breaks)
    return "\n".join(
        collapse_whitespace(line) for line in without_tags.splitlines() if line.strip()
    )


def collapse_whitespace(value: str) -> str:
    """Collapse whitespace without changing meaningful punctuation."""

    return " ".join(value.strip().split())


def parse_salary(text: str) -> CompensationRange | None:
    """Parse a US annual salary range from text when present."""

    match = SALARY_PATTERN.search(text)
    if match is None:
        return None
    return CompensationRange(
        minimum=_parse_salary_number(match.group("first")),
        maximum=_parse_salary_number(match.group("second")),
        currency="USD",
        period="year",
    )


def infer_seniority(title: str) -> str | None:
    """Infer a coarse seniority label from a title."""

    normalized_title = normalize_text(title)
    for term in SENIORITY_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", normalized_title):
            return term
    return None


def _parse_salary_number(value: str) -> int:
    normalized = value.replace("$", "").replace(",", "").strip().casefold()
    if normalized.endswith("k"):
        return int(float(normalized[:-1]) * 1000)
    number = int(normalized)
    if number < 1000:
        return number * 1000
    return number
