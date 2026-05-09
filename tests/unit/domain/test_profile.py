"""Tests for ``kaos_web.domain.profile`` — composite ``profile_domain``.

Mocks each underlying domain probe function. The unit surface is the
TaskGroup orchestration + result aggregation + robots.txt discovery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kaos_web.domain.models import (
    DnsProfile,
    DomainProfile,
    HttpHeadersResult,
    MailSecurityReport,
    ServiceProfile,
    WhoisRecord,
)
from kaos_web.domain.profile import profile_domain


@pytest.mark.asyncio
class TestProfileDomain:
    async def test_full_profile(self) -> None:
        dns = DnsProfile(domain="example.com", apex_domain="example.com")
        services = ServiceProfile(host="example.com")
        whois = WhoisRecord(domain="example.com", registrar="Acme Registrar")
        mail = MailSecurityReport(domain="example.com", overall_posture="strong")
        robots = HttpHeadersResult(url="https://example.com/robots.txt", status_code=200)

        with (
            patch("kaos_web.domain.profile.enumerate_dns", AsyncMock(return_value=dns)),
            patch("kaos_web.domain.profile.detect_services", AsyncMock(return_value=services)),
            patch("kaos_web.domain.profile.whois_lookup", AsyncMock(return_value=whois)),
            patch("kaos_web.domain.profile.analyze_mail_security", AsyncMock(return_value=mail)),
            patch("kaos_web.domain.profile.analyze_headers", AsyncMock(return_value=robots)),
        ):
            p = await profile_domain("example.com", timeout=1.0)
        assert isinstance(p, DomainProfile)
        assert p.domain == "example.com"
        assert p.dns is dns
        assert p.services is services
        assert p.whois is whois
        assert p.mail_security is mail
        assert p.robots_txt == "https://example.com/robots.txt"

    async def test_skip_whois_and_mail(self) -> None:
        dns = DnsProfile(domain="example.com")
        services = ServiceProfile(host="example.com")
        no_robots = HttpHeadersResult(url="https://example.com/robots.txt", status_code=404)

        with (
            patch("kaos_web.domain.profile.enumerate_dns", AsyncMock(return_value=dns)),
            patch("kaos_web.domain.profile.detect_services", AsyncMock(return_value=services)),
            patch("kaos_web.domain.profile.analyze_headers", AsyncMock(return_value=no_robots)),
        ):
            p = await profile_domain(
                "example.com", timeout=1.0, include_whois=False, include_mail_security=False
            )
        assert p.whois is None
        assert p.mail_security is None
        assert p.robots_txt is None  # 404 -> not present

    async def test_robots_check_swallows_errors(self) -> None:
        dns = DnsProfile(domain="example.com")
        services = ServiceProfile(host="example.com")

        with (
            patch("kaos_web.domain.profile.enumerate_dns", AsyncMock(return_value=dns)),
            patch("kaos_web.domain.profile.detect_services", AsyncMock(return_value=services)),
            patch(
                "kaos_web.domain.profile.analyze_headers",
                AsyncMock(side_effect=RuntimeError("network")),
            ),
        ):
            p = await profile_domain(
                "example.com",
                timeout=1.0,
                include_whois=False,
                include_mail_security=False,
            )
        # Robots failure does not raise
        assert p.robots_txt is None
        assert p.dns is dns
