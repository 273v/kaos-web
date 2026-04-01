"""Integration tests against real websites.

Tests the full pipeline: HTTP/Browser fetch → readability → HTML-to-AST →
link/image extraction across diverse page types.

Run with: pytest tests/integration/test_real_sites.py -v
Skip in CI: pytest -m "not integration"
"""

from __future__ import annotations

import pytest

from kaos_content.model.blocks import Heading, Paragraph
from kaos_content.serializers.markdown import serialize_markdown
from kaos_web.clients.config import BrowserClientConfig, HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.extract import extract_images, extract_links, extract_metadata, html_to_document
from kaos_web.models import WebRequest

pytestmark = pytest.mark.integration


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _http_fetch(url: str) -> tuple[str, str]:
    """Fetch via HTTP with realistic UA. Returns (html, final_url)."""
    config = HttpClientConfig(randomize_user_agent=True)
    async with HttpClient(config) as client:
        resp = await client.fetch(WebRequest(url=url))
        return resp.html, resp.url


async def _browser_fetch(url: str, **extra) -> tuple[str, str]:
    """Fetch via browser (Chrome). Returns (html, final_url)."""
    from kaos_web.clients.browser import BrowserClient

    config = BrowserClientConfig(
        channel="chrome",
        block_resources=["font", "media"],
    )
    async with BrowserClient(config) as client:
        resp = await client.fetch(WebRequest(url=url, timeout=25.0, extra=extra))
        return resp.html, resp.url


# ─── Wikipedia (Server-Rendered Baseline) ────────────────────────────────────


class TestWikipedia:
    URL = "https://en.wikipedia.org/wiki/Model_Context_Protocol"

    async def test_http_extracts_content(self):
        html, url = await _http_fetch(self.URL)
        doc = html_to_document(html, url=url)
        md = serialize_markdown(doc)

        assert len(doc.body) > 5, "Should have multiple blocks"
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) >= 2, "Should have section headings"
        assert len(md) > 500, "Should have substantial markdown"

    async def test_links_extracted(self):
        html, url = await _http_fetch(self.URL)
        links = extract_links(html, url=url)

        assert len(links) > 20, "Wikipedia pages have many links"
        internal = [lnk for lnk in links if lnk.is_internal]
        external = [lnk for lnk in links if not lnk.is_internal]
        assert len(internal) > 10, "Should have internal wiki links"
        assert len(external) > 0, "Should have external references"

    async def test_images_extracted(self):
        html, url = await _http_fetch(self.URL)
        images = extract_images(html, url=url)
        # Wikipedia articles usually have at least some images
        assert isinstance(images, list)


# ─── JS-Rendered Test Site (quotes.toscrape.com) ────────────────────────────


class TestJSRenderedSite:
    """quotes.toscrape.com/js/ is a purpose-built JS-rendered test site."""

    URL = "https://quotes.toscrape.com/js/"

    async def test_http_gets_empty_content(self):
        """HTTP without JS should get little/no quote content."""
        html, url = await _http_fetch(self.URL)
        doc = html_to_document(html, url=url, extract_content=False)
        md = serialize_markdown(doc)
        # JS site — httpx should get very little meaningful content
        # (the quotes are rendered by JavaScript)
        assert len(doc.body) < 50 or "quote" not in md.lower()[:500]

    async def test_browser_gets_real_content(self):
        """Browser should render JS and get actual quotes."""
        html, url = await _browser_fetch(self.URL, wait_until="load")
        doc = html_to_document(html, url=url, extract_content=False)
        md = serialize_markdown(doc)

        # Should have actual quote content rendered by JS
        assert len(doc.body) > 5, "Browser should render content"
        assert len(md) > 200, "Should have substantial text"


# ─── GitHub Repository (React SPA) ──────────────────────────────────────────


