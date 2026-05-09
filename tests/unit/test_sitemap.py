"""Unit tests for sitemap parser."""

from __future__ import annotations

from datetime import datetime

import pytest

from kaos_web.discover.sitemap import (
    _parse_lastmod,
    _parse_priority,
    _parse_text_sitemap,
    _parse_xml_sitemap,
    discover_sitemaps,
    parse_sitemap,
)
from kaos_web.models import WebResponse

# --- XML fixtures ---

SIMPLE_SITEMAP = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/page1</loc>
    <lastmod>2026-03-15</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://example.com/page2</loc>
    <lastmod>2026-01-10T12:00:00+00:00</lastmod>
  </url>
</urlset>
"""

NO_NAMESPACE_SITEMAP = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url>
    <loc>https://example.com/no-ns</loc>
    <priority>0.5</priority>
  </url>
</urlset>
"""

SITEMAP_INDEX = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.com/sitemap1.xml</loc>
  </sitemap>
  <sitemap>
    <loc>https://example.com/sitemap2.xml</loc>
  </sitemap>
</sitemapindex>
"""

MALFORMED_XML = b"<urlset><url><loc>https://example.com/ok</loc></url><broken"

TEXT_SITEMAP = b"""\
https://example.com/text1
https://example.com/text2
  https://example.com/text3
