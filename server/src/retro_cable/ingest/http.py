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


# A request takes a method, an API path, query params and a JSON body (or None) and returns
# parsed JSON (None for an empty reply). Tests pass fakes.
Request = Callable[[str, str, Mapping[str, str] | None, Any], Any]


def json_request(base_url: str, headers: Mapping[str, str], timeout: float = 30) -> Request:
    """Send JSON to base_url and read JSON back (sonarr-livetv and radarr-livetv). As with
    json_fetch, credentials go in headers: never in a URL, a log line or a repr."""
    sent = {"Accept": "application/json", **headers}

    def request(
        method: str, path: str, params: Mapping[str, str] | None = None, body: Any = None
    ) -> Any:
        url = base_url.rstrip("/") + path
        if params:
            url += "?" + urlencode(params)
        data = None if body is None else json.dumps(body).encode()
        extra = {} if data is None else {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers={**sent, **extra}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    return request
