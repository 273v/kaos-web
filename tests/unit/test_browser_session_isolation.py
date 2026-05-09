"""Cross-session browser isolation tests (WEB5-002 / audit-04 finding #2).

The shared ``_browser_client`` is process-global and historically keyed
its ``_contexts`` / ``_pages`` / ``_request_logs`` / ``_response_bodies``
/ ``_logging_config`` maps by raw ``context_id`` strings. With the MCP
HTTP server fronting multiple agents, any caller who knows or guesses a
``context_id`` could interact with another caller's pages, cookies, or
captured network traffic.

WEB5-002 binds every browser-state lookup to the tuple
``(KaosContext.session_id, context_id)``. This module pins down the
behavior:

- Cross-session reads (``click``, ``get_cookies``, ``get_request_log``,
  ``get_response_body``, ...) raise the same uniform "No active page" /
  "No context" error a missing context would raise — never disclosing
  that the context exists in another session.
- Cross-session ``close_context`` is a silent no-op; the owning
  session's context is untouched.
- ``active_contexts(session_id)`` returns only the caller's bucket.
- Library callers that omit ``session_id`` fall back to
  ``ANONYMOUS_SESSION_ID`` so the existing single-user stdio surface
  keeps working.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kaos_web.clients.browser import ANONYMOUS_SESSION_ID, BrowserClient
from kaos_web.errors import WebBrowserError

# ── Helpers ─────────────────────────────────────────────────────────


def _mock_page(url: str = "https://example.com") -> MagicMock:
    page = AsyncMock()
    page.url = url
    page.on = MagicMock()
    page.close = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.type = AsyncMock()
    page.press = AsyncMock()
    page.select_option = AsyncMock(return_value=["v"])
    page.evaluate = AsyncMock(return_value=42)
    page.screenshot = AsyncMock(return_value=b"\x89PNG")
    page.content = AsyncMock(return_value="<html></html>")
    body_locator = AsyncMock()
    body_locator.aria_snapshot = AsyncMock(return_value="- heading")
    page.locator = MagicMock(return_value=body_locator)
    return page


def _mock_context() -> MagicMock:
    ctx = AsyncMock()
    ctx.cookies = AsyncMock(return_value=[{"name": "sid", "value": "alice-secret"}])
    ctx.add_cookies = AsyncMock()
    ctx.storage_state = AsyncMock(return_value={"cookies": [{"name": "sid"}]})
    ctx.close = AsyncMock()
    return ctx


def _seed_session(
    client: BrowserClient,
    session_id: str,
    context_id: str,
) -> tuple[MagicMock, MagicMock]:
    page = _mock_page()
    ctx = _mock_context()
    scope = (session_id, context_id)
    client._contexts[scope] = ctx
    client._pages[scope] = page
    return page, ctx


# ── Cross-session interaction blocked ──────────────────────────────


class TestCrossSessionPageAccessBlocked:
    """A different session's interaction tools must miss uniformly."""

    @pytest.mark.asyncio
    async def test_click_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        alice_page, _ = _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No active page for context 'shared'"):
            await client.click("shared", "button", session_id="bob")

        # Alice's page was untouched.
        alice_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_fill_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        alice_page, _ = _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No active page"):
            await client.fill("shared", "input", "x", session_id="bob")

        alice_page.fill.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        alice_page, _ = _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No active page"):
            await client.evaluate_in_context("shared", "1+1", session_id="bob")

        alice_page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_url_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No active page"):
            await client.get_url("shared", session_id="bob")

    @pytest.mark.asyncio
    async def test_screenshot_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        alice_page, _ = _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No active page"):
            await client.screenshot_context("shared", session_id="bob")

        alice_page.screenshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_owner_session_still_works(self) -> None:
        """Sanity: the owning session continues to interact normally."""
        client = BrowserClient()
        alice_page, _ = _seed_session(client, "alice", "shared")

        await client.click("shared", "button#go", session_id="alice")
        alice_page.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_message_does_not_disclose_other_session(self) -> None:
        """The ``available`` list in the error must list only Bob's contexts."""
        client = BrowserClient()
        _seed_session(client, "alice", "alice-secret-context")
        _seed_session(client, "bob", "bob-public")

        with pytest.raises(WebBrowserError) as info:
            await client.click("missing", "button", session_id="bob")
        msg = str(info.value)
        assert "alice-secret-context" not in msg
        assert "bob-public" in msg


