"""Integration tests hitting real HTTP servers.

Run with: pytest tests/integration/ -v
Skip in CI: pytest -m "not integration"

These tests require network access and hit example.com (IANA-managed, always up)
and httpbin.org (public HTTP testing service).
"""

from __future__ import annotations

import asyncio

import pytest

from kaos_web.clients.config import BrowserClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.errors import WebClientError, WebServerError
from kaos_web.middleware.base import MiddlewareChain
from kaos_web.middleware.rate_limit import RateLimitConfig, RateLimitMiddleware
from kaos_web.middleware.retry import RetryConfig, RetryMiddleware
from kaos_web.middleware.robots import RobotsConfig, RobotsMiddleware
from kaos_web.models import WebRequest

pytestmark = pytest.mark.integration


class TestHttpClientIntegration:
    """Tests hitting real HTTP servers."""

    async def test_basic_get(self) -> None:
        """Fetch https://example.com, verify 200 + HTML."""
        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url="https://httpbin.org/html"))

        assert resp.status_code == 200
        assert resp.ok
        assert "<html" in resp.html.lower()
        assert resp.elapsed_ms > 0

    async def test_redirect_following(self) -> None:
        """Fetch https://httpbin.org/redirect/2, verify final URL."""
        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url="https://httpbin.org/redirect/2"))

        assert resp.status_code == 200
        # After 2 redirects we should land at /get
        assert resp.url.endswith("/get")

    async def test_custom_headers_sent(self) -> None:
        """Verify custom headers appear in httpbin.org/headers response."""
        async with HttpClient() as client:
            resp = await client.fetch(
                WebRequest(
                    url="https://httpbin.org/headers",
                    headers={"X-Test-Header": "kaos-web-test"},
                )
            )

        assert resp.status_code == 200
        # httpbin reflects headers back as JSON
        assert "kaos-web-test" in resp.html

    async def test_404_raises_client_error(self) -> None:
        """httpbin.org/status/404 raises WebClientError."""
        async with HttpClient() as client:
            with pytest.raises(WebClientError) as exc_info:
                await client.fetch(WebRequest(url="https://httpbin.org/status/404"))

        assert exc_info.value.status_code == 404
        assert not exc_info.value.retryable

    async def test_500_raises_server_error(self) -> None:
        """httpbin.org/status/500 raises WebServerError."""
        async with HttpClient() as client:
            with pytest.raises(WebServerError) as exc_info:
                await client.fetch(WebRequest(url="https://httpbin.org/status/500"))

        assert exc_info.value.status_code == 500
        assert exc_info.value.retryable


class TestBrowserClientIntegration:
    """Tests using real Playwright + Chrome."""

    async def test_basic_fetch(self) -> None:
        """Browser fetch example.com, verify title and HTML."""
        from kaos_web.clients.browser import BrowserClient

        config = BrowserClientConfig(channel="chrome")
        async with BrowserClient(config) as client:
            resp = await client.fetch(WebRequest(url="https://example.com"))

        assert resp.status_code == 200
        assert resp.title is not None
        assert "example" in resp.title.lower()
        assert "<html" in resp.html.lower()

    async def test_screenshot(self) -> None:
        """Take screenshot, verify PNG bytes."""
        from kaos_web.clients.browser import BrowserClient

        config = BrowserClientConfig(channel="chrome")
        async with BrowserClient(config) as client:
            screenshot_bytes = await client.screenshot("https://example.com")

        # PNG magic bytes: \x89PNG\r\n\x1a\n
        assert screenshot_bytes[:4] == b"\x89PNG"
        assert len(screenshot_bytes) > 1000  # Reasonable size for a page screenshot

    async def test_js_evaluation(self) -> None:
        """Evaluate JS, verify result."""
        from kaos_web.clients.browser import BrowserClient

        config = BrowserClientConfig(channel="chrome")
        async with BrowserClient(config) as client:
            result = await client.evaluate(
                "https://example.com",
                "document.title",
            )

        assert isinstance(result, str)
        assert "example" in result.lower()

    async def test_resource_blocking(self) -> None:
        """Block images, verify fetch still works."""
        from kaos_web.clients.browser import BrowserClient

        config = BrowserClientConfig(
            channel="chrome",
            block_resources=["image", "stylesheet", "font"],
        )
        async with BrowserClient(config) as client:
            resp = await client.fetch(WebRequest(url="https://example.com"))

        assert resp.status_code == 200
        assert resp.ok
        assert "<html" in resp.html.lower()


