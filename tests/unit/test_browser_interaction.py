"""Tests for BrowserClient interaction methods and browser MCP tools.

Tests verify:
- Page tracking lifecycle (store on fetch, clean up on close_context)
- Interaction methods (click, fill, type, select, press_key, evaluate, snapshot)
- Error handling when no page exists for context_id
- All 10 browser MCP tool metadata and error paths
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.clients.browser import BrowserClient
from kaos_web.errors import WebBrowserError

# ── Fixtures ──


def _mock_page(url: str = "https://example.com") -> MagicMock:
    """Create a mock Playwright page."""
    page = AsyncMock()
    page.url = url
    page.title = AsyncMock(return_value="Example")
    page.content = AsyncMock(return_value="<html><body>Hello</body></html>")
    page.close = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.type = AsyncMock()
    page.press = AsyncMock()
    page.select_option = AsyncMock(return_value=["option1"])
    page.evaluate = AsyncMock(return_value=42)
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    # Mock locator chain for aria_snapshot()
    body_locator = AsyncMock()
    body_locator.aria_snapshot = AsyncMock(
        return_value='- heading "Hello" [level=1]\n- button "Click Me"\n- textbox "Search"'
    )
    page.locator = MagicMock(return_value=body_locator)

    # Mock the goto response
    response = AsyncMock()
    response.status = 200
    response.headers = {"content-type": "text/html"}
    page.goto = AsyncMock(return_value=response)
    page.wait_for_selector = AsyncMock()

    return page


def _mock_context(page: MagicMock | None = None) -> MagicMock:
    """Create a mock Playwright browser context."""
    ctx = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page or _mock_page())
    ctx.close = AsyncMock()
    ctx.route = AsyncMock()
    return ctx


def _mock_browser() -> MagicMock:
    """Create a mock Playwright browser."""
    browser = AsyncMock()
    browser.new_context = AsyncMock(side_effect=lambda **kw: _mock_context(_mock_page()))
    browser.close = AsyncMock()
    return browser


# ── BrowserClient page tracking tests ──


class TestPageTracking:
    """Verify pages are stored/cleaned up correctly with named contexts."""

    def test_initial_state_empty(self):
        client = BrowserClient()
        assert client._pages == {}
        assert client.active_contexts == []

    @pytest.mark.asyncio
    async def test_fetch_with_context_id_stores_page(self):
        """Named context fetch should keep page alive."""
        client = BrowserClient()
        mock_browser = _mock_browser()
        client._browser = mock_browser

        page = _mock_page()
        ctx = _mock_context(page)
        mock_browser.new_context = AsyncMock(return_value=ctx)

        from kaos_web.models import WebRequest

        await client.fetch(WebRequest(url="https://example.com", extra={"context_id": "s1"}))

        assert "s1" in client._pages
        assert "s1" in client._contexts
        assert client.active_contexts == ["s1"]
        # Page should NOT be closed
        page.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_without_context_id_closes_page(self):
        """Unnamed fetch should close page and context."""
        client = BrowserClient()
        mock_browser = _mock_browser()
        client._browser = mock_browser

        page = _mock_page()
        ctx = _mock_context(page)
        mock_browser.new_context = AsyncMock(return_value=ctx)

        from kaos_web.models import WebRequest

        await client.fetch(WebRequest(url="https://example.com"))

        assert client._pages == {}
        page.close.assert_called_once()
        ctx.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_context_cleans_page(self):
        """close_context should close both page and context."""
        client = BrowserClient()
        page = _mock_page()
        ctx = _mock_context()
        client._pages["s1"] = page
        client._contexts["s1"] = ctx

        await client.close_context("s1")

        assert "s1" not in client._pages
        assert "s1" not in client._contexts
        page.close.assert_called_once()
        ctx.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_all_cleans_everything(self):
        """close() should clean up all pages and contexts."""
        client = BrowserClient()
        p1, p2 = _mock_page(), _mock_page()
        c1, c2 = _mock_context(), _mock_context()
        client._pages = {"s1": p1, "s2": p2}
        client._contexts = {"s1": c1, "s2": c2}

        await client.close()

        assert client._pages == {}
        assert client._contexts == {}
        p1.close.assert_called_once()
        p2.close.assert_called_once()
        c1.close.assert_called_once()
        c2.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_refetch_replaces_page(self):
        """Fetching with an existing context_id should replace the old page."""
        client = BrowserClient()
        mock_browser = _mock_browser()
        client._browser = mock_browser

        old_page = _mock_page()
        client._pages["s1"] = old_page

        new_page = _mock_page("https://other.com")
        ctx = _mock_context(new_page)
        client._contexts["s1"] = ctx

        from kaos_web.models import WebRequest

        await client.fetch(WebRequest(url="https://other.com", extra={"context_id": "s1"}))

        old_page.close.assert_called_once()
        assert client._pages["s1"] is new_page


# ── Interaction method tests ──


class TestClickMethod:
    @pytest.mark.asyncio
    async def test_click_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        await client.click("s1", "button#submit")
        page.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_click_no_page_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No active page"):
            await client.click("nonexistent", "button")

    @pytest.mark.asyncio
    async def test_click_playwright_error_maps(self):
        client = BrowserClient()
        page = _mock_page()
        page.click = AsyncMock(side_effect=Exception("Timeout 30000ms exceeded"))
        client._pages["s1"] = page

        from kaos_web.errors import WebTimeoutError

        with pytest.raises(WebTimeoutError) as info:
            await client.click("s1", "button")
        assert info.value.timeout_type == "click"


class TestFillMethod:
    @pytest.mark.asyncio
    async def test_fill_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        await client.fill("s1", "input#name", "hello")
        page.fill.assert_called_once()

    @pytest.mark.asyncio
    async def test_fill_no_page_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No active page"):
            await client.fill("nonexistent", "input", "value")


class TestTypeTextMethod:
    @pytest.mark.asyncio
    async def test_type_text_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        await client.type_text("s1", "input#search", "hello", delay=50)
        page.type.assert_called_once()

    @pytest.mark.asyncio
    async def test_type_text_no_page_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No active page"):
            await client.type_text("nonexistent", "input", "text")


class TestPressKeyMethod:
    @pytest.mark.asyncio
    async def test_press_key_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        await client.press_key("s1", "input#search", "Enter")
        page.press.assert_called_once()


class TestSelectOptionMethod:
    @pytest.mark.asyncio
    async def test_select_option_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        result = await client.select_option("s1", "select#color", "red")
        page.select_option.assert_called_once()
        assert result == ["option1"]


class TestGetSnapshotMethod:
    @pytest.mark.asyncio
    async def test_snapshot_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        snapshot = await client.get_snapshot("s1")
        assert isinstance(snapshot, str)
        assert "heading" in snapshot
        assert "Click Me" in snapshot

    @pytest.mark.asyncio
    async def test_snapshot_no_page_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No active page"):
            await client.get_snapshot("nonexistent")


class TestEvaluateInContextMethod:
    @pytest.mark.asyncio
    async def test_evaluate_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        result = await client.evaluate_in_context("s1", "1 + 1")
        assert result == 42  # Mock returns 42

    @pytest.mark.asyncio
    async def test_evaluate_no_page_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No active page"):
            await client.evaluate_in_context("nonexistent", "1+1")


class TestScreenshotContextMethod:
    @pytest.mark.asyncio
    async def test_screenshot_context_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        result = await client.screenshot_context("s1")
        assert result == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_screenshot_context_no_page_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No active page"):
            await client.screenshot_context("nonexistent")


class TestGetContentMethod:
    @pytest.mark.asyncio
    async def test_get_content_success(self):
        client = BrowserClient()
        page = _mock_page()
        client._pages["s1"] = page

        html = await client.get_content("s1")
        assert "<html>" in html


class TestGetUrlMethod:
    @pytest.mark.asyncio
    async def test_get_url_success(self):
        client = BrowserClient()
        page = _mock_page("https://example.com/page")
        client._pages["s1"] = page

        url = await client.get_url("s1")
        assert url == "https://example.com/page"


class TestRequirePage:
    def test_error_message_includes_context_id(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="'missing'"):
            client._require_page("missing")

    def test_error_lists_active_contexts(self):
        client = BrowserClient()
        client._pages["s1"] = _mock_page()
        client._pages["s2"] = _mock_page()
        with pytest.raises(WebBrowserError, match="s1"):
            client._require_page("missing")


# ── Browser MCP tool tests ──


class TestBrowserToolMetadata:
    """Verify all browser tools have correct metadata."""

    @pytest.fixture
    def all_tools(self):
        from kaos_web.browser_tools import (
            BrowserNavigateTool,
            ClickElementTool,
            CloseContextTool,
            EnableRequestLoggingTool,
            EvaluateJSTool,
            FillInputTool,
            GetCookiesTool,
            GetPageContentTool,
            GetRequestDetailTool,
            GetSnapshotTool,
            ListContextsTool,
            ListRequestsTool,
            PressKeyTool,
            SaveAuthStateTool,
            ScreenshotTool,
            SelectOptionTool,
            SetCookieTool,
            TypeTextTool,
        )

        return [
            BrowserNavigateTool(),
            ClickElementTool(),
            FillInputTool(),
            TypeTextTool(),
            PressKeyTool(),
            SelectOptionTool(),
            ScreenshotTool(),
            EvaluateJSTool(),
            GetSnapshotTool(),
            GetPageContentTool(),
            GetCookiesTool(),
            SetCookieTool(),
            SaveAuthStateTool(),
            EnableRequestLoggingTool(),
            ListRequestsTool(),
            GetRequestDetailTool(),
            ListContextsTool(),
            CloseContextTool(),
        ]

    def test_all_tools_have_annotations(self, all_tools):
        for tool in all_tools:
            ann = tool.metadata.annotations
            assert ann is not None, f"{tool.metadata.name} missing annotations"

    def test_tool_names_follow_convention(self, all_tools):
        for tool in all_tools:
            name = tool.metadata.name
            assert name.startswith("kaos-web-browser-"), f"Bad name: {name}"
            parts = name.split("-")
            assert len(parts) >= 4, f"Name too short: {name}"

    def test_write_tools_not_readonly(self, all_tools):
        write_tools = {
            "kaos-web-browser-navigate",
            "kaos-web-browser-click",
            "kaos-web-browser-fill",
            "kaos-web-browser-type",
            "kaos-web-browser-press",
            "kaos-web-browser-select",
            "kaos-web-browser-evaluate",
            "kaos-web-browser-set-cookie",
            "kaos-web-browser-save-auth",
            "kaos-web-browser-log-requests",
            "kaos-web-browser-close-context",
        }
        for tool in all_tools:
            if tool.metadata.name in write_tools:
                assert tool.metadata.annotations.readOnlyHint is False, (
                    f"{tool.metadata.name} should not be readOnly"
                )

    def test_read_tools_are_readonly(self, all_tools):
        read_tools = {
            "kaos-web-browser-screenshot",
            "kaos-web-browser-snapshot",
            "kaos-web-browser-content",
            "kaos-web-browser-cookies",
            "kaos-web-browser-requests",
            "kaos-web-browser-get-request",
            "kaos-web-browser-list-contexts",
        }
        for tool in all_tools:
            if tool.metadata.name in read_tools:
                assert tool.metadata.annotations.readOnlyHint is True, (
                    f"{tool.metadata.name} should be readOnly"
                )

    def test_all_tools_are_open_world_except_save_auth(self, all_tools):
        # SaveAuthState writes to local disk, not network
        local_only = {"kaos-web-browser-save-auth"}
        for tool in all_tools:
            if tool.metadata.name in local_only:
                assert tool.metadata.annotations.openWorldHint is False, (
                    f"{tool.metadata.name} should be local-only (openWorld=False)"
                )
            else:
                assert tool.metadata.annotations.openWorldHint is True, (
                    f"{tool.metadata.name} should be openWorld"
                )

    def test_no_tools_are_destructive(self, all_tools):
        for tool in all_tools:
            assert tool.metadata.annotations.destructiveHint is False

    def test_tool_count(self, all_tools):
        assert len(all_tools) == 19


class TestBrowserToolErrorPaths:
    """Verify tools return helpful errors when preconditions aren't met."""

    @pytest.mark.asyncio
    async def test_click_no_context_returns_error(self):
        from kaos_web.browser_tools import ClickElementTool

        tool = ClickElementTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.click = AsyncMock(
                side_effect=WebBrowserError("No active page for context 'bad'", url="")
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "bad", "selector": "button"})
            assert result.isError
            assert "bad" in result.require_text()
            assert "snapshot" in result.require_text().lower()

    @pytest.mark.asyncio
    async def test_fill_no_context_returns_error(self):
        from kaos_web.browser_tools import FillInputTool

        tool = FillInputTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.fill = AsyncMock(
                side_effect=WebBrowserError("No active page for context 'bad'", url="")
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "bad", "selector": "input", "value": "x"})
            assert result.isError

    @pytest.mark.asyncio
    async def test_screenshot_missing_both_params_returns_error(self):
        from kaos_web.browser_tools import ScreenshotTool

        tool = ScreenshotTool()
        result = await tool.execute({})
        assert result.isError
        assert "context_id" in result.require_text()

    @pytest.mark.asyncio
    async def test_evaluate_missing_both_params_returns_error(self):
        from kaos_web.browser_tools import EvaluateJSTool

        tool = EvaluateJSTool()
        result = await tool.execute({"expression": "1+1"})
        assert result.isError
        assert "context_id" in result.require_text()

    @pytest.mark.asyncio
    async def test_navigate_returns_success_structure(self):
        from kaos_web.browser_tools import BrowserNavigateTool

        tool = BrowserNavigateTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()

            from kaos_web.models import WebResponse

            mock_client.fetch = AsyncMock(
                return_value=WebResponse(
                    url="https://example.com",
                    status_code=200,
                    title="Example",
                )
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"url": "https://example.com", "context_id": "s1"})
            assert not result.isError

    @pytest.mark.asyncio
    async def test_snapshot_returns_tree(self):
        from kaos_web.browser_tools import GetSnapshotTool

        tool = GetSnapshotTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_snapshot = AsyncMock(
                return_value='- heading "Test" [level=1]\n- button "OK"'
            )
            mock_client.get_url = AsyncMock(return_value="https://example.com")
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1"})
            assert not result.isError

    @pytest.mark.asyncio
    async def test_content_tool_markdown(self):
        from kaos_web.browser_tools import GetPageContentTool

        tool = GetPageContentTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_content = AsyncMock(
                return_value="<html><body><h1>Hello</h1><p>World</p></body></html>"
            )
            mock_client.get_url = AsyncMock(return_value="https://example.com")
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1", "output_format": "markdown"})
            assert not result.isError


