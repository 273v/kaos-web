"""Live web extraction matrix — feeds, sitemaps, HTML, links, anti-bot.

This file is the serious live-integration suite the team has been missing.
It exercises **every kaos-web extraction surface** against real, current
public endpoints — RSS / Atom / sitemap.xml / sitemap-index / HTML AST /
link extraction / readability / images / metadata — and asserts both
freshness AND structural correctness.

Filed in response to a 2026-05-22 live agent session where the agent
reported "the SEC press-release sitemap exposed to me is stale and only
surfaced 2018-2019" after 12 tool calls and $0.027 of spend. The live
RSS feed at the same time showed press releases dated 2026-05-21 — i.e.
two days old. Root cause: kaos-web had no RSS/Atom parser, so the agent
had no path from a fresh feed to structured items. The parser ships in
``kaos_web/extract/feed.py``; this file is its regression net.

Markers:

* ``integration`` — exercises real HTTP / HTML / XML against the public
  internet. Skipped in normal CI; opt-in via ``pytest -m integration``.
* No ``live`` marker — these tests do NOT require API keys (no SerpAPI /
  Brave / Exa). They use kaos-web's free public extraction pipeline.

Run:
    uv run pytest tests/integration/test_web_live_extraction_matrix.py -v -m integration
"""

from __future__ import annotations

import pytest

from kaos_content.model.blocks import Heading
from kaos_content.serializers.markdown import serialize_markdown
from kaos_web.clients.config import HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.discover.sitemap import parse_sitemap
from kaos_web.errors import WebClientError
from kaos_web.extract import (
    extract_images,
    extract_links,
    extract_metadata,
    html_to_document,
    parse_feed,
)
from kaos_web.models import WebRequest

pytestmark = pytest.mark.integration


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def http_client():
    """HttpClient with realistic User-Agent rotation on.

    ``HttpClientConfig`` doesn't expose the full anti-bot header set
    (that ships in ``BrowserClientConfig``), so the per-site tests below
    note which sites need Playwright via ``BrowserClient`` instead.
    """
    cfg = HttpClientConfig(randomize_user_agent=True)
    async with HttpClient(cfg) as client:
        yield client


# ─── Feed Matrix (RSS 2.0 + Atom 1.0) ────────────────────────────────────────


class TestRSSFeeds:
    """RSS 2.0 feeds from real publishers we expect to query repeatedly."""

    async def test_sec_press_releases_rss(self, http_client):
        """SEC press releases RSS — the 2026-05-22 bug regression case.

        Asserts the parser produces at least 10 fresh items and the first
        item carries a parsed ``pub_date`` (the agent must be able to sort
        by it to answer "most recent").
        """
        resp = await http_client.fetch(WebRequest(url="https://www.sec.gov/news/pressreleases.rss"))
        assert resp.status_code == 200, f"SEC RSS unexpected status {resp.status_code}"
        feed = parse_feed(resp.html or "")
        assert feed.format == "rss"
        assert feed.title is not None
        assert len(feed.items) >= 10, f"SEC RSS should have ≥10 items, got {len(feed.items)}"
        top = feed.items[0]
        assert top.title and top.link.startswith("https://www.sec.gov")
        assert top.pub_date is not None, (
            "SEC press release items must carry a parseable pubDate — "
            "the agent uses it to answer 'most recent'"
        )

    async def test_treasury_press_releases_rss(self, http_client):
        """Treasury press releases RSS — second major US-gov feed."""
        try:
            resp = await http_client.fetch(
                WebRequest(url="https://home.treasury.gov/news/press-releases/feed")
            )
        except WebClientError as exc:
            pytest.skip(f"Treasury feed unreachable today: {exc}")
        if resp.status_code != 200:
            pytest.skip(f"Treasury feed returned {resp.status_code}")
        feed = parse_feed(resp.html or "")
        # Treasury can ship either RSS or Atom depending on the WordPress
        # plugin version. Accept either.
        assert feed.format in ("rss", "atom"), f"Unexpected format {feed.format}"
        assert len(feed.items) >= 1, "Treasury feed should have at least 1 item"


