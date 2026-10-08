"""A small Scryfall API client that follows the documented rules.

Rules (https://scryfall.com/docs/api and /docs/api/rate-limits, read 09/10/2026):
every request carries a descriptive User-Agent and an Accept header; search, named,
random and collection are limited to 2 requests per second, everything else to 10 per
second; a 429 means back off for 30 seconds. Bulk files on *.scryfall.io have no limit.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

SLOW_PATHS = ("/cards/search", "/cards/named", "/cards/random", "/cards/collection")
SLOW_INTERVAL = 0.5
FAST_INTERVAL = 0.1
RETRY_AFTER_429 = 30.0


class ScryfallError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Scryfall {status}: {message}")
        self.status = status


class RateGate:
    """Minimum spacing between requests, per bucket, across threads."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep=time.sleep) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._sleep = sleep

    def wait(self, bucket: str, interval: float) -> None:
        with self._lock:
            now = self._clock()
            last = self._last.get(bucket)
            ready_at = now if last is None else last + interval
            delay = max(0.0, ready_at - now)
            self._last[bucket] = max(now, ready_at)
        if delay > 0:
            self._sleep(delay)


class ScryfallClient:
    def __init__(
        self,
        user_agent: str,
        base_url: str = "https://api.scryfall.com",
        transport: httpx.BaseTransport | None = None,
        gate: RateGate | None = None,
        sleep=time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0, read=120.0),
            transport=transport,
            follow_redirects=True,
        )
        self._gate = gate or RateGate()
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _bucket(self, path: str) -> tuple[str, float]:
        if path.startswith(SLOW_PATHS):
            return "slow", SLOW_INTERVAL
        return "fast", FAST_INTERVAL

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        bucket, interval = self._bucket(path if not path.startswith("http") else "/other")
        for attempt in (1, 2):
            self._gate.wait(bucket, interval)
            response = self._http.request(method, url, **kwargs)
            if response.status_code == 429 and attempt == 1:
                log.warning("Scryfall 429 on %s; backing off %.0fs", path, RETRY_AFTER_429)
                self._sleep(RETRY_AFTER_429)
                continue
            break
        if response.status_code >= 400:
            try:
                detail = response.json().get("details", response.text)
            except ValueError:
                detail = response.text
            raise ScryfallError(response.status_code, detail)
        return response.json()

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None)

    def bulk_data(self) -> list[dict[str, Any]]:
        return self.get("/bulk-data")["data"]

    def bulk_item(self, kind: str) -> dict[str, Any]:
        for item in self.bulk_data():
            if item.get("type") == kind:
                return item
        raise ScryfallError(404, f"No bulk data of type {kind}")

    def sets(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = []
        page = self.get("/sets")
        data.extend(page["data"])
        while page.get("has_more") and page.get("next_page"):
            page = self.request("GET", page["next_page"])
            data.extend(page["data"])
        return data

    def named_fuzzy(self, name: str, set_code: str | None = None) -> dict[str, Any] | None:
        params: dict[str, Any] = {"fuzzy": name}
        if set_code:
            params["set"] = set_code
        try:
            return self.get("/cards/named", **params)
        except ScryfallError as exc:
            if exc.status == 404:
                return None
            raise

    def collection(self, identifiers: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /cards/collection, up to 75 identifiers per call (documented limit)."""
        found: list[dict[str, Any]] = []
        not_found: list[dict[str, Any]] = []
        for start in range(0, len(identifiers), 75):
            chunk = identifiers[start : start + 75]
            page = self.request("POST", "/cards/collection", json={"identifiers": chunk})
            found.extend(page.get("data", []))
            not_found.extend(page.get("not_found", []))
        return {"data": found, "not_found": not_found}

    def stream(self, url: str) -> httpx.Response:
        """Open a streaming GET (used for bulk files on data.scryfall.io)."""
        request = self._http.build_request("GET", url, headers={"Accept": "*/*"})
        return self._http.send(request, stream=True)
