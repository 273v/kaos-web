"""Per-domain rate limiting middleware using token bucket algorithm."""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from kaos_web.middleware.base import Handler
from kaos_web.models import WebRequest, WebResponse

logger = logging.getLogger(__name__)


class RateLimitConfig(BaseModel):
    """Rate limit middleware configuration."""

    model_config = ConfigDict(frozen=True)

    requests_per_second: float = 10.0
    burst_size: int | None = None
    per_host: bool = True


class RateLimitMiddleware:
    """Per-domain rate limiting using token bucket algorithm.

    Tracks separate token buckets per host (domain). Each bucket allows
    burst_size tokens to be consumed instantly, then refills at
    requests_per_second rate.

    If a request would exceed the rate, the middleware blocks until
    a token is available.
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._burst = (
            self.config.burst_size
            if self.config.burst_size is not None
            else int(self.config.requests_per_second)
        )
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    def _get_bucket_key(self, url: str) -> str:
        """Extract bucket key from URL."""
        if self.config.per_host:
            parsed = urlparse(url)
            return parsed.netloc or "unknown"
        return "global"

    async def _acquire_token(self, key: str) -> float:
        """Acquire a token from the bucket. Returns wait time (0 if immediate)."""
        async with self._lock:
            now = time.monotonic()

            if key not in self._buckets:
                # New bucket: full tokens
                self._buckets[key] = (float(self._burst) - 1.0, now)
                return 0.0

            tokens, last_update = self._buckets[key]

            # Refill tokens based on elapsed time
            elapsed = now - last_update
            tokens = min(float(self._burst), tokens + elapsed * self.config.requests_per_second)

            if tokens >= 1.0:
                # Token available
                self._buckets[key] = (tokens - 1.0, now)
                return 0.0

            # Calculate wait time until next token
            wait_time = (1.0 - tokens) / self.config.requests_per_second
            return wait_time

    async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
        """Rate limit the request, then delegate to next handler."""
        key = self._get_bucket_key(request.url)
        wait_time = await self._acquire_token(key)

        if wait_time > 0:
            logger.debug("Rate limited: waiting %.2fs for %s", wait_time, key)
            await asyncio.sleep(wait_time)
            # Re-acquire after waiting
            await self._acquire_token(key)

        return await next_handler(request)
