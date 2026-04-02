"""Unit tests for site crawl."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kaos_web.crawl import _normalize_url, crawl_site
from kaos_web.models import WebResponse

# HTML with internal links
PAGE_HTML = """\
<html>
<head><title>Test Page</title></head>
<body>
<h1>Hello World</h1>
<p>Some content here.</p>
<a href="https://example.com/page2">Page 2</a>
<a href="https://example.com/page3">Page 3</a>
<a href="https://other.com/external">External</a>
</body>
</html>
"""

CHILD_HTML = """\
<html>
<head><title>Child Page</title></head>
<body>
<h1>Child</h1>
<p>Child content.</p>
<a href="https://example.com/deep">Deep link</a>
</body>
</html>
"""


class TestNormalizeUrl:
    def test_strip_fragment(self):
        assert _normalize_url("https://example.com/page#section") == "https://example.com/page"

    def test_strip_trailing_slash(self):
        assert _normalize_url("https://example.com/page/") == "https://example.com/page"

    def test_root_stays(self):
        assert _normalize_url("https://example.com/") == "https://example.com/"

    def test_no_change(self):
        assert _normalize_url("https://example.com/page") == "https://example.com/page"


class TestCrawlSite:
    def _make_client_mock(self, responses: dict[str, tuple[int, str]]):
        """Create a mock HttpClient with fetch that returns canned responses."""

        async def mock_fetch(request):
            url = request.url
            if url in responses:
                status, html = responses[url]
                return WebResponse(url=url, status_code=status, html=html)
            return WebResponse(url=url, status_code=404, html="")

        instance = AsyncMock()
        instance.fetch = mock_fetch
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        return instance

    @pytest.mark.asyncio
    async def test_single_page(self):
        """Crawl with depth=0 fetches only the start page."""
        client = self._make_client_mock(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, PAGE_HTML),
            }
        )
        with patch("kaos_web.crawl.HttpClient", return_value=client):
            result = await crawl_site("https://example.com", max_depth=0, sitemap="skip")

        assert result.total_extracted >= 1
        assert result.pages[0].url == "https://example.com"
        assert result.pages[0].depth == 0

    @pytest.mark.asyncio
    async def test_depth_one(self):
        """Crawl with depth=1 follows links from start page."""
        client = self._make_client_mock(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, PAGE_HTML),
                "https://example.com/page2": (200, CHILD_HTML),
                "https://example.com/page3": (200, CHILD_HTML),
            }
        )
        with patch("kaos_web.crawl.HttpClient", return_value=client):
            result = await crawl_site("https://example.com", max_depth=1, sitemap="skip")

        assert result.total_extracted >= 2
        urls = {p.url for p in result.pages}
        assert "https://example.com" in urls

    @pytest.mark.asyncio
    async def test_max_pages_limit(self):
        """Crawl stops after max_pages."""
        client = self._make_client_mock(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, PAGE_HTML),
                "https://example.com/page2": (200, CHILD_HTML),
                "https://example.com/page3": (200, CHILD_HTML),
            }
        )
        with patch("kaos_web.crawl.HttpClient", return_value=client):
            result = await crawl_site("https://example.com", max_pages=2, sitemap="skip")

        assert len(result.pages) <= 2

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        """One failing URL doesn't abort the crawl."""

        async def mock_fetch(request):
            if "error" in request.url:
                raise ConnectionError("boom")
            return WebResponse(url=request.url, status_code=200, html=PAGE_HTML)

        client = AsyncMock()
        client.fetch = mock_fetch
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        html_with_error_link = """\
<html><body>
<a href="https://example.com/error-page">Bad</a>
<a href="https://example.com/good-page">Good</a>
</body></html>
"""
        original_fetch = client.fetch

        async def patched_fetch(request):
            if request.url == "https://example.com":
                return WebResponse(url=request.url, status_code=200, html=html_with_error_link)
            return await original_fetch(request)

        client.fetch = patched_fetch

        with patch("kaos_web.crawl.HttpClient", return_value=client):
            result = await crawl_site("https://example.com", max_depth=1, sitemap="skip")

        # Should have at least the start page despite errors
        assert len(result.pages) >= 1

    @pytest.mark.asyncio
    async def test_external_links_not_followed(self):
        """External links should not be crawled."""
        client = self._make_client_mock(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, PAGE_HTML),
            }
        )
        with patch("kaos_web.crawl.HttpClient", return_value=client):
            result = await crawl_site("https://example.com", max_depth=2, sitemap="skip")

        urls = {p.url for p in result.pages}
        assert not any("other.com" in u for u in urls)

    @pytest.mark.asyncio
    async def test_elapsed_ms_tracked(self):
        client = self._make_client_mock(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, PAGE_HTML),
            }
        )
        with patch("kaos_web.crawl.HttpClient", return_value=client):
            result = await crawl_site("https://example.com", max_depth=0, sitemap="skip")
        assert result.elapsed_ms > 0

    @pytest.mark.asyncio
    async def test_bare_domain_normalized(self):
        """Bare domain input gets https:// prefix."""
        client = self._make_client_mock(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, PAGE_HTML),
            }
        )
        with patch("kaos_web.crawl.HttpClient", return_value=client):
            result = await crawl_site("example.com", max_depth=0, sitemap="skip")
        assert len(result.pages) >= 1
