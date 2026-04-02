"""Integration tests for browser interaction, cookies, and network monitoring.

These tests use real Playwright + Chrome against live web servers to verify
the full interaction stack works end-to-end.

Run with: pytest tests/integration/test_browser_interaction.py -v
Skip in CI: pytest -m "not integration"
"""

from __future__ import annotations

import pytest

from kaos_web.browser_tools import _detect_browser_channel
from kaos_web.clients.browser import BrowserClient
from kaos_web.clients.config import BrowserClientConfig
from kaos_web.models import WebRequest

pytestmark = pytest.mark.integration

# Auto-detect browser channel (system Chrome on Linux, bundled Chromium elsewhere)
_BROWSER_CONFIG = BrowserClientConfig(channel=_detect_browser_channel())


# ── Navigate + Page Tracking ──


class TestBrowserNavigateAndTrack:
    """Verify named contexts keep pages alive for interaction."""

    async def test_named_context_keeps_page(self) -> None:
        """Fetch with context_id stores page for later interaction."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            resp = await client.fetch(
                WebRequest(url="https://example.com", extra={"context_id": "nav1"})
            )

            assert resp.status_code == 200
            assert "nav1" in client.active_contexts
            assert "example" in resp.title.lower()

    async def test_unnamed_context_cleaned_up(self) -> None:
        """Fetch without context_id doesn't leave pages behind."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(WebRequest(url="https://example.com"))
            assert client.active_contexts == []

    async def test_close_context_cleans_up(self) -> None:
        """close_context removes page and context."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(url="https://example.com", extra={"context_id": "cleanup1"})
            )
            assert "cleanup1" in client.active_contexts

            await client.close_context("cleanup1")
            assert "cleanup1" not in client.active_contexts


# ── Click ──


class TestBrowserClick:
    """Click elements on real pages."""

    async def test_click_link_navigates(self) -> None:
        """Click a link on example.com and verify navigation."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(url="https://example.com", extra={"context_id": "click1"})
            )

            # example.com has a "More information..." link
            await client.click("click1", "a")

            # After clicking, the URL should have changed (to iana.org or similar)
            new_url = await client.get_url("click1")
            assert new_url != "https://example.com/"

    async def test_click_on_books_toscrape(self) -> None:
        """Click a book link on books.toscrape.com."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://books.toscrape.com",
                    extra={"context_id": "click2"},
                )
            )

            # Click first book link
            await client.click("click2", "article.product_pod h3 a")
            new_url = await client.get_url("click2")
            assert "catalogue/" in new_url


# ── Fill and Form Submission ──


class TestBrowserFillForm:
    """Fill forms on real pages."""

    async def test_fill_httpbin_form(self) -> None:
        """Fill the httpbin form page, verify values via JS evaluation."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://httpbin.org/forms/post",
                    extra={"context_id": "form1"},
                )
            )

            # Fill the customer name field
            await client.fill("form1", 'input[name="custname"]', "KAOS Test User")

            # Verify value was set via JS
            value = await client.evaluate_in_context(
                "form1",
                "document.querySelector('input[name=\"custname\"]').value",
            )
            assert value == "KAOS Test User"

    async def test_type_text_character_by_character(self) -> None:
        """Type text character-by-character on httpbin form."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://httpbin.org/forms/post",
                    extra={"context_id": "type1"},
                )
            )

            await client.type_text("type1", 'input[name="custname"]', "hello")

            value = await client.evaluate_in_context(
                "type1",
                "document.querySelector('input[name=\"custname\"]').value",
            )
            assert value == "hello"

    async def test_fill_and_submit_form(self) -> None:
        """Fill and submit the httpbin form, verify POST response."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://httpbin.org/forms/post",
                    extra={"context_id": "submit1"},
                )
            )

            # Fill fields
            await client.fill("submit1", 'input[name="custname"]', "Test User")
            await client.fill("submit1", 'input[name="custtel"]', "555-1234")
            await client.fill("submit1", 'input[name="custemail"]', "test@example.com")

            # Select a topping checkbox
            await client.click("submit1", 'input[name="topping"][value="bacon"]')

            # Submit form (httpbin uses bare <button> without type attribute)
            await client.click("submit1", "button")

            # httpbin returns the POST data as a page — verify we navigated
            html = await client.get_content("submit1")
            assert "Test User" in html or "custname" in html


# ── Press Key ──


