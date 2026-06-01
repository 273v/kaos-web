"""Tests for MCP tool definitions — register, execute, and error handling."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_core import KaosRuntime, ToolResult
from kaos_web.models import WebResponse
from kaos_web.tools import (
    FetchPageTool,
    GetPageImagesTool,
    GetPageLinksTool,
    GetPageMarkdownTool,
    GetPageMetadataTool,
    GetPageTablesTool,
    GetPageTextTool,
    SearchPageTool,
    WebSearchTool,
    _artifact_id_from_handle,
    _browser_inputs,
    _fetch_html,
    _load_handle_or_signal,
    register_web_tools,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
ARTICLE_HTML = (FIXTURES / "article.html").read_text(encoding="utf-8")

_has_kaos_nlp_core = bool(sys.modules.get("kaos_nlp_core")) or (
    importlib.util.find_spec("kaos_nlp_core") is not None
)


class TestRegisterTools:
    def test_register_web_tools(self) -> None:
        """Register the 10 HTTP fetch / feed / search tools — the `web` group."""
        runtime = KaosRuntime()
        count = register_web_tools(runtime)

        assert count == 10, f"Expected 10 tools registered, got {count}"

        registered = runtime.tools.list_tools()
        expected_names = {
            "kaos-web-fetch-page",
            "kaos-web-get-text",
            "kaos-web-get-markdown",
            "kaos-web-get-metadata",
            "kaos-web-search-page",
            "kaos-web-get-links",
            "kaos-web-get-images",
            "kaos-web-fetch-feed",
        }
        for name in expected_names:
            assert name in registered, (
                f"Tool '{name}' should be registered. Registered tools: {registered}"
            )

    def test_register_web_all_tools_union(self) -> None:
        """`register_web_all_tools` registers all 46 tools across 4 groups.

        Pins the convenience-union entry point: 9 web + 19 browser +
        14 netinfra + 3 crawl = 45. Registration itself is lazy with
        respect to ``[browser]`` / ``[dns]`` extras — construction
        doesn't import Playwright or dnspython.
        """
        from kaos_web.tools import register_web_all_tools

        runtime = KaosRuntime()
        count = register_web_all_tools(runtime)
        # 9 web + 19 browser + 14 netinfra + 3 crawl = 45.
        assert count == 46, f"Expected 46 tools registered, got {count}"

        registered = runtime.tools.list_tools()
        # Spot-check one tool per group is present.
        for required in (
            "kaos-web-fetch-page",  # web group
            "kaos-web-browser-navigate",  # browser group
            "kaos-web-dns-lookup",  # netinfra group
            "kaos-web-crawl-site",  # crawl group (folds into `web`)
        ):
            assert required in registered, (
                f"Tool '{required}' should be registered. Registered: {registered}"
            )

    def test_every_browser_tool_carries_browser_tag(self) -> None:
        """Per `kaos-modules/docs/internal/dynamic-tool-planning-completion-plan.md`
        §2.4, every Playwright-based tool must carry ``tags=["browser"]``
        so kaos-agents' ``derive_group()`` classifies it into the
        SessionToolSet ``browser`` group rather than the broader
        ``web`` group. A new browser tool added without the tag
        silently lands in ``web`` (and gets surfaced on `web`-only
        sessions that haven't opted into Playwright); this test
        catches that drift.
        """
        from kaos_web.browser_tools import register_browser_tools

        runtime = KaosRuntime()
        register_browser_tools(runtime)
        for tool in runtime.tools.list_tool_objects():
            assert "browser" in tool.metadata.tags, (
                f"{tool.metadata.name} is a browser tool but lacks "
                f"tags=['browser']; got tags={tool.metadata.tags}."
            )

    def test_every_netinfra_tool_carries_netinfra_tag(self) -> None:
        """DNS / WHOIS / TLS / TCP banner / UDP probe / HTTP-headers
        tools must carry ``tags=["netinfra"]`` for kaos-agents'
        ``derive_group()``. Default-off at the SessionToolSet
        ceiling; without the tag they'd land in ``web`` and
        accidentally surface on the default research preset."""
        from kaos_web.domain_tools import register_domain_tools

        runtime = KaosRuntime()
        register_domain_tools(runtime)
        for tool in runtime.tools.list_tool_objects():
            assert "netinfra" in tool.metadata.tags, (
                f"{tool.metadata.name} is a netinfra tool but lacks "
                f"tags=['netinfra']; got tags={tool.metadata.tags}."
            )

    def test_web_tools_do_not_carry_browser_or_netinfra_tags(self) -> None:
        """HTTP fetch + search tools and crawl tools are pure `web`
        group — they should NOT carry browser/netinfra tags. The
        kaos-agents derivation reads tags as narrowing signals;
        having them here would incorrectly route these tools to
        opt-in groups."""
        from kaos_web.crawl_tools import register_crawl_tools

        runtime = KaosRuntime()
        register_web_tools(runtime)
        register_crawl_tools(runtime)
        for tool in runtime.tools.list_tool_objects():
            tags = tool.metadata.tags
            assert "browser" not in tags, (
                f"{tool.metadata.name} is not a browser tool but carries "
                f"tags=['browser']; got tags={tags}."
            )
            assert "netinfra" not in tags, (
                f"{tool.metadata.name} is not a netinfra tool but carries "
                f"tags=['netinfra']; got tags={tags}."
            )


class TestFetchPageTool:
    async def test_fetch_page_no_context(self) -> None:
        """FetchPage without a runtime context should return an error suggesting alternatives."""
        tool = FetchPageTool()
        result = await tool.execute({"url": "https://example.com"}, context=None)

        assert result.isError is True, "Should error without runtime context"
        assert "kaos-web-get-markdown" in (result.text or ""), (
            "Error should suggest the context-free alternative tool"
        )


class TestGetPageTextTool:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_text_extracts_content(self, mock_fetch: AsyncMock) -> None:
        """GetPageText should return extracted plain text from HTML."""
        mock_fetch.return_value = (ARTICLE_HTML, "https://example.com/article")

        tool = GetPageTextTool()
        result = await tool.execute({"url": "https://example.com/article"})

        assert result.isError is not True, f"Should succeed, got error: {result.content}"
        text = result.require_text()
        assert text is not None, "Result should contain text"
        assert "Main Article Heading" in text, "Extracted text should contain the main heading"
        assert "Section One" in text, "Extracted text should contain section headings"


class TestGetPageMarkdownTool:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_markdown_extracts_content(self, mock_fetch: AsyncMock) -> None:
        """GetPageMarkdown should return markdown-formatted content."""
        mock_fetch.return_value = (ARTICLE_HTML, "https://example.com/article")

        tool = GetPageMarkdownTool()
        result = await tool.execute({"url": "https://example.com/article"})

        assert result.isError is not True, f"Should succeed, got error: {result.content}"
        md = result.require_text()
        assert md is not None, "Result should contain markdown text"
        # Markdown should contain heading markers
        assert "Main Article Heading" in md, "Markdown should contain the article heading"


class TestGetPageMetadataTool:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_metadata_extracts(self, mock_fetch: AsyncMock) -> None:
        """GetPageMetadata should extract OG tags and structured data."""
        mock_fetch.return_value = (ARTICLE_HTML, "https://example.com/article")

        tool = GetPageMetadataTool()
        result = await tool.execute({"url": "https://example.com/article"})

        assert result.isError is not True, f"Should succeed, got error: {result.content}"
        # Metadata tool uses create_success(output=dict) which puts data in structuredContent
        meta = result.structuredContent
        assert meta is not None, "Metadata should be in structuredContent"
        assert meta.get("title") == "Test Article OG Title", (
            f"Expected OG title, got: {meta.get('title')}"
        )
        assert meta.get("author") == "Jane Doe", (
            f"Expected author 'Jane Doe', got: {meta.get('author')}"
        )
        assert meta.get("site_name") == "Example Site"
        assert len(meta.get("structured_data", [])) >= 1, "Should extract JSON-LD structured data"


@pytest.mark.skipif(not _has_kaos_nlp_core, reason="kaos-nlp-core not installed")
class TestSearchPageTool:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_search_page_finds_results(self, mock_fetch: AsyncMock) -> None:
        """SearchPage should return matching results with block_refs and scores."""
        mock_fetch.return_value = (ARTICLE_HTML, "https://example.com/article")

        tool = SearchPageTool()
        result = await tool.execute(
            {
                "url": "https://example.com/article",
                "query": "blockquote important statement",
                "top_k": 5,
                "level": "paragraph",
            }
        )

        assert result.isError is not True, f"Should succeed, got error: {result.content}"

        # SearchPage uses create_success(output=dict) -> structuredContent
        data = result.structuredContent
        assert data is not None, "Search results should be in structuredContent"
        assert "results" in data, "Response should contain 'results' key"
        assert data["url"] == "https://example.com/article"
        assert data["query"] == "blockquote important statement"

        results = data["results"]
        assert len(results) > 0, "Should find at least one matching result"
        first = results[0]
        assert "text" in first, "Each result should have a 'text' field"
        assert "score" in first, "Each result should have a 'score' field"
        assert "block_ref" in first, "Each result should have a 'block_ref' field"


class TestArtifactHandleComposition:
    """Page tools must compose with their OWN artifact handles.

    Regression for the artifact/VFS failure where `kaos-web-fetch-page`
    returns a large page's body as a ``kaos://artifacts/<id>/body``
    handle, and a follow-up page tool (e.g. `kaos-web-search-page`) then
    fed that handle could not read it — the URL security gate correctly
    blocks the non-(http|https) ``kaos://`` scheme. The page tools now
    resolve the handle from the artifact store instead of re-fetching.
    """

    def test_artifact_id_from_handle(self) -> None:
        assert _artifact_id_from_handle("kaos://artifacts/abc-123/body") == "abc-123"
        assert _artifact_id_from_handle("kaos://content/xyz-9/sections") == "xyz-9"
        assert _artifact_id_from_handle("kaos://content/only-id") == "only-id"
        # Ordinary URLs are fetched normally → not a handle.
        assert _artifact_id_from_handle("https://example.com/page") is None
        assert _artifact_id_from_handle("http://x.test") is None
        # Degenerate / empty handles resolve to None (caller errors clearly).
        assert _artifact_id_from_handle("kaos://artifacts/") is None

    @pytest.mark.asyncio
    async def test_load_handle_or_signal_contract(self) -> None:
        """The shared seam the four page tools route through returns exactly
        one of three signals: fetch-me / rendered-doc / clear-error."""
        # 1. Ordinary URL → (None, None): caller fetches as before.
        doc, err = await _load_handle_or_signal("https://example.com/page", None)
        assert doc is None and err is None

        # 2. Handle + working runtime → (doc, None): resolved, no fetch.
        from kaos_web.extract import html_to_document

        loaded = html_to_document(ARTICLE_HTML, url="https://example.com/article")
        ctx = MagicMock()
        ctx.runtime = MagicMock()
        with patch(
            "kaos_content.artifacts.load_document", new_callable=AsyncMock, return_value=loaded
        ):
            doc, err = await _load_handle_or_signal("kaos://artifacts/abc-1/body", ctx)
        assert doc is loaded and err is None

        # 3. Handle but no runtime → (None, error): names the cause, not a 404.
        doc, err = await _load_handle_or_signal("kaos://artifacts/abc-1/body", None)
        assert doc is None
        assert err is not None and err.isError is True

    @pytest.mark.asyncio
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_markdown_reads_handle_without_fetching(self, mock_fetch: AsyncMock) -> None:
        """A kaos:// handle resolves to the stored document's markdown
        WITHOUT touching the web fetcher."""
        mock_fetch.side_effect = AssertionError("must not fetch a kaos:// handle")
        from kaos_web.extract import html_to_document

        doc = html_to_document(ARTICLE_HTML, url="https://example.com/article")
        ctx = MagicMock()
        ctx.runtime = MagicMock()
        with patch(
            "kaos_content.artifacts.load_document", new_callable=AsyncMock, return_value=doc
        ) as mock_load:
            tool = GetPageMarkdownTool()
            result = await tool.execute({"url": "kaos://content/art-9/markdown"}, context=ctx)

        assert result.isError is not True, f"Should resolve handle, got: {result.content}"
        mock_fetch.assert_not_called()
        mock_load.assert_awaited_once_with("art-9", ctx.runtime, max_bytes=None)

    @pytest.mark.asyncio
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_handle_without_runtime_errors_clearly(self, mock_fetch: AsyncMock) -> None:
        """A handle passed without a runtime/artifact store fails with a
        cause-naming error, not a fetch attempt or an opaque 404."""
        mock_fetch.side_effect = AssertionError("must not fetch a kaos:// handle")
        tool = GetPageMarkdownTool()
        result = await tool.execute({"url": "kaos://artifacts/abc/body"}, context=None)
        assert result.isError is True
        mock_fetch.assert_not_called()

    @pytest.mark.skipif(not _has_kaos_nlp_core, reason="kaos-nlp-core not installed")
    @pytest.mark.asyncio
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_search_page_searches_handle_without_fetching(
        self, mock_fetch: AsyncMock
    ) -> None:
        """`kaos-web-search-page` on a kaos:// handle searches the stored
        document directly (the exact reported failure)."""
        mock_fetch.side_effect = AssertionError("must not fetch a kaos:// handle")
        from kaos_web.extract import html_to_document

        doc = html_to_document(ARTICLE_HTML, url="https://example.com/article")
        ctx = MagicMock()
        ctx.runtime = MagicMock()
        handle = "kaos://artifacts/abc-123/body"
        with patch(
            "kaos_content.artifacts.load_document", new_callable=AsyncMock, return_value=doc
        ) as mock_load:
            tool = SearchPageTool()
            result = await tool.execute(
                {"url": handle, "query": "blockquote important statement", "top_k": 5},
                context=ctx,
            )

        assert result.isError is not True, f"Should search handle, got: {result.content}"
        mock_fetch.assert_not_called()
        mock_load.assert_awaited_once_with("abc-123", ctx.runtime, max_bytes=None)
        data = result.structuredContent
        assert data is not None
        assert data["url"] == handle, "result should attribute the source to the handle"
        assert len(data["results"]) > 0, "should find matches in the stored document"


class TestGetPageLinksTool:
    """Tests for the link extraction tool."""

    def test_metadata(self):
        tool = GetPageLinksTool()
        assert tool.metadata.name == "kaos-web-get-links"
        assert tool.metadata.annotations is not None
        assert tool.metadata.annotations.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_empty_url_error(self):
        tool = GetPageLinksTool()
        result = await tool.execute({"url": ""})
        assert result.isError

    @pytest.mark.asyncio
    async def test_extracts_links(self):
        html = (
            "<html><body>"
            '<nav><a href="/about">About</a></nav>'
            '<a href="/article">Article</a>'
            "</body></html>"
        )
        with patch("kaos_web.tools._fetch_html", return_value=(html, "https://example.com")):
            tool = GetPageLinksTool()
            result = await tool.execute({"url": "https://example.com"})
        assert not result.isError
        data = result.structuredContent
        assert data is not None
        assert data["total"] >= 2

    @pytest.mark.asyncio
    async def test_filter_by_type(self):
        html = (
            "<html><body>"
            '<nav><a href="/about">About</a></nav>'
            '<a href="/article">Article</a>'
            "</body></html>"
        )
        with patch("kaos_web.tools._fetch_html", return_value=(html, "https://example.com")):
            tool = GetPageLinksTool()
            result = await tool.execute(
                {
                    "url": "https://example.com",
                    "link_type": "navigation",
                }
            )
        data = result.structuredContent
        assert data is not None
        # All returned links should be navigation type
        for _pos, links in data["by_position"].items():
            for lnk in links:
                assert lnk["type"] == "navigation"


class TestRawMode:
    """Tests for the raw parameter on extraction tools."""

    @pytest.mark.asyncio
    async def test_raw_mode_has_param(self):
        """All extraction tools should have the raw parameter."""
        for tool_cls in [FetchPageTool, GetPageTextTool, GetPageMarkdownTool]:
            tool = tool_cls()
            params = {p.name for p in tool.metadata.input_schema}
            assert "raw" in params, f"{tool.metadata.name} missing raw param"

    @pytest.mark.asyncio
    async def test_text_raw_vs_normal(self):
        """Raw mode should return more content than normal mode on nav-heavy pages."""
        html = (
            "<html><body>"
            '<nav><a href="/">Home</a> <a href="/about">About</a></nav>'
            "<main><p>Main content paragraph with enough words.</p></main>"
            "<footer><p>Footer content here.</p></footer>"
            "</body></html>"
        )
        with patch("kaos_web.tools._fetch_html", return_value=(html, "https://example.com")):
            tool = GetPageTextTool()
            normal = await tool.execute({"url": "https://example.com"})
            raw = await tool.execute({"url": "https://example.com", "raw": True})
        # Both should succeed
        assert not normal.isError
        assert not raw.isError


class TestGetPageImagesTool:
    """Tests for the image extraction tool."""

    def test_metadata(self):
        tool = GetPageImagesTool()
        assert tool.metadata.name == "kaos-web-get-images"
        assert tool.metadata.annotations is not None
        assert tool.metadata.annotations.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_empty_url_error(self):
        tool = GetPageImagesTool()
        result = await tool.execute({"url": ""})
        assert result.isError

    @pytest.mark.asyncio
    async def test_extracts_images(self):
        html = (
            "<html><body>"
            '<img src="/photo.jpg" alt="A photo" width="800" height="600">'
            '<img src="/icon.png" class="icon" width="16" height="16">'
            "</body></html>"
        )
        with patch(
            "kaos_web.tools._fetch_html",
            return_value=(html, "https://example.com"),
        ):
            tool = GetPageImagesTool()
            result = await tool.execute({"url": "https://example.com"})
        assert not result.isError
        data = result.structuredContent
        assert data is not None
        assert data["total"] == 2
        assert "by_type" in data

    @pytest.mark.asyncio
    async def test_filter_by_type(self):
        html = (
            "<html><body>"
            '<img src="/photo.jpg" alt="Content image" width="800" height="600">'
            '<img src="/pixel.gif" width="1" height="1">'
            "</body></html>"
        )
        with patch(
            "kaos_web.tools._fetch_html",
            return_value=(html, "https://example.com"),
        ):
            tool = GetPageImagesTool()
            result = await tool.execute({"url": "https://example.com", "image_type": "content"})
        data = result.structuredContent
        assert data is not None
        for img in data["images"]:
            assert img["type"] == "content"

    @pytest.mark.asyncio
    async def test_includes_format(self):
        html = '<html><body><img src="/photo.webp" alt="test"></body></html>'
        with patch(
            "kaos_web.tools._fetch_html",
            return_value=(html, "https://example.com"),
        ):
            tool = GetPageImagesTool()
            result = await tool.execute({"url": "https://example.com"})
        data = result.structuredContent
        assert data is not None
        assert data["images"][0]["format"] == "webp"


class TestTrackingPixelClassification:
    """Verify 1x1 in filenames doesn't trigger false positive tracking detection."""

    def test_1x1_in_filename_not_tracking(self):
        from kaos_web.extract.images import extract_images

        html = (
            '<html><body><img src="/headshot-1x1.webp" alt="Person"'
            ' width="400" height="400"></body></html>'
        )
        images = extract_images(html, url="https://example.com")
        assert len(images) == 1
        assert images[0].image_type == "content"

    def test_actual_1x1_pixel_is_tracking(self):
        from kaos_web.extract.images import extract_images

        html = '<html><body><img src="/pixel.gif" width="1" height="1"></body></html>'
        images = extract_images(html, url="https://example.com")
        assert len(images) == 1
        assert images[0].image_type == "tracking"


# ── Backfill: helper utilities (audit-03 WEB3-005) ───────────────────


class TestBrowserInputsHelper:
    """_browser_inputs filters tool inputs to browser-relevant kwargs."""

    def test_empty_returns_empty(self) -> None:
        assert _browser_inputs({}) == {}

    def test_dismiss_overlays_explicit_false(self) -> None:
        # False is not None — it should be passed through
        out = _browser_inputs({"dismiss_overlays": False})
        assert out == {"dismiss_overlays": False}

    def test_wait_for_selector_only_passed_when_truthy(self) -> None:
        assert "wait_for_selector" not in _browser_inputs({"wait_for_selector": ""})
        assert _browser_inputs({"wait_for_selector": "#x"}) == {"wait_for_selector": "#x"}

    def test_wait_for_settled_explicit_false(self) -> None:
        out = _browser_inputs({"wait_for_settled": False})
        assert out == {"wait_for_settled": False}

    def test_all_three_combined(self) -> None:
        out = _browser_inputs(
            {
                "dismiss_overlays": True,
                "wait_for_selector": "#main",
                "wait_for_settled": True,
            }
        )
        assert out == {
            "dismiss_overlays": True,
            "wait_for_selector": "#main",
            "wait_for_settled": True,
        }


def _async_cm(client: MagicMock) -> MagicMock:
    """Wrap a mocked client so it supports `async with HttpClient()`."""
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


# ── Backfill: _fetch_html branches ───────────────────────────────────


@pytest.mark.asyncio
class TestFetchHtmlBranches:
    """Cover the branching logic in _fetch_html: HTTP, browser, fallback."""

    async def test_http_only_path(self) -> None:
        # 0.1.3: _fetch_html is Playwright-first by default. Pass
        # use_browser=False to force the bare httpx path that this
        # test exercises.
        resp = WebResponse(
            url="https://example.com/final",
            status_code=200,
            html="<html><p>hi</p></html>",
        )
        client = _async_cm(MagicMock())
        client.fetch = AsyncMock(return_value=resp)
        with patch("kaos_web.clients.http.HttpClient", return_value=client):
            html, final = await _fetch_html("https://example.com", use_browser=False)
        assert "<p>hi</p>" in html
        assert final == "https://example.com/final"

    async def test_browser_with_context_id_uses_shared_client(self) -> None:
        """use_browser=True + context_id routes through _get_browser_client."""
        resp = WebResponse(
            url="https://example.com/r",
            status_code=200,
            html="<html><body>browser content</body></html>",
        )
        shared = MagicMock()
        shared.fetch = AsyncMock(return_value=resp)
        with patch(
            "kaos_web.browser_tools._get_browser_client",
            AsyncMock(return_value=shared),
        ):
            html, final = await _fetch_html(
                "https://example.com",
                use_browser=True,
                context_id="sess1",
                wait_for_selector="#x",
            )
        assert "browser content" in html
        assert final == "https://example.com/r"
        # WebRequest.extra carries context_id and selector
        call = shared.fetch.await_args
        assert call is not None
        req = call.args[0]
        assert req.extra.get("context_id") == "sess1"
        assert req.extra.get("wait_for_selector") == "#x"

    async def test_browser_one_shot_no_context_id(self) -> None:
        resp = WebResponse(url="u", status_code=200, html="<html>x</html>")
        client = _async_cm(MagicMock())
        client.fetch = AsyncMock(return_value=resp)
        with patch("kaos_web.clients.browser.BrowserClient", return_value=client):
            html, _ = await _fetch_html("https://e.com", use_browser=True)
        assert "x" in html

    async def test_browser_import_error_falls_back_to_http(self) -> None:
        """If `use_browser=True` and playwright is missing, fall back to HTTP."""
        resp = WebResponse(url="u", status_code=200, html="<html>fallback</html>")

        # Patch sys.modules so the inline import inside _fetch_html raises.
        http_client = _async_cm(MagicMock())
        http_client.fetch = AsyncMock(return_value=resp)

        # We can simulate ImportError by patching BrowserClient to raise on import path.
        with (
            patch(
                "kaos_web.clients.browser.BrowserClient",
                side_effect=ImportError("no playwright"),
            ),
            patch("kaos_web.clients.http.HttpClient", return_value=http_client),
        ):
            html, _ = await _fetch_html("https://e.com", use_browser=True)
        assert "fallback" in html

    async def test_http_403_falls_back_to_browser(self) -> None:
        """403 status from HTTP triggers browser fallback."""

        class _Err(Exception):
            status_code = 403

        http_client = _async_cm(MagicMock())
        http_client.fetch = AsyncMock(side_effect=_Err("forbidden"))

        browser_resp = WebResponse(url="u", status_code=200, html="<html>ok</html>")
        browser_client = _async_cm(MagicMock())
        browser_client.fetch = AsyncMock(return_value=browser_resp)

        with (
            patch("kaos_web.clients.http.HttpClient", return_value=http_client),
            patch("kaos_web.clients.browser.BrowserClient", return_value=browser_client),
        ):
            html, _ = await _fetch_html("https://e.com")
        assert "ok" in html

    async def test_http_403_browser_unavailable_reraises(self) -> None:
        class _Err(Exception):
            status_code = 403

        http_client = _async_cm(MagicMock())
        http_client.fetch = AsyncMock(side_effect=_Err("forbidden"))

        with (
            patch("kaos_web.clients.http.HttpClient", return_value=http_client),
            patch(
                "kaos_web.clients.browser.BrowserClient",
                side_effect=ImportError("no playwright"),
            ),
            pytest.raises(_Err),
        ):
            await _fetch_html("https://e.com")

    async def test_http_500_does_not_trigger_fallback(self) -> None:
        """Non-403/406 errors do not fall back — they re-raise.

        Forced httpx path via use_browser=False — 0.1.3 default is
        Playwright-first.
        """

        class _Err(Exception):
            status_code = 500

        http_client = _async_cm(MagicMock())
        http_client.fetch = AsyncMock(side_effect=_Err("boom"))
        with (
            patch("kaos_web.clients.http.HttpClient", return_value=http_client),
            pytest.raises(_Err),
        ):
            await _fetch_html("https://e.com", use_browser=False)


# ── Backfill: artifact-storage paths ────────────────────────────────


def _make_runtime_context(
    *, artifact_id: str = "art-1", body_uri: str = "kaos://artifacts/art-1/body"
) -> tuple[MagicMock, MagicMock]:
    """Build a mock context with a runtime + artifacts that captures store_document."""
    context = MagicMock()
    context.session_id = "test-session"
    manifest = MagicMock()
    manifest.artifact_id = artifact_id
    manifest.body_uri = body_uri
    # to_tool_result returns a real ToolResult so callers can inspect it
    manifest.to_tool_result = MagicMock(
        side_effect=lambda summary=None, structured_content=None, inline_body=None: (
            ToolResult.create_success(output=structured_content, summary=summary)
        )
    )
    runtime = MagicMock()
    runtime.artifacts = MagicMock()
    context.runtime = runtime
    return context, manifest


@pytest.mark.asyncio
class TestFetchPageToolArtifactPath:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_fetch_page_stores_artifact_with_runtime(self, mock_fetch: AsyncMock) -> None:
        html = "<html><body><h1>Doc</h1><p>One paragraph here for body content.</p></body></html>"
        mock_fetch.return_value = (html, "https://example.com/article")
        ctx, manifest = _make_runtime_context()
        with patch("kaos_content.artifacts.store_document", AsyncMock(return_value=manifest)):
            tool = FetchPageTool()
            result = await tool.execute({"url": "https://example.com/article"}, context=ctx)
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["artifact_id"] == "art-1"
        assert out["url"] == "https://example.com/article"
        assert out["body_uri"].endswith("/body")
        assert "outline" in out

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_fetch_page_no_body_returns_helpful_error(self, mock_fetch: AsyncMock) -> None:
        # Empty body → html_to_document returns a doc with no body
        mock_fetch.return_value = ("<html></html>", "https://example.com")
        ctx, _ = _make_runtime_context()
        result = await FetchPageTool().execute({"url": "https://example.com"}, context=ctx)
        assert result.isError
        text = result.text or ""
        assert "No content extracted" in text
        assert "use_browser=true" in text  # alternative recovery suggestion

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_fetch_page_fetch_failure_returns_error(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.side_effect = RuntimeError("connection refused")
        ctx, _ = _make_runtime_context()
        result = await FetchPageTool().execute({"url": "https://example.com"}, context=ctx)
        assert result.isError
        text = result.text or ""
        assert "Failed to fetch" in text
        assert "connection refused" in text

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_fetch_page_extraction_failure_caught(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = ("<html><body>x</body></html>", "https://example.com")
        ctx, _ = _make_runtime_context()
        with patch(
            "kaos_web.extract.html_to_document",
            side_effect=RuntimeError("parse fail"),
        ):
            result = await FetchPageTool().execute({"url": "https://example.com"}, context=ctx)
        assert result.isError
        text = result.text or ""
        assert "Content extraction failed" in text
        assert "parse fail" in text


@pytest.mark.asyncio
class TestGetPageTextArtifactPath:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_text_artifact_path_with_runtime(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (
            "<html><body><h1>T</h1><p>Body</p></body></html>",
            "https://example.com",
        )
        ctx, manifest = _make_runtime_context()
        with patch("kaos_content.artifacts.store_document", AsyncMock(return_value=manifest)):
            result = await GetPageTextTool().execute({"url": "https://example.com"}, context=ctx)
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["artifact_id"] == "art-1"

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_text_fetch_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.side_effect = RuntimeError("dns fail")
        result = await GetPageTextTool().execute({"url": "https://e.com"})
        assert result.isError
        text = result.text or ""
        assert "Failed to fetch" in text
        assert "dns fail" in text

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_text_extraction_failure_suggests_alternative(
        self, mock_fetch: AsyncMock
    ) -> None:
        mock_fetch.return_value = ("<html></html>", "https://e.com")
        with patch("kaos_web.extract.html_to_document", side_effect=RuntimeError("bad parse")):
            result = await GetPageTextTool().execute({"url": "https://e.com"})
        assert result.isError
        text = result.text or ""
        assert "Extraction failed" in text
        assert "kaos-web-fetch-page" in text  # alternative tool referenced


@pytest.mark.asyncio
class TestGetPageMarkdownArtifactPath:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_markdown_artifact_path_with_runtime(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (
            "<html><body><h1>T</h1><p>Body</p></body></html>",
            "https://example.com",
        )
        ctx, manifest = _make_runtime_context()
        with patch("kaos_content.artifacts.store_document", AsyncMock(return_value=manifest)):
            result = await GetPageMarkdownTool().execute(
                {"url": "https://example.com"}, context=ctx
            )
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["artifact_id"] == "art-1"
        assert out["markdown_uri"].startswith("kaos://content/")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_markdown_fetch_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.side_effect = RuntimeError("nope")
        result = await GetPageMarkdownTool().execute({"url": "https://e.com"})
        assert result.isError
        assert "Failed to fetch" in (result.text or "")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_get_markdown_extraction_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = ("<html></html>", "https://e.com")
        with patch("kaos_web.extract.html_to_document", side_effect=RuntimeError("bad")):
            result = await GetPageMarkdownTool().execute({"url": "https://e.com"})
        assert result.isError
        assert "Extraction failed" in (result.text or "")


@pytest.mark.asyncio
class TestGetPageMetadataErrorPaths:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_metadata_fetch_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.side_effect = RuntimeError("nope")
        result = await GetPageMetadataTool().execute({"url": "https://e.com"})
        assert result.isError
        assert "Failed to fetch" in (result.text or "")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_metadata_extraction_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = ("<html><body>x</body></html>", "https://e.com")
        with patch("kaos_web.extract.extract_metadata", side_effect=RuntimeError("bad metadata")):
            result = await GetPageMetadataTool().execute({"url": "https://e.com"})
        assert result.isError
        text = result.text or ""
        assert "Metadata extraction failed" in text
        assert "bad metadata" in text


@pytest.mark.asyncio
class TestSearchPageErrorPaths:
    async def test_empty_query_returns_error(self) -> None:
        result = await SearchPageTool().execute({"url": "https://e.com", "query": "   "})
        assert result.isError
        assert "Query must not be empty" in (result.text or "")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_fetch_failure_translates(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.side_effect = RuntimeError("network down")
        result = await SearchPageTool().execute({"url": "https://e.com", "query": "hello"})
        assert result.isError
        assert "Failed to fetch" in (result.text or "")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_search_failure_translates(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = ("<html><body>x</body></html>", "https://e.com")
        with patch("kaos_content.search.search_document", side_effect=RuntimeError("idx fail")):
            result = await SearchPageTool().execute({"url": "https://e.com", "query": "hello"})
        assert result.isError
        assert "Search failed" in (result.text or "")


@pytest.mark.asyncio
class TestGetPageLinksErrorPaths:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_fetch_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.side_effect = RuntimeError("dns")
        result = await GetPageLinksTool().execute({"url": "https://e.com"})
        assert result.isError
        assert "Failed to fetch" in (result.text or "")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_extract_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = ("<html></html>", "https://e.com")
        with patch("kaos_web.extract.links.extract_links", side_effect=RuntimeError("oops")):
            result = await GetPageLinksTool().execute({"url": "https://e.com"})
        assert result.isError
        assert "Link extraction failed" in (result.text or "")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_internal_only_filter(self, mock_fetch: AsyncMock) -> None:
        html = (
            "<html><body>"
            '<a href="/local">Local</a>'
            '<a href="https://other.com">External</a>'
            "</body></html>"
        )
        mock_fetch.return_value = (html, "https://example.com")
        result = await GetPageLinksTool().execute(
            {"url": "https://example.com", "internal_only": True}
        )
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        for _pos, links in out["by_position"].items():
            for link in links:
                assert link["internal"] is True


@pytest.mark.asyncio
class TestGetPageImagesErrorPaths:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_fetch_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.side_effect = RuntimeError("nope")
        result = await GetPageImagesTool().execute({"url": "https://e.com"})
        assert result.isError
        assert "Failed to fetch" in (result.text or "")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_extract_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = ("<html></html>", "https://e.com")
        with patch("kaos_web.extract.images.extract_images", side_effect=RuntimeError("oops")):
            result = await GetPageImagesTool().execute({"url": "https://e.com"})
        assert result.isError
        assert "Image extraction failed" in (result.text or "")

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_image_with_srcset_and_title(self, mock_fetch: AsyncMock) -> None:
        html = (
            '<html><body><img src="/p.jpg" alt="x" title="My Pic" '
            'srcset="/p-1x.jpg 1x, /p-2x.jpg 2x" '
            'width="800" height="600">'
            "</body></html>"
        )
        mock_fetch.return_value = (html, "https://e.com")
        result = await GetPageImagesTool().execute({"url": "https://e.com"})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        img = out["images"][0]
        assert img["title"] == "My Pic"
        assert "srcset" in img
        assert len(img["srcset"]) >= 1


# ── Backfill: GetPageTablesTool ─────────────────────────────────────


class TestGetPageTablesToolMetadata:
    def test_name_and_annotations(self) -> None:
        tool = GetPageTablesTool()
        assert tool.metadata.name == "kaos-web-get-tables"
        ann = tool.metadata.annotations
        assert ann is not None
        assert ann.readOnlyHint is True


@pytest.mark.asyncio
class TestGetPageTablesToolExecute:
    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_fetch_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.side_effect = RuntimeError("nope")
        result = await GetPageTablesTool().execute({"url": "https://e.com"})
        assert result.isError
        text = result.text or ""
        assert "Failed to fetch" in text
        assert "use_browser=true" in text

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_no_tables_returns_text_helpful(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (
            "<html><body><p>no tables</p></body></html>",
            "https://e.com",
        )
        result = await GetPageTablesTool().execute({"url": "https://e.com"})
        assert not result.isError
        text = result.text or ""
        assert "No tables found" in text
        assert "kaos-web-get-markdown" in text  # alternative tool reference

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_tables_tsv_output(self, mock_fetch: AsyncMock) -> None:
        html = (
            "<html><body><table>"
            "<tr><th>Name</th><th>Score</th></tr>"
            "<tr><td>Alice</td><td>90</td></tr>"
            "<tr><td>Bob</td><td>85</td></tr>"
            "</table></body></html>"
        )
        mock_fetch.return_value = (html, "https://e.com")
        result = await GetPageTablesTool().execute({"url": "https://e.com"})
        assert not result.isError
        text = result.text or ""
        # Token-efficient TSV: header line + data rows
        assert "Alice" in text
        assert "Bob" in text

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_tables_markdown_output(self, mock_fetch: AsyncMock) -> None:
        html = (
            "<html><body><table>"
            "<tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr>"
            "</table></body></html>"
        )
        mock_fetch.return_value = (html, "https://e.com")
        result = await GetPageTablesTool().execute({"url": "https://e.com", "format": "markdown"})
        assert not result.isError

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_tables_json_output(self, mock_fetch: AsyncMock) -> None:
        html = "<html><body><table><tr><th>A</th></tr><tr><td>1</td></tr></table></body></html>"
        mock_fetch.return_value = (html, "https://e.com")
        result = await GetPageTablesTool().execute({"url": "https://e.com", "format": "json"})
        assert not result.isError

    @patch("kaos_web.tools._fetch_html", new_callable=AsyncMock)
    async def test_extract_failure(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = ("<html><body>x</body></html>", "https://e.com")
        with patch(
            "kaos_content.bridges.content_to_tabular.extract_tables_as_tabular",
            side_effect=RuntimeError("bad table"),
        ):
            result = await GetPageTablesTool().execute({"url": "https://e.com"})
        assert result.isError
        assert "Table extraction failed" in (result.text or "")


# ── Backfill: WebSearchTool ─────────────────────────────────────────


class TestWebSearchToolMetadata:
    def test_name_and_annotations(self) -> None:
        tool = WebSearchTool()
        assert tool.metadata.name == "kaos-web-search"
        ann = tool.metadata.annotations
        assert ann is not None
        assert ann.readOnlyHint is True


@pytest.mark.asyncio
class TestWebSearchToolExecute:
    async def test_no_results(self) -> None:
        with patch("kaos_web.search.backends.search_web", AsyncMock(return_value=[])):
            result = await WebSearchTool().execute({"query": "obscure"})
        assert not result.isError
        text = result.text or ""
        assert "No results" in text
        assert "obscure" in text

    async def test_results_format(self) -> None:
        from kaos_web.search.backends import SearchResult

        results: list[Any] = [
            SearchResult(
                position=1,
                title="Hello world",
                url="https://e.com/",
                snippet="A nice intro",
            ),
            SearchResult(position=2, title="Second result", url="https://e.com/2", snippet=""),
        ]
        with patch("kaos_web.search.backends.search_web", AsyncMock(return_value=results)):
            result = await WebSearchTool().execute({"query": "hello"})
        assert not result.isError
        text = result.text or ""
        assert "Hello world" in text
        assert "Second result" in text

    async def test_value_error_returns_helpful_error(self) -> None:
        with patch(
            "kaos_web.search.backends.search_web",
            AsyncMock(side_effect=ValueError("no api key")),
        ):
            result = await WebSearchTool().execute({"query": "hi", "backend": "exa"})
        assert result.isError
        text = result.text or ""
        assert "Web search failed" in text
        assert "(backend=exa)" in text
        assert "KAOS_WEB_EXA_API_KEY" in text

    async def test_other_exception_returns_error(self) -> None:
        with patch(
            "kaos_web.search.backends.search_web",
            AsyncMock(side_effect=RuntimeError("backend dead")),
        ):
            result = await WebSearchTool().execute({"query": "hi"})
        assert result.isError
        text = result.text or ""
        assert "Web search failed" in text
        assert "backend dead" in text


class TestRegisterCount:
    def test_register_count_is_10(self) -> None:
        runtime = KaosRuntime()
        count = register_web_tools(runtime)
        assert count == 10
        names = runtime.tools.list_tools()
        assert "kaos-web-get-tables" in names
        assert "kaos-web-search" in names
        assert "kaos-web-fetch-feed" in names


# ── Playwright-default routing (kaos-web 0.1.8+) ──────────────────────
#
# Goal: when an MCP tool is called without an explicit ``use_browser``
# argument, the resolver picks Playwright (assuming the extra is
# installed). This is the fix for the 2026-05-23 incident where
# federalregister.gov and ecfr.gov returned 200 OK with a "Request
# Access" anti-bot HTML body, the bare httpx path treated it as
# success, and the agent fabricated FR climate-disclosure results.


class TestBotChallengeFingerprint:
    """``_looks_like_bot_challenge`` flags anti-bot interstitial HTML."""

    def test_federal_register_request_access(self) -> None:
        from kaos_web.tools import _looks_like_bot_challenge

        # The exact 2026-05-23 FR / eCFR interstitial body.
        html = (
            "<!DOCTYPE html><html><head>"
            "<title>Federal Register :: Request Access</title>"
            "</head><body>Please wait...</body></html>"
        )
        assert _looks_like_bot_challenge(html) is True

    def test_cloudflare_just_a_moment(self) -> None:
        from kaos_web.tools import _looks_like_bot_challenge

        html = (
            "<html><head><title>Just a moment...</title></head><body>"
            '<div class="cf-browser-verification">Checking your browser</div>'
            "</body></html>"
        )
        assert _looks_like_bot_challenge(html) is True

    def test_datadome_captcha(self) -> None:
        from kaos_web.tools import _looks_like_bot_challenge

        html = (
            "<html><body>"
            '<script src="https://geo.captcha-delivery.com/captcha/?initialCid=..."></script>'
            "</body></html>"
        )
        assert _looks_like_bot_challenge(html) is True

    def test_benign_html_is_not_a_challenge(self) -> None:
        from kaos_web.tools import _looks_like_bot_challenge

        # The article fixture is a real news article — must not trigger.
        assert _looks_like_bot_challenge(ARTICLE_HTML) is False

    def test_empty_html_is_not_a_challenge(self) -> None:
        from kaos_web.tools import _looks_like_bot_challenge

        assert _looks_like_bot_challenge("") is False
        assert _looks_like_bot_challenge(None) is False


class TestFetchHtmlDefaultsToPlaywright:
    """``_fetch_html(use_browser=None)`` picks Playwright when importable.

    The schema default for ``use_browser`` on every kaos-web MCP tool
    is ``None``, and every resolver passes that through verbatim
    (``inputs.get("use_browser")``). So the agent calling
    ``kaos-web-fetch-page`` without overriding gets the realistic
    browser fingerprint by default — the routing that passes
    Cloudflare / SEC.gov / FR / eCFR / Investopedia anti-bot tiers.
    """

    @patch("kaos_web.clients.http.HttpClient", autospec=True)
    async def test_none_takes_browser_path_when_playwright_importable(
        self, mock_http_cls: Any
    ) -> None:
        # If Playwright is importable in this test env, the resolver
        # should pick the browser path and never construct HttpClient.
        # If it isn't, this test is moot.
        try:
            import playwright.async_api  # noqa: F401
        except ImportError:
            pytest.skip("playwright not installed; routing assertion N/A")

        # Stub the BrowserClient so we don't actually launch Chromium.
        with patch("kaos_web.clients.browser.BrowserClient") as mock_bc:
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=None)
            mock_resp = MagicMock()
            mock_resp.html = "<html><body>browser path</body></html>"
            mock_resp.url = "https://example.com"
            mock_inst.fetch = AsyncMock(return_value=mock_resp)
            mock_bc.return_value = mock_inst

            html, url = await _fetch_html("https://example.com", use_browser=None)
            assert "browser path" in html
            assert url == "https://example.com"

        # HttpClient must NOT have been used.
        mock_http_cls.assert_not_called()

    @patch("kaos_web.clients.http.HttpClient", autospec=True)
    async def test_explicit_false_takes_httpx_path(self, mock_http_cls: Any) -> None:
        # When the agent explicitly opts out, we honor it.
        mock_inst = MagicMock()
        mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_inst.__aexit__ = AsyncMock(return_value=None)
        mock_resp = MagicMock()
        mock_resp.html = "<html><body>httpx path</body></html>"
        mock_resp.url = "https://example.com"
        mock_inst.fetch = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_inst

        with patch("kaos_web.clients.browser.BrowserClient") as mock_bc:
            html, _url = await _fetch_html("https://example.com", use_browser=False)
            assert "httpx path" in html
            mock_bc.assert_not_called()

    @patch("kaos_web.clients.http.HttpClient", autospec=True)
    async def test_httpx_200_with_bot_challenge_triggers_browser_fallback(
        self, mock_http_cls: Any
    ) -> None:
        # The 2026-05-23 regression: httpx returns 200 OK with the FR
        # "Request Access" interstitial body. The router must detect
        # the fingerprint and retry on Playwright rather than handing
        # the agent a useless challenge page that looks like success.
        try:
            import playwright.async_api  # noqa: F401
        except ImportError:
            pytest.skip("playwright not installed; fallback N/A")

        # httpx returns a 200 with the FR interstitial body.
        mock_http_inst = MagicMock()
        mock_http_inst.__aenter__ = AsyncMock(return_value=mock_http_inst)
        mock_http_inst.__aexit__ = AsyncMock(return_value=None)
        challenge_resp = MagicMock()
        challenge_resp.html = (
            "<html><head><title>Federal Register :: Request Access</title>"
            "</head><body>Please wait</body></html>"
        )
        challenge_resp.url = "https://www.federalregister.gov/search"
        mock_http_inst.fetch = AsyncMock(return_value=challenge_resp)
        mock_http_cls.return_value = mock_http_inst

        # Browser fallback returns the real content.
        with patch("kaos_web.clients.browser.BrowserClient") as mock_bc:
            mock_browser_inst = MagicMock()
            mock_browser_inst.__aenter__ = AsyncMock(return_value=mock_browser_inst)
            mock_browser_inst.__aexit__ = AsyncMock(return_value=None)
            real_resp = MagicMock()
            real_resp.html = "<html><body>real FR search results</body></html>"
            real_resp.url = "https://www.federalregister.gov/search"
            mock_browser_inst.fetch = AsyncMock(return_value=real_resp)
            mock_bc.return_value = mock_browser_inst

            html, _ = await _fetch_html("https://www.federalregister.gov/search", use_browser=False)
            assert "real FR search results" in html, (
                "Bot-challenge body should trigger browser fallback even "
                "with use_browser=False; the regression that fooled the "
                "2026-05-23 agent is that the challenge HTML was returned "
                "as if it were a successful fetch."
            )


class TestUseBrowserSchemaDefaultIsNone:
    """Every MCP tool advertising ``use_browser`` defaults to ``None``.

    ``None`` means "let the resolver pick" — which is Playwright when
    the extra is installed. ``False`` would lock callers into the
    bare httpx path that gets silently blocked by anti-bot tiers.
    """

    def test_fetch_page_default_none(self) -> None:
        params = {p.name: p for p in FetchPageTool().metadata.input_schema}
        assert params["use_browser"].default is None

    def test_get_text_default_none(self) -> None:
        params = {p.name: p for p in GetPageTextTool().metadata.input_schema}
        assert params["use_browser"].default is None

    def test_get_markdown_default_none(self) -> None:
        params = {p.name: p for p in GetPageMarkdownTool().metadata.input_schema}
        assert params["use_browser"].default is None

    def test_search_page_default_none(self) -> None:
        params = {p.name: p for p in SearchPageTool().metadata.input_schema}
        assert params["use_browser"].default is None

    def test_get_tables_default_none(self) -> None:
        params = {p.name: p for p in GetPageTablesTool().metadata.input_schema}
        assert params["use_browser"].default is None