class TestBrowserChannelDetection:
    def test_env_var_overrides(self, monkeypatch):
        """Legacy KAOS_BROWSER_CHANNEL env var sets channel via KaosWebSettings."""
        from kaos_web.settings import KaosWebSettings

        monkeypatch.setenv("KAOS_BROWSER_CHANNEL", "firefox")
        s = KaosWebSettings(browser_auto_detect_channel=False)
        assert s.browser_channel == "firefox"

    def test_env_var_auto_means_none(self, monkeypatch):
        """browser_channel='auto' maps to None in to_browser_config()."""
        from kaos_web.settings import KaosWebSettings

        monkeypatch.setenv("KAOS_BROWSER_CHANNEL", "auto")
        s = KaosWebSettings(browser_auto_detect_channel=False)
        config = s.to_browser_config()
        assert config.channel is None

    def test_no_env_var_linux_with_chrome(self, monkeypatch):
        from kaos_web.settings import _detect_browser_channel

        monkeypatch.setattr("kaos_web.settings.platform.system", lambda: "Linux")
        monkeypatch.setattr("kaos_web.settings.shutil.which", lambda cmd: "/usr/bin/google-chrome")
        assert _detect_browser_channel() == "chrome"

    def test_no_env_var_macos_no_chrome(self, monkeypatch):
        from kaos_web.settings import _detect_browser_channel

        monkeypatch.setattr("kaos_web.settings.platform.system", lambda: "Darwin")
        assert _detect_browser_channel() is None

    def test_build_config_uses_detection(self, monkeypatch):
        import kaos_web.browser_tools as bt

        monkeypatch.setattr(bt, "_browser_config_override", None)
        monkeypatch.setenv("KAOS_BROWSER_CHANNEL", "webkit")
        config = bt._build_browser_config()
        assert config.channel == "webkit"

    def test_config_override_takes_precedence(self, monkeypatch):
        import kaos_web.browser_tools as bt
        from kaos_web.clients.config import BrowserClientConfig

        override = BrowserClientConfig(channel="firefox", headless=False)
        monkeypatch.setattr(bt, "_browser_config_override", override)
        config = bt._build_browser_config()
        assert config.channel == "firefox"
        assert config.headless is False


