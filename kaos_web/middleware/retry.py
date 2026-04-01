"""Retry middleware with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import logging
import random

from pydantic import BaseModel, ConfigDict

from kaos_web.errors import WebError, WebRateLimitError
from kaos_web.middleware.base import Handler
from kaos_web.models import WebRequest, WebResponse

logger = logging.getLogger(__name__)


class RetryConfig(BaseModel):
    """Retry middleware configuration."""

    model_config = ConfigDict(frozen=True)

    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    respect_retry_after: bool = True


# Status codes that should trigger a retry
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class RetryMiddleware:
    """Retry failed requests with exponential backoff and jitter.

    Retries on:
    - WebError with retryable=True (timeouts, network errors, server errors)
    - WebRateLimitError (respects Retry-After header if configured)
    - Status codes 429, 500, 502, 503, 504

    Backoff: delay = min(initial * base^attempt, max) * random(0.5, 1.0)
    """

    def __init__(self, config: RetryConfig | None = None) -> None:
        self.config = config or RetryConfig()

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate backoff delay for a given attempt."""
        delay = min(
            self.config.initial_delay * (self.config.exponential_base**attempt),
            self.config.max_delay,
        )
        if self.config.jitter:
            delay *= 0.5 + random.random()
        return delay

    async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
        """Process request with retry logic."""
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                response = await next_handler(request)
                return response

            except WebRateLimitError as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    raise

                # Use Retry-After header if available and configured
                if self.config.respect_retry_after and exc.retry_after:
                    delay = exc.retry_after
                else:
                    delay = self._calculate_delay(attempt)

                logger.warning(
                    "Rate limited (429) on %s, retry %d/%d in %.1fs",
                    request.url,
                    attempt + 1,
                    self.config.max_retries,
                    delay,
                )
                await asyncio.sleep(delay)

            except WebError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.config.max_retries:
                    raise

                delay = self._calculate_delay(attempt)
                logger.warning(
                    "%s on %s, retry %d/%d in %.1fs: %s",
                    type(exc).__name__,
                    request.url,
                    attempt + 1,
                    self.config.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        # Should not reach here, but just in case
        if last_error:
            raise last_error
        msg = "Retry loop completed without result"
        raise RuntimeError(msg)
