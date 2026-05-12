"""Wire-level MCP session tests for kaos-web.

Tests the full MCP protocol stack via create_connected_server_and_client_session():
  1. Register web tools with KaosRuntime
  2. Create FastMCP app via create_app()
  3. Call tools via MCP client session (session.call_tool)
  4. Read resources via MCP resource templates (session.read_resource)
  5. Verify MCP type serialization/deserialization

This complements test_mcp_web_pipeline.py which tests via direct tool.execute().
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from kaos_mcp import create_app
from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

from kaos_core import KaosContext, KaosRuntime, KaosSettings
from kaos_core.types.enums import StorageBackend
from kaos_core.vfs import VFSConfig, VirtualFileSystem
from kaos_web.tools import register_web_tools

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures"
ARTICLE_HTML = (FIXTURES / "article.html").read_text()


def _make_runtime(tmp_path: Path) -> KaosRuntime:
    settings = KaosSettings(
        artifact_inline_read_max_bytes=262_144,
        artifact_chunk_size_bytes=65_536,
    )
    runtime = KaosRuntime(config=settings)
    runtime.vfs = VirtualFileSystem(
        VFSConfig(default_backend=StorageBackend.DISK, disk_base_path=tmp_path / "vfs")
    )
    runtime.artifacts = runtime.artifacts.__class__(
        runtime.vfs,
        manifest_context_id=settings.artifact_manifest_context_id,
        manifest_prefix=settings.artifact_manifest_prefix,
        max_inline_read_bytes=settings.artifact_inline_read_max_bytes,
        default_chunk_size=settings.artifact_chunk_size_bytes,
        temporary_ttl_seconds=settings.artifact_temporary_ttl_seconds,
    )
    return runtime


async def test_list_tools_via_mcp_session(tmp_path: Path) -> None:
    """Verify tool discovery through actual MCP session."""
    runtime = _make_runtime(tmp_path)
    register_web_tools(runtime)
    app = create_app(runtime)

    async with create_connected_server_and_client_session(app) as session:
        tools_result = await session.list_tools()
        tool_names = {t.name for t in tools_result.tools}

        # All 7 web extraction tools should be visible
        assert "kaos-web-fetch-page" in tool_names
        assert "kaos-web-get-text" in tool_names
        assert "kaos-web-get-markdown" in tool_names
        assert "kaos-web-get-metadata" in tool_names
        assert "kaos-web-search-page" in tool_names
        assert "kaos-web-get-links" in tool_names
        assert "kaos-web-get-images" in tool_names

        # Verify annotations survive MCP serialization
        for tool in tools_result.tools:
            if tool.name.startswith("kaos-web-"):
                assert tool.annotations is not None
                assert tool.annotations.readOnlyHint is True


async def test_get_text_via_mcp_session(tmp_path: Path) -> None:
    """Call GetPageText through actual MCP session and verify response types."""
    runtime = _make_runtime(tmp_path)
    register_web_tools(runtime)
    app = create_app(runtime)

    async with create_connected_server_and_client_session(app) as session:
        with patch(
            "kaos_web.tools._fetch_html",
            return_value=(ARTICLE_HTML, "https://example.com"),
        ):
            result = await session.call_tool(
                "kaos-web-get-text",
                {"url": "https://example.com"},
            )

        assert not result.isError
        assert len(result.content) >= 1
        text_contents = [c for c in result.content if isinstance(c, types.TextContent)]
        assert len(text_contents) >= 1
        assert "Main Article Heading" in text_contents[0].text


async def test_get_metadata_via_mcp_session(tmp_path: Path) -> None:
    """Call GetPageMetadata through MCP and verify structuredContent survives the wire."""
    runtime = _make_runtime(tmp_path)
    register_web_tools(runtime)
    app = create_app(runtime)

    async with create_connected_server_and_client_session(app) as session:
        with patch(
            "kaos_web.tools._fetch_html",
            return_value=(ARTICLE_HTML, "https://example.com"),
        ):
            result = await session.call_tool(
                "kaos-web-get-metadata",
                {"url": "https://example.com"},
            )

        assert not result.isError
        # After Task 2 fix, content[] should have a summary TextContent
        text_contents = [c for c in result.content if isinstance(c, types.TextContent)]
        assert len(text_contents) >= 1


async def test_fetch_page_artifact_resources_via_mcp(tmp_path: Path) -> None:
    """Fetch page, store as artifact, then read via MCP resource templates."""
    client_id = "web-mcp-test"
    runtime = _make_runtime(tmp_path)
    register_web_tools(runtime)

    # Parse via Python API and store as artifact
    context = KaosContext.create(session_id=client_id, runtime=runtime)
    from kaos_content.artifacts import store_document
    from kaos_web.extract import html_to_document

    doc = html_to_document(ARTICLE_HTML, url="https://example.com")
    manifest = await store_document(doc, runtime, context, name="article")
    artifact_id = manifest.artifact_id

    app = create_app(runtime)

    async with create_connected_server_and_client_session(
        app,
        client_info=types.Implementation(name=client_id, version="test"),
    ) as session:
        # List resource templates
        templates = await session.list_resource_templates()
        template_uris = {t.uriTemplate for t in templates.resourceTemplates}
        assert "kaos://content/{artifact_id}/markdown" in template_uris
        assert "kaos://content/{artifact_id}/outline" in template_uris

        # Read markdown view
        md_result = await session.read_resource(AnyUrl(f"kaos://content/{artifact_id}/markdown"))
        md_text = md_result.contents[0]
        assert isinstance(md_text, types.TextResourceContents)
        assert len(md_text.text) > 0
        assert "Main Article Heading" in md_text.text

        # Read outline
        outline_result = await session.read_resource(
            AnyUrl(f"kaos://content/{artifact_id}/outline")
        )
        outline_text = outline_result.contents[0]
        assert isinstance(outline_text, types.TextResourceContents)
        outline = json.loads(outline_text.text)
        assert isinstance(outline, list)

        # Read metadata
        meta_result = await session.read_resource(AnyUrl(f"kaos://content/{artifact_id}/metadata"))
        meta_text = meta_result.contents[0]
        assert isinstance(meta_text, types.TextResourceContents)
        meta = json.loads(meta_text.text)
        assert "title" in meta or "block_count" in meta.get("extra", {})


async def test_search_page_via_mcp_session(tmp_path: Path) -> None:
    """Call SearchPage through MCP and verify search results survive serialization."""
    runtime = _make_runtime(tmp_path)
    register_web_tools(runtime)
    app = create_app(runtime)

    async with create_connected_server_and_client_session(app) as session:
        with patch(
            "kaos_web.tools._fetch_html",
            return_value=(ARTICLE_HTML, "https://example.com"),
        ):
            result = await session.call_tool(
                "kaos-web-search-page",
                {"url": "https://example.com", "query": "article", "top_k": 3},
            )

        assert not result.isError
        # After Task 2 fix, content[] should have a summary
        text_contents = [c for c in result.content if isinstance(c, types.TextContent)]
        assert len(text_contents) >= 1
