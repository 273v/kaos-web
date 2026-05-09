"""Backfill unit tests for ``kaos_web.browser_tools`` MCP wrappers.

Audit-03 WEB3-005: lifts browser_tools.py coverage above 70% by exercising
the execute() bodies of every tool in the module — happy paths, error
translations, missing-input branches, artifact-storage paths, and the
configure_browser/_get_browser_client helpers.

The underlying ``BrowserClient`` methods are mocked. The autouse
``_block_real_playwright_launch`` fixture in ``conftest.py`` already guards
against any test reaching real Playwright; tests still patch
``kaos_web.browser_tools._get_browser_client`` to return a MagicMock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_core import ToolResult
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
    ListCapturedResponsesTool,
    ListContextsTool,
    ListRequestsTool,
    PressKeyTool,
    SaveAuthStateTool,
    ScreenshotTool,
    SelectOptionTool,
    SetCookieTool,
    TypeTextTool,
    _get_browser_client,
    _shutdown_browser_client,
    configure_browser,
    register_browser_tools,
)
from kaos_web.models import WebResponse

# ── Helpers ──────────────────────────────────────────────────────────


def _err_text(r: ToolResult) -> str:
    if r.content:
        for block in r.content:
            text = getattr(block, "text", None)
            if text:
                return str(text)
    return ""


def _make_runtime_context(
    *,
    artifact_id: str = "art-1",
    body_uri: str = "kaos://artifacts/art-1/body",
) -> tuple[MagicMock, MagicMock]:
    context = MagicMock()
    context.session_id = "sess-test"
    mock_vfs = AsyncMock()
    mock_vfs.write_bytes = AsyncMock()
    context.get_vfs_path = MagicMock(return_value=mock_vfs)

    manifest = MagicMock()
    manifest.artifact_id = artifact_id
    manifest.body_uri = body_uri
    manifest.to_tool_result = MagicMock(
        side_effect=lambda summary=None, structured_content=None, inline_body=None: (
            ToolResult.create_success(output=structured_content, summary=summary)
        )
    )

    runtime = MagicMock()
    runtime.artifacts = MagicMock()
    runtime.artifacts.create_from_path = AsyncMock(return_value=manifest)
    context.runtime = runtime
    return context, manifest


def _patch_client(client: Any) -> Any:
    """Convenience: patch the shared browser client getter."""
    return patch(
        "kaos_web.browser_tools._get_browser_client",
        AsyncMock(return_value=client),
    )


# ── Module-level helpers (configure_browser, _get_browser_client) ───


class TestModuleHelpers:
    def test_build_browser_config_uses_settings_when_no_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import kaos_web.browser_tools as bt

        monkeypatch.setattr(bt, "_browser_config_override", None)
        config = bt._build_browser_config()
        # Returns a real BrowserClientConfig — has expected attrs
        assert hasattr(config, "headless")

    def test_build_browser_config_returns_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import kaos_web.browser_tools as bt
        from kaos_web.clients.config import BrowserClientConfig

        override = BrowserClientConfig(headless=False)
        monkeypatch.setattr(bt, "_browser_config_override", override)
        assert bt._build_browser_config() is override

    @pytest.mark.asyncio
    async def test_get_browser_client_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import kaos_web.browser_tools as bt

        monkeypatch.setattr(bt, "_browser_client", None)
        # Patch BrowserClient to return a sentinel that we can identify
        sentinel = MagicMock(name="BrowserClient")
        with patch("kaos_web.clients.browser.BrowserClient", return_value=sentinel):
            first = await _get_browser_client()
            second = await _get_browser_client()
        assert first is sentinel
        assert second is sentinel  # cached on second call

    @pytest.mark.asyncio
    async def test_shutdown_browser_client_closes_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import kaos_web.browser_tools as bt

        fake = MagicMock()
        fake.close = AsyncMock()
        monkeypatch.setattr(bt, "_browser_client", fake)
        await _shutdown_browser_client()
        fake.close.assert_awaited_once()
        assert bt._browser_client is None

    @pytest.mark.asyncio
    async def test_shutdown_browser_client_noop_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import kaos_web.browser_tools as bt

        monkeypatch.setattr(bt, "_browser_client", None)
        # Should not raise
        await _shutdown_browser_client()

    def test_configure_browser_sets_override_when_no_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import kaos_web.browser_tools as bt
        from kaos_web.clients.config import BrowserClientConfig

        monkeypatch.setattr(bt, "_browser_client", None)
        monkeypatch.setattr(bt, "_browser_config_override", None)
        config = BrowserClientConfig(headless=False)
        configure_browser(config)
        assert bt._browser_config_override is config


# ── BrowserNavigateTool ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestBrowserNavigateTool:
    async def test_success_minimal(self) -> None:
        client = MagicMock()
        client.fetch = AsyncMock(
            return_value=WebResponse(
                url="https://example.com/r",
                status_code=200,
                title="Example",
                html="<html></html>",
            )
        )
        with _patch_client(client):
            result = await BrowserNavigateTool().execute({"url": "https://example.com"})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["context_id"] == "default"
        assert out["title"] == "Example"

    async def test_success_with_all_options(self) -> None:
        client = MagicMock()
        captured = {}

        async def _spy(req: Any) -> WebResponse:
            captured["extra"] = req.extra
            return WebResponse(url=req.url, status_code=200, title="t", html="<html></html>")

        client.fetch = _spy
        with _patch_client(client):
            await BrowserNavigateTool().execute(
                {
                    "url": "https://e.com",
                    "context_id": "s1",
                    "wait_until": "networkidle",
                    "wait_for_selector": "#x",
                    "dismiss_overlays": True,
                    "wait_for_settled": True,
                }
            )
        extra = captured["extra"]
        assert extra["context_id"] == "s1"
        assert extra["wait_until"] == "networkidle"
        assert extra["wait_for_selector"] == "#x"
        assert extra["dismiss_overlays"] is True
        assert extra["wait_for_settled"] is True

    async def test_error_translates(self) -> None:
        client = MagicMock()
        client.fetch = AsyncMock(side_effect=RuntimeError("net down"))
        with _patch_client(client):
            result = await BrowserNavigateTool().execute({"url": "https://e.com"})
        assert result.isError
        text = _err_text(result)
        assert "Navigation failed" in text
        assert "net down" in text
        assert "wait_until='networkidle'" in text  # recovery hint


# ── ClickElementTool ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestClickElementTool:
    async def test_click_success(self) -> None:
        client = MagicMock()
        client.click = AsyncMock()
        client.get_url = AsyncMock(return_value="https://e.com/after")
        with _patch_client(client):
            result = await ClickElementTool().execute({"context_id": "s1", "selector": "button#go"})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["url"] == "https://e.com/after"

    async def test_click_error_includes_recovery(self) -> None:
        client = MagicMock()
        client.click = AsyncMock(side_effect=RuntimeError("Timeout"))
        with _patch_client(client):
            result = await ClickElementTool().execute({"context_id": "s1", "selector": "#x"})
        assert result.isError
        text = _err_text(result)
        assert "Click failed" in text
        assert "kaos-web-browser-snapshot" in text  # alternative tool


# ── FillInputTool ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestFillInputTool:
    async def test_fill_success(self) -> None:
        client = MagicMock()
        client.fill = AsyncMock()
        with _patch_client(client):
            result = await FillInputTool().execute(
                {"context_id": "s1", "selector": "#in", "value": "abc"}
            )
        assert not result.isError

    async def test_fill_error(self) -> None:
        client = MagicMock()
        client.fill = AsyncMock(side_effect=RuntimeError("not found"))
        with _patch_client(client):
            result = await FillInputTool().execute(
                {"context_id": "s1", "selector": "#in", "value": "x"}
            )
        assert result.isError
        text = _err_text(result)
        assert "Fill failed" in text
        assert "kaos-web-browser-snapshot" in text


# ── TypeTextTool ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTypeTextTool:
    async def test_type_success(self) -> None:
        client = MagicMock()
        client.type_text = AsyncMock()
        with _patch_client(client):
            result = await TypeTextTool().execute(
                {"context_id": "s1", "selector": "#i", "text": "hi"}
            )
        assert not result.isError

    async def test_type_error(self) -> None:
        client = MagicMock()
        client.type_text = AsyncMock(side_effect=RuntimeError("fail"))
        with _patch_client(client):
            result = await TypeTextTool().execute(
                {"context_id": "s1", "selector": "#i", "text": "hi"}
            )
        assert result.isError
        assert "Type failed" in _err_text(result)


# ── PressKeyTool ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestPressKeyTool:
    async def test_press_success(self) -> None:
        client = MagicMock()
        client.press_key = AsyncMock()
        with _patch_client(client):
            result = await PressKeyTool().execute(
                {"context_id": "s1", "selector": "#i", "key": "Enter"}
            )
        assert not result.isError

    async def test_press_error(self) -> None:
        client = MagicMock()
        client.press_key = AsyncMock(side_effect=RuntimeError("nope"))
        with _patch_client(client):
            result = await PressKeyTool().execute(
                {"context_id": "s1", "selector": "#i", "key": "Tab"}
            )
        assert result.isError
        assert "Key press failed" in _err_text(result)


# ── SelectOptionTool ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSelectOptionTool:
    async def test_select_success(self) -> None:
        client = MagicMock()
        client.select_option = AsyncMock(return_value=["v1"])
        with _patch_client(client):
            result = await SelectOptionTool().execute(
                {"context_id": "s1", "selector": "#sel", "value": "v1"}
            )
        assert not result.isError

    async def test_select_error(self) -> None:
        client = MagicMock()
        client.select_option = AsyncMock(side_effect=RuntimeError("bad"))
        with _patch_client(client):
            result = await SelectOptionTool().execute(
                {"context_id": "s1", "selector": "#sel", "value": "v"}
            )
        assert result.isError
        assert "Select failed" in _err_text(result)


# ── ScreenshotTool ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestScreenshotTool:
    async def test_missing_context_and_url_error(self) -> None:
        result = await ScreenshotTool().execute({})
        assert result.isError
        text = _err_text(result)
        assert "context_id" in text
        assert "url" in text

    async def test_context_screenshot_inline_returns_image(self) -> None:
        client = MagicMock()
        client.screenshot_context = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
        with _patch_client(client):
            result = await ScreenshotTool().execute({"context_id": "s1"})
        assert not result.isError
        assert result.content
        # ImageContent block carries base64
        assert result.meta is not None
        assert result.meta["format"] == "png"

    async def test_oneshot_url_screenshot(self) -> None:
        client = MagicMock()
        client.screenshot = AsyncMock(return_value=b"FAKE_JPEG")
        with _patch_client(client):
            result = await ScreenshotTool().execute({"url": "https://e.com", "format": "jpeg"})
        assert not result.isError
        assert result.meta is not None
        assert result.meta["format"] == "jpeg"
        client.screenshot.assert_awaited_once()

    async def test_screenshot_artifact_storage_path(self) -> None:
        client = MagicMock()
        client.screenshot_context = AsyncMock(return_value=b"\x89PNG_BYTES")
        ctx, _ = _make_runtime_context(artifact_id="art-shot")
        with _patch_client(client):
            result = await ScreenshotTool().execute({"context_id": "s1"}, context=ctx)
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["artifact_id"] == "art-shot"
        # Verify VFS write
        ctx.get_vfs_path.return_value.write_bytes.assert_awaited_once()
        ctx.runtime.artifacts.create_from_path.assert_awaited_once()

    async def test_screenshot_failure(self) -> None:
        client = MagicMock()
        client.screenshot_context = AsyncMock(side_effect=RuntimeError("crash"))
        with _patch_client(client):
            result = await ScreenshotTool().execute({"context_id": "s1"})
        assert result.isError
        text = _err_text(result)
        assert "Screenshot failed" in text
        assert "kaos-web[browser]" in text


# ── EvaluateJSTool ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestEvaluateJSTool:
    async def test_missing_context_and_url(self) -> None:
        result = await EvaluateJSTool().execute({"expression": "1+1"})
        assert result.isError
        assert "context_id" in _err_text(result)

    async def test_evaluate_in_context_dict_result(self) -> None:
        client = MagicMock()
        client.evaluate_in_context = AsyncMock(return_value={"a": 1, "b": 2})
        with _patch_client(client):
            result = await EvaluateJSTool().execute(
                {"context_id": "s1", "expression": "({a:1, b:2})"}
            )
        assert not result.isError
        out = result.structuredContent
        assert out == {"a": 1, "b": 2}

    async def test_evaluate_oneshot_scalar_result(self) -> None:
        client = MagicMock()
        client.evaluate = AsyncMock(return_value=42)
        with _patch_client(client):
            result = await EvaluateJSTool().execute({"url": "https://e.com", "expression": "21*2"})
        assert not result.isError
        # Scalar result returned as text "42"
        assert (result.text or "") == "42"

    async def test_evaluate_error(self) -> None:
        client = MagicMock()
        client.evaluate_in_context = AsyncMock(side_effect=RuntimeError("ref"))
        with _patch_client(client):
            result = await EvaluateJSTool().execute(
                {"context_id": "s1", "expression": "undefined()"}
            )
        assert result.isError
        text = _err_text(result)
        assert "JavaScript evaluation failed" in text
        assert "non-serializable" in text  # 3-part guidance present


# ── GetSnapshotTool ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetSnapshotTool:
    async def test_snapshot_success(self) -> None:
        client = MagicMock()
        client.get_snapshot = AsyncMock(return_value='- button "OK"')
        client.get_url = AsyncMock(return_value="https://e.com/p")
        with _patch_client(client):
            result = await GetSnapshotTool().execute({"context_id": "s1"})
        assert not result.isError
        assert result.structuredContent is not None
        assert "snapshot" in result.structuredContent

    async def test_snapshot_empty_returns_helpful_success(self) -> None:
        client = MagicMock()
        client.get_snapshot = AsyncMock(return_value="")
        client.get_url = AsyncMock(return_value="https://e.com/p")
        with _patch_client(client):
            result = await GetSnapshotTool().execute({"context_id": "s1"})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert "empty" in out["message"]

    async def test_snapshot_error(self) -> None:
        client = MagicMock()
        client.get_snapshot = AsyncMock(side_effect=RuntimeError("x"))
        with _patch_client(client):
            result = await GetSnapshotTool().execute({"context_id": "s1"})
        assert result.isError
        assert "Snapshot failed" in _err_text(result)


# ── GetPageContentTool ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetPageContentTool:
    async def test_html_format_returns_raw(self) -> None:
        client = MagicMock()
        client.get_content = AsyncMock(return_value="<html><body>raw</body></html>")
        client.get_url = AsyncMock(return_value="https://e.com")
        with _patch_client(client):
            result = await GetPageContentTool().execute(
                {"context_id": "s1", "output_format": "html"}
            )
        assert not result.isError
        assert "<body>raw</body>" in (result.text or "")

    async def test_text_format_no_runtime(self) -> None:
        client = MagicMock()
        client.get_content = AsyncMock(
            return_value="<html><body><h1>T</h1><p>Body</p></body></html>"
        )
        client.get_url = AsyncMock(return_value="https://e.com")
        with _patch_client(client):
            result = await GetPageContentTool().execute(
                {"context_id": "s1", "output_format": "text"}
            )
        assert not result.isError
        assert (result.text or "") != ""

    async def test_markdown_format_no_runtime(self) -> None:
        client = MagicMock()
        client.get_content = AsyncMock(return_value="<html><body><h1>T</h1></body></html>")
        client.get_url = AsyncMock(return_value="https://e.com")
        with _patch_client(client):
            result = await GetPageContentTool().execute({"context_id": "s1"})
        assert not result.isError

    async def test_with_runtime_stores_artifact(self) -> None:
        client = MagicMock()
        client.get_content = AsyncMock(
            return_value="<html><body><h1>T</h1><p>Body content here.</p></body></html>"
        )
        client.get_url = AsyncMock(return_value="https://e.com")
        ctx, manifest = _make_runtime_context(artifact_id="art-page")
        with (
            _patch_client(client),
            patch("kaos_content.artifacts.store_document", AsyncMock(return_value=manifest)),
        ):
            result = await GetPageContentTool().execute({"context_id": "s1"}, context=ctx)
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["artifact_id"] == "art-page"

    async def test_error(self) -> None:
        client = MagicMock()
        client.get_content = AsyncMock(side_effect=RuntimeError("dead"))
        with _patch_client(client):
            result = await GetPageContentTool().execute({"context_id": "s1"})
        assert result.isError
        assert "Content extraction failed" in _err_text(result)


# ── GetCookiesTool ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetCookiesTool:
    async def test_get_cookies_with_url_filter(self) -> None:
        client = MagicMock()
        client.get_cookies = AsyncMock(
            return_value=[
                {
                    "name": "a",
                    "value": "1",
                    "domain": ".e.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "sameSite": "Lax",
                }
            ]
        )
        with _patch_client(client):
            result = await GetCookiesTool().execute(
                {"context_id": "s1", "urls": "https://e.com,https://other.com"}
            )
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["cookie_count"] == 1
        # urls split + passed
        client.get_cookies.assert_awaited_once()
        await_args = client.get_cookies.await_args
        assert await_args is not None
        assert await_args.kwargs["urls"] == ["https://e.com", "https://other.com"]

    async def test_error(self) -> None:
        client = MagicMock()
        client.get_cookies = AsyncMock(side_effect=RuntimeError("x"))
        with _patch_client(client):
            result = await GetCookiesTool().execute({"context_id": "s1"})
        assert result.isError
        assert "Failed to get cookies" in _err_text(result)


# ── SetCookieTool ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSetCookieTool:
    async def test_success_with_domain(self) -> None:
        client = MagicMock()
        client.set_cookies = AsyncMock()
        with _patch_client(client):
            result = await SetCookieTool().execute(
                {
                    "context_id": "s1",
                    "name": "k",
                    "value": "v",
                    "domain": ".e.com",
                    "secure": True,
                    "httpOnly": True,
                }
            )
        assert not result.isError
        # Cookie should include domain (not url)
        await_args = client.set_cookies.await_args
        assert await_args is not None
        call_arg = await_args.args[1][0]
        assert call_arg["domain"] == ".e.com"
        assert "url" not in call_arg

    async def test_success_with_url(self) -> None:
        client = MagicMock()
        client.set_cookies = AsyncMock()
        with _patch_client(client):
            result = await SetCookieTool().execute(
                {
                    "context_id": "s1",
                    "name": "k",
                    "value": "v",
                    "url": "https://e.com",
                }
            )
        assert not result.isError
        await_args = client.set_cookies.await_args
        assert await_args is not None
        call_arg = await_args.args[1][0]
        assert call_arg["url"] == "https://e.com"

    async def test_set_cookie_failure(self) -> None:
        client = MagicMock()
        client.set_cookies = AsyncMock(side_effect=RuntimeError("nope"))
        with _patch_client(client):
            result = await SetCookieTool().execute(
                {"context_id": "s1", "name": "k", "value": "v", "url": "https://e.com"}
            )
        assert result.isError
        assert "Failed to set cookie" in _err_text(result)


# ── SaveAuthStateTool ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestSaveAuthStateTool:
    """WEB5-004: SaveAuthStateTool no longer accepts a caller-supplied
    ``path`` (path-traversal / arbitrary-write fix). The storage state
    is captured in-memory via ``BrowserClient.get_storage_state`` and
    persisted as a session-scoped artifact via
    ``KaosContext.runtime.artifacts``.
    """

    async def test_no_path_param_in_input_schema(self) -> None:
        # Hardens against accidental re-introduction of the path arg.
        tool = SaveAuthStateTool()
        param_names = {p.name for p in tool.metadata.input_schema}
        assert "path" not in param_names
        # context_id stays; optional 'name' replaces 'path'.
        assert "context_id" in param_names
        assert "name" in param_names

    async def test_save_persists_as_artifact(self) -> None:
        client = MagicMock()
        client.get_storage_state = AsyncMock(
            return_value={
                "cookies": [{"name": "sid", "value": "abc"}],
                "origins": [],
            }
        )
        ctx, _manifest = _make_runtime_context(
            artifact_id="art-auth", body_uri="kaos://artifacts/art-auth/body"
        )
        with _patch_client(client):
            result = await SaveAuthStateTool().execute({"context_id": "s1"}, context=ctx)
        assert not result.isError
        # Manifest fields surfaced in the structured output.
        out = result.structuredContent
        assert out is not None
        assert out["artifact_id"] == "art-auth"
        assert out["body_uri"] == "kaos://artifacts/art-auth/body"
        assert out["size_bytes"] > 0
        # Underlying client was called for the in-memory state, not for
        # any caller-supplied path. WEB5-002: session_id threaded through
        # from KaosContext.
        client.get_storage_state.assert_awaited_once_with("s1", session_id="sess-test")
        # Artifact creation was scoped to the caller's session.
        ctx.runtime.artifacts.create_from_path.assert_awaited_once()
        kwargs = ctx.runtime.artifacts.create_from_path.await_args.kwargs
        assert kwargs["session_id"] == "sess-test"
        assert kwargs["mime_type"] == "application/json"

    async def test_save_requires_runtime_context(self) -> None:
        client = MagicMock()
        client.get_storage_state = AsyncMock(return_value={})
        with _patch_client(client):
            # No runtime context provided — must refuse rather than
            # silently fall back to a filesystem path.
            result = await SaveAuthStateTool().execute({"context_id": "s1"})
        assert result.isError
        text = _err_text(result)
        assert "runtime context" in text
        # Suggests the library API as the manual escape hatch.
        assert "save_storage_state" in text
        # Did NOT touch the underlying client.
        client.get_storage_state.assert_not_called()

    async def test_save_failure_propagates_three_part_error(self) -> None:
        client = MagicMock()
        client.get_storage_state = AsyncMock(side_effect=RuntimeError("io"))
        ctx, _ = _make_runtime_context()
        with _patch_client(client):
            result = await SaveAuthStateTool().execute({"context_id": "s1"}, context=ctx)
        assert result.isError
        text = _err_text(result)
        assert "Failed to save auth state" in text
        # 3-part error contract: what + how-to-recover + alternative
        assert "kaos-web-browser-navigate" in text
        assert "save_storage_state" in text  # alternative library API

    async def test_save_uses_provided_artifact_name(self) -> None:
        client = MagicMock()
        client.get_storage_state = AsyncMock(return_value={})
        ctx, _ = _make_runtime_context()
        with _patch_client(client):
            await SaveAuthStateTool().execute(
                {"context_id": "s1", "name": "my-named-auth"}, context=ctx
            )
        kwargs = ctx.runtime.artifacts.create_from_path.await_args.kwargs
        assert kwargs["name"] == "my-named-auth"


# ── EnableRequestLoggingTool ────────────────────────────────────────


@pytest.mark.asyncio
class TestEnableRequestLoggingTool:
    async def test_enable_default_resource_types(self) -> None:
        client = MagicMock()
        client.enable_request_logging = AsyncMock()
        with _patch_client(client):
            result = await EnableRequestLoggingTool().execute({"context_id": "s1"})
        assert not result.isError

    async def test_enable_with_capture_bodies(self) -> None:
        client = MagicMock()
        client.enable_request_logging = AsyncMock()
        with _patch_client(client):
            result = await EnableRequestLoggingTool().execute(
                {
                    "context_id": "s1",
                    "capture_bodies": True,
                    "resource_types": "fetch, xhr, document",
                    "max_body_size": 2048,
                }
            )
        assert not result.isError
        # Message should mention body capture
        out = result.structuredContent
        assert out is not None
        msg = out["message"]
        assert "Body capture active" in msg
        assert "captured-responses" in msg  # follow-up tool referenced

    async def test_enable_failure(self) -> None:
        client = MagicMock()
        client.enable_request_logging = AsyncMock(side_effect=RuntimeError("x"))
        with _patch_client(client):
            result = await EnableRequestLoggingTool().execute({"context_id": "s1"})
        assert result.isError
        assert "Failed to enable logging" in _err_text(result)


# ── ListRequestsTool ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestListRequestsTool:
    async def test_lists_with_resource_filter(self) -> None:
        client = MagicMock()
        client.get_request_log = AsyncMock(
            return_value=[
                {
                    "id": 0,
                    "url": "https://e.com/api",
                    "method": "GET",
                    "resource_type": "fetch",
                    "status": 200,
                    "has_body": True,
                    "body_size": 100,
                },
                {
                    "id": 1,
                    "url": "https://e.com/img.png",
                    "method": "GET",
                    "resource_type": "image",
                    "status": 200,
                },
            ]
        )
        with _patch_client(client):
            result = await ListRequestsTool().execute(
                {"context_id": "s1", "resource_type": "fetch"}
            )
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["total_requests"] == 1
        assert out["requests"][0]["resource_type"] == "fetch"

    async def test_failure(self) -> None:
        client = MagicMock()
        client.get_request_log = AsyncMock(side_effect=RuntimeError("x"))
        with _patch_client(client):
            result = await ListRequestsTool().execute({"context_id": "s1"})
        assert result.isError
        assert "Failed to list requests" in _err_text(result)


# ── GetRequestDetailTool ────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetRequestDetailToolBodyDecoding:
    async def test_text_body_decoded_as_utf8(self) -> None:
        client = MagicMock()
        client.get_request_detail = AsyncMock(
            return_value={
                "id": 0,
                "url": "https://e.com/api",
                "method": "GET",
                "has_body": True,
            }
        )
        client.get_response_body = AsyncMock(
            return_value={
                "body": b'{"x":1}',
                "content_type": "application/json",
                "size": 7,
                "truncated": False,
            }
        )
        with _patch_client(client):
            result = await GetRequestDetailTool().execute({"context_id": "s1", "request_id": 0})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["body"] == '{"x":1}'

    async def test_undecodable_text_falls_back_to_base64(self) -> None:
        client = MagicMock()
        client.get_request_detail = AsyncMock(
            return_value={
                "id": 0,
                "url": "u",
                "method": "GET",
                "has_body": True,
            }
        )
        # Bytes that are NOT valid UTF-8 even though content type is text/*
        client.get_response_body = AsyncMock(
            return_value={
                "body": b"\xff\xfe\xff",
                "content_type": "text/html",
                "size": 3,
                "truncated": False,
            }
        )
        with _patch_client(client):
            result = await GetRequestDetailTool().execute({"context_id": "s1", "request_id": 0})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["body_encoding"] == "base64"

    async def test_binary_body_base64_encoded(self) -> None:
        client = MagicMock()
        client.get_request_detail = AsyncMock(
            return_value={
                "id": 0,
                "url": "u",
                "method": "GET",
                "has_body": True,
            }
        )
        client.get_response_body = AsyncMock(
            return_value={
                "body": b"\x89PNG\r\n",
                "content_type": "image/png",
                "size": 6,
                "truncated": False,
            }
        )
        with _patch_client(client):
            result = await GetRequestDetailTool().execute({"context_id": "s1", "request_id": 0})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["body_encoding"] == "base64"


# ── ListCapturedResponsesTool ───────────────────────────────────────


@pytest.mark.asyncio
class TestListCapturedResponsesTool:
    async def test_no_responses_returns_helpful_message(self) -> None:
        client = MagicMock()
        client.get_captured_responses = AsyncMock(return_value=[])
        with _patch_client(client):
            result = await ListCapturedResponsesTool().execute({"context_id": "s1"})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["total_captured"] == 0
        assert "capture_bodies=true" in out["message"]

    async def test_responses_listed_no_artifacts(self) -> None:
        client = MagicMock()
        client.get_captured_responses = AsyncMock(
            return_value=[
                {
                    "id": 0,
                    "url": "https://e.com/api",
                    "method": "GET",
                    "resource_type": "fetch",
                    "status": 200,
                    "content_type": "application/json",
                    "body_size": 50,
                    "truncated": False,
                }
            ]
        )
        with _patch_client(client):
            result = await ListCapturedResponsesTool().execute({"context_id": "s1"})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["total_captured"] == 1


# ── ListContextsTool ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestListContextsTool:
    async def test_lists_with_url_lookup(self) -> None:
        client = MagicMock()
        # WEB5-002: active_contexts is now a method taking session_id.
        client.active_contexts = MagicMock(return_value=["s1", "s2"])
        client.get_url = AsyncMock(side_effect=["https://a.com", "https://b.com"])
        with _patch_client(client):
            result = await ListContextsTool().execute({})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["active_count"] == 2

    async def test_get_url_failure_falls_back_to_unknown(self) -> None:
        client = MagicMock()
        client.active_contexts = MagicMock(return_value=["s1"])
        client.get_url = AsyncMock(side_effect=RuntimeError("dropped"))
        with _patch_client(client):
            result = await ListContextsTool().execute({})
        assert not result.isError
        out = result.structuredContent
        assert out is not None
        assert out["contexts"][0]["url"] == "(unknown)"

    async def test_outer_failure(self) -> None:
        # Inject a client whose .active_contexts call raises
        class _Bad:
            def active_contexts(self, session_id: str = "") -> list[str]:
                raise RuntimeError("disconnected")

        with _patch_client(_Bad()):
            result = await ListContextsTool().execute({})
        assert result.isError
        text = _err_text(result)
        assert "Failed to list browser contexts" in text


# ── CloseContextTool ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestCloseContextTool:
    async def test_close_success(self) -> None:
        client = MagicMock()
        # WEB5-002: active_contexts is now a method taking session_id.
        client.active_contexts = MagicMock(return_value=["s1"])
        client.close_context = AsyncMock()
        with _patch_client(client):
            result = await CloseContextTool().execute({"context_id": "s1"})
        assert not result.isError

    async def test_close_failure_translates(self) -> None:
        client = MagicMock()
        client.active_contexts = MagicMock(return_value=["s1"])
        client.close_context = AsyncMock(side_effect=RuntimeError("disconnected"))
        with _patch_client(client):
            result = await CloseContextTool().execute({"context_id": "s1"})
        assert result.isError
        text = _err_text(result)
        assert "Failed to close" in text
        assert "kaos-web-browser-list-contexts" in text


# ── register_browser_tools ──────────────────────────────────────────


class TestRegisterBrowserToolsCount:
    def test_register_count_is_19(self) -> None:
        runtime = MagicMock()
        runtime.tools = MagicMock()
        runtime.tools.register_tool = MagicMock()
        count = register_browser_tools(runtime)
        assert count == 19
        assert runtime.tools.register_tool.call_count == 19
