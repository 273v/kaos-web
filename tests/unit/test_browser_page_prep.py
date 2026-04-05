"""Tests for browser page preparation utilities.

Tests CMP detection/dismissal logic using mock Playwright Page objects.
No real browser is launched. The implementation uses a single page.evaluate()
call for detection, so mocks target evaluate() and locator().click().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.browser_page_prep import (
    KNOWN_CMPS,
    KnownCMP,
    dismiss_cookie_banners,
    wait_for_content_settled,
)

# ---------------------------------------------------------------------------
# Helpers — mock Playwright Page
# ---------------------------------------------------------------------------


def _make_mock_page(
    evaluate_return: int = -1,
    click_side_effect: Exception | None = None,
) -> MagicMock:
    """Create a mock Playwright Page for dismiss_cookie_banners.

    Args:
        evaluate_return: Index returned by page.evaluate (_DETECT_JS).
            -1 means no CMP detected. 0 = first CMP, 1 = second, etc.
        click_side_effect: If set, the dismiss button click raises this.
    """
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=evaluate_return)

    def _locator(selector: str) -> MagicMock:
        loc = MagicMock()
        first = MagicMock()
        if click_side_effect:
            first.click = AsyncMock(side_effect=click_side_effect)
        else:
            first.click = AsyncMock()
        loc.first = first
        return loc

    page.locator = _locator
    return page


# ---------------------------------------------------------------------------
# Tests — KNOWN_CMPS data integrity
# ---------------------------------------------------------------------------


class TestKnownCMPs:
    def test_has_entries(self):
        assert len(KNOWN_CMPS) >= 5, "Should have at least 5 known CMPs"

    def test_all_have_required_fields(self):
        for cmp in KNOWN_CMPS:
            assert cmp.name, f"CMP missing name: {cmp}"
            assert cmp.detect, f"CMP {cmp.name} missing detect selector"
            assert cmp.dismiss, f"CMP {cmp.name} missing dismiss selector"

    def test_no_duplicate_names(self):
        names = [cmp.name for cmp in KNOWN_CMPS]
        assert len(names) == len(set(names)), f"Duplicate CMP names: {names}"

    def test_known_cmps_are_frozen(self):
        cmp = KNOWN_CMPS[0]
        with pytest.raises(AttributeError):
            cmp.name = "changed"  # type: ignore[misc]  # ty: ignore[invalid-assignment]


# ---------------------------------------------------------------------------
# Tests — dismiss_cookie_banners
# ---------------------------------------------------------------------------


class TestDismissCookieBanners:
    @pytest.mark.asyncio
    async def test_no_banners_present(self):
        """When evaluate returns -1 (no CMP visible), nothing is dismissed."""
        page = _make_mock_page(evaluate_return=-1)
        dismissed = await dismiss_cookie_banners(page)
        assert dismissed == []
        # Verify evaluate was called with all detect selectors
        page.evaluate.assert_awaited_once()
        args = page.evaluate.call_args[0]
        assert len(args) == 2  # JS code + selector list
        assert len(args[1]) == len(KNOWN_CMPS)

    @pytest.mark.asyncio
    async def test_onetrust_dismissed(self):
        """OneTrust (index 0) is detected and dismissed."""
        page = _make_mock_page(evaluate_return=0)
        dismissed = await dismiss_cookie_banners(page)
        assert dismissed == ["OneTrust"]

    @pytest.mark.asyncio
    async def test_cookiebot_dismissed(self):
        """CookieBot (index 1) is detected and dismissed."""
        page = _make_mock_page(evaluate_return=1)
        dismissed = await dismiss_cookie_banners(page)
        assert dismissed == ["CookieBot"]

    @pytest.mark.asyncio
    async def test_all_cmps_detectable(self):
        """Every CMP in KNOWN_CMPS can be detected by its index."""
        for i, cmp in enumerate(KNOWN_CMPS):
            page = _make_mock_page(evaluate_return=i)
            dismissed = await dismiss_cookie_banners(page)
            assert dismissed == [cmp.name], f"CMP at index {i} ({cmp.name}) not dismissed"

    @pytest.mark.asyncio
    async def test_click_failure_returns_empty(self):
        """If dismiss click fails, returns empty list (no partial results)."""
        page = _make_mock_page(
            evaluate_return=0,
            click_side_effect=Exception("Click failed"),
        )
        dismissed = await dismiss_cookie_banners(page)
        assert dismissed == []

    @pytest.mark.asyncio
    async def test_evaluate_failure_returns_empty(self):
        """If page.evaluate raises (page closed, etc.), returns empty list."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=Exception("Page closed"))
        dismissed = await dismiss_cookie_banners(page)
        assert dismissed == []

    @pytest.mark.asyncio
    async def test_custom_cmp_list(self):
        """Custom CMP list overrides defaults."""
        custom = (KnownCMP(name="TestCMP", detect=".test-banner", dismiss=".test-accept"),)
        page = _make_mock_page(evaluate_return=0)
        dismissed = await dismiss_cookie_banners(page, cmps=custom)
        assert dismissed == ["TestCMP"]
        # Verify only custom selector was passed
        args = page.evaluate.call_args[0]
        assert args[1] == [".test-banner"]

    @pytest.mark.asyncio
    async def test_empty_cmp_list(self):
        """Empty CMP list means evaluate is never called."""
        page = _make_mock_page(evaluate_return=0)
        dismissed = await dismiss_cookie_banners(page, cmps=())
        assert dismissed == []
        page.evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_evaluate_call(self):
        """Detection uses exactly one page.evaluate call, not N is_visible calls."""
        page = _make_mock_page(evaluate_return=-1)
        await dismiss_cookie_banners(page)
        assert page.evaluate.await_count == 1

    @pytest.mark.asyncio
    async def test_dismiss_uses_correct_selector(self):
        """The dismiss click targets the matched CMP's dismiss selector."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=0)  # OneTrust
        clicked_selectors: list[str] = []

        def _locator(selector: str) -> MagicMock:
            loc = MagicMock()
            first = MagicMock()
            first.click = AsyncMock()
            loc.first = first
            clicked_selectors.append(selector)
            return loc

        page.locator = _locator
        dismissed = await dismiss_cookie_banners(page)
        assert dismissed == ["OneTrust"]
        assert "#onetrust-accept-btn-handler" in clicked_selectors


# ---------------------------------------------------------------------------
# Tests — _fetch_html integration (verify params thread through)
# ---------------------------------------------------------------------------


class TestFetchHtmlBrowserParams:
    @pytest.mark.asyncio
    async def test_dismiss_overlays_passed_to_browser(self):
        """dismiss_overlays=True is passed through to BrowserClient via extra."""
        from kaos_web.tools import _fetch_html

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            return_value=MagicMock(html="<html></html>", url="https://example.com")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kaos_web.clients.browser.BrowserClient", return_value=mock_client):
            await _fetch_html(
                "https://example.com",
                use_browser=True,
                dismiss_overlays=True,
                wait_for_selector="#content",
            )

        call_args = mock_client.fetch.call_args
        request = call_args[0][0]
        assert request.extra.get("dismiss_overlays") is True
        assert request.extra.get("wait_for_selector") == "#content"

    @pytest.mark.asyncio
    async def test_dismiss_overlays_default_true(self):
        """dismiss_overlays defaults to True in _fetch_html."""
        from kaos_web.tools import _fetch_html

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            return_value=MagicMock(html="<html></html>", url="https://example.com")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kaos_web.clients.browser.BrowserClient", return_value=mock_client):
            await _fetch_html("https://example.com", use_browser=True)

        call_args = mock_client.fetch.call_args
        request = call_args[0][0]
        assert request.extra.get("dismiss_overlays") is True

    @pytest.mark.asyncio
    async def test_dismiss_overlays_false_not_passed(self):
        """dismiss_overlays=False means the key is not set in extra."""
        from kaos_web.tools import _fetch_html

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            return_value=MagicMock(html="<html></html>", url="https://example.com")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kaos_web.clients.browser.BrowserClient", return_value=mock_client):
            await _fetch_html("https://example.com", use_browser=True, dismiss_overlays=False)

        call_args = mock_client.fetch.call_args
        request = call_args[0][0]
        assert "dismiss_overlays" not in request.extra

    @pytest.mark.asyncio
    async def test_http_mode_ignores_browser_params(self):
        """When use_browser=False, browser params don't affect anything."""
        from kaos_web.tools import _fetch_html

        mock_client = AsyncMock()
        mock_resp = MagicMock(html="<html></html>", url="https://example.com")
        mock_client.fetch = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kaos_web.clients.http.HttpClient", return_value=mock_client):
            html, _url = await _fetch_html(
                "https://example.com",
                use_browser=False,
                dismiss_overlays=True,
                wait_for_selector="#content",
            )

        assert html == "<html></html>"


