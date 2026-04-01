# ruff: noqa: E501
"""Realistic browser user-agent strings for HTTP requests.

Provides a pool of current Chrome/Firefox/Safari user agents across
desktop and mobile platforms. Updated for 2026.
"""

from __future__ import annotations

import random

# Chrome desktop (Windows, macOS, Linux) — versions 140-146
_CHROME_DESKTOP = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
]

# Firefox desktop
_FIREFOX_DESKTOP = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:138.0) Gecko/20100101 Firefox/138.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) Gecko/20100101 Firefox/138.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
]

# Safari desktop
_SAFARI_DESKTOP = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
]

# Chrome mobile
_CHROME_MOBILE = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/146.0.0.0 Mobile/15E148 Safari/604.1",
]

# All desktop UAs
DESKTOP_USER_AGENTS = _CHROME_DESKTOP + _FIREFOX_DESKTOP + _SAFARI_DESKTOP

# All mobile UAs
MOBILE_USER_AGENTS = _CHROME_MOBILE

# All UAs
ALL_USER_AGENTS = DESKTOP_USER_AGENTS + MOBILE_USER_AGENTS

# KAOS bot UA (for when you want to identify as a bot)
KAOS_BOT_UA = "KAOS-Web/0.1 (+https://273ventures.com/kaos-web)"


def random_desktop_ua() -> str:
    """Return a random realistic desktop browser user-agent."""
    return random.choice(DESKTOP_USER_AGENTS)


def random_mobile_ua() -> str:
    """Return a random realistic mobile browser user-agent."""
    return random.choice(MOBILE_USER_AGENTS)


def random_ua() -> str:
    """Return a random realistic browser user-agent (desktop or mobile)."""
    return random.choice(ALL_USER_AGENTS)
