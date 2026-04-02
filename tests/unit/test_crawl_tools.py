"""Unit tests for crawl MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from kaos_web.crawl_tools import (
    BatchFetchTool,
    CrawlSiteTool,
    DiscoverUrlsTool,
    _split_patterns,
    register_crawl_tools,
)


class TestSplitPatterns:
    def test_basic(self):
        assert _split_patterns("/blog/,/docs/") == ["/blog/", "/docs/"]

    def test_whitespace(self):
        assert _split_patterns(" /a/ , /b/ ") == ["/a/", "/b/"]

    def test_none(self):
        assert _split_patterns(None) is None

    def test_empty(self):
        assert _split_patterns("") is None

    def test_single(self):
        assert _split_patterns("/blog/") == ["/blog/"]


class TestToolMetadata:
    """All crawl tools must have correct metadata and annotations."""

    @pytest.mark.parametrize(
        "tool_cls,expected_name",
        [
            (DiscoverUrlsTool, "kaos-web-discover-urls"),
            (BatchFetchTool, "kaos-web-batch-fetch"),
            (CrawlSiteTool, "kaos-web-crawl-site"),
        ],
    )
    def test_tool_names(self, tool_cls, expected_name):
        tool = tool_cls()
        assert tool.metadata.name == expected_name

    @pytest.mark.parametrize(
        "tool_cls",
        [DiscoverUrlsTool, BatchFetchTool, CrawlSiteTool],
    )
    def test_annotations_set(self, tool_cls):
        tool = tool_cls()
        ann = tool.metadata.annotations
        assert ann is not None
        assert ann.readOnlyHint is True
        assert ann.destructiveHint is False
        assert ann.openWorldHint is True

    @pytest.mark.parametrize(
        "tool_cls",
        [DiscoverUrlsTool, BatchFetchTool, CrawlSiteTool],
    )
    def test_has_input_schema(self, tool_cls):
        tool = tool_cls()
        assert len(tool.metadata.input_schema) > 0

    @pytest.mark.parametrize(
        "tool_cls",
        [DiscoverUrlsTool, BatchFetchTool, CrawlSiteTool],
    )
    def test_module_name(self, tool_cls):
        tool = tool_cls()
        assert tool.metadata.module_name == "kaos-web"


class TestDiscoverUrlsToolErrors:
    @pytest.mark.asyncio
    async def test_empty_url(self):
        tool = DiscoverUrlsTool()
        result = await tool.execute({"url": ""})
        assert result.isError
        assert "required" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_missing_url(self):
        tool = DiscoverUrlsTool()
        result = await tool.execute({})
        assert result.isError


class TestBatchFetchToolErrors:
    @pytest.mark.asyncio
    async def test_empty_urls(self):
        tool = BatchFetchTool()
        result = await tool.execute({"urls": ""})
        assert result.isError
        assert "required" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_missing_urls(self):
        tool = BatchFetchTool()
        result = await tool.execute({})
        assert result.isError


class TestCrawlSiteToolErrors:
    @pytest.mark.asyncio
    async def test_empty_url(self):
        tool = CrawlSiteTool()
        result = await tool.execute({"url": ""})
        assert result.isError
        assert "required" in result.content[0].text.lower()


class TestRegisterCrawlTools:
    def test_register_count(self):
        runtime = AsyncMock()
        runtime.tools.register_tool = lambda t: None
        count = register_crawl_tools(runtime)
        assert count == 3
