"""Unit tests for the ``kaos-web-fetch-feed`` MCP tool.

The live counterpart is in
``tests/integration/test_web_live_extraction_matrix.py``. This file
asserts tool **metadata** (schema, name, capability, annotations) and
fixture-driven **error paths** — both runs offline so it can gate every
PR.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.tools import FetchFeedTool


def _text(content_item) -> str:
    """Narrow a ToolResult content item to its ``.text`` attribute.

    ``ToolResult.content`` is a union of TextContent / ImageContent /
    AudioContent / EmbeddedResource / ResourceLinkContent. The feed
    tool only emits TextContent, but ty can't prove that. This helper
    asserts the narrow at test time.
    """
    text = getattr(content_item, "text", None)
    assert isinstance(text, str), (
        f"Expected a TextContent with .text, got {type(content_item).__name__}"
    )
    return text


# ─── Metadata ───────────────────────────────────────────────────────────────


class TestFetchFeedToolMetadata:
    def test_name_is_canonical(self):
        tool = FetchFeedTool()
        assert tool.metadata.name == "kaos-web-fetch-feed"

    def test_module_namespace(self):
        tool = FetchFeedTool()
        assert tool.metadata.module_name == "kaos-web"

    def test_is_read_only(self):
        """RSS/Atom fetch is a bounded HTTP GET — readOnly + idempotent +
        openWorld. Auto-approve-ready for permission policies that gate
        destructive tools."""
        ann = FetchFeedTool().metadata.annotations
        assert ann is not None, "FetchFeedTool must declare ToolAnnotations"
        assert ann.readOnlyHint is True
        assert ann.destructiveHint is False
        assert ann.idempotentHint is True
        assert ann.openWorldHint is True

    def test_input_schema_required_url(self):
        params = {p.name: p for p in FetchFeedTool().metadata.input_schema}
        assert "url" in params
        assert params["url"].type == "string"
        assert params["url"].required is True

    def test_input_schema_optional_limit(self):
        params = {p.name: p for p in FetchFeedTool().metadata.input_schema}
        assert "limit" in params
        assert params["limit"].type == "integer"
        assert params["limit"].required is False
        assert params["limit"].default == 20
        constraints = params["limit"].constraints or {}
        assert constraints.get("minimum") == 1
        assert constraints.get("maximum") == 100

    def test_description_prefers_over_fetch_page(self):
        """Description must steer the agent: when a publisher exposes a
        feed, prefer ``kaos-web-fetch-feed`` over ``kaos-web-fetch-page``.
        This is the load-bearing instruction that closes the 2026-05-22
        SEC bug."""
        desc = FetchFeedTool().metadata.description.lower()
        assert "rss" in desc
        assert "atom" in desc
        assert "prefer" in desc
        assert "kaos-web-fetch-page" in desc


# ─── Error path: unknown / unparseable feed format ──────────────────────────


@pytest.fixture
def mock_http_client():
    """Patch HttpClient to return a configurable response without I/O."""
    with patch("kaos_web.clients.http.HttpClient") as cls:
        instance = MagicMock()
        instance.fetch = AsyncMock()
        cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        cls.return_value.__aexit__ = AsyncMock(return_value=None)
        yield instance


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_unknown_format_returns_typed_error(self, mock_http_client):
        """When the URL returns an HTML 404 page, the parser yields
        ``format='unknown'`` and the tool must return an error with the
        recovery hint (kaos-web-fetch-page, look for <link rel='alternate'>)."""
        response = MagicMock()
        response.html = "<html><body>404 Not Found</body></html>"
        response.content_type = "text/html"
        mock_http_client.fetch.return_value = response

        tool = FetchFeedTool()
        result = await tool.execute({"url": "https://example.com/not-a-feed"})

        assert result.isError is True
        body = _text(result.content[0])
        assert "RSS 2.0 or Atom 1.0" in body
        assert "kaos-web-fetch-page" in body, "Error message must give a fallback recovery path"

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_typed_error(self):
        """HTTP layer raises — tool must convert to a typed ToolResult
        error with the URL surfaced and a recovery hint."""

        with patch("kaos_web.clients.http.HttpClient") as cls:
            instance = MagicMock()
            instance.fetch = AsyncMock(side_effect=RuntimeError("DNS lookup failed"))
            cls.return_value.__aenter__ = AsyncMock(return_value=instance)
            cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = FetchFeedTool()
            result = await tool.execute({"url": "https://nonexistent.invalid/feed"})

            assert result.isError is True
            body = _text(result.content[0])
            assert "nonexistent.invalid" in body
            assert "DNS lookup failed" in body
            assert "kaos-web-fetch-page" in body

    @pytest.mark.asyncio
    async def test_empty_feed_returns_success_with_empty_items(self, mock_http_client):
        """A parseable feed with zero items isn't an error — it's a signal
        the publisher cleared the feed. Return success with empty items so
        downstream callers can branch on item count, not on isError."""
        empty_rss = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Cleared Feed</title>
  <link>https://example.com</link>
  <description>No items</description>
</channel></rss>"""
        response = MagicMock()
        response.html = empty_rss.decode("utf-8")
        response.content_type = "application/rss+xml"
        mock_http_client.fetch.return_value = response

        tool = FetchFeedTool()
        result = await tool.execute({"url": "https://example.com/feed"})

        assert result.isError is False
        assert result.structuredContent is not None
        assert result.structuredContent["format"] == "rss"
        assert result.structuredContent["items"] == []
        assert result.structuredContent["title"] == "Cleared Feed"


