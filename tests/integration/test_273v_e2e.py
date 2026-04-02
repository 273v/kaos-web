"""E2E integration tests against the live 273ventures.com site.

Tests the full crawl pipeline with both httpx and Playwright clients,
comparing results against the sitemap ground truth.

Run with: pytest tests/integration/test_273v_e2e.py -v
"""

from __future__ import annotations

import pytest

from kaos_web.clients.config import BrowserClientConfig, HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.extract import extract_metadata, html_to_document
from kaos_web.models import WebRequest
from kaos_web.sitemap import discover_sitemaps, parse_sitemap

pytestmark = pytest.mark.integration

_SITE = "https://273ventures.com"

_HTTP_CONFIG = HttpClientConfig(
    enable_retry=True,
    enable_rate_limit=True,
    requests_per_second=5.0,
    enable_robots=False,
    enable_cache=True,
)


def _browser_config() -> BrowserClientConfig:
    from kaos_web.browser_tools import _detect_browser_channel

    return BrowserClientConfig(channel=_detect_browser_channel(), ignore_https_errors=True)


# ============================================================
# Ground truth: sitemap
# ============================================================


class TestSitemapGroundTruth:
    """Parse the live sitemap to establish ground truth."""

    @pytest.mark.asyncio
    async def test_robots_txt_has_sitemap(self):
        async with HttpClient(_HTTP_CONFIG) as client:
            sm_urls = await discover_sitemaps(_SITE, client.fetch)
        assert len(sm_urls) >= 1
        assert any("sitemap" in u.lower() for u in sm_urls)

    @pytest.mark.asyncio
    async def test_sitemap_has_entries(self):
        async with HttpClient(_HTTP_CONFIG) as client:
            sm_urls = await discover_sitemaps(_SITE, client.fetch)
            result = await parse_sitemap(sm_urls[0], client.fetch)
        assert len(result.entries) > 100
        assert all(e.url.startswith("https://273ventures.com") for e in result.entries)

    @pytest.mark.asyncio
    async def test_sitemap_covers_key_pages(self):
        async with HttpClient(_HTTP_CONFIG) as client:
            sm_urls = await discover_sitemaps(_SITE, client.fetch)
            result = await parse_sitemap(sm_urls[0], client.fetch)
        urls = {e.url for e in result.entries}
        for path in [
            "",
            "/about",
            "/team",
            "/contact",
            "/blog",
            "/products/kelvin",
            "/services/agentic-enablement",
            "/solutions/law-firms",
        ]:
            assert f"{_SITE}{path}" in urls, f"Missing {path} in sitemap"


# ============================================================
# httpx E2E
# ============================================================


