"""Tests for ``kaos_web.domain.service`` — composite service detection.

Mocks the underlying ``probe_port``, ``inspect_tls``, and
``analyze_headers`` functions to avoid network I/O. The unit surface
here is the composition logic — port-status routing, header merging,
CDN detection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kaos_web.domain.models import (
    HttpHeadersResult,
    PortResult,
    PortStatus,
    ServiceProfile,
    TlsCertInfo,
)
from kaos_web.domain.service import detect_services


def _open(port: int) -> PortResult:
    return PortResult(port=port, status=PortStatus.OPEN, latency_ms=1.0)


def _closed(port: int) -> PortResult:
    return PortResult(port=port, status=PortStatus.CLOSED, latency_ms=1.0)


@pytest.mark.asyncio
class TestDetectServices:
    async def test_both_ports_closed(self) -> None:
        async def _probe(host: str, port: int, **__: object) -> PortResult:
            return _closed(port)

        with patch("kaos_web.domain.service.probe_port", side_effect=_probe):
            result = await detect_services("example.com", timeout=1.0)
        assert isinstance(result, ServiceProfile)
        assert result.host == "example.com"
        assert result.services == []
        assert result.cdn is None

    async def test_both_ports_open(self) -> None:
        async def _probe(host: str, port: int, **__: object) -> PortResult:
            return _open(port)

        http_result = HttpHeadersResult(
            url="http://example.com",
            status_code=200,
            headers={"server": "nginx", "cf-ray": "abc-DFW"},
            server="nginx",
            server_software="nginx",
        )
        https_result = HttpHeadersResult(
            url="https://example.com",
            status_code=200,
            headers={"server": "nginx", "cf-ray": "abc-DFW"},
            server="nginx",
            server_software="nginx",
        )
        tls_info = TlsCertInfo(host="example.com", port=443, protocol="TLSv1.3")

        async def _analyze(url: str, **__: object) -> HttpHeadersResult:
            return https_result if url.startswith("https") else http_result

        with (
            patch("kaos_web.domain.service.probe_port", side_effect=_probe),
            patch("kaos_web.domain.service.analyze_headers", side_effect=_analyze),
            patch("kaos_web.domain.service.inspect_tls", AsyncMock(return_value=tls_info)),
        ):
            profile = await detect_services("example.com", timeout=1.0)

        assert len(profile.services) == 2
        assert profile.cdn == "Cloudflare"  # cf-ray was merged in
        assert profile.server_software == "nginx"
        # First service is port 80, second is port 443
        ports = sorted(s.port for s in profile.services)
        assert ports == [80, 443]
        # 443 service should have TLS info
        https_service = next(s for s in profile.services if s.port == 443)
        assert https_service.tls is not None
        assert https_service.tls.protocol == "TLSv1.3"

    async def test_tls_error_drops_tls(self) -> None:
        async def _probe(host: str, port: int, **__: object) -> PortResult:
            return _open(port) if port == 443 else _closed(port)

        tls_with_error = TlsCertInfo(host="x", error="bad cert")
        headers = HttpHeadersResult(url="https://x", status_code=200)

        with (
            patch("kaos_web.domain.service.probe_port", side_effect=_probe),
            patch("kaos_web.domain.service.analyze_headers", AsyncMock(return_value=headers)),
            patch("kaos_web.domain.service.inspect_tls", AsyncMock(return_value=tls_with_error)),
        ):
            profile = await detect_services("example.com", timeout=1.0)

        assert len(profile.services) == 1
        assert profile.services[0].port == 443
        # tls_result.error truthy → dropped to None
        assert profile.services[0].tls is None

    async def test_only_port_80_open(self) -> None:
        async def _probe(host: str, port: int, **__: object) -> PortResult:
            return _open(port) if port == 80 else _closed(port)

        headers = HttpHeadersResult(
            url="http://example.com",
            status_code=200,
            headers={"server": "Apache"},
            server="Apache/2.4",
            server_software="Apache",
        )
        with (
            patch("kaos_web.domain.service.probe_port", side_effect=_probe),
            patch("kaos_web.domain.service.analyze_headers", AsyncMock(return_value=headers)),
        ):
            profile = await detect_services("example.com", timeout=1.0)
        assert len(profile.services) == 1
        assert profile.services[0].port == 80
        assert profile.services[0].protocol == "http"
        assert profile.server_software == "Apache"
