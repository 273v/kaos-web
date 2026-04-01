"""Tests for middleware chain, retry, rate limit, and robots middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kaos_web.errors import WebClientError, WebRateLimitError, WebServerError
from kaos_web.middleware.base import Handler, MiddlewareChain
from kaos_web.middleware.rate_limit import RateLimitConfig, RateLimitMiddleware
from kaos_web.middleware.retry import RetryConfig, RetryMiddleware
from kaos_web.middleware.robots import RobotsConfig, RobotsMiddleware
from kaos_web.models import WebRequest, WebResponse


def _make_response(url: str = "https://example.com", **kwargs) -> WebResponse:
    """Create a minimal WebResponse for testing."""
    return WebResponse(url=url, status_code=200, **kwargs)


def _make_handler(response: WebResponse | None = None) -> AsyncMock:
    """Create a mock async handler that returns a WebResponse."""
    handler = AsyncMock(spec=Handler)
    handler.return_value = response or _make_response()
    return handler


# ---------------------------------------------------------------------------
# MiddlewareChain
# ---------------------------------------------------------------------------


class TestMiddlewareChain:
    async def test_empty_chain(self) -> None:
        """With no middleware, the handler is called directly."""
        handler = _make_handler()
        chain = MiddlewareChain(handler)

        request = WebRequest(url="https://example.com")
        resp = await chain.execute(request)

        handler.assert_awaited_once_with(request)
        assert resp.status_code == 200

    async def test_single_middleware(self) -> None:
        """A single middleware wraps the handler and can inspect the request."""
        call_log: list[str] = []

        class LogMiddleware:
            async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
                call_log.append("before")
                resp = await next_handler(request)
                call_log.append("after")
                return resp

        handler = _make_handler()
        chain = MiddlewareChain(handler)
        chain.add(LogMiddleware())

        await chain.execute(WebRequest(url="https://example.com"))

        assert call_log == ["before", "after"], f"Middleware should wrap handler: got {call_log}"
        handler.assert_awaited_once()

    async def test_multiple_middleware_execution_order(self) -> None:
        """First middleware added is outermost — executes first on request, last on response."""
        call_log: list[str] = []

        class OrderedMiddleware:
            def __init__(self, name: str) -> None:
                self.name = name

            async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
                call_log.append(f"{self.name}:before")
                resp = await next_handler(request)
                call_log.append(f"{self.name}:after")
                return resp

        handler = _make_handler()
        chain = MiddlewareChain(handler)
        chain.add(OrderedMiddleware("outer"))
        chain.add(OrderedMiddleware("inner"))

        await chain.execute(WebRequest(url="https://example.com"))

        assert call_log == [
            "outer:before",
            "inner:before",
            "inner:after",
            "outer:after",
        ], f"Expected outer-in/inner-out order, got {call_log}"

    async def test_middleware_can_modify_response(self) -> None:
        """Middleware can modify the response before returning it."""

        class HeaderMiddleware:
            async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
                resp = await next_handler(request)
                new_headers = {**resp.headers, "x-modified": "true"}
                return resp.model_copy(update={"headers": new_headers})

        handler = _make_handler()
        chain = MiddlewareChain(handler)
        chain.add(HeaderMiddleware())

        resp = await chain.execute(WebRequest(url="https://example.com"))

        assert resp.headers.get("x-modified") == "true", (
            "Middleware should be able to add headers to the response"
        )


# ---------------------------------------------------------------------------
# RetryMiddleware
# ---------------------------------------------------------------------------


class TestRetryMiddleware:
    async def test_no_retry_on_success(self) -> None:
        """Successful requests should not be retried."""
        handler = _make_handler()
        mw = RetryMiddleware(RetryConfig(max_retries=3))

        request = WebRequest(url="https://example.com")
        resp = await mw.process(request, handler)

        assert resp.status_code == 200
        handler.assert_awaited_once_with(request)

    async def test_retry_on_retryable_error(self) -> None:
        """WebServerError (retryable=True) triggers retries up to success."""
        handler = AsyncMock()
        handler.side_effect = [
            WebServerError("500 error", url="https://example.com", status_code=500),
            _make_response(),
        ]

        config = RetryConfig(max_retries=3, initial_delay=0.001, jitter=False)
        mw = RetryMiddleware(config)

        resp = await mw.process(WebRequest(url="https://example.com"), handler)

        assert resp.status_code == 200, "Should succeed after retry"
        assert handler.await_count == 2, (
            f"Expected 2 calls (1 fail + 1 success), got {handler.await_count}"
        )

    async def test_no_retry_on_client_error(self) -> None:
        """WebClientError (retryable=False) should not be retried."""
        handler = AsyncMock()
        handler.side_effect = WebClientError(
            "404 not found", url="https://example.com", status_code=404
        )

        config = RetryConfig(max_retries=3, initial_delay=0.001)
        mw = RetryMiddleware(config)

        with pytest.raises(WebClientError):
            await mw.process(WebRequest(url="https://example.com"), handler)

        handler.assert_awaited_once(), "Client errors should NOT trigger retries"

    async def test_max_retries_exceeded(self) -> None:
        """After exhausting max retries, the last error should be raised."""
        handler = AsyncMock()
        handler.side_effect = WebServerError(
            "500 error", url="https://example.com", status_code=500
        )

        config = RetryConfig(max_retries=2, initial_delay=0.001, jitter=False)
        mw = RetryMiddleware(config)

        with pytest.raises(WebServerError):
            await mw.process(WebRequest(url="https://example.com"), handler)

        assert handler.await_count == 3, (
            f"Expected 3 calls (1 initial + 2 retries), got {handler.await_count}"
        )

    async def test_retry_with_rate_limit(self) -> None:
        """WebRateLimitError with retry_after uses that delay instead of backoff."""
        handler = AsyncMock()
        handler.side_effect = [
            WebRateLimitError(
                "429 too many requests",
                url="https://example.com",
                retry_after=0.001,
            ),
            _make_response(),
        ]

        config = RetryConfig(
            max_retries=3,
            initial_delay=100.0,  # Very high — should use retry_after instead
            respect_retry_after=True,
            jitter=False,
        )
        mw = RetryMiddleware(config)

        with patch("kaos_web.middleware.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await mw.process(WebRequest(url="https://example.com"), handler)

        assert resp.status_code == 200
        # Should have slept for the retry_after value (0.001), not the initial_delay (100)
        mock_sleep.assert_awaited_once()
        actual_delay = mock_sleep.call_args[0][0]
        assert actual_delay == pytest.approx(0.001, abs=0.01), (
            f"Should use retry_after delay (0.001), not backoff. Got {actual_delay}"
        )


# ---------------------------------------------------------------------------
# RateLimitMiddleware
# ---------------------------------------------------------------------------


class TestRateLimitMiddleware:
    async def test_allows_burst(self) -> None:
        """Burst_size requests should go through immediately without delay."""
        handler = _make_handler()
        config = RateLimitConfig(requests_per_second=10.0, burst_size=5)
        mw = RateLimitMiddleware(config)

        request = WebRequest(url="https://example.com/page")
        responses = []
        for _ in range(5):
            resp = await mw.process(request, handler)
            responses.append(resp)

        assert len(responses) == 5, "All burst requests should complete"
        assert handler.await_count == 5, (
            f"All 5 requests should call through to handler, got {handler.await_count}"
        )

    async def test_per_host_isolation(self) -> None:
        """Different domains should get separate rate limit buckets."""
        handler = _make_handler()
        config = RateLimitConfig(requests_per_second=1.0, burst_size=2, per_host=True)
        mw = RateLimitMiddleware(config)

        # Make 2 requests to each of 2 different hosts — all should succeed immediately
        # because each host gets its own 2-token bucket
        for _ in range(2):
            await mw.process(WebRequest(url="https://alpha.com/a"), handler)
        for _ in range(2):
            await mw.process(WebRequest(url="https://beta.com/b"), handler)

        assert handler.await_count == 4, (
            f"All 4 requests should succeed (2 per host, each with burst=2), "
            f"got {handler.await_count}"
        )


# ---------------------------------------------------------------------------
# RobotsMiddleware
# ---------------------------------------------------------------------------


class TestRobotsMiddleware:
    async def test_allowed_url(self) -> None:
        """When robots.txt allows the URL, the request proceeds normally."""
        robots_txt = "User-agent: *\nAllow: /\n"
        page_response = _make_response(url="https://example.com/page")

        call_count = 0

        async def mock_handler(request: WebRequest) -> WebResponse:
            nonlocal call_count
            call_count += 1
            if request.url.endswith("/robots.txt"):
                return WebResponse(
                    url=request.url,
                    status_code=200,
                    html=robots_txt,
                    content_type="text/plain",
                )
            return page_response

        mw = RobotsMiddleware(RobotsConfig(user_agent="KAOS-Web"))
        resp = await mw.process(WebRequest(url="https://example.com/page"), mock_handler)

        assert resp.status_code == 200
        assert call_count == 2, f"Expected 2 calls (robots.txt + page), got {call_count}"

    async def test_blocked_url(self) -> None:
        """When robots.txt disallows the URL, WebClientError(403) is raised."""
        robots_txt = "User-agent: *\nDisallow: /private/\n"

        async def mock_handler(request: WebRequest) -> WebResponse:
            if request.url.endswith("/robots.txt"):
                return WebResponse(
                    url=request.url,
                    status_code=200,
                    html=robots_txt,
                    content_type="text/plain",
                )
            return _make_response()

        mw = RobotsMiddleware(RobotsConfig(user_agent="KAOS-Web"))

        with pytest.raises(WebClientError) as exc_info:
            await mw.process(
                WebRequest(url="https://example.com/private/secret"),
                mock_handler,
            )

        assert exc_info.value.status_code == 403, (
            f"Blocked URL should raise 403, got {exc_info.value.status_code}"
        )
        assert "robots.txt" in str(exc_info.value), "Error message should mention robots.txt"

    async def test_missing_robots_txt(self) -> None:
        """When robots.txt returns 404, allow everything."""
        page_response = _make_response(url="https://example.com/page")

        async def mock_handler(request: WebRequest) -> WebResponse:
            if request.url.endswith("/robots.txt"):
                return WebResponse(
                    url=request.url,
                    status_code=404,
                    html="",
                    content_type="text/plain",
                )
            return page_response

        mw = RobotsMiddleware(RobotsConfig(user_agent="KAOS-Web"))
        resp = await mw.process(
            WebRequest(url="https://example.com/page"),
            mock_handler,
        )

        assert resp.status_code == 200, "Missing robots.txt should allow the request"

    async def test_robots_txt_cached(self) -> None:
        """Second request to the same domain should use the cached robots.txt."""
        fetch_count = 0

        async def mock_handler(request: WebRequest) -> WebResponse:
            nonlocal fetch_count
            if request.url.endswith("/robots.txt"):
                fetch_count += 1
                return WebResponse(
                    url=request.url,
                    status_code=200,
                    html="User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                )
            return _make_response(url=request.url)

        mw = RobotsMiddleware(RobotsConfig(user_agent="KAOS-Web", cache_ttl=3600))

        # Two requests to the same domain
        await mw.process(WebRequest(url="https://example.com/page1"), mock_handler)
        await mw.process(WebRequest(url="https://example.com/page2"), mock_handler)

        assert fetch_count == 1, (
            f"robots.txt should be fetched only once (cached), got {fetch_count} fetches"
        )