class TestMiddlewareChainIntegration:
    """Test composed middleware chain against real servers."""

    async def test_retry_middleware_with_real_server(self) -> None:
        """Retry middleware wraps the call correctly for a successful request."""
        retry = RetryMiddleware(RetryConfig(max_retries=2, initial_delay=0.1))

        async with HttpClient() as client:
            chain = MiddlewareChain(client.fetch).add(retry)
            resp = await chain.execute(WebRequest(url="https://httpbin.org/get"))

        assert resp.status_code == 200

    async def test_rate_limit_with_burst(self) -> None:
        """Send burst of requests, verify rate limiting delays."""
        rate_limit = RateLimitMiddleware(RateLimitConfig(requests_per_second=5.0, burst_size=2))

        async with HttpClient() as client:
            chain = MiddlewareChain(client.fetch).add(rate_limit)

            # Send 4 requests rapidly — first 2 should be instant (burst),
            # remaining should be rate-limited
            start = asyncio.get_event_loop().time()
            for _ in range(4):
                resp = await chain.execute(WebRequest(url="https://httpbin.org/get"))
                assert resp.status_code == 200
            elapsed = asyncio.get_event_loop().time() - start

            # With burst=2 and 5 rps, we need 2 more tokens at 0.2s each = ~0.4s minimum
            assert elapsed > 0.2, f"Expected rate limiting delay, got {elapsed:.2f}s"

    async def test_robots_middleware_blocks(self) -> None:
        """Test robots middleware against a site with restrictive robots.txt.

        Google's robots.txt disallows /search for most user agents.
        We test that the middleware respects this.
        """
        robots = RobotsMiddleware(RobotsConfig(user_agent="*"))

        async with HttpClient() as client:
            chain = MiddlewareChain(client.fetch).add(robots)

            # This should be blocked by Google's robots.txt for generic user-agents
            with pytest.raises(WebClientError, match=r"robots\.txt"):
                await chain.execute(WebRequest(url="https://www.google.com/search?q=test"))


class TestFullPipelineIntegration:
    """End-to-end: fetch -> extract -> AST -> markdown."""

    async def test_http_to_ast_pipeline(self) -> None:
        """HTTP fetch → html_to_document → serialize_markdown."""
        from kaos_content.serializers.markdown import serialize_markdown
        from kaos_web.extract import html_to_document

        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url="https://httpbin.org/html"))

        doc = html_to_document(resp.html, url=resp.url)

        assert doc.body, "Document should have body blocks"

        md = serialize_markdown(doc)
        assert len(md) > 0

    async def test_browser_to_ast_pipeline(self) -> None:
        """Browser fetch → extract → AST with provenance."""
        from kaos_web.clients.browser import BrowserClient
        from kaos_web.extract import html_to_document

        config = BrowserClientConfig(channel="chrome")
        async with BrowserClient(config) as client:
            resp = await client.fetch(WebRequest(url="https://example.com"))

        doc = html_to_document(resp.html, url=resp.url)

        assert doc.body, "Document should have body blocks"
        # Provenance on first block should reference the URL
        first_block = doc.body[0]
        assert first_block.provenance is not None
        assert first_block.provenance.source is not None
        assert "example.com" in first_block.provenance.source.uri

    async def test_search_within_fetched_page(self) -> None:
        """Browser fetch → extract → BM25 search."""
        from kaos_web.clients.browser import BrowserClient
        from kaos_web.extract import html_to_document

        try:
            from kaos_content.search import search_document
        except ImportError:
            pytest.skip("kaos_content[search] not available")

        config = BrowserClientConfig(channel="chrome")
        async with BrowserClient(config) as client:
            resp = await client.fetch(WebRequest(url="https://example.com"))

        doc = html_to_document(resp.html, url=resp.url)
        results = search_document(doc, "domain", top_k=5)

        assert results.total_matches > 0
        assert len(results.results) > 0
        found_domain = any("domain" in r.text.lower() for r in results.results)
        assert found_domain, "Expected at least one result containing 'domain'"