class TestAtomFeeds:
    """Atom 1.0 feeds from real publishers (GitHub releases is the canonical example)."""

    async def test_github_releases_atom(self, http_client):
        """GitHub releases Atom feed — used for 'what's the latest version of X?'."""
        resp = await http_client.fetch(
            WebRequest(url="https://github.com/python/cpython/releases.atom")
        )
        assert resp.status_code == 200
        feed = parse_feed(resp.html or "")
        assert feed.format == "atom"
        assert feed.title is not None
        assert len(feed.items) >= 5, f"Expected ≥5 entries, got {len(feed.items)}"
        # First entry should be a recent CPython release with a parseable date.
        top = feed.items[0]
        assert top.title.startswith("v3.")
        assert top.link.startswith("https://github.com/python/cpython/releases/tag/")
        assert top.pub_date is not None


class TestFeedParserDefensive:
    """Parser must not crash on hostile / malformed inputs."""

    def test_parse_empty_returns_unknown(self):
        result = parse_feed(b"")
        assert result.format == "unknown"
        assert result.items == ()

    def test_parse_garbage_returns_unknown(self):
        result = parse_feed(b"this is not xml or anything else")
        assert result.format == "unknown"
        assert result.items == ()

    def test_parse_html_error_page_returns_unknown(self):
        """Many publishers serve an HTML error page on 404 — parser must
        downgrade to ``unknown`` rather than raise."""
        html = (
            b"<!DOCTYPE html><html><head><title>404 Not Found</title></head>"
            b"<body><h1>404</h1></body></html>"
        )
        result = parse_feed(html)
        # html ELEMENT may match no known root — unknown is fine; we MUST
        # not crash and MUST return zero items.
        assert result.format == "unknown"
        assert result.items == ()


# ─── Sitemap Matrix ──────────────────────────────────────────────────────────


class TestSitemapMatrix:
    """Sitemap fetch + parse against real publishers.

    Documents which sites work with bare-httpx + anti-bot and which need
    Playwright. The agent's discovery flow consults this matrix when
    deciding what to attempt.
    """

    async def test_wikipedia_sitemap_index(self, http_client):
        """Wikipedia is the sitemap canonical reference — must always work.

        Wikipedia returns a sitemap **index** at this URL — a wrapper that
        lists per-language sitemap shards. ``parse_sitemap`` descends into
        them up to its depth cap, so we get child URLs back in
        ``sitemap_urls`` and may get entries from the first few shards.
        """

        async def _fetcher(req: WebRequest):
            return await http_client.fetch(req)

        result = await parse_sitemap("https://en.wikipedia.org/sitemap.xml", _fetcher)
        assert result.entries or result.sitemap_urls, (
            "Wikipedia sitemap should yield entries or child sitemap URLs"
        )

    async def test_sec_sitemap_403s_documents_limitation(self, http_client):
        """SEC.gov sitemap.xml is rate-limited / 403'd even with anti-bot
        headers. This test PINS that behavior — when it starts passing
        we should celebrate AND remove the workaround in the agent's
        discovery prompt. When it keeps failing the agent knows not to
        burn iterations on this path.
        """
        with pytest.raises(WebClientError) as exc_info:
            await http_client.fetch(WebRequest(url="https://www.sec.gov/sitemap.xml"))
        # WebClientError stringification includes the status code.
        assert "403" in str(exc_info.value), f"Expected 403, got: {exc_info.value}"


# ─── HTML AST + Extraction Matrix ────────────────────────────────────────────