class TestContextManagementTools:
    @pytest.mark.asyncio
    async def test_list_contexts_empty(self):
        from kaos_web.browser_tools import ListContextsTool

        tool = ListContextsTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.active_contexts = []
            mock_get.return_value = mock_client

            result = await tool.execute({})
            assert not result.isError

    @pytest.mark.asyncio
    async def test_close_context_not_found(self):
        from kaos_web.browser_tools import CloseContextTool

        tool = CloseContextTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.active_contexts = ["other"]
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "missing"})
            assert result.isError

    @pytest.mark.asyncio
    async def test_close_context_success(self):
        from kaos_web.browser_tools import CloseContextTool

        tool = CloseContextTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.active_contexts = ["s1"]
            mock_client.close_context = AsyncMock()
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1"})
            assert not result.isError


# ── Cookie/Storage method tests ──


class TestGetCookiesMethod:
    @pytest.mark.asyncio
    async def test_get_cookies_success(self):
        client = BrowserClient()
        ctx = _mock_context()
        ctx.cookies = AsyncMock(return_value=[{"name": "sid", "value": "abc"}])
        client._contexts["s1"] = ctx

        cookies = await client.get_cookies("s1")
        assert len(cookies) == 1
        assert cookies[0]["name"] == "sid"

    @pytest.mark.asyncio
    async def test_get_cookies_with_url_filter(self):
        client = BrowserClient()
        ctx = _mock_context()
        ctx.cookies = AsyncMock(return_value=[])
        client._contexts["s1"] = ctx

        await client.get_cookies("s1", urls=["https://example.com"])
        ctx.cookies.assert_called_once_with(["https://example.com"])

    @pytest.mark.asyncio
    async def test_get_cookies_no_context_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No context"):
            await client.get_cookies("nonexistent")