class TestHttpxE2E:
    """Full pipeline with httpx (HttpClient)."""

    @pytest.mark.asyncio
    async def test_fetch_homepage(self):
        async with HttpClient(_HTTP_CONFIG) as client:
            resp = await client.fetch(WebRequest(url=_SITE))
        assert resp.ok
        assert "273 Ventures" in resp.html

    @pytest.mark.asyncio
    async def test_extract_homepage(self):
        async with HttpClient(_HTTP_CONFIG) as client:
            resp = await client.fetch(WebRequest(url=_SITE))
        doc = html_to_document(resp.html, url=resp.url)
        assert doc.metadata.title is not None
        assert "273" in doc.metadata.title
        assert len(doc.body) > 10

    @pytest.mark.asyncio
    async def test_extract_metadata_homepage(self):
        async with HttpClient(_HTTP_CONFIG) as client:
            resp = await client.fetch(WebRequest(url=_SITE))
        meta = extract_metadata(resp.html, url=resp.url)
        assert meta.title is not None
        assert meta.description is not None
        assert meta.site_name == "273 Ventures"

    @pytest.mark.asyncio
    async def test_extract_blog_post(self):
        url = f"{_SITE}/deploying-claude-code-safely-in-law-firms"
        async with HttpClient(_HTTP_CONFIG) as client:
            resp = await client.fetch(WebRequest(url=url))
        assert resp.ok
        doc = html_to_document(resp.html, url=resp.url)
        text = _serialize_text(doc)
        assert len(text.split()) > 500
        meta = extract_metadata(resp.html, url=resp.url)
        assert "claude" in (meta.title or "").lower()

    @pytest.mark.asyncio
    async def test_discovery_page_links(self):
        from kaos_web.discovery import discover_urls

        async with HttpClient(_HTTP_CONFIG) as client:
            result = await discover_urls(_SITE, client.fetch, sitemap="skip", max_urls=50)
        assert result.total > 10
        assert result.page_link_count > 10
        urls = {u.url for u in result.urls}
        assert any("/products/" in u for u in urls)
        assert any("/services/" in u for u in urls)

    @pytest.mark.asyncio
    async def test_batch_fetch(self):
        from kaos_web.batch import batch_fetch

        urls = [
            f"{_SITE}/",
            f"{_SITE}/about",
            f"{_SITE}/products/kelvin",
            f"{_SITE}/team",
        ]
        result = await batch_fetch(urls, concurrency=2, client_config=_HTTP_CONFIG)
        assert result.succeeded == 4
        assert result.failed == 0
        for resp in result.responses:
            doc = html_to_document(resp.html, url=resp.url)
            assert len(doc.body) > 0

    @pytest.mark.asyncio
    async def test_crawl_depth_zero(self):
        from kaos_web.crawl import crawl_site

        result = await crawl_site(
            _SITE,
            max_depth=0,
            max_pages=1,
            sitemap="skip",
            client_config=_HTTP_CONFIG,
        )
        assert result.total_extracted == 1
        assert result.pages[0].title is not None
        assert len(result.pages[0].content_text.split()) > 100

    @pytest.mark.asyncio
    async def test_crawl_depth_one(self):
        from kaos_web.crawl import crawl_site

        result = await crawl_site(
            _SITE,
            max_depth=1,
            max_pages=10,
            concurrency=3,
            sitemap="skip",
            client_config=_HTTP_CONFIG,
        )
        assert result.total_extracted >= 5
        depths = {p.depth for p in result.pages}
        assert 0 in depths
        assert 1 in depths
        # All pages have content
        for page in result.pages:
            assert page.title is not None

    @pytest.mark.asyncio
    async def test_crawl_with_pattern_filter(self):
        from kaos_web.crawl import crawl_site

        result = await crawl_site(
            _SITE,
            max_depth=1,
            max_pages=20,
            sitemap="skip",
            include_patterns=["/products/", "/services/"],
            client_config=_HTTP_CONFIG,
        )
        for page in result.pages:
            if page.depth > 0:
                assert "/products/" in page.url or "/services/" in page.url


# ============================================================
# Playwright E2E
# ============================================================


class TestPlaywrightE2E:
    """Full pipeline with Playwright (BrowserClient)."""

    @pytest.mark.asyncio
    async def test_fetch_homepage(self):
        from kaos_web.clients.browser import BrowserClient

        async with BrowserClient(_browser_config()) as client:
            resp = await client.fetch(WebRequest(url=_SITE))
        assert resp.ok
        assert "273 Ventures" in resp.html

    @pytest.mark.asyncio
    async def test_extract_homepage(self):
        from kaos_web.clients.browser import BrowserClient

        async with BrowserClient(_browser_config()) as client:
            resp = await client.fetch(WebRequest(url=_SITE))
        doc = html_to_document(resp.html, url=resp.url)
        assert doc.metadata.title is not None
        assert "273" in doc.metadata.title
        assert len(doc.body) > 10

    @pytest.mark.asyncio
    async def test_extract_blog_post(self):
        from kaos_web.clients.browser import BrowserClient

        url = f"{_SITE}/deploying-claude-code-safely-in-law-firms"
        async with BrowserClient(_browser_config()) as client:
            resp = await client.fetch(WebRequest(url=url))
        assert resp.ok
        doc = html_to_document(resp.html, url=resp.url)
        text = _serialize_text(doc)
        assert len(text.split()) > 500

    @pytest.mark.asyncio
    async def test_screenshot(self):
        from kaos_web.clients.browser import BrowserClient

        async with BrowserClient(_browser_config()) as client:
            img = await client.screenshot(f"{_SITE}/products/kelvin")
        assert len(img) > 10_000  # PNG should be > 10 KB
        assert img[:4] == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_navigate_and_snapshot(self):
        from kaos_web.clients.browser import BrowserClient

        async with BrowserClient(_browser_config()) as client:
            await client.fetch(WebRequest(url=_SITE, extra={"context_id": "e2e"}))
            snapshot = await client.get_snapshot("e2e")
            assert len(snapshot) > 100
            assert "273" in snapshot or "Ventures" in snapshot
            await client.close_context("e2e")

    @pytest.mark.asyncio
    async def test_batch_fetch_via_browser(self):
        """Batch fetch using BrowserClient.fetch as the fetch function."""
        from kaos_web.clients.browser import BrowserClient

        urls = [
            f"{_SITE}/",
            f"{_SITE}/about",
            f"{_SITE}/products/kelvin",
        ]
        async with BrowserClient(_browser_config()) as client:
            responses = []
            for url in urls:
                resp = await client.fetch(WebRequest(url=url))
                responses.append(resp)
        assert len(responses) == 3
        for resp in responses:
            assert resp.ok
            doc = html_to_document(resp.html, url=resp.url)
            assert len(doc.body) > 0


