"""Shared fixtures for the bounded unit tier.

Anything autouse here applies to every test under ``tests/unit/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _block_real_playwright_launch() -> Iterator[None]:
    """Hard-fail any unit test that reaches a real Playwright launch.

    Why: ``BrowserClient._ensure_browser()`` calls
    ``playwright.async_api.async_playwright().start()`` and ``.launch()``
    when ``self._browser`` is unset. Several tests (in
    ``test_response_capture.py`` historically — see audit-01 WEB-001 fix
    in commit 63915dd) drove ``client.fetch()`` paths that route through
    ``_ensure_browser()``. With ``_browser`` unset they would launch real
    Chromium when the dev box happened to have it installed, and would
    fail anywhere else.

    This guard short-circuits the launcher with a self-explaining
    AssertionError so any regression is loud and obvious. Tests that need
    a "browser-like" client should seed ``client._browser = MagicMock()``
    in their setup helper.

    Promoted from ``tests/unit/test_response_capture.py:25-43`` to
    ``tests/unit/conftest.py`` per audit-03 WEB3-003 so the autouse guard
    covers the whole unit tier, not just the response-capture module.
    """
    with patch(
        "playwright.async_api.async_playwright",
        side_effect=AssertionError(
            "unit test reached real playwright.async_api.async_playwright(); "
            "seed BrowserClient._browser with a MagicMock in test setup"
        ),
    ):
        yield
