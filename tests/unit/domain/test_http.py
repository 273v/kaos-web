"""Tests for ``kaos_web.domain.http`` — HTTP header analysis.

The module-level helpers (``_identify_server``, ``_identify_cdn``,
``_analyze_security_headers``, ``identify_cdn_from_headers``) are pure
functions on header dicts. ``analyze_headers`` is exercised through
mocked ``httpx`` transport using ``pytest_httpx`` (already a dev dep).
"""

from __future__ import annotations

import httpx
import pytest

from kaos_web.domain.http import (
    _CDN_SIGNATURES,
    _SERVER_SIGNATURES,
    _analyze_security_headers,
    _identify_cdn,
    _identify_server,
    analyze_headers,
    identify_cdn_from_headers,
)
from kaos_web.domain.models import HttpHeadersResult, SecurityHeaderStatus


class TestIdentifyServer:
    def test_empty(self) -> None:
        assert _identify_server("") is None

    def test_nginx(self) -> None:
        assert _identify_server("nginx/1.24.0") == "nginx"

    def test_apache(self) -> None:
        assert _identify_server("Apache/2.4.52 (Ubuntu)") == "Apache"

    def test_iis(self) -> None:
        assert _identify_server("Microsoft-IIS/10.0") == "Microsoft-IIS"

    def test_caddy(self) -> None:
        assert _identify_server("Caddy") == "Caddy"

    def test_litespeed(self) -> None:
        assert _identify_server("LiteSpeed") == "LiteSpeed"

    def test_unknown_with_slash(self) -> None:
        assert _identify_server("WeirdServer/9.9") == "WeirdServer"

    def test_unknown_no_slash(self) -> None:
        assert _identify_server("CustomServer") == "CustomServer"

    def test_all_signatures_resolvable(self) -> None:
        # every sig in the table maps to itself
        for sig, name in _SERVER_SIGNATURES.items():
            assert _identify_server(sig) == name
            assert _identify_server(sig.upper()) == name


class TestIdentifyCdn:
    def test_no_cdn(self) -> None:
        assert _identify_cdn({"server": "nginx"}) is None

    def test_cloudflare_via_server(self) -> None:
        assert _identify_cdn({"server": "cloudflare"}) == "Cloudflare"

    def test_cloudflare_via_cf_ray(self) -> None:
        assert _identify_cdn({"cf-ray": "abc123-DFW", "server": "x"}) == "Cloudflare"

    def test_cloudfront_via_cf_id(self) -> None:
        assert _identify_cdn({"x-amz-cf-id": "abc"}) == "CloudFront"

    def test_cloudfront_via_cf_pop(self) -> None:
        assert _identify_cdn({"x-amz-cf-pop": "DFW3"}) == "CloudFront"

    def test_fastly_via_served_by(self) -> None:
        assert _identify_cdn({"x-served-by": "cache-iad-kjyo7100090"}) == "Fastly"

    def test_fastly_no_cache_text(self) -> None:
        # x-served-by present but without "cache" -> not detected as Fastly
        assert _identify_cdn({"x-served-by": "edge-server-01"}) is None

    def test_vercel(self) -> None:
        assert _identify_cdn({"x-vercel-id": "iad1::abc"}) == "Vercel"

    def test_netlify(self) -> None:
        assert _identify_cdn({"x-nf-request-id": "abc-123"}) == "Netlify"

    def test_akamai_via_server(self) -> None:
        assert _identify_cdn({"server": "AkamaiGHost"}) == "Akamai"

    def test_all_signatures(self) -> None:
        for sig, name in _CDN_SIGNATURES.items():
            assert _identify_cdn({"server": sig}) == name

    def test_public_helper(self) -> None:
        assert identify_cdn_from_headers({"cf-ray": "x"}) == "Cloudflare"
        assert identify_cdn_from_headers({}) is None


