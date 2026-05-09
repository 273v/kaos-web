"""Domain intelligence: DNS, WHOIS, TLS, HTTP/UDP probing, and service detection.

Provides low-level network intelligence tools for domain profiling.
All operations are read-only — no modification, no exploitation.

Modules:

- ``models`` — Pydantic models shared across all domain tools.
- ``tcp`` — TCP port probing + banner grabbing via ``asyncio.open_connection()``.
- ``udp`` — UDP protocol-aware probes (DNS, NTP, SNMP, syslog).
- ``fingerprint`` — Pure banner→ServiceIdentity fingerprinting (no I/O).
- ``tls`` — TLS certificate inspection via stdlib ``ssl``.
- ``http`` — HTTP header analysis and server fingerprinting.
- ``dns`` — DNS record queries and enumeration (requires ``dnspython``).
- ``whois`` — WHOIS client with built-in parsing (stdlib only).
- ``security`` — Mail authentication analysis (SPF/DKIM/DMARC).
- ``profile`` — Composite domain profiling combining all of the above.
- ``org`` — Schema.org Organization entity extraction from page HTML.
"""

from kaos_web.domain.dns import (
    attempt_zone_transfer,
    enumerate_dns,
    lookup,
    lookup_many,
    reverse_ptr,
)
from kaos_web.domain.fingerprint import (
    fingerprint_banner,
    fingerprint_banner_bytes,
    fingerprint_results,
)
from kaos_web.domain.http import analyze_headers, identify_cdn_from_headers
from kaos_web.domain.models import (
    BannerProbeResult,
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
    ServiceIdentity,
    ServiceInfo,
    ServiceProfile,
    TcpProbeResult,
    TlsCertInfo,
    UdpProbeResult,
    UdpProbeStatus,
    WhoisRecord,
    ZoneTransferResult,
    ZoneTransferStatus,
)
from kaos_web.domain.org import OrgAddress, OrgEntity, extract_org_entity
from kaos_web.domain.profile import profile_domain
from kaos_web.domain.security import analyze_mail_security
from kaos_web.domain.service import detect_services
from kaos_web.domain.tcp import probe_banner, probe_banners, probe_port, probe_ports
from kaos_web.domain.tls import inspect_tls
from kaos_web.domain.udp import probe_dns, probe_ntp, probe_snmp, probe_syslog
from kaos_web.domain.whois import whois_lookup

__all__ = [
    "BannerProbeResult",
    "DnsProfile",
    "DnsQueryResult",
    "DnsRecord",
    "DnsRecordStatus",
    "DomainProfile",
    "HttpHeadersResult",
    "MailAuthMechanism",
    "MailAuthRecord",
    "MailAuthStatus",
    "MailSecurityReport",
    "OrgAddress",
    "OrgEntity",
    "PortResult",
    "PortStatus",
    "SecurityHeader",
    "SecurityHeaderStatus",
    "ServiceIdentity",
    "ServiceInfo",
    "ServiceProfile",
    "TcpProbeResult",
    "TlsCertInfo",
    "UdpProbeResult",
    "UdpProbeStatus",
    "WhoisRecord",
    "ZoneTransferResult",
    "ZoneTransferStatus",
    "analyze_headers",
    "analyze_mail_security",
    "attempt_zone_transfer",
    "detect_services",
    "enumerate_dns",
    "extract_org_entity",
    "fingerprint_banner",
    "fingerprint_banner_bytes",
    "fingerprint_results",
    "identify_cdn_from_headers",
    "inspect_tls",
    "lookup",
    "lookup_many",
    "probe_banner",
    "probe_banners",
    "probe_dns",
    "probe_ntp",
    "probe_port",
    "probe_ports",
    "probe_snmp",
    "probe_syslog",
    "profile_domain",
    "reverse_ptr",
    "whois_lookup",
]
