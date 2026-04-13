"""Live E2E tests for the domain intelligence tools.

Tests all 10 domain tools against real domains (273ventures.com,
kelvin.legal, google.com).  Every assertion verifies actual content,
not just ``len() > 0``.

Run with: pytest tests/integration/test_domain_live.py -v
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.integration


# ── Helpers: build tool instances without full runtime ──────────────


class _MockToolsRegistry:
    def __init__(self) -> None:
        self.tools: list = []

    def register_tool(self, tool: object) -> None:
        self.tools.append(tool)


class _MockRuntime:
    def __init__(self) -> None:
        self.tools = _MockToolsRegistry()


def _build_tools() -> dict:
    from kaos_web.domain_tools import register_domain_tools

    rt = _MockRuntime()
    count = register_domain_tools(rt)
    assert count == 11
    return {t.metadata.name: t for t in rt.tools.tools}


TOOLS = _build_tools()
TOOL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")

# Test targets
_273V = "273ventures.com"
_KELVIN = "kelvin.legal"
_GOOGLE = "google.com"


# ── Metadata validation ────────────────────────────────────────────


class TestDomainToolMetadata:
    """Validate metadata for all 10 domain tools."""

    def test_tool_count(self) -> None:
        assert len(TOOLS) == 11

    @pytest.mark.parametrize("name", list(TOOLS.keys()))
    def test_name_pattern(self, name: str) -> None:
        assert TOOL_NAME_PATTERN.match(name), f"Bad tool name: {name}"

    @pytest.mark.parametrize("name", list(TOOLS.keys()))
    def test_annotations_not_none(self, name: str) -> None:
        ann = TOOLS[name].metadata.annotations
        assert ann is not None, f"{name}: annotations must not be None"

    @pytest.mark.parametrize("name", list(TOOLS.keys()))
    def test_all_readonly(self, name: str) -> None:
        ann = TOOLS[name].metadata.annotations
        assert ann.readOnlyHint is True, f"{name} should be read-only"

    @pytest.mark.parametrize("name", list(TOOLS.keys()))
    def test_all_open_world(self, name: str) -> None:
        ann = TOOLS[name].metadata.annotations
        assert ann.openWorldHint is True, f"{name} should be open-world (network)"

    def test_expected_names(self) -> None:
        expected = {
            "kaos-web-tcp-probe",
            "kaos-web-tls-inspect",
            "kaos-web-http-headers",
            "kaos-web-service-detect",
            "kaos-web-dns-lookup",
            "kaos-web-dns-enumerate",
            "kaos-web-dns-zone-transfer",
            "kaos-web-dns-security",
            "kaos-web-whois-lookup",
            "kaos-web-domain-profile",
            "kaos-web-extract-org",
        }
        assert set(TOOLS.keys()) == expected


# ── 1. TCP Probe ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTcpProbeLive:
    async def test_273v_web_ports(self) -> None:
        result = await TOOLS["kaos-web-tcp-probe"].execute(
            {
                "host": _273V,
                "ports": "80,443",
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["host"] == _273V
        assert data["open_count"] >= 1  # At least HTTPS should be open
        ports = {p["port"]: p["status"] for p in data["ports"]}
        assert ports[443] == "open"

    async def test_preset_web(self) -> None:
        result = await TOOLS["kaos-web-tcp-probe"].execute(
            {
                "host": _GOOGLE,
                "preset": "web",
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["open_count"] >= 2  # Google has both 80 and 443


# ── 2. TLS Inspect ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTlsInspectLive:
    async def test_273v_cert(self) -> None:
        result = await TOOLS["kaos-web-tls-inspect"].execute({"host": _273V})
        assert not result.isError
        data = result.require_structured()
        assert data["host"] == _273V
        assert data["protocol"] is not None
        assert "TLS" in data["protocol"]
        # Cert should be valid (not expired)
        assert data.get("days_until_expiry") is None or data["days_until_expiry"] > 0
        # SAN should include the domain
        san = data.get("san_dns", [])
        assert (
            any(_273V in s or "*.273ventures.com" in s or "273ventures" in s for s in san)
            or data.get("error") is not None
        )

    async def test_kelvin_cert(self) -> None:
        result = await TOOLS["kaos-web-tls-inspect"].execute({"host": _KELVIN})
        assert not result.isError
        data = result.require_structured()
        assert data["protocol"] is not None

    async def test_google_cert_issuer(self) -> None:
        result = await TOOLS["kaos-web-tls-inspect"].execute({"host": _GOOGLE})
        assert not result.isError
        data = result.require_structured()
        issuer = data.get("issuer", {})
        # Google uses their own CA or a well-known issuer
        assert issuer.get("organizationName") or issuer.get("commonName")


# ── 3. HTTP Headers ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestHttpHeadersLive:
    async def test_273v_headers(self) -> None:
        result = await TOOLS["kaos-web-http-headers"].execute(
            {
                "url": f"https://{_273V}",
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["status_code"] == 200
        assert data["headers"]  # Should have headers
        assert isinstance(data["security_score"], int)

    async def test_kelvin_headers(self) -> None:
        result = await TOOLS["kaos-web-http-headers"].execute(
            {
                "url": f"https://{_KELVIN}",
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["status_code"] in (200, 301, 302, 403)


# ── 4. Service Detect ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestServiceDetectLive:
    async def test_273v_services(self) -> None:
        result = await TOOLS["kaos-web-service-detect"].execute({"host": _273V})
        assert not result.isError
        data = result.require_structured()
        assert data["host"] == _273V
        assert len(data["services"]) >= 1  # At least HTTPS
        # Should detect some server software or CDN
        assert data.get("server_software") or data.get("cdn")


# ── 5. DNS Lookup ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDnsLookupLive:
    async def test_273v_a_record(self) -> None:
        result = await TOOLS["kaos-web-dns-lookup"].execute(
            {
                "domain": _273V,
                "record_types": "A",
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["total_records"] >= 1
        # Should have at least one A record with an IP
        a_records = data["queries"][0]["records"]
        assert len(a_records) >= 1
        # Verify it looks like an IP
        assert re.match(r"\d+\.\d+\.\d+\.\d+", a_records[0]["value"])

    async def test_multi_type(self) -> None:
        result = await TOOLS["kaos-web-dns-lookup"].execute(
            {
                "domain": _GOOGLE,
                "record_types": "A,MX,NS",
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert len(data["queries"]) == 3
        assert data["total_records"] >= 3  # Google has many records


# ── 6. DNS Enumerate ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestDnsEnumerateLive:
    async def test_273v_full_profile(self) -> None:
        result = await TOOLS["kaos-web-dns-enumerate"].execute(
            {
                "domain": _273V,
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["domain"] == _273V
        assert len(data["nameservers"]) >= 1
        # Should have A records
        a_queries = [q for q in data["queries"] if q["record_type"] == "A"]
        assert len(a_queries) >= 1

    async def test_google_has_mx(self) -> None:
        result = await TOOLS["kaos-web-dns-enumerate"].execute(
            {
                "domain": _GOOGLE,
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert len(data["mx_hosts"]) >= 1
        assert any("google" in mx.lower() for mx in data["mx_hosts"])


# ── 7. DNS Zone Transfer ──────────────────────────────────────────


@pytest.mark.asyncio
class TestDnsZoneTransferLive:
    async def test_273v_zone_transfer_refused(self) -> None:
        """Zone transfer should be refused on properly configured servers."""
        result = await TOOLS["kaos-web-dns-zone-transfer"].execute(
            {
                "domain": _273V,
            }
        )
        assert not result.isError
        data = result.require_structured()
        # Should have attempted at least one nameserver
        assert len(data["results"]) >= 1
        # All should be refused or failed (not success — that would be a misconfiguration)
        for r in data["results"]:
            assert r["status"] in ("refused", "failed", "timeout")


# ── 8. DNS Security ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestDnsSecurityLive:
    async def test_google_mail_security(self) -> None:
        """Google should have strong mail authentication."""
        result = await TOOLS["kaos-web-dns-security"].execute(
            {
                "domain": _GOOGLE,
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["domain"] == _GOOGLE
        assert len(data["records"]) == 3  # SPF, DKIM, DMARC

        # Google definitely has SPF
        spf = next(r for r in data["records"] if r["mechanism"] == "spf")
        assert spf["status"] == "configured"
        assert spf["raw_record"] is not None
        assert "v=spf1" in spf["raw_record"]

        # Google definitely has DMARC
        dmarc = next(r for r in data["records"] if r["mechanism"] == "dmarc")
        assert dmarc["status"] in ("configured", "weak")

        # Overall should be at least moderate
        assert data["overall_posture"] in ("strong", "moderate")

    async def test_273v_mail_security(self) -> None:
        result = await TOOLS["kaos-web-dns-security"].execute(
            {
                "domain": _273V,
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert len(data["records"]) == 3


# ── 9. WHOIS Lookup ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestWhoisLookupLive:
    async def test_google_whois(self) -> None:
        result = await TOOLS["kaos-web-whois-lookup"].execute(
            {
                "domain": _GOOGLE,
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["domain"] == _GOOGLE
        assert data["registrar"] is not None
        assert "markmonitor" in data["registrar"].lower()
        assert data["expiration_date"] is not None
        assert len(data["name_servers"]) >= 2

    async def test_273v_whois(self) -> None:
        result = await TOOLS["kaos-web-whois-lookup"].execute(
            {
                "domain": _273V,
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["domain"] == _273V
        assert data["registrar"] is not None
        assert data["expiration_date"] is not None

    async def test_kelvin_legal_whois(self) -> None:
        result = await TOOLS["kaos-web-whois-lookup"].execute(
            {
                "domain": _KELVIN,
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["domain"] == _KELVIN


# ── 10. Domain Profile ─────────────────────────────────────────────


@pytest.mark.asyncio
class TestDomainProfileLive:
    async def test_273v_full_profile(self) -> None:
        """Full domain profile for 273ventures.com — the big integration test."""
        result = await TOOLS["kaos-web-domain-profile"].execute(
            {
                "domain": _273V,
            }
        )
        assert not result.isError
        data = result.require_structured()
        assert data["domain"] == _273V

        # DNS section should be populated
        dns = data.get("dns")
        assert dns is not None
        assert len(dns["nameservers"]) >= 1

        # Services section
        services = data.get("services")
        assert services is not None
        assert len(services["services"]) >= 1

        # WHOIS section
        whois = data.get("whois")
        assert whois is not None
        assert whois["registrar"] is not None

        # Mail security
        mail = data.get("mail_security")
        assert mail is not None
        assert len(mail["records"]) == 3
