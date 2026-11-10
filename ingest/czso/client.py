"""Polite HTTP client for the ČSÚ DataStat API.

One request at a time, a fixed pause between requests, an identifying
User-Agent, and retries (with backoff) only for transient failures.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

from urllib.parse import urlencode

import httpx

KATALOG = "https://data.csu.gov.cz/api/katalog/v1"
DOTAZ = "https://data.csu.gov.cz/api/dotaz/v1"

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_TRIES = 5


class TooLarge(Exception):
    """The API refuses to generate this selection synchronously."""

    def __init__(self, records: int | None):
        super().__init__(f"selection too large ({records} records)")
        self.records = records


class ApiError(Exception):
    pass


class Client:
    def __init__(self, user_agent: str, delay_s: float = 1.0):
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "cs"},
            timeout=httpx.Timeout(300.0, connect=30.0),
            follow_redirects=False,
        )
        self._delay = delay_s
        self._last = 0.0
        self.requests = 0

    def close(self) -> None:
        self._http.close()

    def _wait(self) -> None:
        pause = self._delay - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)

    def get(self, url: str, params: dict | None = None, accept: str = "application/json") -> httpx.Response:
        if params:
            # The API does not decode %2A / %2C in filters, so keep them literal.
            url = f"{url}?{urlencode(params, safe='*,')}"
        for attempt in range(1, MAX_TRIES + 1):
            self._wait()
            try:
                resp = self._http.get(url, headers={"Accept": accept})
            except httpx.TransportError as exc:
                self._last = time.monotonic()
                if attempt == MAX_TRIES:
                    raise ApiError(f"{url}: {exc!r}") from exc
                time.sleep(min(60, 2 ** attempt))
                continue
            self._last = time.monotonic()
            self.requests += 1
            if resp.status_code == 200:
                return resp
            if resp.status_code == 400 and "too large" in resp.text:
                try:
                    records = resp.json().get("pocetZaznamu")
                except ValueError:
                    records = None
                raise TooLarge(records)
            if resp.status_code in RETRY_STATUS and attempt < MAX_TRIES:
                retry_after = resp.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                time.sleep(min(120, wait))
                continue
            raise ApiError(f"HTTP {resp.status_code} for {resp.url}: {resp.text[:300]}")
        raise AssertionError("unreachable")

    def get_json(self, url: str, params: dict | None = None):
        # Decimal keeps numbers exactly as the API printed them.
        return json.loads(self.get(url, params).text, parse_float=Decimal)

    def get_bytes(self, url: str, params: dict | None = None, accept: str = "text/csv") -> bytes:
        return self.get(url, params, accept=accept).content
