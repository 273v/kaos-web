"""Pydantic models for domain intelligence tools.

All models use ``model_config = ConfigDict(extra="forbid")`` for strict
validation and are JSON-serializable via ``.model_dump(mode="json")``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


# ── TCP probing ─────────────────────────────────────────────────────


class PortStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    TIMEOUT = "timeout"
    FILTERED = "filtered"
    ERROR = "error"
    UNKNOWN = "unknown"


class PortResult(BaseModel):
    """Result of probing a single TCP port."""

    model_config = _STRICT

    port: int
    status: PortStatus
    latency_ms: float | None = None
    banner: str | None = Field(None, description="First bytes read if port is open")
    error: str | None = None


class TcpProbeResult(BaseModel):
    """Aggregate result of probing multiple ports on a host."""

    model_config = _STRICT

    host: str
    ports: list[PortResult] = Field(default_factory=list)
    open_count: int = 0
    closed_count: int = 0
    timeout_count: int = 0


# ── Port presets ────────────────────────────────────────────────────

COMMON_PORTS: dict[str, list[int]] = {
    "web": [80, 443, 8080, 8443],
    "mail": [25, 465, 587, 993, 995, 143, 110],
    "ssh": [22],
    "dns": [53],
    "ftp": [21],
    "database": [3306, 5432, 1433, 27017, 6379],
    "default": [21, 22, 25, 53, 80, 110, 143, 443, 465, 587, 993, 995, 3306, 5432, 8080, 8443],
}


# ── TLS inspection ──────────────────────────────────────────────────


class TlsCertInfo(BaseModel):
    """Parsed TLS certificate information."""

    model_config = _STRICT

    host: str
    port: int = 443
    subject: dict[str, str] = Field(default_factory=dict)
    issuer: dict[str, str] = Field(default_factory=dict)
    serial_number: str | None = None
    not_before: str | None = Field(None, description="ISO 8601")
    not_after: str | None = Field(None, description="ISO 8601")
    days_until_expiry: int | None = None
    san_dns: list[str] = Field(default_factory=list, description="Subject Alternative Names (DNS)")
    protocol: str | None = Field(None, description="TLS protocol version (e.g. TLSv1.3)")
    cipher: str | None = None
    cipher_bits: int | None = None
    error: str | None = None


# ── HTTP headers ────────────────────────────────────────────────────


class SecurityHeaderStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    WEAK = "weak"


class SecurityHeader(BaseModel):
    """Analysis of a single security-related HTTP header."""

    model_config = _STRICT

    name: str
    status: SecurityHeaderStatus
    value: str | None = None
    recommendation: str | None = None


class HttpHeadersResult(BaseModel):
    """Result of HTTP header analysis."""

    model_config = _STRICT

    url: str
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    server: str | None = None
    server_software: str | None = Field(
        None, description="Extracted software name (nginx, Apache, etc.)"
    )
    powered_by: str | None = None
    security_headers: list[SecurityHeader] = Field(default_factory=list)
    security_score: int = Field(0, description="0-100 based on presence of security headers")
    redirect_url: str | None = None
    error: str | None = None


# ── Service detection ───────────────────────────────────────────────


class ServiceInfo(BaseModel):
    """Detected service on a port."""

    model_config = _STRICT

    port: int
    protocol: str = Field(description="http, https, ssh, smtp, etc.")
    software: str | None = None
    version: str | None = None
    tls: TlsCertInfo | None = None
    headers: HttpHeadersResult | None = None


class ServiceProfile(BaseModel):
    """Combined service detection for a domain."""

    model_config = _STRICT

    host: str
    services: list[ServiceInfo] = Field(default_factory=list)
    cdn: str | None = Field(None, description="Detected CDN (Cloudflare, CloudFront, Akamai, etc.)")
    server_software: str | None = None


# ── DNS records ─────────────────────────────────────────────────────


class DnsRecordStatus(StrEnum):
    SUCCESS = "success"
    NXDOMAIN = "nxdomain"
    NO_ANSWER = "no_answer"
    TIMEOUT = "timeout"
    ERROR = "error"


class DnsRecord(BaseModel):
    """A single DNS resource record."""

    model_config = _STRICT

    name: str
    record_type: str
    ttl: int | None = None
    value: str


class DnsQueryResult(BaseModel):
    """Result of querying one record type."""

    model_config = _STRICT

    query_name: str
    record_type: str
    status: DnsRecordStatus
    records: list[DnsRecord] = Field(default_factory=list)
    duration_ms: float | None = None
    error: str | None = None


class DnsProfile(BaseModel):
    """Full DNS enumeration for a domain."""

    model_config = _STRICT

    domain: str
    apex_domain: str | None = None
    queries: list[DnsQueryResult] = Field(default_factory=list)
    reverse_ptr: list[DnsRecord] = Field(default_factory=list)
    dnssec: bool | None = None
    nameservers: list[str] = Field(default_factory=list)
    mx_hosts: list[str] = Field(default_factory=list)


# ── DNS zone transfer ──────────────────────────────────────────────


class ZoneTransferStatus(StrEnum):
    SUCCESS = "success"
    REFUSED = "refused"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ZoneTransferResult(BaseModel):
    """Result of an AXFR attempt against one nameserver."""

    model_config = _STRICT

    nameserver: str
    address: str | None = None
    status: ZoneTransferStatus
    record_count: int | None = None
    serial: int | None = None
    duration_ms: float | None = None
    error: str | None = None


# ── DNS security (mail auth) ───────────────────────────────────────


class MailAuthMechanism(StrEnum):
    SPF = "spf"
    DKIM = "dkim"
    DMARC = "dmarc"


class MailAuthStatus(StrEnum):
    CONFIGURED = "configured"
    MISSING = "missing"
    WEAK = "weak"
    INVALID = "invalid"


class MailAuthRecord(BaseModel):
    """Analysis of a single mail authentication mechanism."""

    model_config = _STRICT

    mechanism: MailAuthMechanism
    status: MailAuthStatus
    raw_record: str | None = None
    policy: str | None = Field(
        None, description="SPF policy (~all, -all, etc.) or DMARC policy (none/quarantine/reject)"
    )
    details: dict[str, Any] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


class MailSecurityReport(BaseModel):
    """Mail authentication posture for a domain."""

    model_config = _STRICT

    domain: str
    records: list[MailAuthRecord] = Field(default_factory=list)
    overall_posture: str = Field("unknown", description="strong, moderate, weak, missing")


# ── WHOIS ───────────────────────────────────────────────────────────


class WhoisRecord(BaseModel):
    """Parsed WHOIS registration data."""

    model_config = _STRICT

    domain: str
    registrar: str | None = None
    whois_server: str | None = None
    creation_date: str | None = Field(None, description="ISO 8601")
    expiration_date: str | None = Field(None, description="ISO 8601")
    updated_date: str | None = Field(None, description="ISO 8601")
    name_servers: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=list)
    registrant_name: str | None = None
    registrant_org: str | None = None
    registrant_country: str | None = None
    dnssec: str | None = None
    raw_text: str | None = None
    error: str | None = None


# ── Composite domain profile ───────────────────────────────────────


class DomainProfile(BaseModel):
    """One-shot domain intelligence combining all available data."""

    model_config = _STRICT

    domain: str
    dns: DnsProfile | None = None
    whois: WhoisRecord | None = None
    services: ServiceProfile | None = None
    mail_security: MailSecurityReport | None = None
    sitemap_urls: list[str] = Field(default_factory=list)
    robots_txt: str | None = Field(None, description="URL to robots.txt if found")


# ── TCP banner grab ────────────────────────────────────────────────


class BannerProbeResult(BaseModel):
    """Result of a TCP banner-grab probe."""

    model_config = _STRICT

    host: str
    port: int
    status: PortStatus
    banner: str | None = Field(
        None, description="Decoded banner text (UTF-8, latin-1, or repr fallback)."
    )
    banner_bytes: bytes | None = Field(
        None, description="Raw bytes captured from the socket (lossless)."
    )
    duration_ms: float | None = None
    error: str | None = None


# ── Service fingerprinting ─────────────────────────────────────────


class ServiceIdentity(BaseModel):
    """Fingerprinted service identity from a banner string.

    ``confidence`` is in [0.0, 1.0]: 0.0 for unknown / port-only guess, ~0.5
    for port-based heuristics, 0.8-1.0 for banner regex matches.
    """

    model_config = _STRICT

    service: str = Field(description="Generic service name (ssh, smtp, http, ...)")
    product: str | None = Field(None, description="Product name (OpenSSH, Postfix, nginx, ...)")
    version: str | None = None
    extra: dict[str, str] = Field(
        default_factory=dict, description="Additional fingerprint fields (protocol, host, ...)"
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)


# ── UDP probing ────────────────────────────────────────────────────


class UdpProbeStatus(StrEnum):
    RESPONDED = "responded"
    TIMEOUT = "timeout"
    ICMP_UNREACHABLE = "icmp_unreachable"
    SENT_NO_RESPONSE_EXPECTED = "sent_no_response_expected"
    ERROR = "error"


class UdpProbeResult(BaseModel):
    """Result of a UDP protocol-aware probe."""

    model_config = _STRICT

    host: str
    port: int
    protocol: str = Field(description="dns, ntp, snmp, syslog, ...")
    status: UdpProbeStatus
    payload: str | None = Field(None, description="Decoded protocol response (text form).")
    raw_response: bytes | None = Field(
        None, description="Raw bytes captured from the wire (lossless)."
    )
    duration_ms: float | None = None
    extra: dict[str, Any] = Field(
        default_factory=dict, description="Protocol-specific decoded fields."
    )
    error: str | None = None
