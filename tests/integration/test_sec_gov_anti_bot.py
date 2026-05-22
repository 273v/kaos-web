"""Live integration tests for SEC.gov anti-bot UA routing.

Closes kaos-modules #444 — the 2026-05-22 237-session audit found
that ``kaos-web-fetch-page`` against SEC.gov returned 403 for the
randomized Chrome UA (21 tool failures across multiple sessions).

These tests verify the per-domain UA routing in
``kaos_web.clients.user_agents.pick_user_agent_for_url`` correctly
routes SEC.gov to ``KAOS_BOT_UA`` and that ``HttpClient`` succeeds
end-to-end against a real SEC.gov URL.

Run with: ``pytest tests/integration/test_sec_gov_anti_bot.py -v``
"""

from __future__ import annotations

import pytest

from kaos_web.clients.http import HttpClient
from kaos_web.models import WebRequest

pytestmark = pytest.mark.integration

# A stable, non-rate-limited SEC.gov resource. The IA-numbered press
# releases route is the exact path the audit saw failing under
# ``kaos-haiku-4-5`` and ``gpt-5.4-mini``.
SEC_URL = "https://www.sec.gov/newsroom/press-releases"
EDGAR_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-K&dateb=&owner=include&count=10"


class TestSECGovAntiBot:
    """SEC.gov returns 403 for Chrome UAs. Verify our routing fixes it."""

    @pytest.mark.asyncio
    async def test_sec_gov_root_press_releases_succeeds(self) -> None:
        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url=SEC_URL))
        assert resp.status_code == 200, (
            f"SEC.gov press-releases should return 200 with KAOS_BOT_UA "
            f"routing; got {resp.status_code}. Body preview: {resp.html[:200]!r}"
        )
        # Sanity-check it's an actual SEC page, not a captcha
        assert "SEC" in resp.html or "Securities" in resp.html

    @pytest.mark.asyncio
    async def test_edgar_company_filings_succeeds(self) -> None:
        """EDGAR's CGI route — historically the strictest SEC.gov path."""
        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url=EDGAR_URL))
        assert resp.status_code == 200, (
            f"EDGAR company-filings should return 200; got "
            f"{resp.status_code}. SEC's anti-bot probably rejected the "
            f"User-Agent."
        )
        # EDGAR pages reference the company we asked for (Apple, CIK 320193)
        assert "APPLE" in resp.html.upper() or "320193" in resp.html

    @pytest.mark.asyncio
    async def test_caller_supplied_ua_is_not_clobbered(self) -> None:
        """Per-domain routing must respect caller's explicit User-Agent."""
        custom_ua = "TestSuite/1.0 (regression test)"
        async with HttpClient() as client:
            resp = await client.fetch(
                WebRequest(
                    url="https://httpbin.org/headers",
                    headers={"User-Agent": custom_ua},
                )
            )
        assert resp.status_code == 200
        # httpbin echoes back what it received
        assert custom_ua in resp.html, (
            "Caller-supplied User-Agent must override the per-domain "
            "routing — never clobber an explicit header."
        )
