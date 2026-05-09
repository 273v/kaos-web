"""Unit tests for the 11 MCP domain intelligence tools.

These wrap the underlying ``kaos_web.domain.*`` functions; the tests
mock the underlying functions and verify the tool's input handling,
output shaping, and error translation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_core import ToolResult
from kaos_web.domain.models import (
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
    ServiceProfile,
    TcpProbeResult,
    TlsCertInfo,
    WhoisRecord,
    ZoneTransferResult,
    ZoneTransferStatus,
)
from kaos_web.domain_tools import (
    DnsEnumerateTool,
    DnsLookupTool,
    DnsSecurityTool,
    DnsZoneTransferTool,
    DomainProfileTool,
    ExtractOrgTool,
    HttpHeadersTool,
    ServiceDetectTool,
    TcpProbeTool,
    TlsInspectTool,
    WhoisLookupTool,
    register_domain_tools,
)

# ── Tool metadata smoke tests ───────────────────────────────────────


class TestToolMetadata:
    @pytest.mark.parametrize(
        "tool_cls,expected_name",
        [
            (TcpProbeTool, "kaos-web-tcp-probe"),
            (TlsInspectTool, "kaos-web-tls-inspect"),
            (HttpHeadersTool, "kaos-web-http-headers"),
            (ServiceDetectTool, "kaos-web-service-detect"),
            (DnsLookupTool, "kaos-web-dns-lookup"),
            (DnsEnumerateTool, "kaos-web-dns-enumerate"),
            (DnsZoneTransferTool, "kaos-web-dns-zone-transfer"),
            (DnsSecurityTool, "kaos-web-dns-security"),
            (WhoisLookupTool, "kaos-web-whois-lookup"),
            (DomainProfileTool, "kaos-web-domain-profile"),
            (ExtractOrgTool, "kaos-web-extract-org"),
        ],
    )
    def test_names(self, tool_cls: type, expected_name: str) -> None:
        tool = tool_cls()
        assert tool.metadata.name == expected_name

    @pytest.mark.parametrize(
        "tool_cls",
        [
            TcpProbeTool,
            TlsInspectTool,
            HttpHeadersTool,
            ServiceDetectTool,
            DnsLookupTool,
            DnsEnumerateTool,
            DnsZoneTransferTool,
            DnsSecurityTool,
            WhoisLookupTool,
            DomainProfileTool,
            ExtractOrgTool,
        ],
    )
    def test_annotations(self, tool_cls: type) -> None:
        tool = tool_cls()
        ann = tool.metadata.annotations
        assert ann is not None
        assert ann.readOnlyHint is True
        assert ann.destructiveHint is False
        assert ann.openWorldHint is True
        assert ann.idempotentHint is True

    @pytest.mark.parametrize(
        "tool_cls",
        [
            TcpProbeTool,
            TlsInspectTool,
            HttpHeadersTool,
            ServiceDetectTool,
            DnsLookupTool,
            DnsEnumerateTool,
            DnsZoneTransferTool,
            DnsSecurityTool,
            WhoisLookupTool,
            DomainProfileTool,
            ExtractOrgTool,
        ],
    )
    def test_input_schema_present(self, tool_cls: type) -> None:
        tool = tool_cls()
        assert len(tool.metadata.input_schema) > 0
        assert tool.metadata.module_name == "kaos-web"


# ── register_domain_tools ────────────────────────────────────────────


class TestRegister:
    def test_register_count(self) -> None:
        runtime = MagicMock()
        runtime.tools.register_tool = MagicMock()
        count = register_domain_tools(runtime)
        assert count == 11
        assert runtime.tools.register_tool.call_count == 11


def _is_error(r: ToolResult) -> bool:
    """Inspect ToolResult for error state — works across kaos-core versions."""
    if hasattr(r, "is_error"):
        return bool(r.is_error)
    if hasattr(r, "error") and r.error is not None:
        return True
    if hasattr(r, "isError"):
        return bool(r.isError)  # type: ignore[attr-defined]
    return False


# ── TcpProbeTool ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTcpProbeToolExecute:
    async def test_missing_host(self) -> None:
        result = await TcpProbeTool().execute({})
        assert _is_error(result)

    async def test_invalid_ports_string(self) -> None:
        result = await TcpProbeTool().execute({"host": "example.com", "ports": "not,ints"})
        assert _is_error(result)

    async def test_success_with_explicit_ports(self) -> None:
        probe = TcpProbeResult(
            host="example.com",
            ports=[PortResult(port=80, status=PortStatus.OPEN)],
            open_count=1,
        )
        with patch(
            "kaos_web.domain.tcp.probe_ports",
            AsyncMock(return_value=probe),
        ):
            result = await TcpProbeTool().execute({"host": "example.com", "ports": "80, 443"})
        assert not _is_error(result)

    async def test_success_with_preset(self) -> None:
        probe = TcpProbeResult(host="example.com")
        with patch(
            "kaos_web.domain.tcp.probe_ports",
            AsyncMock(return_value=probe),
        ):
            result = await TcpProbeTool().execute(
                {"host": "example.com", "preset": "ssh", "timeout": 1.0}
            )
        assert not _is_error(result)

    async def test_unexpected_exception(self) -> None:
        with patch(
            "kaos_web.domain.tcp.probe_ports",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await TcpProbeTool().execute({"host": "example.com"})
        assert _is_error(result)


# ── TlsInspectTool ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTlsInspectExecute:
    async def test_missing_host(self) -> None:
        assert _is_error(await TlsInspectTool().execute({}))

    async def test_success(self) -> None:
        info = TlsCertInfo(
            host="example.com",
            port=443,
            protocol="TLSv1.3",
            days_until_expiry=42,
        )
        with patch("kaos_web.domain.tls.inspect_tls", AsyncMock(return_value=info)):
            result = await TlsInspectTool().execute({"host": "example.com"})
        assert not _is_error(result)

    async def test_error_translates(self) -> None:
        info = TlsCertInfo(host="example.com", port=443, error="bad cert")
        with patch("kaos_web.domain.tls.inspect_tls", AsyncMock(return_value=info)):
            result = await TlsInspectTool().execute({"host": "example.com"})
        assert _is_error(result)


# ── HttpHeadersTool ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestHttpHeadersExecute:
    async def test_missing_url(self) -> None:
        assert _is_error(await HttpHeadersTool().execute({}))

    async def test_success(self) -> None:
        h = HttpHeadersResult(
            url="https://example.com",
            status_code=200,
            server_software="nginx",
            security_score=80,
        )
        with patch("kaos_web.domain.http.analyze_headers", AsyncMock(return_value=h)):
            result = await HttpHeadersTool().execute({"url": "https://example.com"})
        assert not _is_error(result)

    async def test_error(self) -> None:
        h = HttpHeadersResult(url="x", status_code=0, error="timeout")
        with patch("kaos_web.domain.http.analyze_headers", AsyncMock(return_value=h)):
            result = await HttpHeadersTool().execute({"url": "x"})
        assert _is_error(result)

    async def test_verify_tls_threaded_from_settings_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WEB2-001: HttpHeadersTool must read KaosWebSettings.domain_verify_tls
        # and pass it to analyze_headers(verify_tls=...). Default is False.
        monkeypatch.delenv("KAOS_WEB_DOMAIN_VERIFY_TLS", raising=False)
        h = HttpHeadersResult(url="https://x", status_code=200, security_score=0)
        mock_fn = AsyncMock(return_value=h)
        with patch("kaos_web.domain.http.analyze_headers", mock_fn):
            await HttpHeadersTool().execute({"url": "https://x"})
        assert mock_fn.await_count == 1
        assert mock_fn.await_args is not None
        kwargs = mock_fn.await_args.kwargs
        assert kwargs.get("verify_tls") is False

    async def test_verify_tls_threaded_from_settings_env_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAOS_WEB_DOMAIN_VERIFY_TLS", "true")
        h = HttpHeadersResult(url="https://x", status_code=200, security_score=0)
        mock_fn = AsyncMock(return_value=h)
        with patch("kaos_web.domain.http.analyze_headers", mock_fn):
            await HttpHeadersTool().execute({"url": "https://x"})
        assert mock_fn.await_args is not None
        kwargs = mock_fn.await_args.kwargs
        assert kwargs.get("verify_tls") is True


# ── ServiceDetectTool ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestServiceDetectExecute:
    async def test_missing_host(self) -> None:
        assert _is_error(await ServiceDetectTool().execute({}))

    async def test_success(self) -> None:
        s = ServiceProfile(host="example.com", server_software="nginx", cdn="Cloudflare")
        with patch("kaos_web.domain.service.detect_services", AsyncMock(return_value=s)):
            result = await ServiceDetectTool().execute({"host": "example.com"})
        assert not _is_error(result)


# ── DnsLookupTool ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDnsLookupExecute:
    async def test_missing_domain(self) -> None:
        assert _is_error(await DnsLookupTool().execute({}))

    async def test_success(self) -> None:
        results = [
            DnsQueryResult(
                query_name="example.com",
                record_type="A",
                status=DnsRecordStatus.SUCCESS,
                records=[DnsRecord(name="example.com", record_type="A", value="1.2.3.4")],
            )
        ]
        with patch("kaos_web.domain.dns.lookup_many", AsyncMock(return_value=results)):
            result = await DnsLookupTool().execute(
                {"domain": "example.com", "record_types": "A,MX"}
            )
        assert not _is_error(result)

    async def test_import_error(self) -> None:
        with patch(
            "kaos_web.domain.dns.lookup_many", AsyncMock(side_effect=ImportError("dnspython"))
        ):
            result = await DnsLookupTool().execute({"domain": "example.com"})
        assert _is_error(result)

    async def test_other_error(self) -> None:
        with patch("kaos_web.domain.dns.lookup_many", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await DnsLookupTool().execute({"domain": "example.com"})
        assert _is_error(result)


# ── DnsEnumerateTool ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDnsEnumerateExecute:
    async def test_missing_domain(self) -> None:
        assert _is_error(await DnsEnumerateTool().execute({}))

    async def test_success(self) -> None:
        prof = DnsProfile(
            domain="example.com",
            apex_domain="example.com",
            queries=[],
            nameservers=["ns1.example.com"],
            dnssec=True,
        )
        with patch("kaos_web.domain.dns.enumerate_dns", AsyncMock(return_value=prof)):
            result = await DnsEnumerateTool().execute({"domain": "example.com"})
        assert not _is_error(result)

    async def test_import_error(self) -> None:
        with patch("kaos_web.domain.dns.enumerate_dns", AsyncMock(side_effect=ImportError("dns"))):
            result = await DnsEnumerateTool().execute({"domain": "example.com"})
        assert _is_error(result)

    async def test_other_error(self) -> None:
        with patch("kaos_web.domain.dns.enumerate_dns", AsyncMock(side_effect=RuntimeError("x"))):
            result = await DnsEnumerateTool().execute({"domain": "example.com"})
        assert _is_error(result)


# ── DnsZoneTransferTool ─────────────────────────────────────────────


@pytest.mark.asyncio
class TestDnsZoneTransferExecute:
    async def test_missing_domain(self) -> None:
        assert _is_error(await DnsZoneTransferTool().execute({}))

    async def test_success_explicit_nameservers(self) -> None:
        results = [
            ZoneTransferResult(nameserver="ns1.example.com", status=ZoneTransferStatus.REFUSED)
        ]
        with patch(
            "kaos_web.domain.dns.attempt_zone_transfer",
            AsyncMock(side_effect=lambda *a, **kw: results[0]),
        ):
            result = await DnsZoneTransferTool().execute(
                {"domain": "example.com", "nameservers": "ns1.example.com,ns2.example.com"}
            )
        assert not _is_error(result)

    async def test_no_ns_records_discovered(self) -> None:
        empty = DnsQueryResult(
            query_name="example.com", record_type="NS", status=DnsRecordStatus.NXDOMAIN
        )
        with patch("kaos_web.domain.dns.lookup", AsyncMock(return_value=empty)):
            result = await DnsZoneTransferTool().execute({"domain": "example.com"})
        assert _is_error(result)

    async def test_discovers_nameservers(self) -> None:
        ns_query = DnsQueryResult(
            query_name="example.com",
            record_type="NS",
            status=DnsRecordStatus.SUCCESS,
            records=[DnsRecord(name="example.com", record_type="NS", value="ns1.example.com.")],
        )
        zres = ZoneTransferResult(nameserver="ns1.example.com", status=ZoneTransferStatus.REFUSED)
        with (
            patch("kaos_web.domain.dns.lookup", AsyncMock(return_value=ns_query)),
            patch("kaos_web.domain.dns.attempt_zone_transfer", AsyncMock(return_value=zres)),
        ):
            result = await DnsZoneTransferTool().execute({"domain": "example.com"})
        assert not _is_error(result)

    async def test_import_error(self) -> None:
        with patch(
            "kaos_web.domain.dns.attempt_zone_transfer",
            AsyncMock(side_effect=ImportError("dns")),
        ):
            result = await DnsZoneTransferTool().execute(
                {"domain": "example.com", "nameservers": "ns1.example.com"}
            )
        assert _is_error(result)

    async def test_other_error(self) -> None:
        with patch(
            "kaos_web.domain.dns.attempt_zone_transfer",
            AsyncMock(side_effect=RuntimeError("x")),
        ):
            result = await DnsZoneTransferTool().execute(
                {"domain": "example.com", "nameservers": "ns1.example.com"}
            )
        assert _is_error(result)


# ── DnsSecurityTool ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDnsSecurityExecute:
    async def test_missing_domain(self) -> None:
        assert _is_error(await DnsSecurityTool().execute({}))

    async def test_success(self) -> None:
        report = MailSecurityReport(
            domain="example.com",
            records=[
                MailAuthRecord(mechanism=MailAuthMechanism.SPF, status=MailAuthStatus.CONFIGURED),
                MailAuthRecord(mechanism=MailAuthMechanism.DKIM, status=MailAuthStatus.CONFIGURED),
                MailAuthRecord(mechanism=MailAuthMechanism.DMARC, status=MailAuthStatus.CONFIGURED),
            ],
            overall_posture="strong",
        )
        with patch(
            "kaos_web.domain.security.analyze_mail_security",
            AsyncMock(return_value=report),
        ):
            result = await DnsSecurityTool().execute({"domain": "example.com"})
        assert not _is_error(result)

    async def test_import_error(self) -> None:
        with patch(
            "kaos_web.domain.security.analyze_mail_security",
            AsyncMock(side_effect=ImportError("dns")),
        ):
            result = await DnsSecurityTool().execute({"domain": "example.com"})
        assert _is_error(result)

    async def test_other_error(self) -> None:
        with patch(
            "kaos_web.domain.security.analyze_mail_security",
            AsyncMock(side_effect=RuntimeError("x")),
        ):
            result = await DnsSecurityTool().execute({"domain": "example.com"})
        assert _is_error(result)


# ── WhoisLookupTool ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestWhoisExecute:
    async def test_missing_domain(self) -> None:
        assert _is_error(await WhoisLookupTool().execute({}))

    async def test_success(self) -> None:
        rec = WhoisRecord(
            domain="example.com",
            registrar="Registrar Inc",
            expiration_date="2026-01-01T00:00:00",
        )
        with patch("kaos_web.domain.whois.whois_lookup", AsyncMock(return_value=rec)):
            result = await WhoisLookupTool().execute({"domain": "example.com"})
        assert not _is_error(result)

    async def test_no_registrar_fallback_summary(self) -> None:
        rec = WhoisRecord(domain="example.com")
        with patch("kaos_web.domain.whois.whois_lookup", AsyncMock(return_value=rec)):
            result = await WhoisLookupTool().execute({"domain": "example.com"})
        assert not _is_error(result)

    async def test_error(self) -> None:
        rec = WhoisRecord(domain="example.com", error="timeout")
        with patch("kaos_web.domain.whois.whois_lookup", AsyncMock(return_value=rec)):
            result = await WhoisLookupTool().execute({"domain": "example.com"})
        assert _is_error(result)


# ── DomainProfileTool ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestDomainProfileExecute:
    async def test_missing_domain(self) -> None:
        assert _is_error(await DomainProfileTool().execute({}))

    async def test_success(self) -> None:
        prof = DomainProfile(
            domain="example.com",
            services=ServiceProfile(host="example.com", server_software="nginx", cdn="Cloudflare"),
            mail_security=MailSecurityReport(domain="example.com", overall_posture="strong"),
            whois=WhoisRecord(domain="example.com", registrar="Reg Inc", raw_text="REDACT"),
        )
        with patch("kaos_web.domain.profile.profile_domain", AsyncMock(return_value=prof)):
            result = await DomainProfileTool().execute({"domain": "example.com"})
        assert not _is_error(result)

    async def test_error(self) -> None:
        with patch(
            "kaos_web.domain.profile.profile_domain",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await DomainProfileTool().execute({"domain": "example.com"})
        assert _is_error(result)


# ── ExtractOrgTool ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestExtractOrgExecute:
    async def test_missing_url(self) -> None:
        assert _is_error(await ExtractOrgTool().execute({}))

    async def test_success(self, httpx_mock: Any) -> None:
        html = (
            "<html><head><title>ACME</title>"
            '<script type="application/ld+json">{"@type":"Organization",'
            '"name":"Acme"}</script></head><body></body></html>'
        )
        httpx_mock.add_response(
            method="GET", url="https://acme.example/", text=html, status_code=200
        )
        result = await ExtractOrgTool().execute({"url": "https://acme.example/"})
        assert not _is_error(result)

    async def test_verify_tls_threaded_from_settings_default(
        self, monkeypatch: pytest.MonkeyPatch, httpx_mock: Any
    ) -> None:
        # WEB2-001: ExtractOrgTool reads KaosWebSettings.domain_verify_tls
        # and passes it to httpx.AsyncClient(verify=...). Default False.
        monkeypatch.delenv("KAOS_WEB_DOMAIN_VERIFY_TLS", raising=False)
        captured: dict[str, Any] = {}
        import httpx

        real_client = httpx.AsyncClient

        def _spy(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            captured.update(kwargs)
            return real_client(*args, **kwargs)

        httpx_mock.add_response(
            method="GET", url="https://acme.example/", text="<html></html>", status_code=200
        )
        with patch("httpx.AsyncClient", side_effect=_spy):
            await ExtractOrgTool().execute({"url": "https://acme.example/"})
        assert captured.get("verify") is False

    async def test_verify_tls_threaded_from_settings_env_true(
        self, monkeypatch: pytest.MonkeyPatch, httpx_mock: Any
    ) -> None:
        monkeypatch.setenv("KAOS_WEB_DOMAIN_VERIFY_TLS", "true")
        captured: dict[str, Any] = {}
        import httpx

        real_client = httpx.AsyncClient

        def _spy(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            captured.update(kwargs)
            return real_client(*args, **kwargs)

        httpx_mock.add_response(
            method="GET", url="https://acme.example/", text="<html></html>", status_code=200
        )
        with patch("httpx.AsyncClient", side_effect=_spy):
            await ExtractOrgTool().execute({"url": "https://acme.example/"})
        assert captured.get("verify") is True

    async def test_fetch_failure(self, httpx_mock: Any) -> None:
        import httpx

        httpx_mock.add_exception(httpx.ConnectError("nope"))
        result = await ExtractOrgTool().execute({"url": "https://broken.example/"})
        assert _is_error(result)
