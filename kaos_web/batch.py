"""Batch fetch — concurrent URL fetching with per-URL error isolation.

Reuses the existing HttpClient middleware chain (retry, rate limit, robots, cache).
Concurrency controlled via asyncio.Semaphore.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from kaos_web.clients.config import HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.models import WebRequest, WebResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BatchError:
    """Error for a single URL in a batch."""

    url: str
    error: str


@dataclass(slots=True)
class BatchResult:
    """Results from a batch fetch operation."""

    responses: list[WebResponse] = field(default_factory=list)
    errors: list[BatchError] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def total(self) -> int:
        return len(self.responses) + len(self.errors)

    @property
    def succeeded(self) -> int:
        return len(self.responses)

    @property
    def failed(self) -> int:
        return len(self.errors)


async def batch_fetch(
    urls: list[str],
    *,
    concurrency: int = 5,
    timeout: float = 30.0,
    client_config: HttpClientConfig | None = None,
) -> BatchResult:
    """Fetch multiple URLs concurrently with rate limiting.

    Per-URL error isolation: one failure doesn't abort the batch.
    Uses HttpClient with full middleware chain (retry, rate limit, cache).

    Args:
        urls: URLs to fetch.
        concurrency: Max concurrent requests.
        timeout: Per-request timeout in seconds.
        client_config: Optional HTTP client configuration.

    Returns:
        BatchResult with responses, errors, and timing.
    """
    if not urls:
        return BatchResult()

    config = client_config or HttpClientConfig(enable_cache=True)
    semaphore = asyncio.Semaphore(concurrency)
    result = BatchResult()
    lock = asyncio.Lock()
    start = time.monotonic()

    async def _fetch_one(url: str, client: HttpClient) -> None:
        async with semaphore:
            try:
                resp = await client.fetch(WebRequest(url=url, timeout=timeout))
                async with lock:
                    result.responses.append(resp)
            except Exception as exc:
                async with lock:
                    result.errors.append(BatchError(url=url, error=str(exc)))

    async with HttpClient(config) as client:
        tasks = [asyncio.create_task(_fetch_one(url, client)) for url in urls]
        await asyncio.gather(*tasks)

    result.elapsed_ms = (time.monotonic() - start) * 1000
    return result
