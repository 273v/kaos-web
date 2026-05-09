"""MCP tools for domain intelligence: TCP, TLS, HTTP, DNS, UDP, WHOIS, and profiling.

14 tools organized by dependency tier:
- Pure stdlib (1-4): tcp-probe, tls-inspect, http-headers, service-detect
- Requires dnspython (5-8): dns-lookup, dns-enumerate, dns-zone-transfer, dns-security
- Stdlib WHOIS (9): whois-lookup
- Composite (10): domain-profile
- HTML/JSON-LD (11): extract-org
- Pure stdlib (12-14): tcp-banner, fingerprint-service, udp-probe
"""

from __future__ import annotations

from typing import Any

from kaos_core import KaosContext, KaosRuntime, KaosTool, ToolMetadata, ToolResult
from kaos_core.types.annotations import ToolAnnotations
from kaos_core.types.enums import ToolCapability, ToolCategory
from kaos_core.types.parameters import ParameterSchema

_MODULE = "kaos-web"
_VERSION = "0.1.0"

_DOMAIN_LOCAL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


# ── 1. kaos-web-tcp-probe ───────────────────────────────────────────


class TcpProbeTool(KaosTool):
    """Probe TCP ports on a host."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-tcp-probe",
            display_name="TCP Port Probe",
            description=(
                "Probe one or more TCP ports on a host. Reports open/closed/timeout "
                "with connection latency. Use preset='web' for 80,443,8080,8443 or "
                "preset='mail' for 25,465,587,993,995 or preset='default' for common "
                "ports. For full service fingerprinting, use kaos-web-service-detect."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="host", type="string", description="Hostname or IP address."),
                ParameterSchema(
                    name="ports",
                    type="string",
                    description="Comma-separated ports (e.g. '80,443,8080'). Overrides preset.",
                    required=False,
                ),
                ParameterSchema(
                    name="preset",
                    type="string",
                    description="Port preset: web, mail, ssh, dns, ftp, database, default.",
                    required=False,
                    default="web",
                    constraints={
                        "enum": ["web", "mail", "ssh", "dns", "ftp", "database", "default"]
                    },
                ),
                ParameterSchema(
                    name="timeout",
                    type="number",
                    description="Per-port timeout in seconds (default 5).",
                    required=False,
                    default=5.0,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.tcp import probe_ports

        host = inputs.get("host", "")
        if not host:
            return ToolResult.create_error("Parameter 'host' is required.")

        ports_str = inputs.get("ports")
        port_list = None
        if ports_str:
            try:
                port_list = [int(p.strip()) for p in ports_str.split(",")]
            except ValueError:
                return ToolResult.create_error(
                    f"Invalid ports: {ports_str}. Provide comma-separated integers (e.g. '80,443')."
                )

        preset = inputs.get("preset", "web")
        timeout = inputs.get("timeout", 5.0)

        try:
            result = await probe_ports(host, port_list, preset=preset, timeout=timeout)
        except Exception as exc:
            return ToolResult.create_error(
                f"TCP probe failed for {host}: {exc}. "
                "Verify the domain resolves with kaos-web-dns-lookup, or try "
                "kaos-web-tls-inspect for HTTPS connectivity."
            )

        output = result.model_dump(mode="json")
        return ToolResult.create_success(
            output,
            summary=(
                f"{host}: {result.open_count} open, {result.closed_count} closed, "
                f"{result.timeout_count} timeout"
            ),
        )


# ── 2. kaos-web-tls-inspect ─────────────────────────────────────────


class TlsInspectTool(KaosTool):
    """Inspect TLS certificate for a host."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-tls-inspect",
            display_name="TLS Certificate Inspect",
            description=(
                "Connect to a host:port and extract TLS certificate info: subject, "
                "issuer, SAN, validity dates, days until expiry, protocol version, "
                "cipher. For HTTP-level analysis, use kaos-web-http-headers."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="host", type="string", description="Hostname."),
                ParameterSchema(
                    name="port",
                    type="integer",
                    description="TLS port (default 443).",
                    required=False,
                    default=443,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.tls import inspect_tls

        host = inputs.get("host", "")
        if not host:
            return ToolResult.create_error("Parameter 'host' is required.")

        port = inputs.get("port", 443)

        result = await inspect_tls(host, port)
        if result.error:
            return ToolResult.create_error(
                f"TLS inspection failed for {host}:{port}: {result.error}. "
                "Try kaos-web-tcp-probe to check basic port connectivity, or "
                "kaos-web-http-headers for HTTP-level analysis."
            )

        output = result.model_dump(mode="json", exclude_none=True)
        expiry = (
            f", expires in {result.days_until_expiry}d"
            if result.days_until_expiry is not None
            else ""
        )
        return ToolResult.create_success(
            output,
            summary=f"{host}:{port} — {result.protocol or 'unknown'}{expiry}",
        )


# ── 3. kaos-web-http-headers ────────────────────────────────────────


class HttpHeadersTool(KaosTool):
    """Analyze HTTP headers and security posture."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-http-headers",
            display_name="HTTP Header Analysis",
            description=(
                "Send a HEAD request and analyze response headers. Returns status code, "
                "all headers, server software, CDN detection, and security header analysis "
                "(HSTS, CSP, X-Frame-Options, etc.) with a 0-100 security score. "
                "For TLS certificate details, use kaos-web-tls-inspect. "
                "SECURITY: TLS verification is ON by default (secure-by-default). "
                "Set KAOS_WEB_DOMAIN_VERIFY_TLS=false to inspect hosts whose cert is "
                "itself the subject of inspection (self-signed, expired, mismatched "
                "SAN, staging environments). For trusted-endpoint GETs use "
                "kaos-web-fetch-page instead."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(
                    name="url", type="string", description="Full URL (e.g. https://example.com)."
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.http import analyze_headers
        from kaos_web.settings import KaosWebSettings

        url = inputs.get("url", "")
        if not url:
            return ToolResult.create_error(
                "Parameter 'url' is required (e.g. 'https://example.com')."
            )

        settings = KaosWebSettings.from_context(context)
        result = await analyze_headers(url, verify_tls=settings.domain_verify_tls)
        if result.error:
            return ToolResult.create_error(
                f"HTTP request failed: {result.error}. "
                "Try kaos-web-tls-inspect for certificate-level analysis, or "
                "kaos-web-tcp-probe to verify the host is reachable."
            )

        output = result.model_dump(mode="json", exclude_none=True)
        return ToolResult.create_success(
            output,
            summary=(
                f"{result.status_code} {result.server_software or ''} — "
                f"security score {result.security_score}/100"
            ),
        )


# ── 4. kaos-web-service-detect ──────────────────────────────────────


class ServiceDetectTool(KaosTool):
    """Detect web services on a domain."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-service-detect",
            display_name="Service Detection",
            description=(
                "Probe ports 80 and 443, inspect TLS certificate, read HTTP headers, "
                "and identify server software, CDN, and technology hints. Returns a "
                "combined service profile. For deeper port scanning, use kaos-web-tcp-probe."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="host", type="string", description="Hostname to probe."),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.service import detect_services

        host = inputs.get("host", "")
        if not host:
            return ToolResult.create_error("Parameter 'host' is required.")

        result = await detect_services(host)
        output = result.model_dump(mode="json", exclude_none=True)

        parts = []
        if result.server_software:
            parts.append(result.server_software)
        if result.cdn:
            parts.append(f"CDN: {result.cdn}")
        parts.append(f"{len(result.services)} service(s)")

        return ToolResult.create_success(output, summary=f"{host}: {', '.join(parts)}")


# ── 5. kaos-web-dns-lookup ──────────────────────────────────────────


class DnsLookupTool(KaosTool):
    """Look up specific DNS record types."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-dns-lookup",
            display_name="DNS Lookup",
            description=(
                "Query one or more DNS record types for a domain. Returns records with "
                "TTL and response time. For a full DNS profile, use kaos-web-dns-enumerate. "
                "For mail auth analysis, use kaos-web-dns-security."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="domain", type="string", description="Domain name to query."),
                ParameterSchema(
                    name="record_types",
                    type="string",
                    description=(
                        "Comma-separated record types (default: 'A'). E.g. 'A,AAAA,MX,NS,TXT'."
                    ),
                    required=False,
                    default="A",
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.dns import lookup_many

        domain = inputs.get("domain", "")
        if not domain:
            return ToolResult.create_error("Parameter 'domain' is required.")

        rt_str = inputs.get("record_types", "A")
        record_types = [r.strip().upper() for r in rt_str.split(",")]

        try:
            results = await lookup_many(domain, record_types)
        except ImportError:
            return ToolResult.create_error(
                "dnspython is required for DNS tools. "
                "Install with: pip install dnspython. "
                "Try kaos-web-whois-lookup for alternative domain information without dnspython."
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"DNS lookup failed for {domain}: {exc}. "
                "Try kaos-web-whois-lookup for registration-based domain information, or "
                "kaos-web-tcp-probe to check host connectivity directly."
            )

        output = {
            "domain": domain,
            "queries": [r.model_dump(mode="json") for r in results],
            "total_records": sum(len(r.records) for r in results),
        }
        return ToolResult.create_success(
            output,
            summary=(
                f"{domain}: {output['total_records']} record(s) across {len(record_types)} type(s)"
            ),
        )


# ── 6. kaos-web-dns-enumerate ───────────────────────────────────────


class DnsEnumerateTool(KaosTool):
    """Full DNS enumeration for a domain."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-dns-enumerate",
            display_name="DNS Enumeration",
            description=(
                "Query all common DNS record types (A, AAAA, MX, NS, TXT, SOA, CNAME, "
                "CAA, SRV, DNSKEY, DS) plus reverse PTR for discovered IPs and DNSSEC "
                "detection. Returns a complete DNS profile. For zone transfer attempts, "
                "use kaos-web-dns-zone-transfer."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="domain", type="string", description="Domain to enumerate."),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.dns import enumerate_dns

        domain = inputs.get("domain", "")
        if not domain:
            return ToolResult.create_error("Parameter 'domain' is required.")

        try:
            result = await enumerate_dns(domain)
        except ImportError:
            return ToolResult.create_error(
                "dnspython is required for DNS tools. Install with: pip install dnspython. "
                "Try kaos-web-whois-lookup for alternative domain information without dnspython."
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"DNS enumeration failed for {domain}: {exc}. "
                "Try kaos-web-dns-lookup for a targeted single-type query, or "
                "kaos-web-whois-lookup for registration-based domain information."
            )

        output = result.model_dump(mode="json")
        total_records = sum(len(q.records) for q in result.queries)
        parts = [f"{total_records} records"]
        if result.nameservers:
            parts.append(f"NS: {', '.join(result.nameservers[:3])}")
        if result.dnssec:
            parts.append("DNSSEC: yes")
        return ToolResult.create_success(output, summary=f"{domain}: {', '.join(parts)}")


# ── 7. kaos-web-dns-zone-transfer ───────────────────────────────────


class DnsZoneTransferTool(KaosTool):
    """Attempt DNS zone transfer against nameservers."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-dns-zone-transfer",
            display_name="DNS Zone Transfer",
            description=(
                "Attempt AXFR zone transfer against a domain's authoritative nameservers. "
                "Zone transfers are usually refused on public nameservers — a 'refused' "
                "result is normal. A 'success' indicates a misconfiguration. "
                "Run kaos-web-dns-enumerate first to discover nameservers."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="domain", type="string", description="Zone apex domain."),
                ParameterSchema(
                    name="nameservers",
                    type="string",
                    description=(
                        "Comma-separated nameserver hostnames. If omitted, discovers via NS lookup."
                    ),
                    required=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        import asyncio

        from kaos_web.domain.dns import attempt_zone_transfer, lookup

        domain = inputs.get("domain", "")
        if not domain:
            return ToolResult.create_error("Parameter 'domain' is required.")

        ns_str = inputs.get("nameservers")
        if ns_str:
            nameservers = [ns.strip() for ns in ns_str.split(",")]
        else:
            ns_result = await lookup(domain, "NS")
            if not ns_result.records:
                return ToolResult.create_error(
                    f"No NS records found for {domain}. Provide nameservers explicitly, "
                    "or use kaos-web-dns-enumerate to discover the full DNS profile first."
                )
            nameservers = [r.value.rstrip(".") for r in ns_result.records]

        try:
            results = await asyncio.gather(
                *[attempt_zone_transfer(domain, ns) for ns in nameservers]
            )
        except ImportError:
            return ToolResult.create_error(
                "dnspython is required for zone transfer. Install with: pip install dnspython. "
                "Try kaos-web-dns-lookup for standard DNS queries without zone transfer."
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Zone transfer failed for {domain}: {exc}. "
                "Try kaos-web-dns-enumerate for standard DNS enumeration without AXFR."
            )

        output = {
            "domain": domain,
            "results": [r.model_dump(mode="json") for r in results],
        }
        statuses = [r.status.value for r in results]
        return ToolResult.create_success(output, summary=f"{domain}: {', '.join(statuses)}")


# ── 8. kaos-web-dns-security ────────────────────────────────────────


class DnsSecurityTool(KaosTool):
    """Analyze mail authentication (SPF, DKIM, DMARC)."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-dns-security",
            display_name="Mail Security Analysis",
            description=(
                "Check SPF, DKIM (common selectors), and DMARC records for a domain. "
                "Reports mail authentication posture, policy strength, and common "
                "misconfigurations. Overall rating: strong/moderate/weak/missing."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.ANALYZE,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="domain", type="string", description="Domain to check."),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.security import analyze_mail_security

        domain = inputs.get("domain", "")
        if not domain:
            return ToolResult.create_error("Parameter 'domain' is required.")

        try:
            result = await analyze_mail_security(domain)
        except ImportError:
            return ToolResult.create_error(
                "dnspython is required for mail security analysis. "
                "Install with: pip install dnspython. "
                "Try kaos-web-tls-inspect for certificate-only security analysis without dnspython."
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Mail security analysis failed for {domain}: {exc}. "
                "Try kaos-web-dns-lookup with record_types='TXT' for raw SPF/DMARC records, or "
                "kaos-web-tls-inspect for certificate-level security analysis."
            )

        output = result.model_dump(mode="json")
        mechs = [f"{r.mechanism.value}={r.status.value}" for r in result.records]
        return ToolResult.create_success(
            output,
            summary=f"{domain}: {result.overall_posture} ({', '.join(mechs)})",
        )


# ── 9. kaos-web-whois-lookup ────────────────────────────────────────


class WhoisLookupTool(KaosTool):
    """Look up WHOIS registration data."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-whois-lookup",
            display_name="WHOIS Lookup",
            description=(
                "Query WHOIS registration data for a domain: registrar, creation/expiry "
                "dates, nameservers, registrant info, status. Uses direct TCP connection "
                "to WHOIS servers (port 43). No external dependencies."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="domain", type="string", description="Domain to look up."),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.whois import whois_lookup

        domain = inputs.get("domain", "")
        if not domain:
            return ToolResult.create_error("Parameter 'domain' is required.")

        result = await whois_lookup(domain)
        if result.error:
            return ToolResult.create_error(
                f"WHOIS lookup failed for {domain}: {result.error}. "
                "Try kaos-web-dns-lookup for DNS-based domain information, or "
                "kaos-web-dns-enumerate for a full DNS profile."
            )

        # Exclude raw_text from default output (it's large)
        output = result.model_dump(mode="json", exclude={"raw_text"})

        parts = []
        if result.registrar:
            parts.append(result.registrar)
        if result.expiration_date:
            parts.append(f"expires {result.expiration_date[:10]}")
        summary = f"{domain}: {', '.join(parts)}" if parts else f"{domain}: WHOIS data retrieved"

        return ToolResult.create_success(output, summary=summary)


# ── 10. kaos-web-domain-profile ─────────────────────────────────────


class DomainProfileTool(KaosTool):
    """One-shot domain intelligence profile."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-domain-profile",
            display_name="Domain Profile",
            description=(
                "Comprehensive domain intelligence in one call: DNS enumeration, WHOIS, "
                "HTTP/TLS service detection, and mail security (SPF/DKIM/DMARC). "
                "For individual checks, use kaos-web-dns-enumerate, kaos-web-whois-lookup, "
                "kaos-web-service-detect, or kaos-web-dns-security."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="domain", type="string", description="Domain to profile."),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.profile import profile_domain

        domain = inputs.get("domain", "")
        if not domain:
            return ToolResult.create_error("Parameter 'domain' is required.")

        try:
            result = await profile_domain(domain)
        except Exception as exc:
            return ToolResult.create_error(
                f"Domain profiling failed for {domain}: {exc}. "
                "Try individual tools instead: kaos-web-dns-enumerate for DNS, "
                "kaos-web-whois-lookup for registration data, kaos-web-service-detect "
                "for HTTP/TLS services, or kaos-web-dns-security for mail authentication."
            )

        output = result.model_dump(mode="json", exclude_none=True)

        # Exclude WHOIS raw text from output
        if "whois" in output and output["whois"] and "raw_text" in output["whois"]:
            del output["whois"]["raw_text"]

        parts = [domain]
        if result.services and result.services.server_software:
            parts.append(result.services.server_software)
        if result.services and result.services.cdn:
            parts.append(f"CDN: {result.services.cdn}")
        if result.mail_security:
            parts.append(f"mail: {result.mail_security.overall_posture}")
        if result.whois and result.whois.registrar:
            parts.append(result.whois.registrar)

        return ToolResult.create_success(output, summary=" | ".join(parts))


# ── 11. kaos-web-extract-org ─────────────────────────────────────────


class ExtractOrgTool(KaosTool):
    """Extract organization entity data from a website."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-extract-org",
            display_name="Extract Organization Entity",
            description=(
                "Extract legal entity information from a website: name, legal name, "
                "type, description, address, phone, email, social links, founding date, "
                "jurisdiction, registration number, and entity form (LLC, Inc., etc.). "
                "Uses JSON-LD structured data, OpenGraph, meta tags, and footer text "
                "pattern matching. No LLM required. "
                "For full domain infrastructure, use kaos-web-domain-profile. "
                "For GLEIF entity lookup, use kaos-source-gleif-search. "
                "SECURITY: TLS verification is ON by default (secure-by-default). "
                "Set KAOS_WEB_DOMAIN_VERIFY_TLS=false when targeting hosts whose "
                "cert is the subject of inspection — self-signed, expired, or "
                "mismatched-SAN sites you want to extract entity metadata from "
                "regardless. For trusted-endpoint GETs use kaos-web-fetch-page."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(
                    name="url",
                    type="string",
                    description="URL to extract org entity from (e.g. 'https://273ventures.com').",
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.settings import KaosWebSettings

        url = inputs.get("url", "")
        if not url:
            return ToolResult.create_error(
                "Parameter 'url' is required (e.g. 'https://273ventures.com')."
            )

        import httpx

        settings = KaosWebSettings.from_context(context)
        try:
            # TLS verification is ON by default (secure-by-default per WEB5-006).
            # The typical use case is extracting org metadata from healthy public
            # sites; CA validation is the right behavior. Set
            # KAOS_WEB_DOMAIN_VERIFY_TLS=false explicitly when you need to scrape
            # entity metadata from sites whose cert configuration is itself
            # broken (self-signed, expired, mismatched SAN, staging). Disabling
            # verification returns metadata you'd otherwise be blocked from
            # observing; it does NOT make the returned HTML trusted content.
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                verify=settings.domain_verify_tls,
            ) as client:
                resp = await client.get(url)
                html = resp.text
                final_url = str(resp.url)
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Try kaos-web-get-metadata for structured data extraction via a different "
                "HTTP path, or kaos-web-domain-profile for infrastructure-level "
                "domain intelligence."
            )

        from kaos_web.domain.org import extract_org_entity

        entity = extract_org_entity(html, url=final_url)
        output = entity.model_dump(mode="json", exclude_none=True)

        parts = []
        if entity.name:
            parts.append(entity.name)
        if entity.org_type:
            parts.append(entity.org_type)
        if entity.jurisdiction:
            parts.append(entity.jurisdiction)
        if entity.entity_form:
            parts.append(entity.entity_form)
        summary = " | ".join(parts) if parts else f"Org entity from {url}"

        return ToolResult.create_success(output, summary=summary)


# ── 12. kaos-web-tcp-banner ─────────────────────────────────────────


class TcpBannerTool(KaosTool):
    """Grab a TCP banner from a single (host, port)."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-tcp-banner",
            display_name="TCP Banner Grab",
            description=(
                "Open a TCP connection and capture the service's banner. "
                "Many services greet on connect (SSH, SMTP, FTP, POP3, IMAP) — "
                "leave 'send_probe' empty to wait for the unsolicited greeting. "
                "Request/response protocols (HTTP, Redis) need a probe — for "
                "HTTP, send 'HEAD / HTTP/1.0\\r\\n\\r\\n'. The connection is "
                "closed after the first read. To identify the service from the "
                "captured banner, pass it to kaos-web-fingerprint-service. "
                "For full-port sweeps, use kaos-web-tcp-probe first."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="host", type="string", description="Hostname or IP address."),
                ParameterSchema(name="port", type="integer", description="TCP port number."),
                ParameterSchema(
                    name="send_probe",
                    type="string",
                    description=(
                        "Optional probe payload sent as UTF-8 bytes after connect. "
                        "Use '' (empty) for greet-on-connect services."
                    ),
                    required=False,
                ),
                ParameterSchema(
                    name="timeout",
                    type="number",
                    description="Per-stage timeout (connect, send, read) in seconds.",
                    required=False,
                    default=5.0,
                ),
                ParameterSchema(
                    name="max_bytes",
                    type="integer",
                    description="Maximum bytes to read from the socket.",
                    required=False,
                    default=4096,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.tcp import probe_banner

        host = inputs.get("host", "")
        if not host:
            return ToolResult.create_error(
                "Parameter 'host' is required. "
                "Try kaos-web-dns-lookup to resolve a domain to an IP first, or "
                "kaos-web-tcp-probe to scan a host's open ports."
            )

        port_raw = inputs.get("port")
        if port_raw is None:
            return ToolResult.create_error(
                "Parameter 'port' is required (e.g. 22 for SSH, 80 for HTTP). "
                "Try kaos-web-tcp-probe to discover open ports first."
            )
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            return ToolResult.create_error(
                f"Invalid port {port_raw!r}: must be an integer 1-65535. "
                "Try kaos-web-tcp-probe to discover open ports."
            )
        if not (1 <= port <= 65535):
            return ToolResult.create_error(f"Port {port} out of range: must be 1-65535.")

        send_probe_str = inputs.get("send_probe")
        send_probe: bytes | None = None
        if send_probe_str:
            try:
                # Allow common escape sequences in the input string
                send_probe = (
                    send_probe_str.encode("utf-8").decode("unicode_escape").encode("latin-1")
                )
            except (UnicodeDecodeError, UnicodeEncodeError) as exc:
                return ToolResult.create_error(
                    f"Could not encode send_probe as bytes: {exc}. "
                    "Pass plain ASCII (e.g. 'HEAD / HTTP/1.0\\r\\n\\r\\n') or omit "
                    "to wait for an unsolicited banner."
                )

        timeout = float(inputs.get("timeout", 5.0))
        max_bytes = int(inputs.get("max_bytes", 4096))

        try:
            result = await probe_banner(
                host,
                port,
                timeout=timeout,
                send_probe=send_probe,
                max_bytes=max_bytes,
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Banner probe failed for {host}:{port}: {exc}. "
                "Try kaos-web-tcp-probe to verify port reachability, or "
                "kaos-web-tls-inspect for TLS-wrapped ports."
            )

        # Drop banner_bytes from output (already encoded as banner string)
        output = result.model_dump(mode="json", exclude={"banner_bytes"})
        snippet = (result.banner or "").strip().splitlines()[0:1]
        snippet_text = snippet[0][:80] if snippet else "(empty)"
        return ToolResult.create_success(
            output,
            summary=f"{host}:{port} {result.status.value} — {snippet_text}",
        )


# ── 13. kaos-web-fingerprint-service ────────────────────────────────


_DOMAIN_PURE = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class FingerprintServiceTool(KaosTool):
    """Identify a service from a banner string. Pure transform, no network."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-fingerprint-service",
            display_name="Service Banner Fingerprint",
            description=(
                "Identify a service from its banner text. Returns ServiceIdentity "
                "(service, product, version, extra, confidence). Recognises SSH, "
                "SMTP, FTP, POP3, IMAP, HTTP, Redis with regex signatures; falls "
                "back to a port-based hint when the banner doesn't match. Pure "
                "transform — no network. Capture banners with kaos-web-tcp-banner "
                "first."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.ANALYZE,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_PURE,
            input_schema=[
                ParameterSchema(
                    name="banner",
                    type="string",
                    description="Banner text captured from a TCP connection.",
                ),
                ParameterSchema(
                    name="port",
                    type="integer",
                    description=(
                        "Optional TCP port for fallback hinting when the banner "
                        "doesn't match any signature."
                    ),
                    required=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.fingerprint import fingerprint_banner

        banner = inputs.get("banner", "")
        port_raw = inputs.get("port")
        port: int | None = None
        if port_raw is not None:
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                return ToolResult.create_error(
                    f"Invalid port {port_raw!r}: must be an integer or omitted."
                )

        try:
            ident = fingerprint_banner(banner, port=port)
        except Exception as exc:
            return ToolResult.create_error(
                f"Fingerprinting failed: {exc}. "
                "Verify the banner is a string; for raw bytes use the Python API "
                "kaos_web.domain.fingerprint.fingerprint_banner_bytes()."
            )

        output = ident.model_dump(mode="json")
        parts = [ident.service]
        if ident.product:
            parts.append(ident.product)
        if ident.version:
            parts.append(f"v{ident.version}")
        return ToolResult.create_success(
            output,
            summary=f"{' '.join(parts)} (confidence {ident.confidence:.2f})",
        )


# ── 14. kaos-web-udp-probe ──────────────────────────────────────────


class UdpProbeTool(KaosTool):
    """Protocol-aware UDP probe (DNS / NTP / SNMP / syslog)."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-udp-probe",
            display_name="UDP Protocol Probe",
            description=(
                "Send a protocol-aware UDP probe and decode the response. "
                "protocol='dns' sends a DNS query (default version.bind CHAOS TXT — "
                "works on BIND/Unbound/PowerDNS); 'ntp' sends a 48-byte NTPv4 client "
                "packet and decodes stratum/refid; 'snmp' sends an SNMPv1 GET for "
                "sysDescr.0 (community defaults to 'public'); 'syslog' sends a "
                "fire-and-forget '<14>kaos-web-probe' datagram and reports if the "
                "kernel surfaced ICMP-unreachable. For TCP banners, use "
                "kaos-web-tcp-banner instead."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_DOMAIN_LOCAL,
            input_schema=[
                ParameterSchema(name="host", type="string", description="Target hostname or IP."),
                ParameterSchema(
                    name="protocol",
                    type="string",
                    description="UDP protocol to speak: dns, ntp, snmp, syslog.",
                    constraints={"enum": ["dns", "ntp", "snmp", "syslog"]},
                ),
                ParameterSchema(
                    name="port",
                    type="integer",
                    description=("UDP port. Defaults: dns=53, ntp=123, snmp=161, syslog=514."),
                    required=False,
                ),
                ParameterSchema(
                    name="timeout",
                    type="number",
                    description="Per-probe timeout in seconds.",
                    required=False,
                    default=5.0,
                ),
                ParameterSchema(
                    name="community",
                    type="string",
                    description="SNMP v1 community string (snmp protocol only).",
                    required=False,
                    default="public",
                ),
                ParameterSchema(
                    name="query_name",
                    type="string",
                    description="DNS query name (dns protocol only).",
                    required=False,
                    default="version.bind",
                ),
                ParameterSchema(
                    name="query_type",
                    type="string",
                    description="DNS record type (dns protocol only).",
                    required=False,
                    default="TXT",
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.domain.udp import probe_dns, probe_ntp, probe_snmp, probe_syslog

        host = inputs.get("host", "")
        if not host:
            return ToolResult.create_error(
                "Parameter 'host' is required. "
                "Try kaos-web-dns-lookup to resolve a name to an IP first."
            )
        protocol = inputs.get("protocol", "")
        if protocol not in ("dns", "ntp", "snmp", "syslog"):
            return ToolResult.create_error(
                f"Invalid protocol {protocol!r}: must be one of dns, ntp, snmp, syslog. "
                "For TCP services use kaos-web-tcp-banner instead."
            )

        timeout = float(inputs.get("timeout", 5.0))
        port_raw = inputs.get("port")
        port: int | None = None
        if port_raw is not None:
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                return ToolResult.create_error(
                    f"Invalid port {port_raw!r}: must be an integer or omitted to use the default."
                )

        try:
            if protocol == "dns":
                result = await probe_dns(
                    host,
                    port if port is not None else 53,
                    query_name=inputs.get("query_name", "version.bind"),
                    query_type=inputs.get("query_type", "TXT"),
                    timeout=timeout,
                )
            elif protocol == "ntp":
                result = await probe_ntp(
                    host,
                    port if port is not None else 123,
                    timeout=timeout,
                )
            elif protocol == "snmp":
                result = await probe_snmp(
                    host,
                    port if port is not None else 161,
                    community=inputs.get("community", "public"),
                    timeout=timeout,
                )
            else:  # syslog
                result = await probe_syslog(
                    host,
                    port if port is not None else 514,
                    timeout=timeout,
                )
        except Exception as exc:
            return ToolResult.create_error(
                f"UDP {protocol} probe failed for {host}:{port}: {exc}. "
                "Try kaos-web-tcp-probe to verify host reachability, or "
                "kaos-web-dns-lookup if querying a nameserver."
            )

        # Drop raw_response from output (binary, large)
        output = result.model_dump(mode="json", exclude={"raw_response"})
        return ToolResult.create_success(
            output,
            summary=f"{host}:{result.port}/udp {protocol} — {result.status.value}",
        )


# ── Registration ────────────────────────────────────────────────────


def register_domain_tools(runtime: KaosRuntime) -> int:
    """Register all domain intelligence tools with the runtime. Returns count."""
    tools: list[KaosTool] = [
        TcpProbeTool(),
        TlsInspectTool(),
        HttpHeadersTool(),
        ServiceDetectTool(),
        DnsLookupTool(),
        DnsEnumerateTool(),
        DnsZoneTransferTool(),
        DnsSecurityTool(),
        WhoisLookupTool(),
        DomainProfileTool(),
        ExtractOrgTool(),
        TcpBannerTool(),
        FingerprintServiceTool(),
        UdpProbeTool(),
    ]
    for tool in tools:
        runtime.tools.register_tool(tool)
    return len(tools)