not-a-url
"""


# --- Helper ---


def _make_fetch(responses: dict[str, tuple[int, str]]):
    """Create a mock fetch function returning canned responses."""

    async def fetch(request):
        url = request.url
        if url in responses:
            status, html = responses[url]
            return WebResponse(url=url, status_code=status, html=html)
        return WebResponse(url=url, status_code=404, html="")

    return fetch


# --- Test _parse_lastmod ---


class TestParseLastmod:
    def test_iso_date(self):
        assert _parse_lastmod("2026-03-15") == datetime(2026, 3, 15)

    def test_iso_datetime(self):
        dt = _parse_lastmod("2026-03-15T12:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026

    def test_year_month(self):
        assert _parse_lastmod("2026-03") == datetime(2026, 3, 1)

    def test_year_only(self):
        assert _parse_lastmod("2026") == datetime(2026, 1, 1)

    def test_none(self):
        assert _parse_lastmod(None) is None

    def test_empty(self):
        assert _parse_lastmod("") is None

    def test_invalid(self):
        assert _parse_lastmod("not-a-date") is None


class TestParsePriority:
    def test_valid(self):
        assert _parse_priority("0.8") == 0.8

    def test_zero(self):
        assert _parse_priority("0.0") == 0.0

    def test_one(self):
        assert _parse_priority("1.0") == 1.0

    def test_out_of_range(self):
        assert _parse_priority("1.5") is None

    def test_negative(self):
        assert _parse_priority("-0.1") is None

    def test_none(self):
        assert _parse_priority(None) is None

    def test_invalid(self):
        assert _parse_priority("high") is None


# --- Test XML parsing ---


class TestParseXmlSitemap:
    def test_simple_sitemap(self):
        entries, subs = _parse_xml_sitemap(SIMPLE_SITEMAP)
        assert len(entries) == 2
        assert entries[0].url == "https://example.com/page1"
        assert entries[0].changefreq == "weekly"
        assert entries[0].priority == 0.8
        assert entries[1].url == "https://example.com/page2"
        assert subs == []

    def test_no_namespace(self):
        entries, _subs = _parse_xml_sitemap(NO_NAMESPACE_SITEMAP)
        assert len(entries) == 1
        assert entries[0].url == "https://example.com/no-ns"
        assert entries[0].priority == 0.5

    def test_sitemap_index(self):
        entries, subs = _parse_xml_sitemap(SITEMAP_INDEX)
        assert entries == []
        assert len(subs) == 2
        assert subs[0] == "https://example.com/sitemap1.xml"
        assert subs[1] == "https://example.com/sitemap2.xml"

    def test_malformed_xml(self):
        entries, _subs = _parse_xml_sitemap(MALFORMED_XML)
        # Should recover and parse what it can
        assert len(entries) == 1
        assert entries[0].url == "https://example.com/ok"

    def test_empty(self):
        entries, subs = _parse_xml_sitemap(b"")
        assert entries == []
        assert subs == []

    def test_not_xml(self):
        entries, subs = _parse_xml_sitemap(b"just text content")
        assert entries == []
        assert subs == []


class TestParseTextSitemap:
    def test_simple(self):
        entries = _parse_text_sitemap(TEXT_SITEMAP)
        assert len(entries) == 3
        assert entries[0].url == "https://example.com/text1"
        assert entries[1].url == "https://example.com/text2"
        assert entries[2].url == "https://example.com/text3"

    def test_empty(self):
        assert _parse_text_sitemap(b"") == []

    def test_no_urls(self):
        assert _parse_text_sitemap(b"not a url\nstill not") == []


# --- Test parse_sitemap (async) ---


class TestParseSitemap:
    @pytest.mark.asyncio
    async def test_xml_sitemap(self):
        fetch = _make_fetch(
            {
                "https://example.com/sitemap.xml": (200, SIMPLE_SITEMAP.decode()),
            }
        )
        result = await parse_sitemap("https://example.com/sitemap.xml", fetch)
        assert len(result.entries) == 2
        assert result.sitemap_urls == ["https://example.com/sitemap.xml"]
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_sitemap_index_recursion(self):
        fetch = _make_fetch(
            {
                "https://example.com/sitemap_index.xml": (200, SITEMAP_INDEX.decode()),
                "https://example.com/sitemap1.xml": (200, SIMPLE_SITEMAP.decode()),
                "https://example.com/sitemap2.xml": (200, NO_NAMESPACE_SITEMAP.decode()),
            }
        )
        result = await parse_sitemap("https://example.com/sitemap_index.xml", fetch)
        assert len(result.entries) == 3  # 2 from sitemap1 + 1 from sitemap2
        assert len(result.sitemap_urls) == 3

    @pytest.mark.asyncio
    async def test_text_sitemap(self):
        fetch = _make_fetch(
            {
                "https://example.com/sitemap.txt": (200, TEXT_SITEMAP.decode()),
            }
        )
        result = await parse_sitemap("https://example.com/sitemap.txt", fetch)
        assert len(result.entries) == 3

    @pytest.mark.asyncio
    async def test_fetch_failure(self):
        async def failing_fetch(request):
            raise ConnectionError("Network error")

        result = await parse_sitemap("https://example.com/sitemap.xml", failing_fetch)
        assert len(result.entries) == 0
        assert len(result.errors) == 1
        assert "Network error" in result.errors[0]

    @pytest.mark.asyncio
    async def test_404(self):
        fetch = _make_fetch({})
        result = await parse_sitemap("https://example.com/sitemap.xml", fetch)
        assert len(result.entries) == 0
        assert len(result.errors) == 1
        assert "404" in result.errors[0]

    @pytest.mark.asyncio
    async def test_cycle_detection(self):
        # Sitemap index that references itself
        self_ref = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.com/sitemap.xml</loc>
  </sitemap>
</sitemapindex>
"""
        fetch = _make_fetch(
            {
                "https://example.com/sitemap.xml": (200, self_ref.decode()),
            }
        )
        result = await parse_sitemap("https://example.com/sitemap.xml", fetch)
        assert any("Cycle detected" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_depth_limit(self):
        # Chain of sitemap indexes
        responses = {}
        for i in range(6):
            idx = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.com/sm{i + 1}.xml</loc>
  </sitemap>
</sitemapindex>
"""
            responses[f"https://example.com/sm{i}.xml"] = (200, idx)

        fetch = _make_fetch(responses)
        result = await parse_sitemap("https://example.com/sm0.xml", fetch)
        assert any("Max sitemap depth" in e for e in result.errors)


# --- Test discover_sitemaps ---


class TestDiscoverSitemaps:
    @pytest.mark.asyncio
    async def test_from_robots_txt(self):
        robots = "User-agent: *\nDisallow:\nSitemap: https://example.com/sitemap.xml\n"
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (200, robots),
            }
        )
        urls = await discover_sitemaps("example.com", fetch)
        assert urls == ["https://example.com/sitemap.xml"]

    @pytest.mark.asyncio
    async def test_fallback_to_well_known(self):
        sitemap = SIMPLE_SITEMAP.decode()
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (200, sitemap),
            }
        )
        urls = await discover_sitemaps("example.com", fetch)
        assert urls == ["https://example.com/sitemap.xml"]

    @pytest.mark.asyncio
    async def test_no_sitemaps_found(self):
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/sitemap.xml": (404, ""),
                "https://example.com/sitemap_index.xml": (404, ""),
            }
        )
        urls = await discover_sitemaps("example.com", fetch)
        assert urls == []

    @pytest.mark.asyncio
    async def test_full_url_input(self):
        robots = "Sitemap: https://example.com/sm.xml\n"
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (200, robots),
            }
        )
        urls = await discover_sitemaps("https://example.com/some/page", fetch)
        assert urls == ["https://example.com/sm.xml"]

    @pytest.mark.asyncio
    async def test_multiple_sitemaps_in_robots(self):
        robots = (
            "User-agent: *\n"
            "Sitemap: https://example.com/sitemap1.xml\n"
            "Sitemap: https://example.com/sitemap2.xml\n"
        )
        fetch = _make_fetch(
            {
                "https://example.com/robots.txt": (200, robots),
            }
        )
        urls = await discover_sitemaps("example.com", fetch)
        assert len(urls) == 2
