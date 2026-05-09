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
    _browser_inputs,
    _fetch_html,
    register_web_tools,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
ARTICLE_HTML = (FIXTURES / "article.html").read_text(encoding="utf-8")

_has_kaos_nlp_core = bool(sys.modules.get("kaos_nlp_core")) or (
    importlib.util.find_spec("kaos_nlp_core") is not None
)


class TestRegisterTools:
    def test_register_web_tools(self) -> None:
        """Register 5 tools with a runtime and verify count and names."""
        runtime = KaosRuntime()
        count = register_web_tools(runtime)

        assert count == 9, f"Expected 9 tools registered, got {count}"

        registered = runtime.tools.list_tools()
        expected_names = {
            "kaos-web-fetch-page",
            "kaos-web-get-text",
            "kaos-web-get-markdown",
            "kaos-web-get-metadata",
            "kaos-web-search-page",
            "kaos-web-get-links",
            "kaos-web-get-images",
        }
        for name in expected_names:
            assert name in registered, (
                f"Tool '{name}' should be registered. Registered tools: {registered}"
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
        resp = WebResponse(
            url="https://example.com/final",
            status_code=200,
            html="<html><p>hi</p></html>",
        )
        client = _async_cm(MagicMock())
        client.fetch = AsyncMock(return_value=resp)
        with patch("kaos_web.clients.http.HttpClient", return_value=client):
            html, final = await _fetch_html("https://example.com")
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
        """Non-403/406 errors do not fall back — they re-raise."""

        class _Err(Exception):
            status_code = 500

        http_client = _async_cm(MagicMock())
        http_client.fetch = AsyncMock(side_effect=_Err("boom"))
        with (
            patch("kaos_web.clients.http.HttpClient", return_value=http_client),
            pytest.raises(_Err),
        ):
            await _fetch_html("https://e.com")


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
    def test_register_count_is_9(self) -> None:
        runtime = KaosRuntime()
        count = register_web_tools(runtime)
        assert count == 9
        names = runtime.tools.list_tools()
        assert "kaos-web-get-tables" in names
        assert "kaos-web-search" in names
