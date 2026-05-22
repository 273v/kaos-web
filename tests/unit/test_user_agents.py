"""Unit tests for per-domain UA routing (closes kaos-modules #444)."""

from __future__ import annotations

import pytest

from kaos_web.clients.user_agents import (
    BOT_FRIENDLY_HOSTS,
    KAOS_BOT_UA,
    _host_matches_bot_friendly,
    pick_user_agent_for_url,
    random_desktop_ua,
)


class TestHostMatching:
    """The suffix-with-boundary matcher used by per-domain UA routing."""

    @pytest.mark.parametrize(
        "host",
        [
            "sec.gov",
            "www.sec.gov",
            "efts.sec.gov",
            "data.sec.gov",
            "GOVINFO.GOV",  # case-insensitive
            "www.federalregister.gov",
            "ecfr.gov",
            "www.congress.gov",
        ],
    )
    def test_bot_friendly_hosts_match(self, host: str) -> None:
        assert _host_matches_bot_friendly(host), f"{host!r} should match"

    @pytest.mark.parametrize(
        "host",
        [
            "",
            "example.com",
            "wikipedia.org",
            "not-sec.gov",  # boundary check — must NOT match sec.gov
            "fakesec.gov",  # ditto
            "sec.gov.evil.com",  # suffix attack
        ],
    )
    def test_unrelated_hosts_do_not_match(self, host: str) -> None:
        assert not _host_matches_bot_friendly(host), f"{host!r} should NOT match"


class TestPickUserAgent:
    """``pick_user_agent_for_url`` is the public API the HTTP client calls."""

    def test_sec_gov_gets_bot_ua(self) -> None:
        ua = pick_user_agent_for_url("https://www.sec.gov/newsroom/press-releases/2026-34")
        assert ua == KAOS_BOT_UA

    def test_govinfo_gets_bot_ua(self) -> None:
        ua = pick_user_agent_for_url("https://api.govinfo.gov/collections")
        assert ua == KAOS_BOT_UA

    def test_consumer_site_gets_realistic_ua(self) -> None:
        ua = pick_user_agent_for_url(
            "https://en.wikipedia.org/wiki/Constitution_of_the_United_States"
        )
        # Random desktop UA looks like a real browser.
        assert ua != KAOS_BOT_UA
        assert "Mozilla" in ua

    def test_default_random_false_falls_back_to_bot(self) -> None:
        ua = pick_user_agent_for_url(
            "https://en.wikipedia.org",
            default_random=False,
        )
        assert ua == KAOS_BOT_UA

    def test_unparseable_url_falls_back_safely(self) -> None:
        # Malformed input must not raise — the HTTP client calls this
        # in the hot path; an exception here would crash the fetch
        # before validate_url could give a clean error.
        ua = pick_user_agent_for_url("not-a-url-at-all")
        assert ua  # any non-empty string


class TestRandomDesktopUA:
    """The pre-existing helper still works."""

    def test_returns_realistic_string(self) -> None:
        ua = random_desktop_ua()
        assert "Mozilla" in ua
        assert ua != KAOS_BOT_UA


def test_bot_friendly_hosts_is_frozen() -> None:
    """Guards against accidental mutation of the global set."""
    assert isinstance(BOT_FRIENDLY_HOSTS, frozenset)
    # Sanity: every entry is lowercase + has no leading dot
    for h in BOT_FRIENDLY_HOSTS:
        assert h == h.lower()
        assert not h.startswith(".")
