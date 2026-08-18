from __future__ import annotations

import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request, status


def verify_api_token(request: Request) -> None:
    """Reject unauthenticated requests when API_TOKEN is configured."""
    settings = request.app.state.runtime.settings
    expected = (settings.api_token or "").strip()
    if not expected:
        return
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        provided = header[len("Bearer "):].strip()
        if provided == expected:
            return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


class RateLimiter:
    """Fixed-window token bucket limiter keyed by client IP + route."""

    def __init__(self, max_per_minute: int) -> None:
        self._max_per_minute = max(1, int(max_per_minute))
        self._lock = Lock()
        self._buckets: dict[str, deque[float]] = {}

    def _client_key(self, request: Request) -> str:
        client = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            client = forwarded.split(",", 1)[0].strip() or client
        return f"{client}:{request.url.path}"

    def check(self, request: Request) -> None:
        key = self._client_key(request)
        now = time.monotonic()
        window = 60.0
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= self._max_per_minute:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Slow down and retry shortly.",
                )
            bucket.append(now)


_RATE_LIMITER: RateLimiter | None = None


def get_rate_limiter(max_per_minute: int = 60) -> RateLimiter:
    global _RATE_LIMITER
    if _RATE_LIMITER is None:
        _RATE_LIMITER = RateLimiter(max_per_minute=max_per_minute)
    return _RATE_LIMITER


def enforce_rate_limit(request: Request) -> None:
    get_rate_limiter().check(request)