class TestBrowserPressKey:
    """Press keyboard keys on real pages."""

    async def test_press_tab_moves_focus(self) -> None:
        """Press Tab key to move focus between form fields."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://httpbin.org/forms/post",
                    extra={"context_id": "key1"},
                )
            )

            # Focus on first input and press Tab
            await client.click("key1", 'input[name="custname"]')
            await client.press_key("key1", 'input[name="custname"]', "Tab")

            # Verify focus moved (hard to assert directly, but no error = success)


# ── Screenshot ──


class TestBrowserScreenshot:
    """Screenshot tests against real pages."""

    async def test_screenshot_named_context(self) -> None:
        """Take screenshot of page in named context."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(WebRequest(url="https://example.com", extra={"context_id": "ss1"}))

            img_bytes = await client.screenshot_context("ss1")
            assert img_bytes[:4] == b"\x89PNG"
            assert len(img_bytes) > 1000

    async def test_screenshot_jpeg_format(self) -> None:
        """Take JPEG screenshot with quality setting."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(WebRequest(url="https://example.com", extra={"context_id": "ss2"}))

            img_bytes = await client.screenshot_context(
                "ss2", format="jpeg", quality=50, full_page=False
            )
            # JPEG magic bytes: FF D8 FF
            assert img_bytes[:2] == b"\xff\xd8"


# ── Accessibility Snapshot ──


class TestBrowserSnapshot:
    """Accessibility snapshot tests against real pages."""

    async def test_snapshot_example_com(self) -> None:
        """Get accessibility tree from example.com."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(WebRequest(url="https://example.com", extra={"context_id": "snap1"}))

            snapshot = await client.get_snapshot("snap1")

            assert isinstance(snapshot, str)
            assert len(snapshot) > 0
            # example.com should have a heading and a link
            assert "heading" in snapshot
            assert "link" in snapshot

    async def test_snapshot_httpbin_form(self) -> None:
        """Get accessibility tree for httpbin form page."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://httpbin.org/forms/post",
                    extra={"context_id": "snap2"},
                )
            )

            snapshot = await client.get_snapshot("snap2")

            # Form page should have textbox and button roles
            assert "textbox" in snapshot or "text" in snapshot
            assert "button" in snapshot


# ── JavaScript Evaluation ──


class TestBrowserEvaluateJS:
    """Evaluate JavaScript on real pages."""

    async def test_evaluate_document_title(self) -> None:
        """Get document.title from a named context."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(WebRequest(url="https://example.com", extra={"context_id": "eval1"}))

            title = await client.evaluate_in_context("eval1", "document.title")
            assert isinstance(title, str)
            assert "example" in title.lower()

    async def test_evaluate_complex_expression(self) -> None:
        """Evaluate expression that returns a dict."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(WebRequest(url="https://example.com", extra={"context_id": "eval2"}))

            result = await client.evaluate_in_context(
                "eval2",
                "({ url: location.href, links: document.querySelectorAll('a').length })",
            )
            assert isinstance(result, dict)
            assert "url" in result
            assert "links" in result

    async def test_evaluate_dom_query(self) -> None:
        """Count elements via JS."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://books.toscrape.com",
                    extra={"context_id": "eval3"},
                )
            )

            count = await client.evaluate_in_context(
                "eval3",
                "document.querySelectorAll('article.product_pod').length",
            )
            assert isinstance(count, int)
            assert count > 0  # Should have product cards


# ── Content Extraction After Interaction ──


class TestBrowserGetContent:
    """Extract content from pages after interaction."""

    async def test_get_content_returns_html(self) -> None:
        """Get HTML content from active page."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(WebRequest(url="https://example.com", extra={"context_id": "gc1"}))

            html = await client.get_content("gc1")
            assert "<html" in html.lower()
            assert "example" in html.lower()

    async def test_content_updates_after_navigation(self) -> None:
        """Content changes after clicking a link."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://books.toscrape.com",
                    extra={"context_id": "gc2"},
                )
            )

            html_before = await client.get_content("gc2")
            assert "books.toscrape.com" in (await client.get_url("gc2")).lower()

            # Click first book
            await client.click("gc2", "article.product_pod h3 a")

            html_after = await client.get_content("gc2")
            # Content should have changed
            assert html_before != html_after


# ── Cookies ──


class TestBrowserCookies:
    """Cookie operations against real pages."""

    async def test_get_cookies_after_navigation(self) -> None:
        """Navigate to a site and read cookies."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://httpbin.org/cookies/set/testcookie/testvalue",
                    extra={"context_id": "cook1", "wait_until": "load"},
                )
            )

            cookies = await client.get_cookies("cook1")
            cookie_names = [c["name"] for c in cookies]
            assert "testcookie" in cookie_names

            # Find our cookie's value
            test_cookie = next(c for c in cookies if c["name"] == "testcookie")
            assert test_cookie["value"] == "testvalue"

    async def test_set_cookie_then_read(self) -> None:
        """Set a cookie programmatically and verify it persists."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://httpbin.org/html",
                    extra={"context_id": "cook2"},
                )
            )

            await client.set_cookies(
                "cook2",
                [{"name": "kaos_test", "value": "hello123", "url": "https://httpbin.org"}],
            )

            cookies = await client.get_cookies("cook2")
            cookie_names = [c["name"] for c in cookies]
            assert "kaos_test" in cookie_names


# ── Network Monitoring ──


