"""Normalization helpers for deterministic job identity."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "gh_src",
    "lever-source",
    "source",
    "src",
    "ref",
    "referrer",
}


def canonicalize_url(url: str) -> str:
    """Return a stable URL for duplicate detection.

    Args:
        url: Raw URL from a job source.

    Returns:
        A normalized URL with lowercase scheme/host, no fragment, and common
        tracking query parameters removed.
    """

    parsed = urlsplit(url.strip())
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    ]
    path = parsed.path.rstrip("/") or parsed.path
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(filtered_query, doseq=True),
            "",
        )
    )


def make_job_fingerprint(company: str, title: str, location: str | None) -> str:
    """Return a deterministic company/title/location fingerprint.

    Args:
        company: Employer name.
        title: Job title.
        location: Human-readable location, if known.

    Returns:
        A compact fingerprint suitable for duplicate checks.
    """

    return "|".join(
        [
            normalize_text(company),
            normalize_text(title),
            normalize_text(location or ""),
        ]
    )


def normalize_text(value: str) -> str:
    """Normalize free text for equality checks."""

    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _is_tracking_key(key: str) -> bool:
    """Return whether a query parameter is used for tracking."""

    lowered = key.lower()
    return lowered in TRACKING_QUERY_KEYS or lowered.startswith(TRACKING_QUERY_PREFIXES)
