"""Tests for MCP tool definitions — register, execute, and error handling."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kaos_core import KaosRuntime
from kaos_web.tools import (
    FetchPageTool,
    GetPageImagesTool,
    GetPageLinksTool,
    GetPageMarkdownTool,
    GetPageMetadataTool,
    GetPageTextTool,
    SearchPageTool,
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
