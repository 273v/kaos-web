"""Unit tests for the 3 crawl/discovery MCP tool wrappers.

Coverage backfill (audit-03 WEB3-005): exercises the execute() bodies of
DiscoverUrlsTool, BatchFetchTool, and CrawlSiteTool — input parsing, success
shaping, error translation, artifact-storage path, and the format branches
of CrawlSiteTool. The underlying ``kaos_web.discovery``, ``kaos_web.batch``,
and ``kaos_web.crawl`` functions are mocked; tests assert on contract
semantics (output shape, error text contents, artifact-creation effects).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_core import ToolResult
from kaos_web.batch import BatchError, BatchResult
from kaos_web.crawl import CrawlError, CrawlPage, CrawlResult
from kaos_web.crawl_tools import (
    BatchFetchTool,
    CrawlSiteTool,
    DiscoverUrlsTool,
    _extract_response,
    _split_patterns,
    _store_crawl_page_artifact,
    _store_response_artifact,
    register_crawl_tools,
)
from kaos_web.discovery import DiscoveredUrl, DiscoveryResult
from kaos_web.models import WebResponse

# ── Helpers ──────────────────────────────────────────────────────────


def _is_error(r: ToolResult) -> bool:
    if hasattr(r, "isError"):
        return bool(r.isError)
    return False


def _error_text(r: ToolResult) -> str:
    if r.content:
        for block in r.content:
            text = getattr(block, "text", None)
            if text:
                return str(text)
    return ""


def _make_mock_context_with_runtime(
    *,
    artifact_id: str = "art-1",
    body_uri: str = "kaos://artifacts/art-1/body",
    size: int = 100,
    title: str | None = "Doc",
    block_count: int = 1,
) -> tuple[MagicMock, MagicMock]:
    """Build a mock KaosContext with a runtime + working artifact creation."""
    mock_context = MagicMock()
    mock_context.session_id = "test-session"

    mock_vfs = AsyncMock()
    mock_vfs.write_bytes = AsyncMock()
    mock_context.get_vfs_path = MagicMock(return_value=mock_vfs)

    mock_manifest = MagicMock()
    mock_manifest.artifact_id = artifact_id
    mock_manifest.body_uri = body_uri
    mock_manifest.size = size

    mock_runtime = MagicMock()
    mock_runtime.artifacts.create_from_path = AsyncMock(return_value=mock_manifest)
    mock_context.runtime = mock_runtime
    return mock_context, mock_manifest


# ── _split_patterns helper (preserved) ──────────────────────────────


class TestSplitPatterns:
    def test_basic(self) -> None:
        assert _split_patterns("/blog/,/docs/") == ["/blog/", "/docs/"]

    def test_whitespace(self) -> None:
        assert _split_patterns(" /a/ , /b/ ") == ["/a/", "/b/"]

    def test_none(self) -> None:
        assert _split_patterns(None) is None

    def test_empty(self) -> None:
        assert _split_patterns("") is None

    def test_single(self) -> None:
        assert _split_patterns("/blog/") == ["/blog/"]

    def test_only_commas(self) -> None:
        assert _split_patterns(",,,") is None


# ── Tool metadata smoke tests (preserved + tightened) ───────────────


class TestToolMetadata:
    @pytest.mark.parametrize(
        "tool_cls,expected_name",
        [
            (DiscoverUrlsTool, "kaos-web-discover-urls"),
            (BatchFetchTool, "kaos-web-batch-fetch"),
            (CrawlSiteTool, "kaos-web-crawl-site"),
        ],
    )
    def test_tool_names(self, tool_cls: type, expected_name: str) -> None:
        tool = tool_cls()
        assert tool.metadata.name == expected_name

    @pytest.mark.parametrize(
        "tool_cls",
        [DiscoverUrlsTool, BatchFetchTool, CrawlSiteTool],
    )
    def test_annotations_set(self, tool_cls: type) -> None:
        tool = tool_cls()
        ann = tool.metadata.annotations
        assert ann is not None
        assert ann.readOnlyHint is True
        assert ann.destructiveHint is False
        assert ann.openWorldHint is True
        assert ann.idempotentHint is True

    @pytest.mark.parametrize(
        "tool_cls",
        [DiscoverUrlsTool, BatchFetchTool, CrawlSiteTool],
    )
    def test_has_input_schema(self, tool_cls: type) -> None:
        tool = tool_cls()
        assert len(tool.metadata.input_schema) > 0

    @pytest.mark.parametrize(
        "tool_cls",
        [DiscoverUrlsTool, BatchFetchTool, CrawlSiteTool],
    )
    def test_module_name(self, tool_cls: type) -> None:
        tool = tool_cls()
        assert tool.metadata.module_name == "kaos-web"


# ── DiscoverUrlsTool.execute() ───────────────────────────────────────


@pytest.mark.asyncio
class TestDiscoverUrlsToolExecute:
    async def test_empty_url_error(self) -> None:
        result = await DiscoverUrlsTool().execute({"url": ""})
        assert _is_error(result)
        text = _error_text(result).lower()
        assert "url is required" in text
        assert "example.com" in text  # fix guidance includes a concrete example

    async def test_missing_url_error(self) -> None:
        result = await DiscoverUrlsTool().execute({})
        assert _is_error(result)

    async def test_success_default_args(self) -> None:
        disc = DiscoveryResult(
            urls=[
                DiscoveredUrl(
                    url="https://example.com/a",
                    source="sitemap",
                    lastmod=datetime(2026, 1, 1),
                ),
                DiscoveredUrl(
                    url="https://example.com/b",
                    source="page_link",
                    link_type="content",
                ),
            ],
            sitemap_count=1,
            page_link_count=1,
            errors=["one error"],
        )
        with patch(
            "kaos_web.discovery.discover_urls",
            AsyncMock(return_value=disc),
        ):
            result = await DiscoverUrlsTool().execute({"url": "https://example.com"})
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        assert out["total"] == 2
        assert out["sitemap_count"] == 1
        assert out["page_link_count"] == 1
        assert len(out["urls"]) == 2
        # First URL: lastmod is iso-formatted; second URL: lastmod is None.
        assert out["urls"][0]["lastmod"] == "2026-01-01T00:00:00"
        assert out["urls"][1]["lastmod"] is None
        assert out["urls"][1]["link_type"] == "content"

    async def test_passes_pattern_filters_through(self) -> None:
        captured: dict[str, Any] = {}

        async def _spy(url: str, fetch_fn: Any, **kwargs: Any) -> DiscoveryResult:
            captured.update({"url": url, **kwargs})
            return DiscoveryResult()

        with patch("kaos_web.discovery.discover_urls", _spy):
            await DiscoverUrlsTool().execute(
                {
                    "url": "https://example.com",
                    "sitemap": "only",
                    "include_patterns": "/blog/, /docs/",
                    "exclude_patterns": "/tag/",
                    "max_urls": 50,
                }
            )
        assert captured["url"] == "https://example.com"
        assert captured["sitemap"] == "only"
        assert captured["max_urls"] == 50
        assert captured["include_patterns"] == ["/blog/", "/docs/"]
        assert captured["exclude_patterns"] == ["/tag/"]

    async def test_unexpected_exception_returns_error_with_recovery(self) -> None:
        with patch(
            "kaos_web.discovery.discover_urls",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await DiscoverUrlsTool().execute({"url": "https://example.com"})
        assert _is_error(result)
        text = _error_text(result)
        assert "URL discovery failed" in text
        assert "boom" in text
        assert "sitemap='skip'" in text  # alternative is named explicitly

    async def test_truncates_errors_to_first_10(self) -> None:
        disc = DiscoveryResult(errors=[f"e{i}" for i in range(25)])
        with patch("kaos_web.discovery.discover_urls", AsyncMock(return_value=disc)):
            result = await DiscoverUrlsTool().execute({"url": "https://example.com"})
        assert not _is_error(result)
        assert result.structuredContent is not None
        assert len(result.structuredContent["errors"]) == 10


# ── BatchFetchTool.execute() ─────────────────────────────────────────


@pytest.mark.asyncio
class TestBatchFetchToolExecute:
    async def test_empty_urls_error(self) -> None:
        result = await BatchFetchTool().execute({"urls": ""})
        assert _is_error(result)
        text = _error_text(result).lower()
        assert "urls are required" in text
        assert "kaos-web-discover-urls" in text  # alternative tool referenced

    async def test_missing_urls_error(self) -> None:
        assert _is_error(await BatchFetchTool().execute({}))

    async def test_only_commas_returns_no_valid_urls_error(self) -> None:
        result = await BatchFetchTool().execute({"urls": ",,,"})
        assert _is_error(result)
        text = _error_text(result).lower()
        assert "no valid urls" in text
        assert "http://" in text

    async def test_success_no_runtime_inline_extraction(self) -> None:
        """Without context.runtime, results are extracted inline and returned."""
        resp = WebResponse(
            url="https://example.com/a",
            status_code=200,
            html="<html><body><h1>Hello</h1><p>Text body content.</p></body></html>",
        )
        batch = BatchResult(responses=[resp], elapsed_ms=12.34)
        with patch("kaos_web.batch.batch_fetch", AsyncMock(return_value=batch)):
            result = await BatchFetchTool().execute(
                {"urls": "https://example.com/a", "output_format": "markdown"}
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        assert out["total"] == 1
        assert out["succeeded"] == 1
        assert out["failed"] == 0
        assert out["artifact_backed"] is False
        assert out["elapsed_ms"] == 12.3
        assert len(out["pages"]) == 1
        page = out["pages"][0]
        assert page["url"] == "https://example.com/a"
        assert page["status_code"] == 200

    async def test_per_url_errors_surface_through(self) -> None:
        resp = WebResponse(url="https://ok.example", status_code=200, html="<p>ok</p>")
        batch = BatchResult(
            responses=[resp],
            errors=[BatchError(url="https://bad.example", error="DNS fail")],
        )
        with patch("kaos_web.batch.batch_fetch", AsyncMock(return_value=batch)):
            result = await BatchFetchTool().execute(
                {"urls": "https://ok.example,https://bad.example"}
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        assert out["failed"] == 1
        assert out["errors"][0]["url"] == "https://bad.example"
        assert "DNS fail" in out["errors"][0]["error"]

    async def test_artifact_storage_path_with_runtime(self) -> None:
        """When runtime is available and response is OK, _store_response_artifact runs."""
        resp = WebResponse(
            url="https://example.com/a",
            status_code=200,
            html="<html><body><h1>Title</h1><p>Body.</p></body></html>",
        )
        batch = BatchResult(responses=[resp])
        ctx, _ = _make_mock_context_with_runtime(artifact_id="art-9", size=200)

        # store_document is called inside _store_response_artifact.
        with (
            patch("kaos_web.batch.batch_fetch", AsyncMock(return_value=batch)),
            patch(
                "kaos_content.artifacts.store_document",
                AsyncMock(return_value=ctx.runtime.artifacts.create_from_path.return_value),
            ),
        ):
            result = await BatchFetchTool().execute(
                {"urls": "https://example.com/a"},
                context=ctx,
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        assert out["artifact_backed"] is True
        page = out["pages"][0]
        assert page["artifact_id"] == "art-9"
        assert page["body_uri"].endswith("/body")

    async def test_artifact_failure_falls_back_to_inline(self) -> None:
        """If artifact storage raises, batch falls back to inline extraction."""
        resp = WebResponse(
            url="https://example.com/a",
            status_code=200,
            html="<html><body><p>fallback content</p></body></html>",
        )
        batch = BatchResult(responses=[resp])
        ctx, _ = _make_mock_context_with_runtime()

        with (
            patch("kaos_web.batch.batch_fetch", AsyncMock(return_value=batch)),
            patch(
                "kaos_content.artifacts.store_document",
                AsyncMock(side_effect=RuntimeError("vfs full")),
            ),
        ):
            result = await BatchFetchTool().execute(
                {"urls": "https://example.com/a"},
                context=ctx,
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        assert out["artifact_backed"] is True  # has_context flag, not per-page result
        # But the per-page entry is the inline extraction output (no artifact_id).
        assert "artifact_id" not in out["pages"][0]

    async def test_unexpected_exception(self) -> None:
        with patch(
            "kaos_web.batch.batch_fetch",
            AsyncMock(side_effect=RuntimeError("upstream broke")),
        ):
            result = await BatchFetchTool().execute({"urls": "https://example.com"})
        assert _is_error(result)
        text = _error_text(result)
        assert "Batch fetch failed" in text
        assert "upstream broke" in text


# ── CrawlSiteTool.execute() ──────────────────────────────────────────


@pytest.mark.asyncio
class TestCrawlSiteToolExecute:
    async def test_empty_url_error(self) -> None:
        result = await CrawlSiteTool().execute({"url": ""})
        assert _is_error(result)
        text = _error_text(result).lower()
        assert "url is required" in text
        assert "kaos-web-discover-urls" in text  # alternative tool reference

    async def test_summary_format_default(self) -> None:
        page = CrawlPage(
            url="https://example.com/a",
            depth=0,
            title="Hello",
            content_text="five whole words right here",
            content_markdown="# Hello\n\nfive whole words right here",
            links=["https://example.com/b", "https://example.com/c"],
        )
        cresult = CrawlResult(
            pages=[page],
            total_discovered=10,
            total_crawled=1,
            total_extracted=1,
            sitemap_entries=5,
            elapsed_ms=42.7,
            errors=[CrawlError(url="https://example.com/x", error="bad", depth=0)],
        )
        with patch("kaos_web.crawl.crawl_site", AsyncMock(return_value=cresult)):
            result = await CrawlSiteTool().execute(
                {"url": "https://example.com", "output_format": "summary"}
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        assert out["total_discovered"] == 10
        assert out["total_crawled"] == 1
        assert out["total_extracted"] == 1
        assert out["sitemap_entries"] == 5
        assert out["elapsed_ms"] == 42.7
        assert out["artifact_backed"] is False
        assert out["errors"][0]["url"] == "https://example.com/x"
        page_out = out["pages"][0]
        assert page_out["title"] == "Hello"
        assert page_out["word_count"] == 5
        assert page_out["link_count"] == 2

    async def test_text_format_truncates(self) -> None:
        long_text = "x" * 6000
        page = CrawlPage(
            url="https://example.com/a",
            depth=1,
            title="t",
            content_text=long_text,
        )
        cresult = CrawlResult(pages=[page])
        with patch("kaos_web.crawl.crawl_site", AsyncMock(return_value=cresult)):
            result = await CrawlSiteTool().execute(
                {"url": "https://example.com", "output_format": "text"}
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        page_out = out["pages"][0]
        assert page_out["truncated"] is True
        assert len(page_out["content"]) == 5000

    async def test_markdown_format_short_not_truncated(self) -> None:
        page = CrawlPage(
            url="https://example.com/a",
            depth=0,
            title="t",
            content_markdown="# small",
        )
        cresult = CrawlResult(pages=[page])
        with patch("kaos_web.crawl.crawl_site", AsyncMock(return_value=cresult)):
            result = await CrawlSiteTool().execute(
                {"url": "https://example.com", "output_format": "markdown"}
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        page_out = out["pages"][0]
        assert page_out["truncated"] is False
        assert page_out["content"] == "# small"

    async def test_passes_filters_through(self) -> None:
        captured: dict[str, Any] = {}

        async def _spy(url: str, **kwargs: Any) -> CrawlResult:
            captured.update({"url": url, **kwargs})
            return CrawlResult()

        with patch("kaos_web.crawl.crawl_site", _spy):
            await CrawlSiteTool().execute(
                {
                    "url": "https://example.com",
                    "max_depth": 3,
                    "max_pages": 25,
                    "concurrency": 7,
                    "sitemap": "skip",
                    "include_patterns": "/blog/",
                    "exclude_patterns": "/tag/",
                }
            )
        assert captured["max_depth"] == 3
        assert captured["max_pages"] == 25
        assert captured["concurrency"] == 7
        assert captured["sitemap"] == "skip"
        assert captured["include_patterns"] == ["/blog/"]
        assert captured["exclude_patterns"] == ["/tag/"]

    async def test_artifact_storage_path_with_runtime(self) -> None:
        page = CrawlPage(
            url="https://example.com/a",
            depth=0,
            title="hello",
            content_markdown="# hello",
        )
        cresult = CrawlResult(pages=[page])
        ctx, _manifest = _make_mock_context_with_runtime(
            artifact_id="art-md", size=42, body_uri="kaos://artifacts/art-md/body"
        )

        with patch("kaos_web.crawl.crawl_site", AsyncMock(return_value=cresult)):
            result = await CrawlSiteTool().execute(
                {"url": "https://example.com", "output_format": "summary"},
                context=ctx,
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        assert out["artifact_backed"] is True
        # Crawl page artifacts use create_from_path, not store_document
        assert ctx.runtime.artifacts.create_from_path.await_count == 1
        page_out = out["pages"][0]
        assert page_out["artifact_id"] == "art-md"
        assert page_out["depth"] == 0

    async def test_artifact_failure_falls_back_to_inline(self) -> None:
        page = CrawlPage(
            url="https://example.com/a",
            depth=2,
            title="t",
            content_text="some text",
            content_markdown="# t",
        )
        cresult = CrawlResult(pages=[page])
        ctx, _ = _make_mock_context_with_runtime()
        ctx.runtime.artifacts.create_from_path = AsyncMock(side_effect=RuntimeError("disk full"))

        with patch("kaos_web.crawl.crawl_site", AsyncMock(return_value=cresult)):
            result = await CrawlSiteTool().execute(
                {"url": "https://example.com", "output_format": "summary"},
                context=ctx,
            )
        assert not _is_error(result)
        out = result.structuredContent
        assert out is not None
        # Inline fallback path takes the summary branch
        assert "word_count" in out["pages"][0]

    async def test_unexpected_exception(self) -> None:
        with patch(
            "kaos_web.crawl.crawl_site",
            AsyncMock(side_effect=RuntimeError("bad")),
        ):
            result = await CrawlSiteTool().execute({"url": "https://example.com"})
        assert _is_error(result)
        text = _error_text(result)
        assert "Crawl failed" in text
        assert "bad" in text
        assert "max_depth" in text  # recovery suggestion present


# ── _extract_response helper ─────────────────────────────────────────


@pytest.mark.asyncio
class TestExtractResponseHelper:
    async def test_failed_response_returns_status_error(self) -> None:
        resp = WebResponse(url="u", status_code=500, html="")
        out = await _extract_response(resp, "markdown")
        assert out["status_code"] == 500
        assert "HTTP 500" in out["error"]

    async def test_metadata_format(self) -> None:
        html = (
            "<html><head><title>X</title>"
            '<meta property="og:title" content="OG Title">'
            "</head><body>x</body></html>"
        )
        resp = WebResponse(url="https://e.com", status_code=200, html=html)
        out = await _extract_response(resp, "metadata")
        assert "metadata" in out
        assert isinstance(out["metadata"], dict)

    async def test_text_format(self) -> None:
        html = "<html><body><h1>Hi</h1><p>Body</p></body></html>"
        resp = WebResponse(url="https://e.com", status_code=200, html=html)
        out = await _extract_response(resp, "text")
        assert "content" in out
        assert "truncated" in out

    async def test_markdown_format_default(self) -> None:
        html = "<html><body><h1>Hi</h1><p>Body</p></body></html>"
        resp = WebResponse(url="https://e.com", status_code=200, html=html)
        out = await _extract_response(resp, "markdown")
        assert "content" in out
        assert "truncated" in out

    async def test_extract_failure_caught(self) -> None:
        resp = WebResponse(url="https://e.com", status_code=200, html="<html></html>")
        with patch(
            "kaos_web.extract.html_to_document",
            side_effect=RuntimeError("boom"),
        ):
            out = await _extract_response(resp, "markdown")
        assert "error" in out
        assert "boom" in out["error"]


# ── _store_response_artifact + _store_crawl_page_artifact helpers ────


@pytest.mark.asyncio
class TestStoreHelpers:
    async def test_store_response_artifact_empty_body_raises(self) -> None:
        resp = WebResponse(url="https://e.com", status_code=200, html="")
        ctx, _ = _make_mock_context_with_runtime()
        # html_to_document with empty html returns a doc with no body — raises ValueError.
        with pytest.raises(ValueError, match="No content extracted"):
            await _store_response_artifact(resp, ctx)

    async def test_store_crawl_page_artifact_writes_vfs(self) -> None:
        page = CrawlPage(
            url="https://e.com",
            depth=1,
            title="hello",
            content_markdown="# hello",
        )
        ctx, _manifest = _make_mock_context_with_runtime(artifact_id="ar-1", size=7)
        out = await _store_crawl_page_artifact(page, ctx)
        # Verify VFS write happened
        ctx.get_vfs_path.return_value.write_bytes.assert_awaited_once()
        # Verify artifact metadata makes it into the output
        assert out["artifact_id"] == "ar-1"
        assert out["depth"] == 1
        assert out["title"] == "hello"
        # Verify create_from_path was called with markdown mime + role
        ctx.runtime.artifacts.create_from_path.assert_awaited_once()
        kwargs = ctx.runtime.artifacts.create_from_path.await_args.kwargs
        assert kwargs["mime_type"] == "text/markdown"


# ── register_crawl_tools ─────────────────────────────────────────────


class TestRegisterCrawlTools:
    def test_register_count(self) -> None:
        runtime = MagicMock()
        runtime.tools.register_tool = MagicMock()
        count = register_crawl_tools(runtime)
        assert count == 3
        assert runtime.tools.register_tool.call_count == 3