# ---------------------------------------------------------------------------
# Tests — Tool parameter schemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def _get_param_names(self, tool_cls: type) -> list[str]:
        tool = tool_cls()
        return [p.name for p in tool.metadata.input_schema]

    def test_get_markdown_has_browser_params(self):
        from kaos_web.tools import GetPageMarkdownTool

        params = self._get_param_names(GetPageMarkdownTool)
        assert "dismiss_overlays" in params
        assert "wait_for_selector" in params

    def test_get_text_has_browser_params(self):
        from kaos_web.tools import GetPageTextTool

        params = self._get_param_names(GetPageTextTool)
        assert "dismiss_overlays" in params
        assert "wait_for_selector" in params

    def test_fetch_page_has_browser_params(self):
        from kaos_web.tools import FetchPageTool

        params = self._get_param_names(FetchPageTool)
        assert "dismiss_overlays" in params
        assert "wait_for_selector" in params

    def test_search_page_has_browser_params(self):
        from kaos_web.tools import SearchPageTool

        params = self._get_param_names(SearchPageTool)
        assert "dismiss_overlays" in params
        assert "wait_for_selector" in params

    def test_get_tables_has_browser_params(self):
        from kaos_web.tools import GetPageTablesTool

        params = self._get_param_names(GetPageTablesTool)
        assert "dismiss_overlays" in params
        assert "wait_for_selector" in params

    def test_browser_navigate_has_dismiss_overlays(self):
        from kaos_web.browser_tools import BrowserNavigateTool

        params = self._get_param_names(BrowserNavigateTool)
        assert "dismiss_overlays" in params

    def test_tools_have_wait_for_settled(self):
        from kaos_web.browser_tools import BrowserNavigateTool
        from kaos_web.tools import GetPageMarkdownTool

        for cls in [GetPageMarkdownTool, BrowserNavigateTool]:
            params = self._get_param_names(cls)
            assert "wait_for_settled" in params, f"{cls.__name__} missing wait_for_settled"


