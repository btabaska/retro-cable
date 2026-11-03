"""Minimal JSON-over-HTTP for the ingest clients (stdlib only)."""

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

# A fetch takes an API path and query params and returns parsed JSON. Tests pass recorded ones.
Fetch = Callable[[str, Mapping[str, str] | None], Any]


class FetchError(OSError):
    """A request failed. It names only the status (or error type) and the API path: never the
    credentials, the base URL or the original exception, which is not chained."""


def json_fetch(base_url: str, headers: Mapping[str, str], timeout: float = 30) -> Fetch:
    """Fetch JSON from base_url. Credentials go in headers so they never appear in URLs or logs.

    Query values are percent-encoded (spaces as %20), which every API here accepts and Seerr
    requires. Any failure is re-raised as a FetchError without the original attached."""

    def fetch(path: str, params: Mapping[str, str] | None = None) -> Any:
        url = base_url.rstrip("/") + path
        if params:
            url += "?" + urlencode(params, quote_via=quote)
        request = Request(url, headers={"Accept": "application/json", **headers})
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as err:
            raise FetchError(f"HTTP {err.code} for {path}") from None
        except (OSError, ValueError) as err:
            raise FetchError(f"{type(err).__name__} for {path}") from None

    return fetch
