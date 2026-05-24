"""Batch fetch — concurrent URL fetching with per-URL error isolation.

Reuses the existing HttpClient middleware chain (retry, rate limit, robots, cache).
Concurrency controlled via asyncio.Semaphore.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from kaos_core.logging import get_logger
from kaos_web.clients.config import HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.discover.sitemap import FetchFn
from kaos_web.models import WebRequest, WebResponse

logger = get_logger(__name__)

# Async fetcher signature: ``fetch(WebRequest) -> WebResponse``. Matches
# both ``HttpClient.fetch`` and ``BrowserClient.fetch`` so callers can
# route through either backend without batch_fetch knowing which one.
_Fetcher = FetchFn


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
    use_browser: bool | None = None,
) -> BatchResult:
    """Fetch multiple URLs concurrently with rate limiting.

    Per-URL error isolation: one failure doesn't abort the batch.
    Uses HttpClient (or BrowserClient when ``use_browser`` resolves true)
    with full middleware chain (retry, rate limit, cache) on the httpx
    path.

    Args:
        urls: URLs to fetch.
        concurrency: Max concurrent requests.
        timeout: Per-request timeout in seconds.
        client_config: Optional HTTP client configuration. Ignored when
            the browser path is selected.
        use_browser: Fetcher selection (Playwright-default routing per
            kaos-web 0.1.9). ``None`` (default) auto-detects: browser
            when the ``[browser]`` extra is installed, httpx otherwise.
            ``True`` forces browser; ``False`` forces httpx. Matches the
            ``kaos-web-fetch-page`` / ``kaos-web-crawl-site`` agent
            surface so the agent gets the same anti-bot coverage on a
            many-URL batch.

    Returns:
        BatchResult with responses, errors, and timing.
    """
    if not urls:
        return BatchResult()

    effective_use_browser = _resolve_use_browser(use_browser)
    semaphore = asyncio.Semaphore(concurrency)
    result = BatchResult()
    lock = asyncio.Lock()
    start = time.monotonic()

    async def _fetch_one(url: str, fetcher: _Fetcher) -> None:
        async with semaphore:
            try:
                resp = await fetcher(WebRequest(url=url, timeout=timeout))
                async with lock:
                    result.responses.append(resp)
            except Exception as exc:
                async with lock:
                    result.errors.append(BatchError(url=url, error=str(exc)))

    if effective_use_browser:
        try:
            from kaos_web.clients.browser import BrowserClient

            async with BrowserClient() as browser:
                tasks = [asyncio.create_task(_fetch_one(url, browser.fetch)) for url in urls]
                await asyncio.gather(*tasks)
            result.elapsed_ms = (time.monotonic() - start) * 1000
            return result
        except ImportError:
            logger.warning(
                "batch_fetch: Playwright extra missing — falling back to httpx. "
                "Install with `pip install 'kaos-web[browser]'` for anti-bot coverage."
            )

    config = client_config or HttpClientConfig(enable_cache=True)
    async with HttpClient(config) as client:
        tasks = [asyncio.create_task(_fetch_one(url, client.fetch)) for url in urls]
        await asyncio.gather(*tasks)

    result.elapsed_ms = (time.monotonic() - start) * 1000
    return result


def _resolve_use_browser(use_browser: bool | None) -> bool:
    """Decide whether to route through Playwright.

    ``True`` / ``False`` are honored literally. ``None`` probes for the
    ``[browser]`` extra and returns True when Playwright is importable.
    Mirrors :func:`kaos_web.tools._fetch_html`'s routing default so the
    batch / crawl / fetch-page surface behave consistently.
    """
    if use_browser is not None:
        return use_browser
    try:
        import playwright  # noqa: F401 — probe only

        return True
    except ImportError:
        return False


# Public surface — ``_resolve_use_browser`` is re-exported through the
# module's private API so crawl.py and the MCP tool layer share one
# source of truth for the Playwright-default contract.
__all__ = ["BatchError", "BatchResult", "batch_fetch"]
