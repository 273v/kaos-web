"""HTTP header analysis and server fingerprinting.

Uses httpx (already a kaos-web dependency) to perform HEAD requests
and analyze response headers for server identification and security
posture assessment.
"""

from __future__ import annotations

import httpx

from kaos_web.domain.models import (
    HttpHeadersResult,
    SecurityHeader,
    SecurityHeaderStatus,
)

# ── Security headers to check ───────────────────────────────────────

_SECURITY_HEADERS: list[tuple[str, str]] = [
    ("strict-transport-security", "Enables HSTS — forces HTTPS for all future requests"),
    ("content-security-policy", "Controls which resources the browser can load"),
    ("x-frame-options", "Prevents clickjacking by controlling iframe embedding"),
    ("x-content-type-options", "Prevents MIME-type sniffing (should be 'nosniff')"),
    ("referrer-policy", "Controls how much referrer info is sent with requests"),
    ("permissions-policy", "Controls which browser features the site can use"),
    ("x-xss-protection", "Legacy XSS filter (deprecated but still checked)"),
    ("cross-origin-opener-policy", "Isolates browsing context from cross-origin documents"),
    ("cross-origin-resource-policy", "Controls which origins can load the resource"),
]

# ── Server fingerprinting ───────────────────────────────────────────

_CDN_SIGNATURES: dict[str, str] = {
    "cloudflare": "Cloudflare",
    "cloudfront": "CloudFront",
    "akamai": "Akamai",
    "fastly": "Fastly",
    "vercel": "Vercel",
    "netlify": "Netlify",
    "stackpath": "StackPath",
    "sucuri": "Sucuri",
    "incapsula": "Incapsula",
    "imperva": "Imperva",
}

_SERVER_SIGNATURES: dict[str, str] = {
    "nginx": "nginx",
    "apache": "Apache",
    "microsoft-iis": "Microsoft-IIS",
    "iis": "Microsoft-IIS",
    "litespeed": "LiteSpeed",
    "caddy": "Caddy",
    "tomcat": "Apache Tomcat",
    "jetty": "Jetty",
    "gunicorn": "Gunicorn",
    "uvicorn": "Uvicorn",
    "openresty": "OpenResty",
    "envoy": "Envoy",
}


def _identify_server(server_header: str) -> str | None:
    """Extract server software name from the Server header."""
    if not server_header:
        return None
    lower = server_header.lower()
    for sig, name in _SERVER_SIGNATURES.items():
        if sig in lower:
            return name
    # Fallback: first segment before /
    if "/" in server_header:
        return server_header.split("/")[0].strip()
    return server_header.strip()


def _identify_cdn(headers: dict[str, str]) -> str | None:
    """Detect CDN from response headers."""
    # Check Server header
    server = headers.get("server", "").lower()
    for sig, name in _CDN_SIGNATURES.items():
        if sig in server:
            return name

    # Check CDN-specific headers
    if "cf-ray" in headers:
        return "Cloudflare"
    if "x-amz-cf-id" in headers or "x-amz-cf-pop" in headers:
        return "CloudFront"
    if "x-served-by" in headers and "cache" in headers.get("x-served-by", "").lower():
        return "Fastly"
    if "x-vercel-id" in headers:
        return "Vercel"
    if "x-nf-request-id" in headers:
        return "Netlify"

    return None


def _analyze_security_headers(headers: dict[str, str]) -> tuple[list[SecurityHeader], int]:
    """Analyze security headers and compute a 0-100 score."""
    results: list[SecurityHeader] = []
    points = 0
    max_points = len(_SECURITY_HEADERS)

    for header_name, recommendation in _SECURITY_HEADERS:
        value = headers.get(header_name)
        if value:
            # Check for weak values
            status = SecurityHeaderStatus.PRESENT
            if (header_name == "x-xss-protection" and value.strip() == "0") or (
                header_name == "referrer-policy" and value.strip() == "unsafe-url"
            ):
                status = SecurityHeaderStatus.WEAK

            if status == SecurityHeaderStatus.PRESENT:
                points += 1

            results.append(
                SecurityHeader(
                    name=header_name,
                    status=status,
                    value=value,
                )
            )
        else:
            results.append(
                SecurityHeader(
                    name=header_name,
                    status=SecurityHeaderStatus.MISSING,
                    recommendation=recommendation,
                )
            )

    score = int((points / max_points) * 100) if max_points else 0
    return results, score


async def analyze_headers(
    url: str,
    *,
    timeout: float = 10.0,
    follow_redirects: bool = False,
    verify_tls: bool = False,
) -> HttpHeadersResult:
    """Fetch HTTP headers and analyze server/security posture.

    Args:
        url: Full URL to probe (e.g., ``https://example.com``).
        timeout: Request timeout in seconds.
        follow_redirects: Whether to follow redirects.
        verify_tls: When ``False`` (default), TLS certificates are NOT
            verified. This is intentional for domain-intelligence probes —
            the cert is part of what we're inspecting, and failing closed
            on an expired or self-signed cert would defeat the purpose of
            the probe. Pass ``True`` to require standard CA validation.

    Returns:
        HttpHeadersResult with headers, server info, and security analysis.

    Security note:
        Default ``verify_tls=False`` accepts any cert presented by the
        target host — DO NOT use this function as a transport for
        sensitive data. For verified GETs use the ``kaos-web-fetch-page``
        / ``HttpClient`` paths which keep TLS verification on.
    """
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify_tls,
        ) as client:
            response = await client.head(url)

            headers = {k.lower(): v for k, v in response.headers.items()}
            server_header = headers.get("server", "")

            security_results, security_score = _analyze_security_headers(headers)

            return HttpHeadersResult(
                url=str(response.url),
                status_code=response.status_code,
                headers=headers,
                server=server_header or None,
                server_software=_identify_server(server_header),
                powered_by=headers.get("x-powered-by"),
                security_headers=security_results,
                security_score=security_score,
                redirect_url=headers.get("location"),
            )

    except httpx.TimeoutException:
        return HttpHeadersResult(
            url=url,
            status_code=0,
            error=f"Request timed out after {timeout}s",
        )
    except httpx.ConnectError as exc:
        return HttpHeadersResult(
            url=url,
            status_code=0,
            error=f"Connection failed: {exc}",
        )
    except Exception as exc:
        return HttpHeadersResult(
            url=url,
            status_code=0,
            error=f"Request failed: {exc}",
        )


def identify_cdn_from_headers(headers: dict[str, str]) -> str | None:
    """Public CDN detection for use by other modules."""
    return _identify_cdn(headers)
