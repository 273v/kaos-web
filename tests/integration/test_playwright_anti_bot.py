"""Live integration tests for the Playwright-first anti-bot stack.

Ports the production-validated context configuration from
``kelvin-legal-intelligence/kelvin_firm_db/services/browser/collector.py``
into kaos-web's ``BrowserClient`` defaults — rotated desktop UA,
1365x768 viewport, en-US locale, America/New_York timezone, full
sec-ch-ua / sec-fetch / Accept header set. Verifies that
``_fetch_html`` (Playwright-first) succeeds against representative
anti-bot stacks: SEC.gov, EDGAR, and Cloudflare's own site.

Requires Playwright + chromium browser. Run with:

    uv sync --extra browser
    uv run playwright install chromium
    uv run pytest tests/integration/test_playwright_anti_bot.py -v
"""

from __future__ import annotations

import pytest

from kaos_web.clients.browser import BrowserClient
from kaos_web.models import WebRequest
from kaos_web.tools import _fetch_html

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_fetch_html_defaults_to_playwright_when_available() -> None:
    """The Playwright-first default path must succeed against SEC.gov."""
    html, final_url = await _fetch_html("https://www.sec.gov/newsroom/press-releases")
    assert "sec.gov" in final_url
    assert "SEC" in html or "Securities" in html or "Commission" in html, (
        "SEC press releases page should contain SEC/Securities/Commission "
        f"keywords; got {len(html)} chars starting with {html[:120]!r}"
    )


@pytest.mark.asyncio
async def test_fetch_html_browser_handles_edgar() -> None:
    """EDGAR's CGI route — historically the strictest SEC.gov path."""
    edgar_url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        "&CIK=0000320193&type=10-K&dateb=&owner=include&count=5"
    )
    html, _ = await _fetch_html(edgar_url)
    # Apple's CIK page should mention Apple and the CIK number
    assert "APPLE" in html.upper() or "320193" in html


@pytest.mark.asyncio
async def test_fetch_html_browser_passes_cloudflare() -> None:
    """Cloudflare's own site has aggressive bot detection on the
    challenge page. With realistic Playwright context (sec-ch-ua +
    viewport + locale) the request should be allowed through."""
    html, _ = await _fetch_html("https://www.cloudflare.com/")
    # Cloudflare home page is large and JS-heavy; assert it rendered
    assert len(html) > 50_000, (
        f"Cloudflare home should render to >50KB; got {len(html)} chars. Failed fingerprint check?"
    )
    assert "Cloudflare" in html


@pytest.mark.asyncio
async def test_browser_client_rotates_user_agents() -> None:
    """Two consecutive BrowserClient fetches with default config must
    surface different User-Agent strings (round-robin rotation)."""
    seen_uas: list[str] = []
    for _ in range(3):
        async with BrowserClient() as client:
            resp = await client.fetch(WebRequest(url="https://httpbin.org/headers"))
        # httpbin echoes received headers in JSON; substring match is fine.
        for ua_candidate in resp.html.split('"User-Agent": "'):
            if ua_candidate and ua_candidate[0] != "{":
                ua_str = ua_candidate.split('"', 1)[0]
                if "Mozilla" in ua_str:
                    seen_uas.append(ua_str)
                    break
    assert len(set(seen_uas)) >= 2, (
        "Expected at least two distinct UAs across three fetches "
        f"(round-robin should rotate); got {seen_uas!r}"
    )


@pytest.mark.asyncio
async def test_browser_client_sends_sec_ch_ua_headers() -> None:
    """The default anti-bot header set (sec-ch-ua, sec-fetch-*,
    accept-language) must be present on browser navigations.

    Verified by inspecting what httpbin echoes back. Cloudflare and
    SEC.gov fingerprint check rely on these headers being well-formed.
    """
    async with BrowserClient() as client:
        resp = await client.fetch(WebRequest(url="https://httpbin.org/headers"))
    # Headers echoed in lowercased JSON; check the canonical
    # anti-bot signals are present.
    body_lower = resp.html.lower()
    for expected in (
        "sec-ch-ua",
        "sec-fetch-site",
        "accept-language",
        "upgrade-insecure-requests",
    ):
        assert expected in body_lower, (
            f"Expected {expected!r} in httpbin echo; got body starting with {resp.html[:200]!r}"
        )


@pytest.mark.asyncio
async def test_explicit_httpx_fallback_still_works() -> None:
    """``use_browser=False`` forces the httpx path. Should still work
    for non-anti-bot sites (e.g. wikipedia, httpbin)."""
    html, _ = await _fetch_html("https://httpbin.org/html", use_browser=False)
    assert "Herman Melville" in html or "<html" in html.lower()
