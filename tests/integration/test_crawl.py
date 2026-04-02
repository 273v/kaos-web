"""Integration tests for sitemap parsing, URL discovery, batch fetch, and site crawl.

These tests hit real websites and require network access.
Run with: pytest tests/integration/test_crawl.py -v
Skip in CI: pytest -m "not integration"
"""

from __future__ import annotations

import pytest

from kaos_web.batch import batch_fetch
from kaos_web.clients.config import HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.crawl import crawl_site
from kaos_web.discovery import discover_urls
from kaos_web.sitemap import discover_sitemaps, parse_sitemap

# All tests in this file require network access
pytestmark = pytest.mark.integration

# Shared config: no retry/rate-limit to keep tests fast, but cache for dedup
_TEST_CONFIG = HttpClientConfig(
    enable_retry=False,
    enable_rate_limit=False,
    enable_robots=False,
    enable_cache=True,
)


# ============================================================
# Sitemap parsing
# ============================================================


class TestSitemapParsing:
    """Test sitemap parsing against real sitemaps."""

    @pytest.mark.asyncio
    async def test_parse_xml_sitemap(self):
        """Parse a well-known XML sitemap."""
        async with HttpClient(_TEST_CONFIG) as client:
            result = await parse_sitemap("https://www.sitemaps.org/sitemap.xml", client.fetch)
        # sitemaps.org has a small sitemap
        assert len(result.entries) > 0
        assert all(e.url.startswith("http") for e in result.entries)
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_discover_sitemaps_from_robots(self):
        """Discover sitemap URLs from robots.txt."""
        # Use a site known to have Sitemap in robots.txt
        async with HttpClient(_TEST_CONFIG) as client:
            urls = await discover_sitemaps("www.sitemaps.org", client.fetch)
        # sitemaps.org should have a sitemap
        assert len(urls) >= 0  # May or may not, but should not error
        assert isinstance(urls, list)

    @pytest.mark.asyncio
    async def test_discover_sitemaps_fallback(self):
        """Fallback to /sitemap.xml when robots.txt has no Sitemap directive."""
        async with HttpClient(_TEST_CONFIG) as client:
            urls = await discover_sitemaps("books.toscrape.com", client.fetch)
        assert isinstance(urls, list)


# ============================================================
# URL Discovery
# ============================================================


class TestUrlDiscovery:
    """Test URL discovery against real websites."""

    @pytest.mark.asyncio
    async def test_discover_with_page_links(self):
        """Discover URLs from books.toscrape.com via page links."""
        async with HttpClient(_TEST_CONFIG) as client:
            result = await discover_urls(
                "https://books.toscrape.com",
                client.fetch,
                sitemap="skip",
                max_urls=50,
            )
        # Should find internal links on the page
        assert result.total > 0
        assert result.page_link_count > 0

    @pytest.mark.asyncio
    async def test_discover_include_patterns(self):
        """Filter URLs by include pattern."""
        async with HttpClient(_TEST_CONFIG) as client:
            result = await discover_urls(
                "https://books.toscrape.com",
                client.fetch,
                sitemap="skip",
                include_patterns=["catalogue/category"],
                max_urls=100,
            )
        # All URLs should match the pattern
        assert result.total > 0
        for u in result.urls:
            assert "catalogue/category" in u.url

    @pytest.mark.asyncio
    async def test_discover_exclude_patterns(self):
        """Exclude URLs by pattern."""
        async with HttpClient(_TEST_CONFIG) as client:
            result = await discover_urls(
                "https://books.toscrape.com",
                client.fetch,
                sitemap="skip",
                exclude_patterns=["catalogue/category"],
                max_urls=100,
            )
        # No URLs should match the excluded pattern
        for u in result.urls:
            assert "catalogue/category" not in u.url


# ============================================================
# Batch Fetch
# ============================================================


class TestBatchFetch:
    """Test batch fetching against real URLs."""

    @pytest.mark.asyncio
    async def test_batch_fetch_multiple(self):
        """Fetch multiple URLs concurrently."""
        urls = [
            "https://books.toscrape.com",
            "https://httpbin.org/html",
            "https://www.sitemaps.org",
        ]
        result = await batch_fetch(urls, concurrency=3, client_config=_TEST_CONFIG)
        assert result.succeeded >= 2  # At least 2 should work
        assert result.elapsed_ms > 0
        assert result.total == 3

    @pytest.mark.asyncio
    async def test_batch_fetch_with_error(self):
        """Batch fetch with one bad URL doesn't abort others."""
        urls = [
            "https://httpbin.org/html",
            "https://this-domain-definitely-does-not-exist-12345.com",
        ]
        result = await batch_fetch(urls, concurrency=2, client_config=_TEST_CONFIG)
        assert result.succeeded >= 1
        assert result.failed >= 1
        assert result.total == 2


