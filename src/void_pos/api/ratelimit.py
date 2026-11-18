"""Throttle guessing at pairing codes.

A pairing code is 8 characters from a 31-letter alphabet and lives 15 minutes, so guessing
one is already impractical; this makes it hopeless. Only failed attempts count: after
``per_client`` failures from one address, or ``overall`` failures from everywhere, within
``window`` seconds, pairing answers 429 until the oldest failure ages out.

The count lives in memory, which suits the one API process a venue runs. The client is
the first ``X-Forwarded-For`` address when a reverse proxy sets one, else the connection's
address. The web till's own ``/api`` proxy doesn't set the header, so tills pairing through
it share one count: ten wrong codes hold up pairing at the venue for a few minutes, which
is the point. The header can be forged by anyone who reaches the API directly, which is
what the overall limit is for.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from functools import lru_cache
from threading import Lock

from fastapi import Request

from ..errors import VoidPosError

GLOBAL = "*"


class TooManyAttempts(VoidPosError):
    status_code = 429
    code = "too_many_attempts"

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            f"too many wrong pairing codes; try again in {retry_after} seconds",
            retry_after=retry_after,
        )
        self.headers = {"Retry-After": str(retry_after)}


class AttemptLimiter:
    def __init__(
        self,
        *,
        per_client: int = 10,
        overall: int = 50,
        window: float = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_client = per_client
        self.overall = overall
        self.window = window
        self.clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        """The key's failures still inside the window (empty if it has none: not stored)."""
        times = self._failures.get(key, deque())
        while times and times[0] <= now - self.window:
            times.popleft()
        return times

    def check(self, client: str) -> None:
        """Raise TooManyAttempts if this client, or everyone, has failed too often. Only
        failures are stored, so checking costs no memory however many clients call."""
        with self._lock:
            now = self.clock()
            for key, limit in ((client, self.per_client), (GLOBAL, self.overall)):
                times = self._recent(key, now)
                if len(times) >= limit:
                    raise TooManyAttempts(max(1, math.ceil(times[0] + self.window - now)))

    def failed(self, client: str) -> None:
        with self._lock:
            now = self.clock()
            for key in (client, GLOBAL):
                self._failures[key] = self._recent(key, now)
                self._failures[key].append(now)
            for key in list(self._failures):
                if not self._recent(key, now):
                    del self._failures[key]


def client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"


@lru_cache
def get_pairing_limiter() -> AttemptLimiter:
    return AttemptLimiter()