# ─── Limit parameter behavior ───────────────────────────────────────────────


class TestLimitParameter:
    @pytest.mark.asyncio
    async def test_limit_caps_returned_items(self, mock_http_client):
        # Build a feed with 30 items
        items_xml = "\n".join(
            f"<item><title>Item {i}</title><link>https://example.com/{i}</link></item>"
            for i in range(30)
        )
        feed_xml = f"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Many Items</title><link>https://example.com</link><description>x</description>
  {items_xml}
</channel></rss>"""
        response = MagicMock()
        response.html = feed_xml
        response.content_type = "application/rss+xml"
        mock_http_client.fetch.return_value = response

        tool = FetchFeedTool()
        result = await tool.execute({"url": "https://example.com/feed", "limit": 5})

        assert result.isError is False
        assert result.structuredContent is not None
        items = result.structuredContent["items"]
        assert len(items) == 5, "limit=5 must return exactly 5 items"
        # Items must be the first 5 (feeds are typically newest-first)
        assert items[0]["title"] == "Item 0"
        assert items[4]["title"] == "Item 4"

    @pytest.mark.asyncio
    async def test_limit_caps_at_100_for_safety(self, mock_http_client):
        """Even if the caller asks for 9999, the tool caps at 100 to bound
        output tokens. Otherwise the agent could blow its context window
        on an archive sweep."""
        items_xml = "\n".join(
            f"<item><title>X{i}</title><link>https://example.com/{i}</link></item>"
            for i in range(150)
        )
        feed_xml = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>X</title><link>https://example.com</link>
<description>x</description>{items_xml}</channel></rss>"""
        response = MagicMock()
        response.html = feed_xml
        response.content_type = "application/rss+xml"
        mock_http_client.fetch.return_value = response

        tool = FetchFeedTool()
        result = await tool.execute({"url": "https://example.com/feed", "limit": 9999})
        assert result.structuredContent is not None
        items = result.structuredContent["items"]
        assert len(items) == 100, "limit must hard-cap at 100"

    @pytest.mark.asyncio
    async def test_default_limit_is_20(self, mock_http_client):
        items_xml = "\n".join(
            f"<item><title>Y{i}</title><link>https://example.com/{i}</link></item>"
            for i in range(50)
        )
        feed_xml = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Y</title><link>https://example.com</link>
<description>x</description>{items_xml}</channel></rss>"""
        response = MagicMock()
        response.html = feed_xml
        response.content_type = "application/rss+xml"
        mock_http_client.fetch.return_value = response

        tool = FetchFeedTool()
        result = await tool.execute({"url": "https://example.com/feed"})  # no limit
        assert result.structuredContent is not None
        assert len(result.structuredContent["items"]) == 20


# ─── Success-path structured content shape ──────────────────────────────────


class TestSuccessStructuredContent:
    @pytest.mark.asyncio
    async def test_each_item_carries_full_field_set(self, mock_http_client):
        """structuredContent['items'][i] must include all 6 documented fields
        so downstream consumers can rely on the shape. Missing fields are
        explicit None / empty list, not absent keys."""
        feed_xml = """<?xml version="1.0"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"><channel>
  <title>Test</title><link>https://example.com</link><description>x</description>
  <item>
    <title>Article A</title>
    <link>https://example.com/a</link>
    <description>About A</description>
    <pubDate>Thu, 21 May 2026 08:51:10 -0400</pubDate>
    <dc:creator>Alice</dc:creator>
    <guid isPermaLink="false">abc-123</guid>
    <category>tech</category>
    <category>news</category>
  </item>
</channel></rss>"""
        response = MagicMock()
        response.html = feed_xml
        response.content_type = "application/rss+xml"
        mock_http_client.fetch.return_value = response

        tool = FetchFeedTool()
        result = await tool.execute({"url": "https://example.com/feed"})

        assert result.structuredContent is not None
        item = result.structuredContent["items"][0]
        # All 6 documented keys MUST be present
        for key in ("title", "link", "pub_date", "description", "author", "categories"):
            assert key in item, f"Item missing '{key}' key"
        assert item["title"] == "Article A"
        assert item["link"] == "https://example.com/a"
        assert item["pub_date"] == "2026-05-21T08:51:10-04:00"
        assert item["description"] == "About A"
        assert item["author"] == "Alice"
        assert item["categories"] == ["tech", "news"]
        # forward-compat: guid is optional (some publishers omit it)
        assert item.get("guid", True)

    @pytest.mark.asyncio
    async def test_summary_carries_top_item_preview(self, mock_http_client):
        """The summary string (shown in tool-call cards in the SPA) must
        include the top item's title so a user can see at a glance what
        the feed returned."""
        feed_xml = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>X</title><link>https://example.com</link>
<description>x</description>
<item><title>Breaking: Major Announcement Today</title>
<link>https://example.com/1</link></item>
</channel></rss>"""
        response = MagicMock()
        response.html = feed_xml
        response.content_type = "application/rss+xml"
        mock_http_client.fetch.return_value = response

        tool = FetchFeedTool()
        result = await tool.execute({"url": "https://example.com/feed"})

        # The summary is the human-readable line shown in tool-call cards.
        # It's surfaced via summary kwarg in create_success.
        # Pull it from the first TextContent of result.content
        body = _text(result.content[0])
        assert "Breaking: Major Announcement Today" in body