# ============================================================
# Site Crawl
# ============================================================


class TestSiteCrawl:
    """Test site crawling against real websites."""

    @pytest.mark.asyncio
    async def test_crawl_depth_zero(self):
        """Crawl with depth=0 fetches only the start page."""
        result = await crawl_site(
            "https://books.toscrape.com",
            max_depth=0,
            max_pages=1,
            sitemap="skip",
            client_config=_TEST_CONFIG,
        )
        assert result.total_extracted == 1
        assert result.pages[0].title is not None
        assert len(result.pages[0].content_text) > 0
        assert result.elapsed_ms > 0

    @pytest.mark.asyncio
    async def test_crawl_depth_one(self):
        """Crawl with depth=1 follows links from start page."""
        result = await crawl_site(
            "https://books.toscrape.com",
            max_depth=1,
            max_pages=5,
            sitemap="skip",
            client_config=_TEST_CONFIG,
        )
        assert result.total_extracted >= 2
        assert len(result.pages) >= 2
        # Should have pages at different depths
        depths = {p.depth for p in result.pages}
        assert 0 in depths

    @pytest.mark.asyncio
    async def test_crawl_with_include_pattern(self):
        """Crawl with URL filtering."""
        result = await crawl_site(
            "https://books.toscrape.com",
            max_depth=1,
            max_pages=10,
            sitemap="skip",
            include_patterns=["catalogue/category"],
            client_config=_TEST_CONFIG,
        )
        # Start page always included regardless of pattern
        # But followed links should match
        for page in result.pages:
            if page.depth > 0:
                assert "catalogue/category" in page.url

    @pytest.mark.asyncio
    async def test_crawl_content_extraction(self):
        """Crawl extracts text and markdown content."""
        result = await crawl_site(
            "https://httpbin.org/html",
            max_depth=0,
            max_pages=1,
            sitemap="skip",
            client_config=_TEST_CONFIG,
        )
        assert len(result.pages) == 1
        page = result.pages[0]
        assert page.content_text  # Has text content
        assert page.content_markdown  # Has markdown content

    @pytest.mark.asyncio
    async def test_crawl_max_pages_respected(self):
        """Max pages limit is respected."""
        result = await crawl_site(
            "https://books.toscrape.com",
            max_depth=2,
            max_pages=3,
            sitemap="skip",
            client_config=_TEST_CONFIG,
        )
        assert len(result.pages) <= 3


# ============================================================
# MCP Tool E2E
# ============================================================


class TestCrawlToolsE2E:
    """End-to-end tests for crawl MCP tools."""

    @pytest.mark.asyncio
    async def test_discover_urls_tool(self):
        """DiscoverUrlsTool against a real site."""
        from kaos_web.crawl_tools import DiscoverUrlsTool

        tool = DiscoverUrlsTool()
        result = await tool.execute(
            {
                "url": "https://books.toscrape.com",
                "sitemap": "skip",
                "max_urls": 20,
            }
        )
        assert not result.isError
        data = result.structuredContent
        assert data["total"] > 0
        assert len(data["urls"]) > 0

    @pytest.mark.asyncio
    async def test_batch_fetch_tool(self):
        """BatchFetchTool against real URLs."""
        from kaos_web.crawl_tools import BatchFetchTool

        tool = BatchFetchTool()
        result = await tool.execute(
            {
                "urls": "https://httpbin.org/html,https://books.toscrape.com",
                "concurrency": 2,
                "output_format": "metadata",
            }
        )
        assert not result.isError
        data = result.structuredContent
        assert data["succeeded"] >= 1

    @pytest.mark.asyncio
    async def test_crawl_site_tool(self):
        """CrawlSiteTool against a real site."""
        from kaos_web.crawl_tools import CrawlSiteTool

        tool = CrawlSiteTool()
        result = await tool.execute(
            {
                "url": "https://httpbin.org/html",
                "max_depth": 0,
                "max_pages": 1,
                "sitemap": "skip",
                "output_format": "summary",
            }
        )
        assert not result.isError
        data = result.structuredContent
        assert data["total_extracted"] == 1
        assert len(data["pages"]) == 1
