"""UDP protocol-aware probes — DNS, NTP, SNMP, syslog.

Each probe opens an asyncio datagram endpoint, sends a valid request, and
either waits for a response with a timeout or — for fire-and-forget
protocols (syslog) — confirms the datagram was sent without immediate
ICMP-unreachable.

These are *intelligence* probes (one packet, read-only). They don't
reflect, don't amplify, and don't replay. ``probe_snmp`` uses the
public ``community="public"`` default and only issues a single GET; do
not raise the request rate without operator consent.

dnspython is preferred for DNS query construction (already an optional
``[dns]`` extra) — when present we use ``dns.message.make_query`` and
``dns.message.from_wire`` for parsing. Without dnspython we hand-build
the wire bytes (``_build_dns_query``, ``_parse_dns_response``).
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
import time
from typing import Any

from kaos_core.logging import get_logger
from kaos_web.domain.models import UdpProbeResult, UdpProbeStatus

logger = get_logger(__name__)


# ── DatagramProtocol shim for capturing one response ───────────────


class _SingleDatagramProtocol(asyncio.DatagramProtocol):
    """Capture exactly one inbound datagram and signal via an Event.

    Errors received via ``error_received`` (e.g. ICMP port-unreachable
    surfaced by the kernel) are stored on ``self.error`` and also signal
    the event so the caller can return promptly.
    """

    def __init__(self) -> None:
        self.event: asyncio.Event = asyncio.Event()
        self.data: bytes | None = None
        self.addr: tuple[str, int] | None = None
        self.error: Exception | None = None

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.data is None:
            self.data = data
            self.addr = addr
            self.event.set()

    def error_received(self, exc: Exception) -> None:
        self.error = exc
        self.event.set()


async def _send_and_wait(
    host: str,
    port: int,
    payload: bytes,
    *,
    timeout: float,
) -> tuple[_SingleDatagramProtocol, float]:
    """Send a datagram and await the first response or timeout.

    Returns the populated protocol object and the elapsed duration in
    milliseconds. Always closes the transport in a ``finally`` block.
    """
    loop = asyncio.get_running_loop()
    start = time.perf_counter()

    transport, protocol = await loop.create_datagram_endpoint(
        _SingleDatagramProtocol,
        remote_addr=(host, port),
    )
    assert isinstance(protocol, _SingleDatagramProtocol)
    try:
        transport.sendto(payload)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(protocol.event.wait(), timeout=timeout)
    finally:
        transport.close()

    duration_ms = (time.perf_counter() - start) * 1000
    return protocol, duration_ms


# ── DNS ────────────────────────────────────────────────────────────


def _build_dns_query(name: str, rdtype: int, rdclass: int) -> bytes:
    """Build a minimal DNS query without dnspython.

    Returns a single-question wire-format packet with RD=1 and a random
    transaction id. Used as a fallback when dnspython isn't installed.
    """
    import secrets

    txid = secrets.randbits(16)
    flags = 0x0100  # standard query, RD=1
    header = struct.pack(">HHHHHH", txid, flags, 1, 0, 0, 0)

    # QNAME
    parts = (
        [b""] if name == "" else [label.encode("ascii") for label in name.rstrip(".").split(".")]
    )
    qname = b"".join(bytes([len(p)]) + p for p in parts) + b"\x00"
    question = qname + struct.pack(">HH", rdtype, rdclass)
    return header + question


def _decode_dns_name(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a (possibly compressed) DNS name. Returns (name, next_offset)."""
    labels: list[str] = []
    next_off: int | None = None
    pos = offset
    visited: set[int] = set()
    while True:
        if pos >= len(data):
            break
        length = data[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0 == 0xC0:
            # Pointer
            if pos + 1 >= len(data):
                break
            ptr = ((length & 0x3F) << 8) | data[pos + 1]
            if next_off is None:
                next_off = pos + 2
            if ptr in visited:
                break
            visited.add(ptr)
            pos = ptr
            continue
        labels.append(data[pos + 1 : pos + 1 + length].decode("ascii", errors="replace"))
        pos += 1 + length
    if next_off is None:
        next_off = pos
    return ".".join(labels), next_off


def _parse_dns_txt_answers(data: bytes) -> list[str]:
    """Parse a DNS response and return the concatenated TXT answers.

    Returns an empty list on parse error, NXDOMAIN, or no-answer responses.
    """
    if len(data) < 12:
        return []
    _txid, _flags, qd, an, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    pos = 12
    # Skip questions
    for _ in range(qd):
        _, pos = _decode_dns_name(data, pos)
        pos += 4  # qtype + qclass
    answers: list[str] = []
    for _ in range(an):
        _, pos = _decode_dns_name(data, pos)
        if pos + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[pos : pos + 10])
        pos += 10
        rdata = data[pos : pos + rdlen]
        pos += rdlen
        if rtype == 16:  # TXT
            # TXT rdata is a sequence of <length><bytes> chunks
            txt = ""
            tpos = 0
            while tpos < len(rdata):
                tlen = rdata[tpos]
                tpos += 1
                txt += rdata[tpos : tpos + tlen].decode("utf-8", errors="replace")
                tpos += tlen
            answers.append(txt)
    return answers