# ── Cross-session cookie / storage blocked ─────────────────────────


class TestCrossSessionCookieAccessBlocked:
    """Cookies + auth state are some of the highest-value targets."""

    @pytest.mark.asyncio
    async def test_get_cookies_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        _, alice_ctx = _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No context 'shared'"):
            await client.get_cookies("shared", session_id="bob")

        # Alice's context was never queried — Bob did not see her cookies.
        alice_ctx.cookies.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_cookies_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        _, alice_ctx = _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No context"):
            await client.set_cookies(
                "shared",
                [{"name": "evil", "value": "x", "url": "https://e.com"}],
                session_id="bob",
            )

        alice_ctx.add_cookies.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_storage_state_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        _, alice_ctx = _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No context"):
            await client.get_storage_state("shared", session_id="bob")

        alice_ctx.storage_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_storage_state_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        _, alice_ctx = _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No context"):
            await client.save_storage_state("shared", "/tmp/exfil.json", session_id="bob")

        alice_ctx.storage_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_owner_session_reads_own_cookies(self) -> None:
        """Sanity: Alice still reads her own cookies."""
        client = BrowserClient()
        _, alice_ctx = _seed_session(client, "alice", "shared")

        cookies = await client.get_cookies("shared", session_id="alice")
        assert cookies == [{"name": "sid", "value": "alice-secret"}]
        alice_ctx.cookies.assert_called_once()


# ── Cross-session network log access blocked ───────────────────────


class TestCrossSessionRequestLogAccessBlocked:
    """Request logs and response bodies can carry auth tokens, API
    payloads, and proprietary data — must be session-scoped."""

    @pytest.mark.asyncio
    async def test_get_request_log_cross_session_returns_empty(self) -> None:
        client = BrowserClient()
        scope = ("alice", "shared")
        client._request_logs[scope] = [
            {"id": 0, "url": "https://internal/api", "headers": {"X-Auth": "token"}}
        ]

        log = await client.get_request_log("shared", session_id="bob")
        assert log == []

        # Sanity: Alice still reads her own log.
        alice_log = await client.get_request_log("shared", session_id="alice")
        assert len(alice_log) == 1

    @pytest.mark.asyncio
    async def test_get_request_detail_cross_session_returns_none(self) -> None:
        client = BrowserClient()
        scope = ("alice", "shared")
        client._request_logs[scope] = [{"id": 0, "url": "https://x", "method": "GET"}]

        detail = await client.get_request_detail("shared", 0, session_id="bob")
        assert detail is None

    @pytest.mark.asyncio
    async def test_get_response_body_cross_session_returns_none(self) -> None:
        client = BrowserClient()
        scope = ("alice", "shared")
        client._response_bodies[scope] = {
            0: {
                "body": b'{"secret": true}',
                "content_type": "application/json",
                "size": 16,
                "truncated": False,
            }
        }

        body = await client.get_response_body("shared", 0, session_id="bob")
        assert body is None

    @pytest.mark.asyncio
    async def test_get_captured_responses_cross_session_returns_empty(self) -> None:
        client = BrowserClient()
        scope = ("alice", "shared")
        client._request_logs[scope] = [
            {
                "id": 0,
                "url": "https://x",
                "method": "GET",
                "resource_type": "fetch",
                "status": 200,
            }
        ]
        client._response_bodies[scope] = {
            0: {"body": b"{}", "content_type": "application/json", "size": 2, "truncated": False}
        }

        results = await client.get_captured_responses("shared", session_id="bob")
        assert results == []

        # Sanity: owner sees them.
        alice_results = await client.get_captured_responses("shared", session_id="alice")
        assert len(alice_results) == 1

    @pytest.mark.asyncio
    async def test_enable_request_logging_cross_session_uniform_miss(self) -> None:
        client = BrowserClient()
        _seed_session(client, "alice", "shared")

        with pytest.raises(WebBrowserError, match="No context 'shared'"):
            await client.enable_request_logging("shared", session_id="bob")

        # No leakage into Alice's bucket.
        assert ("bob", "shared") not in client._request_logs
        assert ("alice", "shared") not in client._request_logs


