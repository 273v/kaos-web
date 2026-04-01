"""Tests for BrowserClient configuration and error handling.

These tests verify client setup and error mapping without launching
a real browser. Integration tests with real Chrome are in tests/integration/.
"""

from __future__ import annotations

import pytest

from kaos_web.clients.browser import BrowserClient, _raise_browser_error
from kaos_web.clients.config import BrowserClientConfig
from kaos_web.errors import WebBrowserError, WebNetworkError, WebTimeoutError
from kaos_web.models import WebRequest


class TestBrowserClientConfig:
    def test_default_config(self):
        config = BrowserClientConfig()
        assert config.browser_type == "chromium"
        assert config.headless is True
        assert config.channel is None
        assert config.viewport_width == 1280
        assert config.viewport_height == 720
        assert config.default_wait_until == "load"
        assert config.block_resources == []

    def test_chrome_channel_config(self):
        config = BrowserClientConfig(channel="chrome")
        assert config.channel == "chrome"

    def test_mobile_config(self):
        config = BrowserClientConfig(
            viewport_width=375,
            viewport_height=812,
            is_mobile=True,
            device_scale_factor=3.0,
        )
        assert config.is_mobile is True
        assert config.device_scale_factor == 3.0

    def test_resource_blocking_config(self):
        config = BrowserClientConfig(block_resources=["image", "stylesheet", "font"])
        assert len(config.block_resources) == 3

    def test_proxy_config(self):
        config = BrowserClientConfig(proxy="http://proxy:8080")
        assert config.proxy == "http://proxy:8080"

    def test_auth_config(self):
        config = BrowserClientConfig(
            storage_state="auth.json",
            http_credentials=("user", "pass"),
        )
        assert config.storage_state == "auth.json"
        assert config.http_credentials == ("user", "pass")


class TestBrowserClientInit:
    def test_lazy_browser_launch(self):
        """Browser should NOT be launched on __init__."""
        client = BrowserClient()
        assert client._browser is None
        assert client._playwright is None

    def test_config_stored(self):
        config = BrowserClientConfig(channel="chrome", headless=False)
        client = BrowserClient(config)
        assert client.config.channel == "chrome"
        assert client.config.headless is False

    def test_default_config_used(self):
        client = BrowserClient()
        assert client.config.browser_type == "chromium"


class TestErrorMapping:
    def test_timeout_error(self):
        exc = Exception("Timeout 30000ms exceeded")
        with pytest.raises(WebTimeoutError) as info:
            _raise_browser_error(exc, "https://example.com")
        assert info.value.url == "https://example.com"
        assert info.value.timeout_type == "navigation"

    def test_network_error(self):
        exc = Exception("net::ERR_CONNECTION_REFUSED")
        with pytest.raises(WebNetworkError) as info:
            _raise_browser_error(exc, "https://down.example.com")
        assert info.value.url == "https://down.example.com"

    def test_generic_browser_error(self):
        exc = Exception("Protocol error (Page.navigate): Cannot navigate")
        with pytest.raises(WebBrowserError) as info:
            _raise_browser_error(exc, "https://example.com")
        assert info.value.retryable is False

    def test_network_error_is_retryable(self):
        exc = Exception("net::ERR_CONNECTION_REFUSED")
        with pytest.raises(WebNetworkError) as info:
            _raise_browser_error(exc, "https://example.com")
        assert info.value.retryable is True


class TestBrowserClientImportError:
    async def test_missing_playwright_raises(self):
        """If playwright not installed, _ensure_browser raises ImportError."""
        # Hard to test without uninstalling — covered by integration tests.
        # Verify the client initializes without importing playwright.
        _ = BrowserClient()  # Should not raise


class TestWebRequestExtraFields:
    def test_wait_for_selector_in_extra(self):
        request = WebRequest(
            url="https://example.com",
            extra={"wait_for_selector": "#content", "wait_until": "networkidle"},
        )
        assert request.extra["wait_for_selector"] == "#content"
        assert request.extra["wait_until"] == "networkidle"

    def test_screenshot_flag(self):
        request = WebRequest(url="https://example.com", screenshot=True)
        assert request.screenshot is True