async def probe_dns(
    host: str,
    port: int = 53,
    *,
    query_name: str = "version.bind",
    query_type: str = "TXT",
    timeout: float = 5.0,
) -> UdpProbeResult:
    """Send a DNS query and capture the response.

    Defaults to ``version.bind CHAOS TXT`` — a long-standing convention
    for fingerprinting BIND/Unbound/Knot/PowerDNS resolvers. Many
    operators disable this in production, in which case the response
    will be REFUSED or empty.

    Args:
        host: Target nameserver IP or hostname.
        port: DNS UDP port (default 53).
        query_name: Owner name to query.
        query_type: Record type string (TXT, A, AAAA, ...). Currently TXT
            is parsed for ``payload``; other types still exercise the
            request path and return raw bytes.
        timeout: Per-probe timeout in seconds.

    Returns:
        UdpProbeResult with status RESPONDED on success, TIMEOUT if no
        response within ``timeout``, ERROR on local socket errors.
    """
    # Map type string → numeric rdtype for the fallback path
    rdtype_map = {"A": 1, "AAAA": 28, "TXT": 16, "MX": 15, "NS": 2, "SOA": 6}
    rdclass_chaos = 3
    rdclass_in = 1

    # Build query
    use_dnspython = False
    payload: bytes
    try:
        import dns.message  # type: ignore[import-untyped]
        import dns.rdataclass  # type: ignore[import-untyped]
        import dns.rdatatype  # type: ignore[import-untyped]

        rdclass_obj = dns.rdataclass.CHAOS if query_name == "version.bind" else dns.rdataclass.IN
        try:
            rdtype_obj = dns.rdatatype.from_text(query_type)
        except Exception:
            rdtype_obj = dns.rdatatype.TXT
        msg = dns.message.make_query(query_name, rdtype_obj, rdclass_obj)
        payload = msg.to_wire()
        use_dnspython = True
    except ImportError:
        rdtype_num = rdtype_map.get(query_type.upper(), 16)
        rdclass = rdclass_chaos if query_name == "version.bind" else rdclass_in
        payload = _build_dns_query(query_name, rdtype_num, rdclass)

    try:
        protocol, duration_ms = await _send_and_wait(host, port, payload, timeout=timeout)
    except OSError as exc:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="dns",
            status=UdpProbeStatus.ERROR,
            error=str(exc),
        )

    if protocol.error is not None:
        # Likely ICMP port-unreachable surfaced by the kernel
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="dns",
            status=UdpProbeStatus.ICMP_UNREACHABLE,
            duration_ms=round(duration_ms, 2),
            error=str(protocol.error),
        )

    if protocol.data is None:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="dns",
            status=UdpProbeStatus.TIMEOUT,
            duration_ms=round(duration_ms, 2),
            error="no response within timeout",
        )

    # Parse response
    extra: dict[str, Any] = {"query_name": query_name, "query_type": query_type}
    payload_text: str | None = None
    if use_dnspython:
        try:
            import dns.message  # type: ignore[import-untyped]

            resp = dns.message.from_wire(protocol.data)
            extra["rcode"] = str(resp.rcode())
            answers: list[str] = []
            for rrset in resp.answer:
                for rdata in rrset:
                    answers.append(rdata.to_text().strip('"'))
            if answers:
                payload_text = " ".join(answers)
                extra["answers"] = " | ".join(answers)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("dnspython parse failed: %s", exc)
            answers = _parse_dns_txt_answers(protocol.data)
            if answers:
                payload_text = " ".join(answers)
    else:
        answers = _parse_dns_txt_answers(protocol.data)
        if answers:
            payload_text = " ".join(answers)
            extra["answers"] = " | ".join(answers)

    return UdpProbeResult(
        host=host,
        port=port,
        protocol="dns",
        status=UdpProbeStatus.RESPONDED,
        payload=payload_text,
        raw_response=protocol.data,
        duration_ms=round(duration_ms, 2),
        extra=extra,
    )


