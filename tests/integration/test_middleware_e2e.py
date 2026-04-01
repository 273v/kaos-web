"""E2E tests proving middleware actually works through HttpClient.fetch().

These tests verify that middleware is NOT dead code — retry actually retries,
rate limit actually delays, cache actually caches, robots actually blocks.
All through the real HttpClient.fetch() path, not isolated middleware.process().
"""

from __future__ import annotations

import time

import pytest
from pytest_httpx import HTTPXMock

from kaos_web.clients.config import HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.errors import WebClientError, WebServerError
from kaos_web.models import WebRequest


class TestRetryActuallyRetries:
    """Prove retry middleware retries through HttpClient.fetch()."""

    async def test_retry_on_500_then_succeed(self, httpx_mock: HTTPXMock) -> None:
        """First request returns 500, retry succeeds with 200."""
        httpx_mock.add_response(url="https://flaky.example.com/api", status_code=500)
        httpx_mock.add_response(
            url="https://flaky.example.com/api",
            status_code=200,
            html="<p>success</p>",
        )

        config = HttpClientConfig(
            enable_retry=True,
            enable_rate_limit=False,
            max_retries=2,
        )
        async with HttpClient(config) as client:
            resp = await client.fetch(WebRequest(url="https://flaky.example.com/api"))

        assert resp.status_code == 200
        assert "success" in resp.html

    async def test_retry_exhausted_raises(self, httpx_mock: HTTPXMock) -> None:
        """All retries fail — should raise WebServerError."""
        for _ in range(4):  # 1 initial + 3 retries
            httpx_mock.add_response(url="https://down.example.com/api", status_code=500)

        config = HttpClientConfig(
            enable_retry=True,
            enable_rate_limit=False,
            max_retries=3,
        )
        async with HttpClient(config) as client:
            with pytest.raises(WebServerError):
                await client.fetch(WebRequest(url="https://down.example.com/api"))

    async def test_no_retry_on_404(self, httpx_mock: HTTPXMock) -> None:
        """404 is not retryable — should fail immediately."""
        httpx_mock.add_response(url="https://example.com/missing", status_code=404)

        config = HttpClientConfig(
            enable_retry=True,
            enable_rate_limit=False,
            max_retries=3,
        )
        async with HttpClient(config) as client:
            with pytest.raises(WebClientError):
                await client.fetch(WebRequest(url="https://example.com/missing"))


class TestRateLimitActuallyDelays:
    """Prove rate limit middleware delays through HttpClient.fetch()."""

    async def test_rate_limit_slows_requests(self, httpx_mock: HTTPXMock) -> None:
        """Burst of requests should be slower with rate limiting."""
        for _ in range(3):
            httpx_mock.add_response(url="https://api.example.com/data", status_code=200, html="ok")

        # Very low rate: 2 req/s with burst of 1
        config = HttpClientConfig(
            enable_retry=False,
            enable_rate_limit=True,
            requests_per_second=2.0,
        )
        async with HttpClient(config) as client:
            start = time.monotonic()
            for _ in range(3):
                await client.fetch(WebRequest(url="https://api.example.com/data"))
            elapsed = time.monotonic() - start

        # 3 requests at 2/s should take at least ~0.5s (after burst exhausted)
        assert elapsed >= 0.3, f"Rate limiting should delay requests, took {elapsed:.2f}s"


class TestCacheActuallyCaches:
    """Prove cache middleware caches through HttpClient.fetch()."""

    async def test_second_request_uses_cache(self, httpx_mock: HTTPXMock) -> None:
        """Second request should return cached response, not hit server."""
        httpx_mock.add_response(
            url="https://cacheable.example.com/page",
            status_code=200,
            html="<p>cached content</p>",
        )

        config = HttpClientConfig(
            enable_retry=False,
            enable_rate_limit=False,
            enable_cache=True,
            cache_ttl=60,
        )
        async with HttpClient(config) as client:
            resp1 = await client.fetch(WebRequest(url="https://cacheable.example.com/page"))
            resp2 = await client.fetch(WebRequest(url="https://cacheable.example.com/page"))

        assert resp1.html == resp2.html
        assert resp1.status_code == 200
        # httpx_mock only registered ONE response — if cache didn't work,
        # the second request would fail with "no response registered"

    async def test_post_not_cached(self, httpx_mock: HTTPXMock) -> None:
        """POST requests should not be cached."""
        httpx_mock.add_response(
            url="https://api.example.com/submit",
            status_code=200,
            html="response1",
        )
        httpx_mock.add_response(
            url="https://api.example.com/submit",
            status_code=200,
            html="response2",
        )

        config = HttpClientConfig(
            enable_retry=False,
            enable_rate_limit=False,
            enable_cache=True,
        )
        async with HttpClient(config) as client:
            resp1 = await client.fetch(
                WebRequest(url="https://api.example.com/submit", method="POST")
            )
            resp2 = await client.fetch(
                WebRequest(url="https://api.example.com/submit", method="POST")
            )

        # Both hit the server (POST not cached)
        assert resp1.html == "response1"
        assert resp2.html == "response2"


class TestRobotsActuallyBlocks:
    """Prove robots middleware blocks through HttpClient.fetch()."""

    async def test_blocked_by_robots(self, httpx_mock: HTTPXMock) -> None:
        """URL disallowed by robots.txt should raise WebClientError."""
        # Serve robots.txt that blocks /private/
        httpx_mock.add_response(
            url="https://protected.example.com/robots.txt",
            status_code=200,
            text="User-agent: *\nDisallow: /private/",
        )

        config = HttpClientConfig(
            enable_retry=False,
            enable_rate_limit=False,
            enable_robots=True,
        )
        async with HttpClient(config) as client:
            with pytest.raises(WebClientError) as exc_info:
                await client.fetch(WebRequest(url="https://protected.example.com/private/secret"))

            assert "robots.txt" in str(exc_info.value)

    async def test_allowed_by_robots(self, httpx_mock: HTTPXMock) -> None:
        """URL allowed by robots.txt should proceed normally."""
        httpx_mock.add_response(
            url="https://open.example.com/robots.txt",
            status_code=200,
            text="User-agent: *\nDisallow: /admin/",
        )
        httpx_mock.add_response(
            url="https://open.example.com/public/page",
            status_code=200,
            html="<p>public</p>",
        )

        config = HttpClientConfig(
            enable_retry=False,
            enable_rate_limit=False,
            enable_robots=True,
        )
        async with HttpClient(config) as client:
            resp = await client.fetch(WebRequest(url="https://open.example.com/public/page"))

        assert resp.status_code == 200
        assert "public" in resp.html


class TestMiddlewareComposition:
    """Prove multiple middleware compose correctly."""

    async def test_retry_plus_cache(self, httpx_mock: HTTPXMock) -> None:
        """Retry + cache: first fails, retry succeeds, second is cached."""
        httpx_mock.add_response(url="https://flaky-cache.example.com/data", status_code=500)
        httpx_mock.add_response(
            url="https://flaky-cache.example.com/data",
            status_code=200,
            html="<p>data</p>",
        )

        config = HttpClientConfig(
            enable_retry=True,
            enable_rate_limit=False,
            enable_cache=True,
            max_retries=2,
            cache_ttl=60,
        )
        async with HttpClient(config) as client:
            # First call: 500 → retry → 200 → cached
            resp1 = await client.fetch(WebRequest(url="https://flaky-cache.example.com/data"))
            # Second call: from cache (no server hit)
            resp2 = await client.fetch(WebRequest(url="https://flaky-cache.example.com/data"))

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.html == resp2.html
