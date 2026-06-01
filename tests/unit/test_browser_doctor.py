"""Tests for the browser preflight diagnostic (`browser_doctor`).

The diagnostic turns the silent runtime failure — Playwright's bundled
Chromium having no build for the host OS (e.g. Ubuntu 26.04, where
`playwright install` itself refuses) — into an explicit, never-raising
report. These tests respect the autouse `_block_real_playwright_launch`
guard: the blocked-launch path is itself the failure case under test, and
the success path patches `async_playwright` with a working mock chain.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.clients.browser import BrowserDoctorReport, browser_doctor
from kaos_web.clients.config import BrowserClientConfig


def test_report_ok_property() -> None:
    ok = BrowserDoctorReport("ok", True, "chrome", True, "launched", None)
    bad = BrowserDoctorReport("unavailable", True, None, False, "nope", "do X")
    assert ok.ok is True
    assert bad.ok is False


@pytest.mark.asyncio
async def test_never_raises_and_reports_unavailable_when_launch_blocked() -> None:
    """Under the autouse guard, `async_playwright()` raises — the doctor
    must catch it and return a graceful, actionable report, never raise."""
    report = await browser_doctor()
    assert isinstance(report, BrowserDoctorReport)
    assert report.status == "unavailable"
    assert report.ok is False
    assert report.launched is False
    assert report.remedy  # actionable remedy present, not None/empty


@pytest.mark.asyncio
async def test_reports_unavailable_when_extra_missing() -> None:
    """No `playwright` import → extra_installed False + install remedy.

    A ``None`` entry in ``sys.modules`` makes ``from playwright.async_api
    import …`` raise ``ImportError`` — the missing-[browser]-extra case —
    without touching the real import machinery."""
    import sys

    with patch.dict(sys.modules, {"playwright.async_api": None}):
        report = await browser_doctor()
    assert report.extra_installed is False
    assert report.status == "unavailable"
    assert "kaos-web[browser]" in (report.remedy or "")


@pytest.mark.asyncio
async def test_reports_ok_when_launch_succeeds() -> None:
    """A working launch chain → status ok, channel reflected, no remedy."""
    page = AsyncMock()
    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)
    browser.close = AsyncMock()
    pw = MagicMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    pw.stop = AsyncMock()
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)

    with patch("playwright.async_api.async_playwright", return_value=starter):
        report = await browser_doctor(BrowserClientConfig(channel="chrome"))

    assert report.ok is True
    assert report.effective_channel == "chrome"
    assert report.launched is True
    assert report.remedy is None
    pw.chromium.launch.assert_awaited_once()
    pw.stop.assert_awaited_once()  # cleanup ran