# ── NTP ────────────────────────────────────────────────────────────


# RFC 5905: NTPv4 client packet — LI=0 (00), VN=4 (100), Mode=3 (011) → 0x23
_NTP_CLIENT_PACKET: bytes = b"\x23" + b"\x00" * 47


def _decode_refid(stratum: int, refid_bytes: bytes) -> str:
    """Decode the NTP reference identifier.

    For stratum ≤ 1 it is a 4-byte ASCII KOD/refclock id (e.g. ``GPS\\0``).
    For stratum ≥ 2 it is the IPv4 address of the upstream server.
    """
    if stratum <= 1:
        return refid_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
    return ".".join(str(b) for b in refid_bytes)


async def probe_ntp(
    host: str,
    port: int = 123,
    *,
    timeout: float = 5.0,
) -> UdpProbeResult:
    """Send an NTPv4 client packet and decode the response header.

    Decodes leap indicator, version, mode, stratum, poll, precision, and
    the reference id (ASCII for stratum ≤ 1, IPv4 dotted otherwise).

    Args:
        host: Target NTP server.
        port: NTP UDP port (default 123).
        timeout: Per-probe timeout in seconds.

    Returns:
        UdpProbeResult with stratum / refid / poll in ``extra`` on success.
    """
    try:
        protocol, duration_ms = await _send_and_wait(
            host, port, _NTP_CLIENT_PACKET, timeout=timeout
        )
    except OSError as exc:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="ntp",
            status=UdpProbeStatus.ERROR,
            error=str(exc),
        )

    if protocol.error is not None:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="ntp",
            status=UdpProbeStatus.ICMP_UNREACHABLE,
            duration_ms=round(duration_ms, 2),
            error=str(protocol.error),
        )

    if protocol.data is None:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="ntp",
            status=UdpProbeStatus.TIMEOUT,
            duration_ms=round(duration_ms, 2),
            error="no response within timeout",
        )

    if len(protocol.data) < 48:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="ntp",
            status=UdpProbeStatus.RESPONDED,
            raw_response=protocol.data,
            duration_ms=round(duration_ms, 2),
            error=f"short response: {len(protocol.data)} bytes (expected 48)",
        )

    first = protocol.data[0]
    leap = (first >> 6) & 0x03
    version = (first >> 3) & 0x07
    mode = first & 0x07
    stratum = protocol.data[1]
    poll = protocol.data[2]
    precision = struct.unpack(">b", protocol.data[3:4])[0]
    refid = _decode_refid(stratum, protocol.data[12:16])

    extra: dict[str, Any] = {
        "leap": leap,
        "version": version,
        "mode": mode,
        "stratum": stratum,
        "poll": poll,
        "precision": precision,
        "refid": refid,
    }
    return UdpProbeResult(
        host=host,
        port=port,
        protocol="ntp",
        status=UdpProbeStatus.RESPONDED,
        payload=f"stratum={stratum} refid={refid}",
        raw_response=protocol.data,
        duration_ms=round(duration_ms, 2),
        extra=extra,
    )


