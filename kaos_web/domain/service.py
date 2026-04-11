"""Composite service detection — combines TCP, TLS, and HTTP probes.

Probes common ports (80, 443) on a domain, inspects TLS certificates,
reads HTTP headers, and identifies server software, CDN, and tech stack.
"""

from __future__ import annotations

import asyncio

from kaos_web.domain.http import analyze_headers, identify_cdn_from_headers
from kaos_web.domain.models import ServiceInfo, ServiceProfile
from kaos_web.domain.tcp import probe_port
from kaos_web.domain.tls import inspect_tls


async def detect_services(
    host: str,
    *,
    timeout: float = 10.0,
) -> ServiceProfile:
    """Detect web services on a domain by probing ports 80 and 443.

    For each open port, gathers HTTP headers and (for 443) TLS certificate.
    Identifies server software and CDN from combined signals.

    Args:
        host: Target hostname.
        timeout: Per-probe timeout.

    Returns:
        ServiceProfile with detected services.
    """
    # Probe both ports concurrently
    port_80, port_443 = await asyncio.gather(
        probe_port(host, 80, timeout=timeout),
        probe_port(host, 443, timeout=timeout),
    )

    services: list[ServiceInfo] = []
    all_headers: dict[str, str] = {}
    server_software: str | None = None

    # HTTP on port 80
    if port_80.status.value == "open":
        http_result = await analyze_headers(
            f"http://{host}",
            timeout=timeout,
            follow_redirects=False,
        )
        services.append(
            ServiceInfo(
                port=80,
                protocol="http",
                software=http_result.server_software,
                version=http_result.server,
                headers=http_result,
            )
        )
        all_headers.update(http_result.headers)
        if http_result.server_software:
            server_software = http_result.server_software

    # HTTPS on port 443
    if port_443.status.value == "open":
        tls_result, https_result = await asyncio.gather(
            inspect_tls(host, 443, timeout=timeout),
            analyze_headers(
                f"https://{host}",
                timeout=timeout,
                follow_redirects=False,
            ),
        )
        services.append(
            ServiceInfo(
                port=443,
                protocol="https",
                software=https_result.server_software,
                version=https_result.server,
                tls=tls_result if not tls_result.error else None,
                headers=https_result,
            )
        )
        all_headers.update(https_result.headers)
        if https_result.server_software:
            server_software = https_result.server_software

    cdn = identify_cdn_from_headers(all_headers)

    return ServiceProfile(
        host=host,
        services=services,
        cdn=cdn,
        server_software=server_software,
    )