# ── Cross-session close ────────────────────────────────────────────


class TestCrossSessionCloseSilentlyUnaware:
    """``close_context`` from the wrong session must NOT close the
    owning session's context (and must not raise — the lookup just
    misses)."""

    @pytest.mark.asyncio
    async def test_close_cross_session_does_not_close_other_session(self) -> None:
        client = BrowserClient()
        alice_page, alice_ctx = _seed_session(client, "alice", "shared")

        # Bob calls close on Alice's context_id — silent no-op.
        await client.close_context("shared", session_id="bob")

        # Alice's page and context are untouched.
        alice_page.close.assert_not_called()
        alice_ctx.close.assert_not_called()
        assert ("alice", "shared") in client._pages
        assert ("alice", "shared") in client._contexts

    @pytest.mark.asyncio
    async def test_owner_close_actually_closes(self) -> None:
        client = BrowserClient()
        alice_page, alice_ctx = _seed_session(client, "alice", "shared")

        await client.close_context("shared", session_id="alice")

        alice_page.close.assert_called_once()
        alice_ctx.close.assert_called_once()
        assert ("alice", "shared") not in client._pages
        assert ("alice", "shared") not in client._contexts


# ── Listing filtered by session ────────────────────────────────────


class TestActiveContextsFilteredBySession:
    """``active_contexts(session_id)`` lists only the caller's bucket."""

    def test_alice_sees_only_alice_contexts(self) -> None:
        client = BrowserClient()
        _seed_session(client, "alice", "a1")
        _seed_session(client, "alice", "a2")
        _seed_session(client, "bob", "b1")

        assert sorted(client.active_contexts("alice")) == ["a1", "a2"]

    def test_bob_sees_only_bob_contexts(self) -> None:
        client = BrowserClient()
        _seed_session(client, "alice", "a1")
        _seed_session(client, "bob", "b1")

        assert client.active_contexts("bob") == ["b1"]

    def test_unknown_session_sees_nothing(self) -> None:
        client = BrowserClient()
        _seed_session(client, "alice", "a1")

        assert client.active_contexts("nonexistent") == []

    def test_anonymous_default_lists_anonymous_contexts(self) -> None:
        client = BrowserClient()
        _seed_session(client, ANONYMOUS_SESSION_ID, "stdio-default")

        # No explicit session_id → ANONYMOUS bucket.
        assert client.active_contexts() == ["stdio-default"]


# ── Anonymous fallback ─────────────────────────────────────────────


class TestAnonymousSessionDefault:
    """Library callers that don't pass a session_id continue to work
    unchanged — they share the ANONYMOUS bucket."""

    @pytest.mark.asyncio
    async def test_click_default_session_id_is_anonymous(self) -> None:
        client = BrowserClient()
        page, _ = _seed_session(client, ANONYMOUS_SESSION_ID, "default")

        # No session_id kwarg.
        await client.click("default", "button#go")
        page.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cookies_default_session_id(self) -> None:
        client = BrowserClient()
        _, ctx = _seed_session(client, ANONYMOUS_SESSION_ID, "default")

        cookies = await client.get_cookies("default")
        assert cookies == [{"name": "sid", "value": "alice-secret"}]
        ctx.cookies.assert_called_once()

    @pytest.mark.asyncio
    async def test_anonymous_callers_share_bucket(self) -> None:
        """Two library-style callers (no runtime context) share state.

        WEB5-002 protects against cross-MCP-session leaks. It does NOT
        try to isolate two in-process library callers from each other —
        if you're sharing a Python interpreter you already share the
        BrowserClient. That's the original use case.
        """
        client = BrowserClient()
        page, _ = _seed_session(client, ANONYMOUS_SESSION_ID, "shared")

        await client.click("shared", "button")
        await client.click("shared", "button2")  # second "anonymous" caller
        assert page.click.call_count == 2


# ── Tool-layer threading ───────────────────────────────────────────


