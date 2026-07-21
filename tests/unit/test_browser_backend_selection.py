"""Unit tests for the pluggable browser backend (playwright | rustwright).

Exercise the selection helpers on ``BrowserClient`` WITHOUT launching a real
browser: backend resolution (config -> settings -> default), the
``async_playwright`` import branch per backend, and Chromium-executable
resolution for the rustwright backend. Launch itself is guarded by the autouse
``_block_real_playwright_launch`` fixture in ``conftest.py``; these tests never
reach it (they only touch the pure resolution/import helpers).
"""

from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from kaos_web.clients.browser import BrowserClient
from kaos_web.clients.config import BrowserClientConfig


class TestBackendConfig:
    def test_default_backend_is_none(self) -> None:
        # None means "resolve from settings, default playwright".
        assert BrowserClientConfig().backend is None

    @pytest.mark.parametrize("backend", ["playwright", "rustwright"])
    def test_backend_accepts_valid(self, backend: str) -> None:
        assert BrowserClientConfig(backend=backend).backend == backend  # ty: ignore[invalid-argument-type]

    def test_backend_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            BrowserClientConfig(backend="selenium")  # ty: ignore[invalid-argument-type]


class TestResolveBackend:
    def test_explicit_config_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Even with the env pointing elsewhere, an explicit config value wins.
        monkeypatch.setenv("KAOS_WEB_BROWSER_BACKEND", "playwright")
        client = BrowserClient(BrowserClientConfig(backend="rustwright"))
        assert client._resolve_backend() == "rustwright"

    def test_none_falls_back_to_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_WEB_BROWSER_BACKEND", "rustwright")
        client = BrowserClient(BrowserClientConfig(backend=None))
        assert client._resolve_backend() == "rustwright"

    def test_none_defaults_to_playwright(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAOS_WEB_BROWSER_BACKEND", raising=False)
        client = BrowserClient(BrowserClientConfig(backend=None))
        assert client._resolve_backend() == "playwright"


class TestImportBackend:
    def test_playwright_entrypoint(self) -> None:
        from playwright.async_api import async_playwright

        assert BrowserClient()._import_async_playwright("playwright") is async_playwright

    def test_rustwright_entrypoint(self) -> None:
        from rustwright.async_api import async_playwright

        assert BrowserClient()._import_async_playwright("rustwright") is async_playwright

    def test_missing_rustwright_gives_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate the [browser-rust] extra not being installed.
        monkeypatch.setitem(sys.modules, "rustwright.async_api", None)
        with pytest.raises(ImportError, match=r"browser-rust"):
            BrowserClient()._import_async_playwright("rustwright")


class TestResolveChromiumExecutable:
    def test_explicit_config_path_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUSTWRIGHT_CHROMIUM", "/env/chrome")
        client = BrowserClient(BrowserClientConfig(executable_path="/opt/chrome"))
        assert client._resolve_chromium_executable() == "/opt/chrome"

    def test_env_var_used_when_no_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUSTWRIGHT_CHROMIUM", "/env/chrome")
        assert BrowserClient()._resolve_chromium_executable() == "/env/chrome"

    def test_path_lookup_when_no_config_or_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RUSTWRIGHT_CHROMIUM", raising=False)
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/usr/bin/chromium" if name == "chromium" else None,
        )
        assert BrowserClient()._resolve_chromium_executable() == "/usr/bin/chromium"

    def test_none_when_nothing_discoverable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # None lets rustwright surface its own actionable executable error.
        monkeypatch.delenv("RUSTWRIGHT_CHROMIUM", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert BrowserClient()._resolve_chromium_executable() is None
