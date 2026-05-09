"""Smoke tests for ``kaos_web.domain.models`` Pydantic models.

Construction + (de)serialization round-trips. No network. Real-shaped data
based on actual WHOIS, DNS, TLS, and HTTP probe outputs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kaos_web.domain.models import (
    COMMON_PORTS,
    DnsProfile,
    DnsQueryResult,
    DnsRecord,
    DnsRecordStatus,
    DomainProfile,
    HttpHeadersResult,
    MailAuthMechanism,
    MailAuthRecord,
    MailAuthStatus,
    MailSecurityReport,
    PortResult,
    PortStatus,
    SecurityHeader,
    SecurityHeaderStatus,
    ServiceInfo,
    ServiceProfile,
    TcpProbeResult,
    TlsCertInfo,
    WhoisRecord,
    ZoneTransferResult,
    ZoneTransferStatus,
)


class TestEnums:
    def test_port_status_values(self) -> None:
        assert PortStatus.OPEN == "open"
        assert PortStatus.CLOSED == "closed"
        assert PortStatus.TIMEOUT == "timeout"
        assert PortStatus.ERROR == "error"

    def test_security_header_status(self) -> None:
        assert SecurityHeaderStatus.PRESENT == "present"
        assert SecurityHeaderStatus.MISSING == "missing"
        assert SecurityHeaderStatus.WEAK == "weak"

    def test_dns_record_status(self) -> None:
        assert DnsRecordStatus.SUCCESS == "success"
        assert DnsRecordStatus.NXDOMAIN == "nxdomain"
        assert DnsRecordStatus.NO_ANSWER == "no_answer"
        assert DnsRecordStatus.TIMEOUT == "timeout"
        assert DnsRecordStatus.ERROR == "error"

    def test_zone_transfer_status(self) -> None:
        assert ZoneTransferStatus.SUCCESS == "success"
        assert ZoneTransferStatus.REFUSED == "refused"
        assert ZoneTransferStatus.FAILED == "failed"
        assert ZoneTransferStatus.TIMEOUT == "timeout"

    def test_mail_auth_mechanism(self) -> None:
        assert MailAuthMechanism.SPF == "spf"
        assert MailAuthMechanism.DKIM == "dkim"
        assert MailAuthMechanism.DMARC == "dmarc"

    def test_mail_auth_status(self) -> None:
        assert MailAuthStatus.CONFIGURED == "configured"
        assert MailAuthStatus.MISSING == "missing"
        assert MailAuthStatus.WEAK == "weak"
        assert MailAuthStatus.INVALID == "invalid"


class TestPortPresets:
    def test_common_ports_has_default(self) -> None:
        assert "default" in COMMON_PORTS
        assert 80 in COMMON_PORTS["default"]
        assert 443 in COMMON_PORTS["default"]

    def test_common_ports_categories(self) -> None:
        assert COMMON_PORTS["web"] == [80, 443, 8080, 8443]
        assert 22 in COMMON_PORTS["ssh"]
        assert 53 in COMMON_PORTS["dns"]


class TestPortResult:
    def test_minimal_construction(self) -> None:
        r = PortResult(port=443, status=PortStatus.OPEN)
        assert r.port == 443
        assert r.status == PortStatus.OPEN
        assert r.latency_ms is None
        assert r.banner is None
        assert r.error is None

    def test_full_construction(self) -> None:
        r = PortResult(
            port=22,
            status=PortStatus.OPEN,
            latency_ms=12.34,
            banner="SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.4",
            error=None,
        )
        assert r.banner is not None
        assert "SSH-2.0" in r.banner

    def test_extra_forbidden(self) -> None:
        # Validate via dict to bypass static type checking — Pydantic
        # enforces extra="forbid" at runtime regardless.
        with pytest.raises(ValidationError):
            PortResult.model_validate({"port": 80, "status": "open", "bogus_field": "x"})

    def test_serialization_round_trip(self) -> None:
        r = PortResult(port=80, status=PortStatus.CLOSED, latency_ms=1.0)
        dumped = r.model_dump(mode="json")
        assert dumped["status"] == "closed"
        round = PortResult.model_validate(dumped)
        assert round == r


class TestTcpProbeResult:
    def test_default_lists(self) -> None:
        t = TcpProbeResult(host="example.com")
        assert t.ports == []
        assert t.open_count == 0
        assert t.closed_count == 0
        assert t.timeout_count == 0

    def test_with_results(self) -> None:
        t = TcpProbeResult(
            host="example.com",
            ports=[
                PortResult(port=80, status=PortStatus.OPEN),
                PortResult(port=443, status=PortStatus.OPEN),
                PortResult(port=22, status=PortStatus.CLOSED),
            ],
            open_count=2,
            closed_count=1,
        )
        assert len(t.ports) == 3


class TestTlsCertInfo:
    def test_minimal(self) -> None:
        t = TlsCertInfo(host="example.com")
        assert t.port == 443
        assert t.subject == {}
        assert t.san_dns == []

    def test_full_construction(self) -> None:
        t = TlsCertInfo(
            host="example.com",
            port=443,
            subject={"commonName": "example.com"},
            issuer={"commonName": "DigiCert Global G3", "organizationName": "DigiCert Inc"},
            serial_number="0123456789ABCDEF",
            not_before="2025-01-01T00:00:00",
            not_after="2026-01-01T00:00:00",
            days_until_expiry=240,
            san_dns=["example.com", "www.example.com"],
            protocol="TLSv1.3",
            cipher="TLS_AES_256_GCM_SHA384",
            cipher_bits=256,
        )
        assert t.protocol == "TLSv1.3"
        assert "www.example.com" in t.san_dns

    def test_error_path(self) -> None:
        t = TlsCertInfo(host="bad.example.com", error="Connection refused")
        assert t.error == "Connection refused"


class TestSecurityHeader:
    def test_present(self) -> None:
        h = SecurityHeader(
            name="strict-transport-security",
            status=SecurityHeaderStatus.PRESENT,
            value="max-age=63072000",
        )
        assert h.value == "max-age=63072000"

    def test_missing(self) -> None:
        h = SecurityHeader(
            name="content-security-policy",
            status=SecurityHeaderStatus.MISSING,
            recommendation="Controls which resources the browser can load",
        )
        assert h.value is None


class TestHttpHeadersResult:
    def test_success(self) -> None:
        r = HttpHeadersResult(
            url="https://example.com/",
            status_code=200,
            headers={"server": "nginx"},
            server="nginx",
            server_software="nginx",
            security_score=42,
        )
        assert r.status_code == 200
        assert r.security_score == 42

    def test_error(self) -> None:
        r = HttpHeadersResult(url="https://x", status_code=0, error="timeout")
        assert r.error == "timeout"


class TestServiceInfo:
    def test_minimal(self) -> None:
        s = ServiceInfo(port=443, protocol="https")
        assert s.tls is None
        assert s.headers is None

    def test_full(self) -> None:
        s = ServiceInfo(
            port=443,
            protocol="https",
            software="nginx",
            version="nginx/1.24.0",
            tls=TlsCertInfo(host="example.com"),
            headers=HttpHeadersResult(url="https://example.com", status_code=200),
        )
        assert s.tls is not None
        assert s.headers is not None


class TestServiceProfile:
    def test_default(self) -> None:
        p = ServiceProfile(host="example.com")
        assert p.services == []
        assert p.cdn is None


class TestDnsRecord:
    def test_construction(self) -> None:
        r = DnsRecord(name="example.com", record_type="A", ttl=300, value="93.184.216.34")
        assert r.value == "93.184.216.34"


class TestDnsQueryResult:
    def test_success(self) -> None:
        q = DnsQueryResult(
            query_name="example.com",
            record_type="A",
            status=DnsRecordStatus.SUCCESS,
            records=[DnsRecord(name="example.com", record_type="A", ttl=300, value="1.2.3.4")],
            duration_ms=12.5,
        )
        assert q.records[0].value == "1.2.3.4"

    def test_nxdomain(self) -> None:
        q = DnsQueryResult(
            query_name="nope.invalid",
            record_type="A",
            status=DnsRecordStatus.NXDOMAIN,
            error="Domain nope.invalid does not exist (NXDOMAIN)",
        )
        assert q.status == DnsRecordStatus.NXDOMAIN


class TestDnsProfile:
    def test_default(self) -> None:
        p = DnsProfile(domain="example.com")
        assert p.queries == []
        assert p.dnssec is None
        assert p.nameservers == []


class TestZoneTransferResult:
    def test_refused(self) -> None:
        z = ZoneTransferResult(
            nameserver="ns1.example.com",
            status=ZoneTransferStatus.REFUSED,
            error="REFUSED",
        )
        assert z.status == ZoneTransferStatus.REFUSED

    def test_success(self) -> None:
        z = ZoneTransferResult(
            nameserver="ns1.example.com",
            address="93.184.216.34",
            status=ZoneTransferStatus.SUCCESS,
            record_count=42,
            serial=2024010101,
            duration_ms=80.0,
        )
        assert z.record_count == 42


class TestMailAuthRecord:
    def test_spf_configured(self) -> None:
        r = MailAuthRecord(
            mechanism=MailAuthMechanism.SPF,
            status=MailAuthStatus.CONFIGURED,
            raw_record="v=spf1 include:_spf.google.com ~all",
            policy="~all (soft fail)",
        )
        assert r.policy is not None and r.policy.startswith("~all")

    def test_missing(self) -> None:
        r = MailAuthRecord(
            mechanism=MailAuthMechanism.DMARC,
            status=MailAuthStatus.MISSING,
            issues=["No DMARC record found at _dmarc.example.com"],
        )
        assert "_dmarc" in r.issues[0]


class TestMailSecurityReport:
    def test_default_posture(self) -> None:
        r = MailSecurityReport(domain="example.com")
        assert r.overall_posture == "unknown"
        assert r.records == []


class TestWhoisRecord:
    def test_minimal(self) -> None:
        w = WhoisRecord(domain="example.com")
        assert w.registrar is None
        assert w.name_servers == []

    def test_full(self) -> None:
        w = WhoisRecord(
            domain="example.com",
            registrar="RESERVED-Internet Assigned Numbers Authority",
            whois_server="whois.iana.org",
            creation_date="1995-08-14T04:00:00",
            expiration_date="2026-08-13T04:00:00",
            updated_date="2024-08-14T07:01:38",
            name_servers=["a.iana-servers.net", "b.iana-servers.net"],
            status=["clientDeleteProhibited", "clientTransferProhibited"],
            dnssec="signedDelegation",
        )
        assert "a.iana-servers.net" in w.name_servers


class TestDomainProfile:
    def test_default(self) -> None:
        d = DomainProfile(domain="example.com")
        assert d.dns is None
        assert d.whois is None
        assert d.sitemap_urls == []
        assert d.robots_txt is None

    def test_combined(self) -> None:
        d = DomainProfile(
            domain="example.com",
            dns=DnsProfile(domain="example.com"),
            whois=WhoisRecord(domain="example.com"),
            services=ServiceProfile(host="example.com"),
            mail_security=MailSecurityReport(domain="example.com"),
            sitemap_urls=["https://example.com/sitemap.xml"],
            robots_txt="https://example.com/robots.txt",
        )
        json_round = DomainProfile.model_validate(d.model_dump(mode="json"))
        assert json_round.domain == "example.com"