class TestToolLayerThreadsSessionId:
    """End-to-end check that browser_tools._session_id pulls from
    KaosContext.session_id and tools propagate it into BrowserClient
    method calls."""

    @pytest.mark.asyncio
    async def test_click_tool_threads_session_id(self) -> None:
        from unittest.mock import patch as _patch

        from kaos_web.browser_tools import ClickElementTool

        client = MagicMock()
        client.click = AsyncMock()
        client.get_url = AsyncMock(return_value="https://x")

        ctx = MagicMock()
        ctx.session_id = "kaos-session-42"

        with _patch("kaos_web.browser_tools._get_browser_client", AsyncMock(return_value=client)):
            result = await ClickElementTool().execute(
                {"context_id": "s1", "selector": "button"}, context=ctx
            )
        assert not result.isError
        # session_id is threaded through the kwarg.
        client.click.assert_awaited_once_with("s1", "button", session_id="kaos-session-42")

    @pytest.mark.asyncio
    async def test_navigate_tool_threads_session_id_into_extra(self) -> None:
        from unittest.mock import patch as _patch

        from kaos_web.browser_tools import BrowserNavigateTool
        from kaos_web.models import WebResponse

        client = MagicMock()
        captured: dict = {}

        async def _spy(req):
            captured["extra"] = req.extra
            return WebResponse(url=req.url, status_code=200, title="t", html="")

        client.fetch = _spy

        ctx = MagicMock()
        ctx.session_id = "kaos-session-99"

        with _patch("kaos_web.browser_tools._get_browser_client", AsyncMock(return_value=client)):
            await BrowserNavigateTool().execute(
                {"url": "https://e.com", "context_id": "s1"}, context=ctx
            )
        # WEB5-002: navigate puts session_id into request.extra so
        # BrowserClient.fetch can scope the new context properly.
        assert captured["extra"]["session_id"] == "kaos-session-99"
        assert captured["extra"]["context_id"] == "s1"

    @pytest.mark.asyncio
    async def test_session_id_falls_back_to_anonymous_when_no_context(self) -> None:
        from unittest.mock import patch as _patch

        from kaos_web.browser_tools import ClickElementTool

        client = MagicMock()
        client.click = AsyncMock()
        client.get_url = AsyncMock(return_value="https://x")

        with _patch("kaos_web.browser_tools._get_browser_client", AsyncMock(return_value=client)):
            # No context kwarg → ANONYMOUS_SESSION_ID.
            await ClickElementTool().execute({"context_id": "s1", "selector": "b"})
        client.click.assert_awaited_once_with("s1", "b", session_id=ANONYMOUS_SESSION_ID)

    @pytest.mark.asyncio
    async def test_list_contexts_tool_filters_by_session(self) -> None:
        """ListContextsTool calls active_contexts(session_id) so a
        caller never sees another session's contexts."""
        from unittest.mock import patch as _patch

        from kaos_web.browser_tools import ListContextsTool

        client = MagicMock()
        # Active contexts returns whatever was passed to it (mocked
        # filter-by-session at the BrowserClient layer).
        client.active_contexts = MagicMock(return_value=["alice-only"])
        client.get_url = AsyncMock(return_value="https://e.com")

        ctx = MagicMock()
        ctx.session_id = "alice"

        with _patch("kaos_web.browser_tools._get_browser_client", AsyncMock(return_value=client)):
            result = await ListContextsTool().execute({}, context=ctx)
        assert not result.isError
        client.active_contexts.assert_called_once_with("alice")

    @pytest.mark.asyncio
    async def test_close_context_tool_cross_session_returns_not_found(self) -> None:
        """A cross-session close hits the ``not in active_contexts(session_id)``
        guard and returns the standard "No active context" error — the
        owning session's context_id is never disclosed."""
        from unittest.mock import patch as _patch

        from kaos_web.browser_tools import CloseContextTool

        client = MagicMock()
        # Bob's view: empty.
        client.active_contexts = MagicMock(return_value=[])
        client.close_context = AsyncMock()

        ctx = MagicMock()
        ctx.session_id = "bob"

        with _patch("kaos_web.browser_tools._get_browser_client", AsyncMock(return_value=client)):
            result = await CloseContextTool().execute({"context_id": "alice-shared"}, context=ctx)
        assert result.isError
        # Did NOT call close_context — the guard short-circuited.
        client.close_context.assert_not_called()