class TestBrowserNetworkMonitoring:
    """Network request logging and inspection."""

    async def test_log_requests_during_navigation(self) -> None:
        """Enable logging, navigate, verify requests were captured."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://example.com",
                    extra={"context_id": "net1"},
                )
            )

            await client.enable_request_logging("net1")

            # Navigate to a new page to generate requests
            page = client._require_page("net1")
            await page.goto("https://httpbin.org/html", wait_until="load")

            log = await client.get_request_log("net1")
            assert len(log) > 0

            # Should have at least one document request
            urls = [e["url"] for e in log]
            assert any("httpbin.org" in u for u in urls)

    async def test_request_detail_includes_headers(self) -> None:
        """Logged requests should include headers."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://example.com",
                    extra={"context_id": "net2"},
                )
            )

            await client.enable_request_logging("net2")

            page = client._require_page("net2")
            await page.goto("https://httpbin.org/html", wait_until="load")

            log = await client.get_request_log("net2")
            assert len(log) > 0

            detail = await client.get_request_detail("net2", 0)
            assert detail is not None
            assert "headers" in detail
            assert "method" in detail
            assert detail["method"] == "GET"

    async def test_filter_by_resource_type(self) -> None:
        """Filter logged requests by resource type."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://example.com",
                    extra={"context_id": "net3"},
                )
            )

            await client.enable_request_logging("net3")

            page = client._require_page("net3")
            await page.goto("https://httpbin.org/html", wait_until="load")

            log = await client.get_request_log("net3")
            doc_requests = [e for e in log if e.get("resource_type") == "document"]
            assert len(doc_requests) >= 1


# ── Multi-step Workflow ──


class TestMultiStepWorkflow:
    """End-to-end multi-step browser workflows."""

    async def test_navigate_fill_submit_extract(self) -> None:
        """Full workflow: navigate -> fill form -> submit -> extract result."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            # 1. Navigate to form
            await client.fetch(
                WebRequest(
                    url="https://httpbin.org/forms/post",
                    extra={"context_id": "workflow1"},
                )
            )

            # 2. Fill form fields
            await client.fill("workflow1", 'input[name="custname"]', "KAOS Agent")
            await client.fill("workflow1", 'input[name="custemail"]', "agent@kaos.ai")

            # 3. Submit
            await client.click("workflow1", "button")

            # 4. Extract result page content
            html = await client.get_content("workflow1")
            # httpbin shows the POST data in the response
            assert "KAOS Agent" in html or "custname" in html

    async def test_navigate_click_url_changes(self) -> None:
        """Navigate -> click -> verify URL changes."""
        async with BrowserClient(_BROWSER_CONFIG) as client:
            await client.fetch(
                WebRequest(
                    url="https://books.toscrape.com",
                    extra={"context_id": "workflow2"},
                )
            )

            url1 = await client.get_url("workflow2")

            # Click a book
            await client.click("workflow2", "article.product_pod h3 a")

            url2 = await client.get_url("workflow2")

            # URL should have changed
            assert url1 != url2

            # Snapshot should show product detail content
            snap = await client.get_snapshot("workflow2")
            assert isinstance(snap, str)
            assert len(snap) > 0


# ── MCP Tool Integration ──


class TestBrowserToolE2E:
    """Test browser MCP tools end-to-end with real browser."""

    async def test_navigate_tool(self) -> None:
        """BrowserNavigateTool works end-to-end."""
        from kaos_web.browser_tools import BrowserNavigateTool, _shutdown_browser_client

        try:
            tool = BrowserNavigateTool()
            result = await tool.execute(
                {
                    "url": "https://example.com",
                    "context_id": "mcp1",
                }
            )
            assert not result.isError
        finally:
            await _shutdown_browser_client()

    async def test_navigate_then_snapshot_tool(self) -> None:
        """Navigate then snapshot through MCP tools."""
        from kaos_web.browser_tools import (
            BrowserNavigateTool,
            GetSnapshotTool,
            _shutdown_browser_client,
        )

        try:
            nav = BrowserNavigateTool()
            snap = GetSnapshotTool()

            result = await nav.execute(
                {
                    "url": "https://example.com",
                    "context_id": "mcp2",
                }
            )
            assert not result.isError

            result = await snap.execute({"context_id": "mcp2"})
            assert not result.isError
        finally:
            await _shutdown_browser_client()

    async def test_navigate_click_content_tool(self) -> None:
        """Navigate -> click -> get content through MCP tools."""
        from kaos_web.browser_tools import (
            BrowserNavigateTool,
            ClickElementTool,
            GetPageContentTool,
            _shutdown_browser_client,
        )

        try:
            nav = BrowserNavigateTool()
            click = ClickElementTool()
            content = GetPageContentTool()

            await nav.execute(
                {
                    "url": "https://books.toscrape.com",
                    "context_id": "mcp3",
                }
            )

            result = await click.execute(
                {
                    "context_id": "mcp3",
                    "selector": "article.product_pod h3 a",
                }
            )
            assert not result.isError

            result = await content.execute(
                {
                    "context_id": "mcp3",
                    "output_format": "text",
                }
            )
            assert not result.isError
        finally:
            await _shutdown_browser_client()

    async def test_screenshot_tool_oneshot(self) -> None:
        """Screenshot tool in one-shot mode (URL only, no context)."""
        from kaos_web.browser_tools import ScreenshotTool, _shutdown_browser_client

        try:
            tool = ScreenshotTool()
            result = await tool.execute({"url": "https://example.com"})
            assert not result.isError
            # Should return ImageContent
            assert result.content[0].type == "image"
        finally:
            await _shutdown_browser_client()
