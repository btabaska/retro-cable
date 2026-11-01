"""Minimal JSON-over-HTTP for the ingest clients (stdlib only)."""

import json
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode

# A fetch takes an API path and query params and returns parsed JSON. Tests pass recorded ones.
Fetch = Callable[[str, Mapping[str, str] | None], Any]


def json_fetch(base_url: str, headers: Mapping[str, str], timeout: float = 30) -> Fetch:
    """Fetch JSON from base_url. Credentials go in headers so they never appear in URLs or logs."""

    def fetch(path: str, params: Mapping[str, str] | None = None) -> Any:
        url = base_url.rstrip("/") + path
        if params:
            url += "?" + urlencode(params)
        request = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    return fetch