class TestGitHub:
    URL = "https://github.com/anthropics/anthropic-cookbook"

    async def test_browser_gets_readme(self):
        """Browser should render the README from the SPA."""
        html, url = await _browser_fetch(self.URL, wait_until="domcontentloaded")
        doc = html_to_document(html, url=url)
        md = serialize_markdown(doc)

        assert len(doc.body) > 3, "Should extract README content"
        assert "anthropic" in md.lower() or "cookbook" in md.lower(), "Should contain repo content"

    async def test_links_include_repo_links(self):
        html, url = await _browser_fetch(self.URL, wait_until="load")
        links = extract_links(html, url=url)

        assert len(links) > 10, "GitHub pages have many links"
        github_links = [lnk for lnk in links if "github.com" in lnk.url]
        assert len(github_links) > 5, "Should have GitHub internal links"


# ─── Government Site (Semantic HTML) ─────────────────────────────────────────


class TestGovernment:
    URL = "https://www.usa.gov/about-the-us"

    async def test_http_extracts_content(self):
        html, url = await _http_fetch(self.URL)
        doc = html_to_document(html, url=url)
        md = serialize_markdown(doc)

        assert len(doc.body) >= 1, "Should have at least one block"
        assert len(md) > 50, "Should have some text"

    async def test_metadata(self):
        html, url = await _http_fetch(self.URL)
        meta = extract_metadata(html, url=url)

        assert meta.title is not None, "Should have a title"


# ─── Legal Document (Cornell LII) ───────────────────────────────────────────


class TestLegalDocument:
    URL = "https://www.law.cornell.edu/uscode/text/17/107"

    async def test_http_extracts_legal_text(self):
        html, url = await _http_fetch(self.URL)
        doc = html_to_document(html, url=url)
        md = serialize_markdown(doc)

        assert len(doc.body) >= 1, "Should have content blocks"
        # Should contain legal text about fair use (Section 107)
        has_legal = "fair use" in md.lower() or "copyright" in md.lower() or "107" in md
        assert has_legal, "Should contain fair use / copyright content"

    async def test_links_include_cross_references(self):
        html, url = await _http_fetch(self.URL)
        links = extract_links(html, url=url)

        assert len(links) > 5, "Legal pages have cross-reference links"
        content_links = [lnk for lnk in links if lnk.link_type == "content"]
        assert len(content_links) > 0, "Should have content links (cross-refs)"


# ─── Static Scraping Sandbox (books.toscrape.com) ───────────────────────────


class TestScrapingSandbox:
    URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"

    async def test_http_extracts_product_info(self):
        html, url = await _http_fetch(self.URL)
        doc = html_to_document(html, url=url, extract_content=False)
        md = serialize_markdown(doc)

        assert len(doc.body) > 3
        assert "light in the attic" in md.lower(), "Should contain book title"

    async def test_images_extracted(self):
        html, url = await _http_fetch(self.URL)
        images = extract_images(html, url=url)

        content_images = [i for i in images if i.image_type == "content"]
        assert len(content_images) >= 1, "Should have at least the book cover image"


# ─── W3C Spec (Permanent, Semantic HTML) ─────────────────────────────────────


class TestW3CSpec:
    URL = "https://www.w3.org/TR/WCAG22/"

    async def test_http_extracts_spec(self):
        html, url = await _http_fetch(self.URL)
        doc = html_to_document(html, url=url)
        md = serialize_markdown(doc)

        assert len(doc.body) >= 1, "Should have content blocks"
        assert len(md) > 200, "Spec should have substantial content"

    async def test_metadata(self):
        html, url = await _http_fetch(self.URL)
        meta = extract_metadata(html, url=url)

        assert meta.title is not None


# ─── httpbin.org (HTTP Testing) ──────────────────────────────────────────────


class TestHttpbin:
    async def test_html_page(self):
        html, url = await _http_fetch("https://httpbin.org/html")
        doc = html_to_document(html, url=url, extract_content=False)

        assert len(doc.body) > 0
        paras = [b for b in doc.body if isinstance(b, Paragraph)]
        assert len(paras) > 0

    async def test_user_agent_is_realistic(self):
        """Verify our random UA is sent, not a bot identifier."""
        config = HttpClientConfig(randomize_user_agent=True)
        async with HttpClient(config) as client:
            resp = await client.fetch(WebRequest(url="https://httpbin.org/user-agent"))

        # httpbin returns {"user-agent": "..."}
        assert "Mozilla" in resp.html, "Should send realistic browser UA"
        assert "KAOS" not in resp.html, "Should not send bot UA by default"