class TestSetCookiesMethod:
    @pytest.mark.asyncio
    async def test_set_cookies_success(self):
        client = BrowserClient()
        ctx = _mock_context()
        ctx.add_cookies = AsyncMock()
        client._contexts["s1"] = ctx

        await client.set_cookies("s1", [{"name": "x", "value": "y", "url": "https://example.com"}])
        ctx.add_cookies.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_cookies_no_context_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No context"):
            await client.set_cookies("nonexistent", [])


class TestSaveStorageStateMethod:
    @pytest.mark.asyncio
    async def test_save_storage_state_success(self):
        client = BrowserClient()
        ctx = _mock_context()
        ctx.storage_state = AsyncMock()
        client._contexts["s1"] = ctx

        path = await client.save_storage_state("s1", "/tmp/state.json")
        assert path == "/tmp/state.json"
        ctx.storage_state.assert_called_once_with(path="/tmp/state.json")

    @pytest.mark.asyncio
    async def test_save_storage_state_no_context_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No context"):
            await client.save_storage_state("nonexistent", "/tmp/state.json")


# ── Network monitoring method tests ──


class TestRequestLogging:
    @pytest.mark.asyncio
    async def test_enable_logging_initializes_log(self):
        client = BrowserClient()
        ctx = _mock_context()
        page = _mock_page()
        page.on = MagicMock()
        client._contexts["s1"] = ctx
        client._pages["s1"] = page

        await client.enable_request_logging("s1")
        assert hasattr(client, "_request_logs")
        assert client._request_logs["s1"] == []
        assert page.on.call_count == 2  # request + response handlers

    @pytest.mark.asyncio
    async def test_get_request_log_empty(self):
        client = BrowserClient()
        log = await client.get_request_log("nonexistent")
        assert log == []

    @pytest.mark.asyncio
    async def test_get_request_detail_not_found(self):
        client = BrowserClient()
        detail = await client.get_request_detail("s1", 999)
        assert detail is None

    @pytest.mark.asyncio
    async def test_get_request_detail_found(self):
        client = BrowserClient()
        client._request_logs = {
            "s1": [
                {"id": 0, "url": "https://example.com", "method": "GET"},
                {"id": 1, "url": "https://example.com/api", "method": "POST"},
            ]
        }
        detail = await client.get_request_detail("s1", 1)
        assert detail is not None
        assert detail["url"] == "https://example.com/api"

    @pytest.mark.asyncio
    async def test_close_context_cleans_request_log(self):
        client = BrowserClient()
        page = _mock_page()
        ctx = _mock_context()
        client._pages["s1"] = page
        client._contexts["s1"] = ctx
        client._request_logs = {"s1": [{"id": 0}]}

        await client.close_context("s1")
        assert "s1" not in client._request_logs

    @pytest.mark.asyncio
    async def test_enable_logging_no_context_raises(self):
        client = BrowserClient()
        with pytest.raises(WebBrowserError, match="No context"):
            await client.enable_request_logging("nonexistent")