# ---------------------------------------------------------------------------
# Tests — content settling
# ---------------------------------------------------------------------------


class TestWaitForContentSettled:
    @pytest.mark.asyncio
    async def test_fast_path_returns_true(self):
        """When page already has content, returns True immediately."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=True)
        result = await wait_for_content_settled(page)
        assert result is True
        # Only one evaluate call (the fast-path check), no slow path
        assert page.evaluate.await_count == 1

    @pytest.mark.asyncio
    async def test_slow_path_on_empty_page(self):
        """When page has no content, falls through to MutationObserver wait."""
        page = MagicMock()
        # First call (fast path) returns False, second call (settle) returns None
        page.evaluate = AsyncMock(side_effect=[False, None])
        result = await wait_for_content_settled(page)
        assert result is True
        # Two evaluate calls: fast path + settle wait
        assert page.evaluate.await_count == 2

    @pytest.mark.asyncio
    async def test_evaluate_exception_returns_true(self):
        """If page.evaluate throws on fast path, returns True (let caller handle)."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=Exception("Page closed"))
        result = await wait_for_content_settled(page)
        assert result is True

    @pytest.mark.asyncio
    async def test_settle_exception_returns_false(self):
        """If MutationObserver wait throws, returns False."""
        page = MagicMock()
        # Fast path returns False, settle wait throws
        page.evaluate = AsyncMock(side_effect=[False, Exception("Timeout")])
        result = await wait_for_content_settled(page)
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_settled_threaded_to_browser(self):
        """wait_for_settled=True is passed through _fetch_html to BrowserClient."""
        from kaos_web.tools import _fetch_html

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            return_value=MagicMock(html="<html></html>", url="https://example.com")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kaos_web.clients.browser.BrowserClient", return_value=mock_client):
            await _fetch_html("https://example.com", use_browser=True, wait_for_settled=True)

        request = mock_client.fetch.call_args[0][0]
        assert request.extra.get("wait_for_settled") is True

    @pytest.mark.asyncio
    async def test_wait_for_settled_default_true(self):
        """wait_for_settled defaults to True in _fetch_html."""
        from kaos_web.tools import _fetch_html

        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(
            return_value=MagicMock(html="<html></html>", url="https://example.com")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("kaos_web.clients.browser.BrowserClient", return_value=mock_client):
            await _fetch_html("https://example.com", use_browser=True)

        request = mock_client.fetch.call_args[0][0]
        assert request.extra.get("wait_for_settled") is True
