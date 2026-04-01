"""MCP E2E tests: tools registered with KaosRuntime, called through ToolAdapter.

Tests the full path: register_web_tools → KaosRuntime → ToolAdapter → tool.execute()
This validates that tool metadata, schemas, and execution work through the MCP layer.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kaos_core import KaosContext, KaosRuntime
from kaos_web.tools import register_web_tools

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures"
ARTICLE_HTML = (FIXTURES / "article.html").read_text()


@pytest.fixture
def runtime():
    """Fresh runtime per test to avoid 'tool already registered' errors."""
    return KaosRuntime()


class TestToolRegistration:
    def test_register_5_tools(self, runtime: KaosRuntime) -> None:
        count = register_web_tools(runtime)
        assert count == 5

        names = {t.metadata.name for t in runtime.tools.list_tool_objects()}
        assert "kaos-web-fetch-page" in names
        assert "kaos-web-get-text" in names
        assert "kaos-web-get-markdown" in names
        assert "kaos-web-get-metadata" in names
        assert "kaos-web-search-page" in names

    def test_all_tools_have_annotations(self, runtime: KaosRuntime) -> None:
        register_web_tools(runtime)
        for tool in runtime.tools.list_tool_objects():
            if tool.metadata.module_name == "kaos-web":
                ann = tool.metadata.annotations
                assert ann is not None, f"{tool.metadata.name} missing annotations"
                assert ann.readOnlyHint is True
                assert ann.openWorldHint is True
                assert ann.destructiveHint is False

    def test_tool_schemas_valid(self, runtime: KaosRuntime) -> None:
        register_web_tools(runtime)
        for tool in runtime.tools.list_tool_objects():
            if tool.metadata.module_name == "kaos-web":
                schema = tool.metadata.get_input_json_schema()
                assert "properties" in schema
                assert "required" in schema
                # All tools require at least 'url'
                assert "url" in schema["properties"]


class TestGetPageTextThroughMCP:
    async def test_get_text_via_execute(self, runtime: KaosRuntime) -> None:
        """Execute GetPageText tool with mocked HTML — full MCP path."""
        register_web_tools(runtime)

        tool = runtime.tools.get_tool("kaos-web-get-text")
        assert tool is not None

        with patch(
            "kaos_web.tools._fetch_html", return_value=(ARTICLE_HTML, "https://example.com")
        ):
            result = await tool.execute({"url": "https://example.com"})

        assert not result.isError
        assert len(result.content) > 0
        text = result.content[0].text
        assert "Main Article Heading" in text

    async def test_get_text_error_message(self, runtime: KaosRuntime) -> None:
        register_web_tools(runtime)
        tool = runtime.tools.get_tool("kaos-web-get-text")

        with patch("kaos_web.tools._fetch_html", side_effect=Exception("Connection refused")):
            result = await tool.execute({"url": "https://down.example.com"})

        assert result.isError
        assert "Connection refused" in result.content[0].text
        assert "Verify" in result.content[0].text


class TestGetPageMarkdownThroughMCP:
    async def test_get_markdown_via_execute(self, runtime: KaosRuntime) -> None:
        register_web_tools(runtime)
        tool = runtime.tools.get_tool("kaos-web-get-markdown")

        with patch(
            "kaos_web.tools._fetch_html", return_value=(ARTICLE_HTML, "https://example.com")
        ):
            result = await tool.execute({"url": "https://example.com"})

        assert not result.isError
        md = result.content[0].text
        assert "# Main Article Heading" in md
        assert "**first paragraph**" in md


class TestGetPageMetadataThroughMCP:
    async def test_metadata_via_execute(self, runtime: KaosRuntime) -> None:
        register_web_tools(runtime)
        tool = runtime.tools.get_tool("kaos-web-get-metadata")

        with patch(
            "kaos_web.tools._fetch_html", return_value=(ARTICLE_HTML, "https://example.com")
        ):
            result = await tool.execute({"url": "https://example.com"})

        assert not result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["title"] == "Test Article OG Title"
        assert result.structuredContent["author"] == "Jane Doe"


class TestFetchPageThroughMCP:
    async def test_fetch_page_requires_context(self, runtime: KaosRuntime) -> None:
        register_web_tools(runtime)
        tool = runtime.tools.get_tool("kaos-web-fetch-page")

        with patch(
            "kaos_web.tools._fetch_html", return_value=(ARTICLE_HTML, "https://example.com")
        ):
            result = await tool.execute({"url": "https://example.com"}, context=None)

        assert result.isError
        assert "kaos-web-get-markdown" in result.content[0].text

    async def test_fetch_page_with_context(self, runtime: KaosRuntime) -> None:
        register_web_tools(runtime)
        tool = runtime.tools.get_tool("kaos-web-fetch-page")
        context = KaosContext.create(session_id="test", runtime=runtime)

        with patch(
            "kaos_web.tools._fetch_html", return_value=(ARTICLE_HTML, "https://example.com")
        ):
            result = await tool.execute({"url": "https://example.com"}, context=context)

        assert not result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["artifact_id"] is not None
        assert result.structuredContent["block_count"] > 0
        assert "body_uri" in result.structuredContent


class TestToolAdapterIntegration:
    def test_adapter_registers_tools(self, runtime: KaosRuntime) -> None:
        """Verify ToolAdapter can register web tools with FastMCP."""
        from kaos_mcp.adapters.tool import ToolAdapter
        from kaos_mcp.config import KaosMCPSettings
        from mcp.server.fastmcp import FastMCP

        register_web_tools(runtime)
        settings = KaosMCPSettings()
        adapter = ToolAdapter(runtime, settings)
        app = FastMCP("test")
        count = adapter.register_runtime_tools(app)

        assert count >= 5  # At least our 5 web tools

    def test_adapter_preserves_annotations(self, runtime: KaosRuntime) -> None:
        """Verify annotations pass through ToolAdapter to FastMCP."""
        from kaos_mcp.adapters.tool import ToolAdapter
        from kaos_mcp.config import KaosMCPSettings

        register_web_tools(runtime)
        settings = KaosMCPSettings()
        adapter = ToolAdapter(runtime, settings)

        for tool in runtime.tools.list_tool_objects():
            if tool.metadata.module_name == "kaos-web":
                fastmcp_annotations = adapter.to_fastmcp_annotations(tool.metadata)
                assert fastmcp_annotations is not None
                assert fastmcp_annotations.readOnlyHint is True
                assert fastmcp_annotations.openWorldHint is True
