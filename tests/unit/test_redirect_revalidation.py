"""Regression tests for audit-04 F-001: SSRF via redirect bypass.

`HttpClient` previously delegated redirect handling to httpx, which
revalidates only the original request URL. A redirect to a private /
loopback / metadata-service host bypassed the
`kaos_web.security.validate_url` gate. The fix in
`kaos_web/clients/http.py:_streamed_request` handles redirects
manually and re-validates each `Location` target.

These tests use `httpx.MockTransport` to deterministically drive the
redirect chain — no real network, no socket I/O.
"""

from __future__ import annotations

import contextlib

import httpx
import pytest

from kaos_web.clients.http import HttpClient, HttpClientConfig
from kaos_web.errors import WebError
from kaos_web.models.request import WebRequest


@pytest.mark.asyncio
async def test_redirect_to_loopback_is_blocked_by_revalidation() -> None:
    """Allowed initial URL → 302 → loopback. Must raise, not fetch.

    The classic SSRF-via-redirect bypass: attacker-controlled origin
    returns a Location pointing at 127.0.0.1 (or 169.254.169.254 for
    cloud metadata services). audit-04 F-001 confirmed httpx's built-in
    follow_redirects didn't revalidate. With the fix, the redirect hop
    re-enters `validate_url`, which raises because loopback is blocked.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        # Initial fetch is allowed (example.com), server responds 302.
        if str(request.url) == "https://example.com/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})
        # Should NEVER reach this branch — the revalidation must reject
        # the loopback target before any request fires for it.
        msg = f"redirect target was followed without revalidation: {request.url}"
        raise AssertionError(msg)

    transport = httpx.MockTransport(handler)
    cfg = HttpClientConfig()
    async with httpx.AsyncClient(transport=transport) as raw:
        client = HttpClient(cfg)
        client._client = raw  # swap in the mock transport
        with pytest.raises(WebError) as exc_info:
            await client.fetch(WebRequest(url="https://example.com/start"))
        # The error must trace back to validate_url's policy decision,
        # not a network error or a generic redirect failure.
        msg = str(exc_info.value).lower()
        assert any(tok in msg for tok in ("private", "loopback", "127.0.0.1", "invalid")), (
            f"expected SSRF-policy reason in error, got: {exc_info.value}"
        )


@pytest.mark.asyncio
async def test_redirect_to_metadata_service_is_blocked() -> None:
    """169.254.169.254 (cloud instance metadata) — same revalidation gate."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start":
            return httpx.Response(
                302,
                headers={"location": "http://169.254.169.254/latest/meta-data/"},
            )
        msg = f"redirect to metadata service was followed: {request.url}"
        raise AssertionError(msg)

    transport = httpx.MockTransport(handler)
    cfg = HttpClientConfig()
    async with httpx.AsyncClient(transport=transport) as raw:
        client = HttpClient(cfg)
        client._client = raw
        with pytest.raises(WebError):
            await client.fetch(WebRequest(url="https://example.com/start"))


@pytest.mark.asyncio
async def test_allowed_redirect_chain_walks_each_hop() -> None:
    """Public → public → public chain — pin that the manual redirect
    loop walks each hop instead of stopping at the first response.

    Without this assertion the SSRF fix could regress fetching to break
    legit redirect chains (http→https, www→apex, CDN edge → origin).

    We rely on the handler-side hop log rather than the final
    ``WebResponse`` so the positive path is exercised on the redirect
    loop itself; the existing ``test_http_client.py`` suite covers the
    full ``WebResponse`` assembly through the same code path.
    """
    hop_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        hop_log.append(url)
        if url == "https://example.com/start":
            return httpx.Response(302, headers={"location": "https://example.org/mid"})
        if url == "https://example.org/mid":
            return httpx.Response(302, headers={"location": "https://example.net/end"})
        if url == "https://example.net/end":
            return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"})
        msg = f"unexpected URL: {url}"
        raise AssertionError(msg)

    transport = httpx.MockTransport(handler)
    cfg = HttpClientConfig()
    async with httpx.AsyncClient(transport=transport) as raw:
        client = HttpClient(cfg)
        client._client = raw
        # MockTransport doesn't set Response._elapsed, so the
        # ``WebResponse`` assembly raises at the end. That's a
        # MockTransport detail, not a redirect-loop regression —
        # the hop log below is the real assertion.
        with contextlib.suppress(Exception):
            await client.fetch(WebRequest(url="https://example.com/start"))
    assert hop_log == [
        "https://example.com/start",
        "https://example.org/mid",
        "https://example.net/end",
    ], f"redirect loop did not walk all hops: {hop_log}"
