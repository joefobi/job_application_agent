"""Structured job fact extraction from job descriptions."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol, cast

from job_application_agent.models import (
    CompensationRange,
    ExtractedJobFacts,
    JobPosting,
)
from job_application_agent.parser import (
    infer_remote,
    infer_seniority,
    infer_work_authorization,
    parse_minimum_years_experience,
    parse_salary,
    strip_html,
)

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
LLM_MODEL_ENV = "JOB_AGENT_LLM_MODEL"
LLM_ENDPOINT_ENV = "JOB_AGENT_LLM_ENDPOINT"
DEFAULT_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_EXTRACTION_MODEL = "gpt-4.1-mini"


class JobFactExtractor(Protocol):
    """Extract structured job facts from a normalized posting."""

    def extract(self, job: JobPosting) -> ExtractedJobFacts:
        """Extract structured facts from a job posting.

        Args:
            job: Normalized job posting.

        Returns:
            Structured facts parsed from the posting.
        """


class JsonLLMClient(Protocol):
    """Minimal JSON-returning LLM client used by the extractor."""

    def complete_json(self, prompt: str) -> Mapping[str, Any]:
        """Return a JSON object for a prompt.

        Args:
            prompt: Extraction prompt.

        Returns:
            Decoded JSON object returned by the LLM.
        """


class LLMJobFactExtractor:
    """Extract hard-filter and role-scoring facts with an LLM."""

    def __init__(self, client: JsonLLMClient) -> None:
        """Create an LLM-backed fact extractor.

        Args:
            client: JSON-returning LLM client.
        """

        self.client = client

    def extract(self, job: JobPosting) -> ExtractedJobFacts:
        """Extract structured job facts from one posting.

        Args:
            job: Normalized job posting.

        Returns:
            Structured extracted facts.
        """

        return facts_from_mapping(self.client.complete_json(_extraction_prompt(job)))


class OpenAIResponsesJsonClient:
    """Small stdlib client for OpenAI-compatible Responses JSON extraction."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEFAULT_OPENAI_RESPONSES_URL,
        model: str = DEFAULT_EXTRACTION_MODEL,
        timeout: float = 30.0,
    ) -> None:
        """Create an OpenAI Responses JSON client.

        Args:
            api_key: API key for the Responses API.
            endpoint: HTTP endpoint for JSON responses.
            model: Model name to use for extraction.
            timeout: Request timeout in seconds.
        """

        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout

    def complete_json(self, prompt: str) -> Mapping[str, Any]:
        """Return a JSON object for a prompt.

        Args:
            prompt: Extraction prompt.

        Returns:
            Parsed JSON object from the model response.
        """

        payload = json.dumps(
            {
                "model": self.model,
                "input": prompt,
                "text": {"format": {"type": "json_object"}},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError:
            return {}
        return _response_json(decoded)


class LocalJobFactExtractor:
    """Deterministic local extractor used when no LLM client is configured."""

    def extract(self, job: JobPosting) -> ExtractedJobFacts:
        """Extract structured facts from normalized posting fields.

        Args:
            job: Normalized job posting.

        Returns:
            Structured facts inferred from existing normalized fields.
        """

        content = strip_html(job.content or "")
        searchable_text = " ".join(
            item for item in (job.title, job.location or "", content) if item
        )
        remote = job.remote if job.remote is not None else infer_remote(searchable_text)
        authorization = job.work_authorization or infer_work_authorization(content)
        return ExtractedJobFacts(
            minimum_years_experience=(
                job.minimum_years_experience
                if job.minimum_years_experience is not None
                else parse_minimum_years_experience(searchable_text)
            ),
            salary_range=job.salary_range or parse_salary(content),
            remote_policy=_remote_policy(remote),
            locations=tuple(item for item in (job.location or "",) if item.strip()),
            requires_us_work_authorization=(
                True if "us_authorization_required" in authorization else None
            ),
            visa_sponsorship=(
                "not_available" if "no_visa_sponsorship" in authorization else "unknown"
            ),
            employment_type=job.employment_type,
            seniority=job.seniority or infer_seniority(job.title),
            required_skills=job.requirements,
            nice_to_have_skills=job.nice_to_haves,
            responsibilities=tuple(_responsibility_lines(content, job.requirements)),
            company_description=None,
            evidence={"extractor": "local_fallback"},
        )


def default_job_fact_extractor() -> JobFactExtractor:
    """Return the configured job fact extractor.

    Returns:
        LLM extractor when an API key is configured, otherwise local fallback.
    """

    api_key = os.environ.get(OPENAI_API_KEY_ENV)
    if not api_key:
        return LocalJobFactExtractor()
    return LLMJobFactExtractor(
        OpenAIResponsesJsonClient(
            api_key,
            endpoint=os.environ.get(LLM_ENDPOINT_ENV, DEFAULT_OPENAI_RESPONSES_URL),
            model=os.environ.get(LLM_MODEL_ENV, DEFAULT_EXTRACTION_MODEL),
        )
    )


def facts_from_mapping(data: Mapping[str, Any]) -> ExtractedJobFacts:
    """Convert LLM JSON output into extracted job facts.

    Args:
        data: LLM JSON object.

    Returns:
        Structured extracted facts.
    """

    facts = ExtractedJobFacts.from_json_dict(
        {
            "minimum_years_experience": data.get("minimum_years_experience"),
            "salary_range": _salary_mapping(data),
            "remote_policy": data.get("remote_policy"),
            "locations": data.get("locations"),
            "requires_us_work_authorization": data.get(
                "requires_us_work_authorization"
            ),
            "visa_sponsorship": data.get("visa_sponsorship"),
            "employment_type": data.get("employment_type"),
            "seniority": data.get("seniority"),
            "required_skills": data.get("required_skills"),
            "nice_to_have_skills": data.get("nice_to_have_skills"),
            "responsibilities": data.get("responsibilities"),
            "company_description": data.get("company_description"),
            "evidence": data.get("evidence"),
        }
    )
    return replace(facts, remote_policy=_normalize_remote_policy(facts.remote_policy))


def _extraction_prompt(job: JobPosting) -> str:
    """Build the JSON extraction prompt for one job posting.

    Args:
        job: Normalized job posting.

    Returns:
        Prompt instructing an LLM to extract structured job facts.
    """

    return "\n".join(
        (
            "Extract structured hard-filter and role-relevance facts from this job.",
            "Return only JSON with these keys:",
            "minimum_years_experience, salary_range, remote_policy, locations,",
            "requires_us_work_authorization, visa_sponsorship, employment_type,",
            "seniority, required_skills, nice_to_have_skills, responsibilities,",
            "company_description, evidence.",
            "Use null when the posting does not clearly state a fact.",
            "remote_policy must be remote, hybrid, onsite, or unknown.",
            "visa_sponsorship must be available, not_available, or unknown.",
            "Do not infer from company reputation. Use only the job text.",
            "",
            f"Title: {job.title}",
            f"Company: {job.company}",
            f"Location: {job.location or ''}",
            f"Employment type: {job.employment_type or ''}",
            "Job description:",
            strip_html(job.content or ""),
        )
    )


def _response_json(response: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extract a JSON object from a Responses API payload.

    Args:
        response: Decoded Responses API payload.

    Returns:
        Extracted JSON object, or an empty mapping when no text is present.
    """

    output_text = response.get("output_text")
    if isinstance(output_text, str):
        parsed = json.loads(output_text)
        return cast(Mapping[str, Any], parsed)

    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                text = content_item.get("text")
                if isinstance(text, str):
                    parsed = json.loads(text)
                    return cast(Mapping[str, Any], parsed)
    return {}


def _salary_mapping(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return a normalized salary mapping from LLM output.

    Args:
        data: LLM JSON object.

    Returns:
        Salary mapping, or None when no salary fields are present.
    """

    salary_range = data.get("salary_range")
    if isinstance(salary_range, dict):
        return salary_range
    salary_min = data.get("salary_min")
    salary_max = data.get("salary_max")
    if salary_min is None and salary_max is None:
        return None
    return {
        "minimum": salary_min,
        "maximum": salary_max,
        "currency": data.get("salary_currency") or "USD",
        "period": data.get("salary_period") or "year",
    }


def _normalize_remote_policy(value: str | None) -> str | None:
    """Normalize remote policy labels from LLM output.

    Args:
        value: Raw remote policy value.

    Returns:
        Normalized remote policy or None.
    """

    if value is None:
        return None
    normalized = value.strip().casefold().replace("-", "_")
    if normalized in {"remote", "hybrid", "onsite", "unknown"}:
        return normalized
    if normalized in {"on_site", "on site", "in_office"}:
        return "onsite"
    return "unknown"


def _remote_policy(remote: bool | None) -> str:
    """Convert a parsed remote flag into an extracted policy label.

    Args:
        remote: Parsed remote flag.

    Returns:
        Remote policy label.
    """

    if remote is True:
        return "remote"
    if remote is False:
        return "onsite"
    return "unknown"


def _responsibility_lines(
    content: str, requirements: tuple[str, ...]
) -> tuple[str, ...]:
    """Return role-focused responsibility lines for local fallback extraction.

    Args:
        content: Plain-text job description.
        requirements: Parsed requirements.

    Returns:
        Responsibility-like lines to use for role relevance.
    """

    if content.strip():
        lines = [
            line.strip("-* ")
            for line in content.splitlines()
            if line.strip() and len(line.strip()) > 12
        ]
        if lines:
            return tuple(lines[:8])
    return requirements[:8]
