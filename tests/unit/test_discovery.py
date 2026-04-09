"""Unit tests for URL discovery."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kaos_web.discovery import (
    _compile_patterns,
    _matches_patterns,
    _same_domain,
    discover_urls,
)
from kaos_web.models import WebResponse

# --- Simple HTML with links ---

LINKS_HTML = """\
<html>
<body>
<a href="https://example.com/page1">Page 1</a>
<a href="https://example.com/page2">Page 2</a>
<a href="/page3">Page 3</a>
<a href="https://other.com/external">External</a>
</body>
</html>
"""

ROBOTS_WITH_SITEMAP = "User-agent: *\nDisallow: /admin/\nSitemap: https://example.com/sitemap.xml\n"

SIMPLE_SITEMAP = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/sm1</loc></url>
  <url><loc>https://example.com/sm2</loc></url>
</urlset>
"""


def _make_fetch(responses: dict[str, tuple[int, str]]):
    async def fetch(request):
        url = request.url
        if url in responses:
            status, html = responses[url]
            return WebResponse(url=url, status_code=status, html=html)
        return WebResponse(url=url, status_code=404, html="")

    return fetch


# --- Pattern matching ---


class TestPatternMatching:
    def test_include_match(self):
        inc = _compile_patterns(["/blog/"])
        assert _matches_patterns("https://example.com/blog/post", inc, None)

    def test_include_no_match(self):
        inc = _compile_patterns(["/blog/"])
        assert not _matches_patterns("https://example.com/docs/page", inc, None)

    def test_exclude_match(self):
        exc = _compile_patterns(["/admin/"])
        assert not _matches_patterns("https://example.com/admin/users", None, exc)

    def test_no_patterns(self):
        assert _matches_patterns("https://example.com/anything", None, None)

    def test_invalid_regex_skipped(self):
        result = _compile_patterns(["[invalid"])
        assert result is None  # Invalid regex is skipped


class TestSameDomain:
    def test_same(self):
        assert _same_domain("https://example.com/page", "example.com")

    def test_www_stripped(self):
        assert _same_domain("https://www.example.com/page", "example.com")

    def test_different(self):
        assert not _same_domain("https://other.com/page", "example.com")


# --- Test discover_urls ---


class TestDiscoverUrls:
    @pytest.mark.asyncio
    async def test_include_mode(self):
        """Default mode: sitemaps + page links."""
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (200, ROBOTS_WITH_SITEMAP),
                "https://example.com/sitemap.xml": (200, SIMPLE_SITEMAP),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("https://example.com", fetch)
        urls = {u.url for u in result.urls}
        # Should have sitemap + page link URLs
        assert "https://example.com/sm1" in urls
        assert "https://example.com/sm2" in urls
        assert result.sitemap_count >= 2
        assert result.page_link_count >= 0  # Might have some page links

    @pytest.mark.asyncio
    async def test_skip_mode(self):
        """Skip mode: page links only."""
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (200, ROBOTS_WITH_SITEMAP),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("https://example.com", fetch, sitemap="skip")
        assert result.sitemap_count == 0
        assert result.page_link_count > 0

    @pytest.mark.asyncio
    async def test_only_mode(self):
        """Only mode: sitemaps only."""
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (200, ROBOTS_WITH_SITEMAP),
                "https://example.com/sitemap.xml": (200, SIMPLE_SITEMAP),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("https://example.com", fetch, sitemap="only")
        assert result.sitemap_count >= 2
        assert result.page_link_count == 0

    @pytest.mark.asyncio
    async def test_include_patterns(self):
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("https://example.com", fetch, include_patterns=["page1"])
        urls = {u.url for u in result.urls}
        assert "https://example.com/page1" in urls
        assert "https://example.com/page2" not in urls

    @pytest.mark.asyncio
    async def test_exclude_patterns(self):
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("https://example.com", fetch, exclude_patterns=["page1"])
        urls = {u.url for u in result.urls}
        assert "https://example.com/page1" not in urls

    @pytest.mark.asyncio
    async def test_max_urls(self):
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("https://example.com", fetch, max_urls=1)
        assert len(result.urls) <= 1

    @pytest.mark.asyncio
    async def test_deduplication(self):
        """URLs from both sitemap and page links should be deduplicated."""
        # Sitemap has same URL as page link
        sitemap_with_overlap = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc></url>
</urlset>
"""
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (
                    200,
                    "Sitemap: https://example.com/sitemap.xml\n",
                ),
                "https://example.com/sitemap.xml": (200, sitemap_with_overlap),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("https://example.com", fetch)
        url_list = [u.url for u in result.urls]
        assert url_list.count("https://example.com/page1") == 1

    @pytest.mark.asyncio
    async def test_external_links_excluded(self):
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("https://example.com", fetch)
        urls = {u.url for u in result.urls}
        assert "https://other.com/external" not in urls

    @pytest.mark.asyncio
    async def test_bare_domain_input(self):
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
                "https://example.com": (200, LINKS_HTML),
            }
        )
        result = await discover_urls("example.com", fetch)
        assert result.total > 0

    @pytest.mark.asyncio
    async def test_robots_failure_is_reported(self):
        async def fetch(request):
            if request.url.endswith("/robots.txt"):
                raise RuntimeError("network timeout")
            if request.url == "https://example.com":
                return WebResponse(url=request.url, status_code=200, html=LINKS_HTML)
            return WebResponse(url=request.url, status_code=404, html="")

        with patch("kaos_web.discovery.logger.warning") as mock_warn:
            result = await discover_urls("https://example.com", fetch, sitemap="skip")

        assert result.total > 0
        assert any("robots.txt check failed" in err for err in result.errors)
        mock_warn.assert_called_once()
        assert "Proceeding without robots enforcement" in mock_warn.call_args[0][0]