# ── SNMP v1 ────────────────────────────────────────────────────────


def _ber_length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    out = b""
    while n > 0:
        out = bytes([n & 0xFF]) + out
        n >>= 8
    return bytes([0x80 | len(out)]) + out


def _ber_int(n: int) -> bytes:
    if n == 0:
        body = b"\x00"
    else:
        # Two's-complement minimal encoding (positive only here).
        length = (n.bit_length() + 7) // 8
        body = n.to_bytes(length, "big")
        if body[0] & 0x80:
            body = b"\x00" + body
    return b"\x02" + _ber_length(len(body)) + body


def _ber_octet(b: bytes) -> bytes:
    return b"\x04" + _ber_length(len(b)) + b


def _ber_oid(oid: str) -> bytes:
    parts = [int(p) for p in oid.split(".")]
    if len(parts) < 2:
        raise ValueError(f"OID too short: {oid}")
    body = bytes([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        if p < 0x80:
            body += bytes([p])
        else:
            chunks: list[int] = []
            while p > 0:
                chunks.insert(0, p & 0x7F)
                p >>= 7
            for i in range(len(chunks) - 1):
                chunks[i] |= 0x80
            body += bytes(chunks)
    return b"\x06" + _ber_length(len(body)) + body


def _ber_sequence(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _ber_length(len(content)) + content


def _build_snmp_v1_get(community: str, oid: str, request_id: int) -> bytes:
    """Hand-build an SNMP v1 GET PDU per RFC 1157."""
    null = b"\x05\x00"
    varbind = _ber_sequence(0x30, _ber_oid(oid) + null)
    varbind_list = _ber_sequence(0x30, varbind)
    pdu = _ber_sequence(
        0xA0,  # GetRequest-PDU
        _ber_int(request_id) + _ber_int(0) + _ber_int(0) + varbind_list,
    )
    message = _ber_sequence(
        0x30,
        _ber_int(0)  # version: SNMPv1 = 0
        + _ber_octet(community.encode("ascii"))
        + pdu,
    )
    return message


def _parse_ber_length(data: bytes, offset: int) -> tuple[int, int]:
    """Return (length, next_offset)."""
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    n = first & 0x7F
    length = int.from_bytes(data[offset + 1 : offset + 1 + n], "big")
    return length, offset + 1 + n


def _parse_snmp_v1_response(data: bytes) -> str | None:
    """Extract the first OctetString from an SNMP v1 GetResponse.

    Returns None if the structure can't be parsed. Conservative:
    minimal validation, just enough to grab the sysDescr.0 string.
    """
    try:
        # SEQUENCE { version, community, PDU }
        if data[0] != 0x30:
            return None
        _, off = _parse_ber_length(data, 1)
        # version INTEGER
        if data[off] != 0x02:
            return None
        vlen, off = _parse_ber_length(data, off + 1)
        off += vlen
        # community OCTET STRING
        if data[off] != 0x04:
            return None
        clen, off = _parse_ber_length(data, off + 1)
        off += clen
        # PDU [2] (GetResponse = 0xA2)
        if data[off] not in (0xA0, 0xA1, 0xA2, 0xA3):
            return None
        _, off = _parse_ber_length(data, off + 1)
        # request-id INTEGER
        if data[off] != 0x02:
            return None
        rlen, off = _parse_ber_length(data, off + 1)
        off += rlen
        # error-status INTEGER
        if data[off] != 0x02:
            return None
        elen, off = _parse_ber_length(data, off + 1)
        off += elen
        # error-index INTEGER
        if data[off] != 0x02:
            return None
        ilen, off = _parse_ber_length(data, off + 1)
        off += ilen
        # variable-bindings SEQUENCE
        if data[off] != 0x30:
            return None
        _, off = _parse_ber_length(data, off + 1)
        # First varbind SEQUENCE
        if data[off] != 0x30:
            return None
        _, off = _parse_ber_length(data, off + 1)
        # OID
        if data[off] != 0x06:
            return None
        olen, off = _parse_ber_length(data, off + 1)
        off += olen
        # Value — accept OCTET STRING (0x04) or any printable
        if data[off] == 0x04:
            slen, off = _parse_ber_length(data, off + 1)
            return data[off : off + slen].decode("utf-8", errors="replace")
    except (IndexError, ValueError):
        return None
    return None


async def probe_snmp(
    host: str,
    port: int = 161,
    *,
    community: str = "public",
    timeout: float = 5.0,
) -> UdpProbeResult:
    """SNMP v1 GET sysDescr.0 (1.3.6.1.2.1.1.1.0).

    Only attempts SNMP v1 with the supplied community. v2c/v3 are out of
    scope for this probe — the goal is fingerprinting, not full
    instrumentation.

    Args:
        host: Target SNMP agent.
        port: SNMP UDP port (default 161).
        community: SNMP v1 community string (default "public").
        timeout: Per-probe timeout in seconds.

    Returns:
        UdpProbeResult with the sysDescr OctetString in ``payload`` on
        success.
    """
    import secrets

    request_id = secrets.randbits(31) + 1
    payload = _build_snmp_v1_get(community, "1.3.6.1.2.1.1.1.0", request_id)

    try:
        protocol, duration_ms = await _send_and_wait(host, port, payload, timeout=timeout)
    except OSError as exc:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="snmp",
            status=UdpProbeStatus.ERROR,
            error=str(exc),
        )

    if protocol.error is not None:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="snmp",
            status=UdpProbeStatus.ICMP_UNREACHABLE,
            duration_ms=round(duration_ms, 2),
            error=str(protocol.error),
        )

    if protocol.data is None:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="snmp",
            status=UdpProbeStatus.TIMEOUT,
            duration_ms=round(duration_ms, 2),
            error="no response within timeout",
        )

    sysdescr = _parse_snmp_v1_response(protocol.data)
    extra: dict[str, Any] = {"oid": "1.3.6.1.2.1.1.1.0", "community": community}
    return UdpProbeResult(
        host=host,
        port=port,
        protocol="snmp",
        status=UdpProbeStatus.RESPONDED,
        payload=sysdescr,
        raw_response=protocol.data,
        duration_ms=round(duration_ms, 2),
        extra=extra,
    )


