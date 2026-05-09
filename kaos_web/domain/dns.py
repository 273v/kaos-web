"""DNS record queries and enumeration.

Requires ``dnspython`` (``pip install dnspython``).  Pure Python, zero
transitive dependencies.

Provides targeted lookup (single record type) and full enumeration
(all common types + reverse PTR + DNSSEC detection).
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from collections.abc import Sequence

from kaos_core.logging import get_logger
from kaos_web.domain.models import (
    DnsProfile,
    DnsQueryResult,
    DnsRecord,
    DnsRecordStatus,
    ZoneTransferResult,
    ZoneTransferStatus,
)

logger = get_logger(__name__)

# Default record types for full enumeration
DEFAULT_RECORD_TYPES: tuple[str, ...] = (
    "A",
    "AAAA",
    "CNAME",
    "MX",
    "NS",
    "TXT",
    "SOA",
    "CAA",
    "SRV",
)

DNSSEC_RECORD_TYPES: tuple[str, ...] = ("DNSKEY", "DS")

# Multi-part public-suffix TLDs where the apex domain is 3+ labels.
# Without a full PSL download, we maintain the most common ones.
_MULTI_PART_TLDS: frozenset[str] = frozenset(
    {
        # United Kingdom
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "me.uk",
        "net.uk",
        "sch.uk",
        # Australia
        "com.au",
        "net.au",
        "org.au",
        "edu.au",
        "gov.au",
        "asn.au",
        "id.au",
        # Japan
        "co.jp",
        "ac.jp",
        "ne.jp",
        "or.jp",
        "go.jp",
        # Brazil
        "com.br",
        "net.br",
        "org.br",
        "gov.br",
        "edu.br",
        # India
        "co.in",
        "net.in",
        "org.in",
        "gen.in",
        "firm.in",
        "ind.in",
        "gov.in",
        "ac.in",
        "edu.in",
        "res.in",
        # New Zealand
        "co.nz",
        "net.nz",
        "org.nz",
        "govt.nz",
        "ac.nz",
        # South Africa
        "co.za",
        "org.za",
        "net.za",
        "gov.za",
        "ac.za",
        # China
        "com.cn",
        "net.cn",
        "org.cn",
        "gov.cn",
        "edu.cn",
        # Hong Kong
        "com.hk",
        "org.hk",
        "net.hk",
        "edu.hk",
        "gov.hk",
        # South Korea
        "co.kr",
        "or.kr",
        "ne.kr",
        "re.kr",
        "pe.kr",
        "go.kr",
        "ac.kr",
        # Singapore
        "com.sg",
        "net.sg",
        "org.sg",
        "edu.sg",
        "gov.sg",
        # Taiwan
        "com.tw",
        "net.tw",
        "org.tw",
        "edu.tw",
        "gov.tw",
        # Turkey
        "com.tr",
        "net.tr",
        "org.tr",
        "gen.tr",
        "gov.tr",
        "edu.tr",
        # Mexico
        "com.mx",
        "net.mx",
        "org.mx",
        "gob.mx",
        "edu.mx",
        # Argentina
        "com.ar",
        "net.ar",
        "org.ar",
        "gov.ar",
        "edu.ar",
        # Colombia
        "com.co",
        "net.co",
        "org.co",
        "gov.co",
        "edu.co",
        # Israel
        "co.il",
        "org.il",
        "net.il",
        "ac.il",
        "gov.il",
        # Thailand
        "co.th",
        "in.th",
        "ac.th",
        "go.th",
        "or.th",
        "net.th",
        # Malaysia
        "com.my",
        "net.my",
        "org.my",
        "gov.my",
        "edu.my",
        # Indonesia
        "co.id",
        "or.id",
        "ac.id",
        "go.id",
        "web.id",
        # Nigeria
        "com.ng",
        "org.ng",
        "net.ng",
        "gov.ng",
        "edu.ng",
        # Kenya
        "co.ke",
        "or.ke",
        "ne.ke",
        "go.ke",
        "ac.ke",
        # Egypt
        "com.eg",
        "org.eg",
        "net.eg",
        "gov.eg",
        "edu.eg",
        # Pakistan
        "com.pk",
        "net.pk",
        "org.pk",
        "gov.pk",
        "edu.pk",
        # Bangladesh
        "com.bd",
        "net.bd",
        "org.bd",
        "gov.bd",
        "edu.bd",
        # Vietnam
        "com.vn",
        "net.vn",
        "org.vn",
        "gov.vn",
        "edu.vn",
        # Philippines
        "com.ph",
        "net.ph",
        "org.ph",
        "gov.ph",
        "edu.ph",
    }
)


def _derive_apex_domain(hostname: str) -> str:
    """Derive the apex (registered) domain from a hostname.

    Handles multi-part public-suffix TLDs like ``co.uk``, ``com.au`` using
    a curated set of known suffixes.  Falls back to the last two labels for
    simple TLDs (``com``, ``org``, ``net``, etc.).
    """
    parts = hostname.rstrip(".").split(".")
    if len(parts) <= 2:
        return ".".join(parts)

    # Check if the last two labels form a known multi-part TLD
    candidate_tld = ".".join(parts[-2:])
    if candidate_tld in _MULTI_PART_TLDS:
        # Apex is <name>.<multi-part-tld>, i.e. last 3 labels
        return ".".join(parts[-3:]) if len(parts) >= 3 else ".".join(parts)

    # Simple TLD — apex is the last two labels
    return ".".join(parts[-2:])


# ── Single record type lookup ───────────────────────────────────────


async def lookup(
    domain: str,
    record_type: str = "A",
    *,
    timeout: float = 10.0,
    nameservers: Sequence[str] | None = None,
) -> DnsQueryResult:
    """Query a single DNS record type for a domain.

    Args:
        domain: Domain name to query.
        record_type: DNS record type (A, AAAA, MX, NS, TXT, etc.).
        timeout: Query timeout in seconds.
        nameservers: Optional custom nameservers.

    Returns:
        DnsQueryResult with records or error status.

    Raises:
        UrlPolicyError: WEB5-001 gate rejection. Asymmetric: the typical
        input is a hostname (which is what's being resolved) — the
        ``validate_host`` IP-literal check only fires when ``domain`` is
        an IP literal like ``"169.254.169.254"`` (which would actually
        be a reverse-PTR-style query, not a forward A lookup, but also
        a clear policy violation we want to block). Hostname-only
        inputs fall through to the resolver.
    """
    # WEB5-001: gate the query target. Pass-through for hostnames;
    # rejects IP-literal queries that hit private/loopback/metadata.
    from kaos_web.security import validate_host

    validate_host(domain)
    import dns.asyncresolver
    import dns.exception
    import dns.flags
    import dns.rdatatype
    from dns.resolver import NXDOMAIN, NoAnswer, NoNameservers

    resolver = dns.asyncresolver.Resolver(configure=True)
    resolver.lifetime = timeout
    resolver.timeout = timeout
    if nameservers:
        resolver.nameservers = list(nameservers)

    start = time.perf_counter()
    rt = record_type.upper()

    try:
        answer = await resolver.resolve(domain, rdtype=rt, raise_on_no_answer=False)
    except NXDOMAIN:
        return DnsQueryResult(
            query_name=domain,
            record_type=rt,
            status=DnsRecordStatus.NXDOMAIN,
            duration_ms=_elapsed(start),
            error=f"Domain {domain} does not exist (NXDOMAIN)",
        )
    except NoAnswer:
        return DnsQueryResult(
            query_name=domain,
            record_type=rt,
            status=DnsRecordStatus.NO_ANSWER,
            duration_ms=_elapsed(start),
        )
    except (NoNameservers, dns.exception.Timeout):
        return DnsQueryResult(
            query_name=domain,
            record_type=rt,
            status=DnsRecordStatus.TIMEOUT,
            duration_ms=_elapsed(start),
            error="DNS query timed out or no nameservers available",
        )
    except Exception as exc:
        return DnsQueryResult(
            query_name=domain,
            record_type=rt,
            status=DnsRecordStatus.ERROR,
            duration_ms=_elapsed(start),
            error=f"{type(exc).__name__}: {exc}",
        )

    records: list[DnsRecord] = []
    response = answer.response
    if response and response.answer:
        for rrset in response.answer:
            name = rrset.name.to_text(omit_final_dot=True)
            rtype = dns.rdatatype.to_text(rrset.rdtype)
            ttl = getattr(rrset, "ttl", None)
            for rdata in rrset:
                records.append(
                    DnsRecord(
                        name=name,
                        record_type=rtype,
                        ttl=ttl,
                        value=rdata.to_text(),
                    )
                )

    status = DnsRecordStatus.SUCCESS if records else DnsRecordStatus.NO_ANSWER
    return DnsQueryResult(
        query_name=domain,
        record_type=rt,
        status=status,
        records=records,
        duration_ms=_elapsed(start),
    )


# ── Multi-type lookup ───────────────────────────────────────────────


async def lookup_many(
    domain: str,
    record_types: Sequence[str],
    *,
    timeout: float = 10.0,
    nameservers: Sequence[str] | None = None,
    concurrency: int = 10,
) -> list[DnsQueryResult]:
    """Query multiple record types concurrently.

    Args:
        domain: Domain name.
        record_types: List of record types to query.
        timeout: Per-query timeout.
        nameservers: Optional custom nameservers.
        concurrency: Max concurrent queries.

    Returns:
        List of DnsQueryResult, one per record type.

    Raises:
        UrlPolicyError: WEB5-001 gate rejection (when ``domain`` is an
        IP literal in a private/loopback/metadata range). Hostname-only
        inputs fall through; see :func:`lookup` for the asymmetry note.
    """
    # WEB5-001: gate once at the front of the fan-out — lookup() also
    # gates per-call but short-circuiting here avoids spawning N tasks
    # that all raise the same UrlPolicyError.
    from kaos_web.security import validate_host

    validate_host(domain)
    sem = asyncio.Semaphore(concurrency)

    async def _limited(rt: str) -> DnsQueryResult:
        async with sem:
            return await lookup(domain, rt, timeout=timeout, nameservers=nameservers)

    return list(await asyncio.gather(*[_limited(rt) for rt in record_types]))


# ── Reverse PTR ─────────────────────────────────────────────────────


async def reverse_ptr(
    ip_address: str,
    *,
    timeout: float = 10.0,
) -> DnsRecord | None:
    """Reverse DNS lookup for an IP address.

    Returns:
        DnsRecord with PTR value, or None if lookup fails.

    Raises:
        UrlPolicyError: WEB5-001 gate rejection. ``ip_address`` is
        always an IP literal here (it's a reverse PTR), so the gate
        always fires when the input is private/loopback/metadata.
    """
    # WEB5-001: gate the IP literal before constructing the reverse name.
    from kaos_web.security import validate_host

    validate_host(ip_address)
    import dns.asyncresolver
    import dns.reversename
    from dns.resolver import NXDOMAIN, NoAnswer, NoNameservers

    try:
        rev_name = dns.reversename.from_address(ip_address)
    except Exception:
        return None

    resolver = dns.asyncresolver.Resolver(configure=True)
    resolver.lifetime = timeout

    try:
        answer = await resolver.resolve(rev_name, "PTR")
        for rdata in answer:
            return DnsRecord(
                name=str(rev_name),
                record_type="PTR",
                ttl=getattr(answer.rrset, "ttl", None),
                value=rdata.to_text().rstrip("."),
            )
    except (NXDOMAIN, NoAnswer, NoNameservers):
        # Expected "no record" outcomes for reverse PTR — silent.
        pass
    except Exception as exc:
        # Catch-all for resolver/network errors so reverse PTR never raises
        # back to the caller. Logged at DEBUG so failures are observable
        # (audit-03 WEB3-002: previously the resolver path silently swallowed
        # everything via a redundant trailing `Exception` in the same tuple).
        logger.debug("reverse-PTR resolver error for %s: %s", ip_address, exc)
    return None


# ── Full enumeration ────────────────────────────────────────────────


async def enumerate_dns(
    domain: str,
    *,
    timeout: float = 10.0,
    nameservers: Sequence[str] | None = None,
    include_reverse_ptr: bool = True,
    include_dnssec: bool = True,
) -> DnsProfile:
    """Full DNS enumeration: all common record types + reverse PTR + DNSSEC.

    Args:
        domain: Domain to enumerate.
        timeout: Per-query timeout.
        nameservers: Optional custom nameservers.
        include_reverse_ptr: Resolve PTR for discovered IPs.
        include_dnssec: Query DNSKEY and DS records.

    Returns:
        DnsProfile with all results.

    Raises:
        UrlPolicyError: WEB5-001 gate rejection (when ``domain`` is an
        IP literal in a private/loopback/metadata range). See
        :func:`lookup` for the hostname-vs-IP-literal asymmetry note.
    """
    # WEB5-001: gate the query target before fan-out.
    from kaos_web.security import validate_host

    validate_host(domain)
    record_types = list(DEFAULT_RECORD_TYPES)
    if include_dnssec:
        record_types.extend(DNSSEC_RECORD_TYPES)

    queries = await lookup_many(domain, record_types, timeout=timeout, nameservers=nameservers)

    # Extract IP addresses for reverse PTR
    reverse_records: list[DnsRecord] = []
    if include_reverse_ptr:
        ips: set[str] = set()
        for q in queries:
            if q.record_type in ("A", "AAAA") and q.status == DnsRecordStatus.SUCCESS:
                for rec in q.records:
                    try:
                        ipaddress.ip_address(rec.value)
                        ips.add(rec.value)
                    except ValueError:
                        pass

        if ips:
            ptrs = await asyncio.gather(*[reverse_ptr(ip, timeout=timeout) for ip in sorted(ips)])
            reverse_records = [p for p in ptrs if p is not None]

    # Extract nameservers from NS records
    ns_list: list[str] = []
    for q in queries:
        if q.record_type == "NS" and q.status == DnsRecordStatus.SUCCESS:
            ns_list.extend(rec.value.rstrip(".") for rec in q.records)

    # Extract MX hosts
    mx_list: list[str] = []
    for q in queries:
        if q.record_type == "MX" and q.status == DnsRecordStatus.SUCCESS:
            for rec in q.records:
                parts = rec.value.split()
                if len(parts) >= 2:
                    mx_list.append(parts[-1].rstrip("."))

    # Detect DNSSEC
    dnssec = None
    if include_dnssec:
        for q in queries:
            if (
                q.record_type in ("DNSKEY", "DS")
                and q.status == DnsRecordStatus.SUCCESS
                and q.records
            ):
                dnssec = True
                break
        if dnssec is None:
            dnssec = False

    # Derive apex domain (handles multi-part TLDs like co.uk, com.au)
    apex = _derive_apex_domain(domain)

    return DnsProfile(
        domain=domain,
        apex_domain=apex,
        queries=queries,
        reverse_ptr=reverse_records,
        dnssec=dnssec,
        nameservers=ns_list,
        mx_hosts=mx_list,
    )


# ── Zone transfer ──────────────────────────────────────────────────


async def attempt_zone_transfer(
    domain: str,
    nameserver: str,
    *,
    timeout: float = 10.0,
) -> ZoneTransferResult:
    """Attempt AXFR zone transfer against a single nameserver.

    Zone transfers are rarely permitted on public nameservers.  A
    ``refused`` result is normal and expected.

    Args:
        domain: Zone apex domain.
        nameserver: Nameserver hostname or IP.
        timeout: Transfer timeout.

    Returns:
        ZoneTransferResult with status and optional record count.

    Raises:
        UrlPolicyError: WEB5-001 gate rejection (when ``domain`` or
        ``nameserver`` is an IP literal in a private/loopback/metadata
        range).
    """
    # WEB5-001: gate both the zone target and the nameserver.
    from kaos_web.security import validate_host

    validate_host(domain)
    validate_host(nameserver)
    import dns.name
    import dns.query
    import dns.rdatatype
    import dns.zone
    from dns.query import TransferError

    def _do_xfr() -> ZoneTransferResult:
        start = time.perf_counter()

        # Resolve nameserver to IP if it's a hostname
        import socket

        try:
            addresses = socket.getaddrinfo(nameserver, 53, type=socket.SOCK_STREAM)
            ns_addr = str(addresses[0][4][0]) if addresses else nameserver
        except (socket.gaierror, OSError):
            return ZoneTransferResult(
                nameserver=nameserver,
                status=ZoneTransferStatus.FAILED,
                duration_ms=_elapsed(start),
                error=f"Cannot resolve nameserver {nameserver}",
            )

        try:
            xfr = dns.query.xfr(ns_addr, domain, timeout=timeout, lifetime=timeout)
            zone = dns.zone.from_xfr(xfr, relativize=False, check_origin=False)
            record_count = sum(1 for _ in zone.iterate_rdatas())
            soa = zone.get_rdataset(dns.name.from_text(domain), dns.rdatatype.SOA)
            serial = soa[0].serial if soa else None
            return ZoneTransferResult(
                nameserver=nameserver,
                address=ns_addr,
                status=ZoneTransferStatus.SUCCESS,
                record_count=record_count,
                serial=serial,
                duration_ms=_elapsed(start),
            )
        except TransferError as exc:
            status = (
                ZoneTransferStatus.REFUSED
                if "refused" in str(exc).lower()
                else ZoneTransferStatus.FAILED
            )
            return ZoneTransferResult(
                nameserver=nameserver,
                address=ns_addr,
                status=status,
                duration_ms=_elapsed(start),
                error=str(exc),
            )
        except Exception as exc:
            return ZoneTransferResult(
                nameserver=nameserver,
                address=ns_addr,
                status=ZoneTransferStatus.FAILED,
                duration_ms=_elapsed(start),
                error=f"{type(exc).__name__}: {exc}",
            )

    return await asyncio.to_thread(_do_xfr)


# ── Helpers ─────────────────────────────────────────────────────────


def _elapsed(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