# ── Cookie/Storage MCP tool tests ──


class TestCookieToolMetadata:
    @pytest.fixture
    def cookie_tools(self):
        from kaos_web.browser_tools import (
            GetCookiesTool,
            SaveAuthStateTool,
            SetCookieTool,
        )

        return [GetCookiesTool(), SetCookieTool(), SaveAuthStateTool()]

    def test_all_have_annotations(self, cookie_tools):
        for tool in cookie_tools:
            assert tool.metadata.annotations is not None

    def test_names_follow_convention(self, cookie_tools):
        for tool in cookie_tools:
            assert tool.metadata.name.startswith("kaos-web-browser-")


class TestNetworkToolMetadata:
    @pytest.fixture
    def network_tools(self):
        from kaos_web.browser_tools import (
            EnableRequestLoggingTool,
            GetRequestDetailTool,
            ListCapturedResponsesTool,
            ListRequestsTool,
        )

        return [
            EnableRequestLoggingTool(),
            ListRequestsTool(),
            GetRequestDetailTool(),
            ListCapturedResponsesTool(),
        ]

    def test_all_have_annotations(self, network_tools):
        for tool in network_tools:
            assert tool.metadata.annotations is not None

    def test_names_follow_convention(self, network_tools):
        for tool in network_tools:
            assert tool.metadata.name.startswith("kaos-web-browser-")


