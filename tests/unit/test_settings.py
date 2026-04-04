"""Tests for KaosWebSettings typed settings layer."""

from __future__ import annotations

import pytest

from kaos_web.settings import KaosWebSettings


class TestKaosWebSettingsDefaults:
    def test_defaults(self) -> None:
        s = KaosWebSettings()
        assert s.browser_type == "chromium"
        assert s.browser_headless is True
        assert s.browser_channel is None
        assert s.browser_auto_detect_channel is True
        assert s.search_backend == ""
        assert s.serpapi_api_key is None
        assert s.exa_api_key is None
        assert s.brave_api_key is None


class TestKaosWebSettingsNewEnvVars:
    def test_new_prefix_browser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_WEB_BROWSER_TYPE", "firefox")
        monkeypatch.setenv("KAOS_WEB_BROWSER_HEADLESS", "false")
        monkeypatch.setenv("KAOS_WEB_BROWSER_CHANNEL", "firefox")
        s = KaosWebSettings()
        assert s.browser_type == "firefox"
        assert s.browser_headless is False
        assert s.browser_channel == "firefox"

    def test_new_prefix_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_WEB_SEARCH_BACKEND", "brave")
        monkeypatch.setenv("KAOS_WEB_BRAVE_API_KEY", "key-123")
        s = KaosWebSettings()
        assert s.search_backend == "brave"
        assert s.brave_api_key is not None
        assert s.brave_api_key.get_secret_value() == "key-123"


class TestKaosWebSettingsLegacyEnvVars:
    def test_legacy_browser_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_BROWSER_CHANNEL", "chrome")
        s = KaosWebSettings()
        assert s.browser_channel == "chrome"

    def test_legacy_browser_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_BROWSER_TYPE", "webkit")
        s = KaosWebSettings()
        assert s.browser_type == "webkit"

    def test_legacy_browser_headless(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_BROWSER_HEADLESS", "false")
        s = KaosWebSettings()
        assert s.browser_headless is False

    def test_legacy_search_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SEARCH_BACKEND", "exa")
        s = KaosWebSettings()
        assert s.search_backend == "exa"

    def test_legacy_serpapi_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERPAPI_API_KEY", "sk-test-123")
        s = KaosWebSettings()
        assert s.serpapi_api_key is not None
        assert s.serpapi_api_key.get_secret_value() == "sk-test-123"

    def test_legacy_exa_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
        s = KaosWebSettings()
        assert s.exa_api_key is not None
        assert s.exa_api_key.get_secret_value() == "exa-test-key"

    def test_legacy_brave_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRAVE_API_KEY", "brave-test-key")
        s = KaosWebSettings()
        assert s.brave_api_key is not None
        assert s.brave_api_key.get_secret_value() == "brave-test-key"


class TestKaosWebSettingsNewOverridesLegacy:
    def test_new_prefix_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_WEB_BROWSER_CHANNEL", "new-channel")
        monkeypatch.setenv("KAOS_BROWSER_CHANNEL", "legacy-channel")
        s = KaosWebSettings()
        # New prefix takes priority (set by pydantic-settings before validator runs)
        assert s.browser_channel == "new-channel"


class TestKaosWebSettingsSecretMasking:
    def test_secret_not_in_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_WEB_SERPAPI_API_KEY", "super-secret-key")
        s = KaosWebSettings()
        assert "super-secret-key" not in repr(s)
        assert "super-secret-key" not in str(s.model_dump())


class TestToBrowserConfig:
    def test_default_config(self) -> None:
        s = KaosWebSettings(browser_auto_detect_channel=False)
        config = s.to_browser_config()
        assert config.browser_type == "chromium"
        assert config.headless is True
        assert config.channel is None

    def test_explicit_channel(self) -> None:
        s = KaosWebSettings(browser_channel="firefox", browser_auto_detect_channel=False)
        config = s.to_browser_config()
        assert config.channel == "firefox"

    def test_auto_channel_maps_to_none(self) -> None:
        s = KaosWebSettings(browser_channel="auto", browser_auto_detect_channel=False)
        config = s.to_browser_config()
        assert config.channel is None


class TestGetSearchApiKey:
    def test_returns_none_when_not_set(self) -> None:
        s = KaosWebSettings()
        assert s.get_search_api_key("serpapi") is None

    def test_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_WEB_SERPAPI_API_KEY", "sk-123")
        s = KaosWebSettings()
        assert s.get_search_api_key("serpapi") == "sk-123"

    def test_unknown_backend(self) -> None:
        s = KaosWebSettings()
        assert s.get_search_api_key("unknown") is None


class TestDetectSearchBackend:
    def test_no_keys_returns_duckduckgo(self) -> None:
        s = KaosWebSettings()
        assert s.detect_search_backend() == "duckduckgo"

    def test_serpapi_key_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERPAPI_API_KEY", "key")
        s = KaosWebSettings()
        assert s.detect_search_backend() == "serpapi"

    def test_exa_key_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXA_API_KEY", "key")
        s = KaosWebSettings()
        assert s.detect_search_backend() == "exa"

    def test_brave_key_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRAVE_API_KEY", "key")
        s = KaosWebSettings()
        assert s.detect_search_backend() == "brave"

    def test_priority_serpapi_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERPAPI_API_KEY", "key1")
        monkeypatch.setenv("BRAVE_API_KEY", "key2")
        s = KaosWebSettings()
        assert s.detect_search_backend() == "serpapi"