class TestAnalyzeSecurityHeaders:
    def test_all_missing(self) -> None:
        results, score = _analyze_security_headers({})
        assert score == 0
        assert all(r.status == SecurityHeaderStatus.MISSING for r in results)
        # Each missing header has a recommendation
        for r in results:
            assert r.recommendation

    def test_all_present(self) -> None:
        headers = {
            "strict-transport-security": "max-age=63072000",
            "content-security-policy": "default-src 'self'",
            "x-frame-options": "DENY",
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
            "permissions-policy": "geolocation=()",
            "x-xss-protection": "1; mode=block",
            "cross-origin-opener-policy": "same-origin",
            "cross-origin-resource-policy": "same-origin",
        }
        results, score = _analyze_security_headers(headers)
        assert score == 100
        assert all(r.status == SecurityHeaderStatus.PRESENT for r in results)

    def test_weak_xss(self) -> None:
        results, _ = _analyze_security_headers({"x-xss-protection": "0"})
        weak = next(r for r in results if r.name == "x-xss-protection")
        assert weak.status == SecurityHeaderStatus.WEAK

    def test_weak_referrer_unsafe(self) -> None:
        results, _ = _analyze_security_headers({"referrer-policy": "unsafe-url"})
        weak = next(r for r in results if r.name == "referrer-policy")
        assert weak.status == SecurityHeaderStatus.WEAK

    def test_weak_does_not_count(self) -> None:
        # A weak header should not count towards score
        _, score_weak = _analyze_security_headers({"x-xss-protection": "0"})
        _, score_present = _analyze_security_headers({"x-xss-protection": "1; mode=block"})
        assert score_weak < score_present

    def test_partial_score(self) -> None:
        # 1 of 9 = ~11
        _, score = _analyze_security_headers({"x-frame-options": "DENY"})
        assert score == int((1 / 9) * 100)


@pytest.mark.asyncio
class TestAnalyzeHeaders:
    async def test_success(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        httpx_mock.add_response(
            method="HEAD",
            url="https://example.com/",
            status_code=200,
            headers={
                "Server": "nginx/1.24.0",
                "Strict-Transport-Security": "max-age=63072000",
                "X-Powered-By": "Express",
            },
        )
        result = await analyze_headers("https://example.com/")
        assert isinstance(result, HttpHeadersResult)
        assert result.status_code == 200
        assert result.server is not None and "nginx" in result.server
        assert result.server_software == "nginx"
        assert result.powered_by == "Express"
        # Lowercased headers
        assert "strict-transport-security" in result.headers

    async def test_redirect_location(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        httpx_mock.add_response(
            method="HEAD",
            url="https://example.com/",
            status_code=301,
            headers={"Location": "https://www.example.com/"},
        )
        result = await analyze_headers("https://example.com/", follow_redirects=False)
        assert result.status_code == 301
        assert result.redirect_url == "https://www.example.com/"

    async def test_timeout(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        httpx_mock.add_exception(httpx.TimeoutException("timeout"))
        result = await analyze_headers("https://timeout.example.com/", timeout=0.1)
        assert result.status_code == 0
        assert result.error is not None and "timed out" in result.error

    async def test_connect_error(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        httpx_mock.add_exception(httpx.ConnectError("name resolution failed"))
        result = await analyze_headers("https://does-not-resolve.invalid/")
        assert result.status_code == 0
        assert result.error is not None and "Connection failed" in result.error

    async def test_other_exception(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        httpx_mock.add_exception(RuntimeError("boom"))
        result = await analyze_headers("https://broken.example.com/")
        assert result.status_code == 0
        assert result.error is not None and "Request failed" in result.error

    async def test_verify_tls_default_off(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        # WEB2-001: verify_tls defaults to False so probes can succeed
        # against hosts whose TLS configuration is the *subject* of
        # inspection (self-signed, expired, mismatched SAN). The test
        # asserts the call simply succeeds — pytest_httpx's mock transport
        # ignores `verify`, so the assertion here is on the function
        # signature contract, not on the underlying TLS behavior.
        httpx_mock.add_response(
            method="HEAD",
            url="https://self-signed.example.invalid/",
            status_code=200,
            headers={"Server": "nginx/1.24.0"},
        )
        result = await analyze_headers("https://self-signed.example.invalid/")
        assert result.status_code == 200

    async def test_verify_tls_explicit_true(self, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        # Confirms the parameter is keyword-accepted; a True value should
        # not break the happy path.
        httpx_mock.add_response(
            method="HEAD",
            url="https://verified.example.com/",
            status_code=200,
        )
        result = await analyze_headers("https://verified.example.com/", verify_tls=True)
        assert result.status_code == 200