# ============================================================
# Cross-client comparison
# ============================================================


class TestCrossClientComparison:
    """Compare httpx vs Playwright extraction on the same pages."""

    @pytest.mark.asyncio
    async def test_same_title_extracted(self):
        from kaos_web.clients.browser import BrowserClient

        url = f"{_SITE}/next-chapter-for-kelvin"

        async with HttpClient(_HTTP_CONFIG) as client:
            http_resp = await client.fetch(WebRequest(url=url))
        async with BrowserClient(_browser_config()) as client:
            browser_resp = await client.fetch(WebRequest(url=url))

        http_doc = html_to_document(http_resp.html, url=http_resp.url)
        browser_doc = html_to_document(browser_resp.html, url=browser_resp.url)

        # Titles should match (Astro is static, no JS-only content)
        assert http_doc.metadata.title == browser_doc.metadata.title

    @pytest.mark.asyncio
    async def test_similar_word_count(self):
        """Both clients should extract similar content from a static page."""
        from kaos_web.clients.browser import BrowserClient

        url = f"{_SITE}/deploying-claude-code-safely-in-law-firms"

        async with HttpClient(_HTTP_CONFIG) as client:
            http_resp = await client.fetch(WebRequest(url=url))
        async with BrowserClient(_browser_config()) as client:
            browser_resp = await client.fetch(WebRequest(url=url))

        http_text = _serialize_text(html_to_document(http_resp.html, url=http_resp.url))
        browser_text = _serialize_text(html_to_document(browser_resp.html, url=browser_resp.url))

        http_words = len(http_text.split())
        browser_words = len(browser_text.split())

        # Static site: word counts should be within 20% of each other
        ratio = min(http_words, browser_words) / max(http_words, browser_words)
        assert ratio > 0.8, (
            f"Word count divergence: httpx={http_words}, "
            f"playwright={browser_words}, ratio={ratio:.2f}"
        )

    @pytest.mark.asyncio
    async def test_same_metadata(self):
        from kaos_web.clients.browser import BrowserClient

        url = f"{_SITE}/products/kelvin"

        async with HttpClient(_HTTP_CONFIG) as client:
            http_resp = await client.fetch(WebRequest(url=url))
        async with BrowserClient(_browser_config()) as client:
            browser_resp = await client.fetch(WebRequest(url=url))

        http_meta = extract_metadata(http_resp.html, url=http_resp.url)
        browser_meta = extract_metadata(browser_resp.html, url=browser_resp.url)

        assert http_meta.title == browser_meta.title
        assert http_meta.description == browser_meta.description
        assert http_meta.site_name == browser_meta.site_name


# ============================================================
# Known failures (regression tests for gaps)
# ============================================================


class TestKnownGaps:
    """Tests that document known extraction failures.

    These use xfail so they pass the suite but are tracked. When the
    underlying issue is fixed, the xfail will start failing (strict=True)
    and we'll know to remove it.
    """

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="Readability discards blog listing cards as boilerplate", strict=True)
    async def test_blog_listing_extracts_posts(self):
        """Blog listing page should extract post titles and excerpts."""
        async with HttpClient(_HTTP_CONFIG) as client:
            resp = await client.fetch(WebRequest(url=f"{_SITE}/blog"))
        doc = html_to_document(resp.html, url=resp.url)
        text = _serialize_text(doc)
        # Should have at least 100 words of blog post excerpts
        assert len(text.split()) > 100

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="Wikipedia [edit] links leak into markdown", strict=True)
    async def test_wikipedia_no_edit_links(self):
        """Wikipedia extraction should not include [edit] section links."""
        from kaos_content.serializers.markdown import serialize_markdown

        async with HttpClient(_HTTP_CONFIG) as client:
            resp = await client.fetch(
                WebRequest(url="https://en.wikipedia.org/wiki/Large_language_model")
            )
        doc = html_to_document(resp.html, url=resp.url)
        md = serialize_markdown(doc)
        assert md.count("[edit]") == 0


# ============================================================
# Helpers
# ============================================================


def _serialize_text(doc) -> str:
    from kaos_content.serializers.text import serialize_text

    return serialize_text(doc)