# ── Syslog ─────────────────────────────────────────────────────────


async def probe_syslog(
    host: str,
    port: int = 514,
    *,
    timeout: float = 1.0,
) -> UdpProbeResult:
    """Send a benign syslog datagram.

    Syslog over UDP is fire-and-forget: the spec defines no response.
    This probe sends a single ``<14>kaos-web-probe`` message (priority 14
    = user.info) and reports SENT_NO_RESPONSE_EXPECTED if the OS accepted
    it without ICMP-unreachable. Use it to confirm the port accepts
    datagrams.

    Args:
        host: Target syslog host.
        port: Syslog UDP port (default 514).
        timeout: How long to wait for an ICMP-unreachable signal before
            declaring "sent OK". 1 second is plenty.

    Returns:
        UdpProbeResult with status SENT_NO_RESPONSE_EXPECTED on the happy
        path, ICMP_UNREACHABLE if the kernel surfaced one, ERROR on local
        socket errors.
    """
    payload = b"<14>kaos-web-probe"
    try:
        protocol, duration_ms = await _send_and_wait(host, port, payload, timeout=timeout)
    except OSError as exc:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="syslog",
            status=UdpProbeStatus.ERROR,
            error=str(exc),
        )

    if protocol.error is not None:
        return UdpProbeResult(
            host=host,
            port=port,
            protocol="syslog",
            status=UdpProbeStatus.ICMP_UNREACHABLE,
            duration_ms=round(duration_ms, 2),
            error=str(protocol.error),
        )

    return UdpProbeResult(
        host=host,
        port=port,
        protocol="syslog",
        status=UdpProbeStatus.SENT_NO_RESPONSE_EXPECTED,
        duration_ms=round(duration_ms, 2),
        payload="datagram sent (syslog is fire-and-forget)",
        extra={"sent_bytes": len(payload)},
    )
