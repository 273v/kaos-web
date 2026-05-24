"""Unit tests for batch fetch."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.discover.batch import (
    BatchError,
    BatchResult,
    _resolve_use_browser,
    batch_fetch,
)
from kaos_web.models import WebResponse


class TestBatchResult:
    def test_empty(self):
        r = BatchResult()
        assert r.total == 0
        assert r.succeeded == 0
        assert r.failed == 0

    def test_with_responses(self):
        r = BatchResult(
            responses=[
                WebResponse(url="https://a.com", status_code=200),
                WebResponse(url="https://b.com", status_code=200),
            ],
            errors=[BatchError(url="https://c.com", error="timeout")],
        )
        assert r.total == 3
        assert r.succeeded == 2
        assert r.failed == 1


class TestBatchFetch:
    @pytest.mark.asyncio
    async def test_empty_urls(self):
        result = await batch_fetch([])
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_single_url(self):
        resp = WebResponse(url="https://example.com", status_code=200, html="<html></html>")
        with patch("kaos_web.discover.batch.HttpClient") as MockClient:
            instance = AsyncMock()
            instance.fetch = AsyncMock(return_value=resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await batch_fetch(["https://example.com"], use_browser=False)
            assert result.succeeded == 1
            assert result.failed == 0

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        call_count = 0

        async def mock_fetch(request):
            nonlocal call_count
            call_count += 1
            if "fail" in request.url:
                raise ConnectionError("Network error")
            return WebResponse(url=request.url, status_code=200, html="<html></html>")

        with patch("kaos_web.discover.batch.HttpClient") as MockClient:
            instance = AsyncMock()
            instance.fetch = mock_fetch
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await batch_fetch(
                [
                    "https://example.com/ok1",
                    "https://example.com/fail",
                    "https://example.com/ok2",
                ],
                use_browser=False,
            )
            assert result.succeeded == 2
            assert result.failed == 1
            assert result.errors[0].url == "https://example.com/fail"

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        """Verify semaphore limits concurrent requests."""
        max_concurrent = 0
        current_concurrent = 0
        import asyncio

        lock = asyncio.Lock()

        async def mock_fetch(request):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return WebResponse(url=request.url, status_code=200, html="ok")

        with patch("kaos_web.discover.batch.HttpClient") as MockClient:
            instance = AsyncMock()
            instance.fetch = mock_fetch
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            urls = [f"https://example.com/{i}" for i in range(10)]
            await batch_fetch(urls, concurrency=3, use_browser=False)
            assert max_concurrent <= 3

    @pytest.mark.asyncio
    async def test_elapsed_ms_tracked(self):
        resp = WebResponse(url="https://example.com", status_code=200, html="ok")
        with patch("kaos_web.discover.batch.HttpClient") as MockClient:
            instance = AsyncMock()
            instance.fetch = AsyncMock(return_value=resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await batch_fetch(["https://example.com"], use_browser=False)
            assert result.elapsed_ms > 0


# ── 2026-05-24: Playwright-default routing (task #634) ──


class TestResolveUseBrowser:
    """``_resolve_use_browser`` mirrors ``_fetch_html``'s routing contract."""

    def test_explicit_true_returns_true(self) -> None:
        assert _resolve_use_browser(True) is True

    def test_explicit_false_returns_false(self) -> None:
        assert _resolve_use_browser(False) is False

    def test_none_with_playwright_installed_returns_true(self) -> None:
        # Inject a fake playwright module so the importlib probe succeeds
        # without requiring the [browser] extra in the dev venv.
        with patch.dict(sys.modules, {"playwright": MagicMock()}):
            assert _resolve_use_browser(None) is True

    def test_none_without_playwright_returns_false(self) -> None:
        # Force ImportError by removing playwright from sys.modules and
        # blocking the import via builtins. The simplest equivalent is to
        # stub the module to a sentinel that breaks ``import playwright``:
        # patch the resolver to see a missing module by temporarily
        # removing it from sys.modules and short-circuiting __import__.
        real_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        def _block_playwright(name, *args, **kwargs):
            if name == "playwright" or name.startswith("playwright."):
                raise ImportError("no playwright in test env")
            return real_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {}, clear=False):
            sys.modules.pop("playwright", None)
            with patch("builtins.__import__", _block_playwright):
                assert _resolve_use_browser(None) is False


class TestBatchFetchUseBrowserRouting:
    """``batch_fetch`` routes through BrowserClient vs HttpClient per use_browser."""

    @pytest.mark.asyncio
    async def test_explicit_true_uses_browser_client(self) -> None:
        resp = WebResponse(url="https://example.com", status_code=200, html="ok")
        fake_browser_mod = MagicMock()
        FakeBrowserClient = MagicMock()
        fake_instance = AsyncMock()
        fake_instance.fetch = AsyncMock(return_value=resp)
        fake_instance.__aenter__ = AsyncMock(return_value=fake_instance)
        fake_instance.__aexit__ = AsyncMock(return_value=False)
        FakeBrowserClient.return_value = fake_instance
        fake_browser_mod.BrowserClient = FakeBrowserClient

        with patch.dict(sys.modules, {"kaos_web.clients.browser": fake_browser_mod}):
            result = await batch_fetch(["https://example.com"], use_browser=True)
        assert result.succeeded == 1
        assert FakeBrowserClient.called

    @pytest.mark.asyncio
    async def test_explicit_false_uses_http_client(self) -> None:
        resp = WebResponse(url="https://example.com", status_code=200, html="ok")
        with patch("kaos_web.discover.batch.HttpClient") as MockHttp:
            instance = AsyncMock()
            instance.fetch = AsyncMock(return_value=resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockHttp.return_value = instance

            result = await batch_fetch(["https://example.com"], use_browser=False)
        assert result.succeeded == 1
        assert MockHttp.called

    @pytest.mark.asyncio
    async def test_browser_import_error_falls_back_to_httpx(self) -> None:
        """ImportError on the browser path must not abort — degrade to httpx."""
        resp = WebResponse(url="https://example.com", status_code=200, html="ok")

        def _fail_browser(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "kaos_web.clients.browser":
                raise ImportError("playwright extra missing")
            return real_import(name, globals, locals, fromlist, level)

        real_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        with (
            patch("kaos_web.discover.batch.HttpClient") as MockHttp,
            patch("builtins.__import__", _fail_browser),
        ):
            instance = AsyncMock()
            instance.fetch = AsyncMock(return_value=resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockHttp.return_value = instance

            result = await batch_fetch(["https://example.com"], use_browser=True)
        assert result.succeeded == 1
        assert MockHttp.called  # httpx fallback fired
