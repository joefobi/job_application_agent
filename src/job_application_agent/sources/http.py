"""Small HTTP helpers for source ingestion."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen


def fetch_json(url: str, timeout: float = 30.0) -> Any:
    """Fetch JSON from a URL using the standard library.

    Args:
        url: URL to fetch.
        timeout: Network timeout in seconds.

    Returns:
        Decoded JSON data.
    """

    request = Request(url, headers={"User-Agent": "job-application-agent/0.1"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)