class TestCookieToolErrorPaths:
    @pytest.mark.asyncio
    async def test_set_cookie_missing_domain_and_url(self):
        from kaos_web.browser_tools import SetCookieTool

        tool = SetCookieTool()
        result = await tool.execute(
            {
                "context_id": "s1",
                "name": "x",
                "value": "y",
            }
        )
        assert result.isError
        assert "domain" in result.require_text()

    @pytest.mark.asyncio
    async def test_get_cookies_success_structure(self):
        from kaos_web.browser_tools import GetCookiesTool

        tool = GetCookiesTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_cookies = AsyncMock(
                return_value=[{"name": "sid", "value": "abc", "domain": ".example.com"}]
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1"})
            assert not result.isError


class TestNetworkToolErrorPaths:
    @pytest.mark.asyncio
    async def test_list_requests_success(self):
        from kaos_web.browser_tools import ListRequestsTool

        tool = ListRequestsTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_request_log = AsyncMock(
                return_value=[
                    {
                        "id": 0,
                        "url": "https://example.com",
                        "method": "GET",
                        "resource_type": "document",
                        "status": 200,
                    },
                ]
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1"})
            assert not result.isError

    @pytest.mark.asyncio
    async def test_get_request_detail_not_found(self):
        from kaos_web.browser_tools import GetRequestDetailTool

        tool = GetRequestDetailTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_request_detail = AsyncMock(return_value=None)
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1", "request_id": 999})
            assert result.isError
            assert "999" in result.require_text()


class TestRegisterBrowserTools:
    def test_registers_all_tools(self):
        from kaos_web.browser_tools import register_browser_tools

        runtime = MagicMock()
        runtime.tools = MagicMock()
        count = register_browser_tools(runtime)
        assert count == 19
        assert runtime.tools.register_tool.call_count == 19