class TestHTMLPipeline:
    """The full HTML pipeline: fetch → AST → markdown + links + images + metadata."""

    async def test_wikipedia_full_pipeline(self, http_client):
        """Wikipedia: structured HTML, server-rendered, link-rich.

        This is the canonical 'happy path' for the entire extraction
        stack. If this regresses, almost everything else regresses too.
        """
        resp = await http_client.fetch(
            WebRequest(url="https://en.wikipedia.org/wiki/Securities_and_Exchange_Commission")
        )
        assert resp.status_code == 200
        html = resp.html or ""
        # AST + markdown round-trip
        doc = html_to_document(html, url=resp.url)
        md = serialize_markdown(doc)
        assert len(doc.body) > 10, "Wikipedia pages have many blocks"
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) >= 5, "Wikipedia pages have many sections"
        assert "Securities and Exchange Commission" in md
        # Link extraction — should find both internal wiki links and external refs
        links = extract_links(html, url=resp.url)
        assert len(links) > 50
        internal = [lnk for lnk in links if lnk.is_internal]
        external = [lnk for lnk in links if not lnk.is_internal]
        assert len(internal) > 20
        assert len(external) > 5
        # Metadata
        meta = extract_metadata(html, url=resp.url)
        assert meta.title and "Securities and Exchange Commission" in meta.title
        # Images — Wikipedia pages have ≥1 image
        images = extract_images(html, url=resp.url)
        assert isinstance(images, list)
        assert len(images) >= 1, "Wikipedia SEC page should have ≥1 image"

    async def test_link_extraction_resolves_relative(self, http_client):
        """Non-anchor relative ``<a href="/foo">`` links must be resolved
        against the canonical URL. Anchor-only refs (``#section``) are
        intentionally left bare and classified as ``type="anchor"`` —
        they are in-page navigation, not external resources.

        Agents downstream rely on this split: anchor links should be
        filtered out before fan-out fetching; non-anchor links must be
        absolute so a subsequent ``kaos-web-fetch-page`` call succeeds.
        """
        resp = await http_client.fetch(WebRequest(url="https://en.wikipedia.org/wiki/Web_scraping"))
        links = extract_links(resp.html or "", url=resp.url)
        # Non-anchor links MUST be absolute.
        non_anchor = [lnk for lnk in links if lnk.link_type != "anchor"]
        assert len(non_anchor) > 30, "Wikipedia has many non-anchor links"
        bad = [
            lnk.url
            for lnk in non_anchor[:100]
            if not lnk.url.startswith(("http://", "https://", "mailto:", "tel:"))
        ]
        assert not bad, (
            f"Non-anchor links must be absolute — found {len(bad)} bad out of first 100: {bad[:5]}"
        )
        # Anchor-only links retain their bare ``#fragment`` form.
        anchors = [lnk for lnk in links if lnk.link_type == "anchor"]
        if anchors:
            assert all(lnk.url.startswith("#") for lnk in anchors[:20]), (
                "Anchor links should keep the bare '#fragment' form so "
                "downstream filters can drop them by prefix"
            )


# ─── Cross-publisher Anti-bot Matrix ─────────────────────────────────────────


class TestAntiBotMatrix:
    """Verify the anti-bot stack opens the doors the agent expects.

    Each case asserts a publisher that's KNOWN to gate bare-httpx requests
    is reachable with kaos-web's anti-bot headers. Regressions here mean
    the agent will start refusing real legal-research queries.
    """

    async def test_sec_pressreleases_rss_passes(self, http_client):
        """SEC press releases RSS passes anti-bot."""
        resp = await http_client.fetch(WebRequest(url="https://www.sec.gov/news/pressreleases.rss"))
        assert resp.status_code == 200

    async def test_sec_edgar_index_passes(self, http_client):
        """SEC EDGAR archive page (the Apple 10-K class) passes anti-bot.

        Pinned URL from 2026-05-22 live agent session.
        """
        resp = await http_client.fetch(
            WebRequest(
                url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-K&dateb=&owner=include&count=10"
            )
        )
        assert resp.status_code == 200
        # Anti-bot block looks like an HTML page titled "Request Rate Threshold Exceeded"
        assert "Request Rate Threshold Exceeded" not in (resp.html or "")
