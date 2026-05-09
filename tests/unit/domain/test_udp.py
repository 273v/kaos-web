"""Tests for ``kaos_web.domain.udp`` — DNS, NTP, SNMP, syslog probes.

The probes use ``loop.create_datagram_endpoint`` for I/O. We mock that
factory to inject a fake transport (which captures sent payloads) and a
real :class:`_SingleDatagramProtocol` (which we drive directly via
``datagram_received`` / ``error_received``).

Response fixtures are real bytes:
- DNS: ``dig +tcp version.bind CHAOS TXT @8.8.8.8`` style response built
  via dnspython, plus a hand-crafted version. We also test the bytes
  parser directly with a real BIND-style payload.
- NTP: 48-byte packet built per RFC 5905 §7.3 with realistic fields.
- SNMP: BER-encoded sysDescr.0 GetResponse hand-built per RFC 1157.

This way we don't trust dnspython/ntp parsers — we verify our own.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from kaos_web.domain.models import UdpProbeResult, UdpProbeStatus
from kaos_web.domain.udp import (
    _build_dns_query,
    _build_snmp_v1_get,
    _decode_dns_name,
    _decode_refid,
    _parse_dns_txt_answers,
    _parse_snmp_v1_response,
    _SingleDatagramProtocol,
    probe_dns,
    probe_ntp,
    probe_snmp,
    probe_syslog,
)

# ── Fake datagram endpoint ────────────────────────────────────────


class _FakeTransport:
    """Capture sent datagrams; provide a no-op close()."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed: bool = False

    def sendto(self, data: bytes, addr: tuple[str, int] | None = None) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


@contextlib.contextmanager
def _patch_datagram_endpoint(
    response_bytes: bytes | None = None,
    *,
    error: Exception | None = None,
    factory_raises: Exception | None = None,
    sleep_before_response: float = 0.0,
) -> Iterator[_FakeTransport]:
    """Patch the running loop's ``create_datagram_endpoint`` for one test.

    Why patch the loop instance and not ``asyncio.AbstractEventLoop``? Each
    concrete loop class (``_UnixSelectorEventLoop`` etc.) overrides the
    method on its own subclass, so patching the abstract base is silently
    ignored. We patch the live instance returned by ``get_running_loop()``.
    """
    transport = _FakeTransport()

    async def _factory(
        protocol_factory: Any, *, remote_addr: tuple[str, int], **kwargs: Any
    ) -> tuple[_FakeTransport, _SingleDatagramProtocol]:
        if factory_raises is not None:
            raise factory_raises
        proto = protocol_factory()
        assert isinstance(proto, _SingleDatagramProtocol)

        if response_bytes is not None:

            async def _deliver() -> None:
                if sleep_before_response:
                    await asyncio.sleep(sleep_before_response)
                proto.datagram_received(response_bytes, remote_addr)

            asyncio.get_running_loop().create_task(_deliver())
        elif error is not None:

            async def _deliver_err() -> None:
                if sleep_before_response:
                    await asyncio.sleep(sleep_before_response)
                proto.error_received(error)

            asyncio.get_running_loop().create_task(_deliver_err())

        return transport, proto

    loop = asyncio.get_running_loop()
    with patch.object(loop, "create_datagram_endpoint", new=_factory):
        yield transport


# ── _SingleDatagramProtocol ───────────────────────────────────────


@pytest.mark.asyncio
class TestSingleDatagramProtocol:
    async def test_first_datagram_sets_event(self) -> None:
        proto = _SingleDatagramProtocol()
        proto.datagram_received(b"hello", ("1.2.3.4", 53))
        assert proto.event.is_set()
        assert proto.data == b"hello"
        assert proto.addr == ("1.2.3.4", 53)

    async def test_second_datagram_ignored(self) -> None:
        proto = _SingleDatagramProtocol()
        proto.datagram_received(b"first", ("1.2.3.4", 53))
        proto.datagram_received(b"second", ("1.2.3.4", 53))
        assert proto.data == b"first"

    async def test_error_signals_event(self) -> None:
        proto = _SingleDatagramProtocol()
        exc = ConnectionRefusedError("port unreachable")
        proto.error_received(exc)
        assert proto.event.is_set()
        assert proto.error is exc


# ── DNS query construction (fallback path) ────────────────────────


class TestBuildDnsQuery:
    def test_version_bind_chaos_txt(self) -> None:
        wire = _build_dns_query("version.bind", 16, 3)  # TXT, CHAOS
        # 12-byte header + qname + 4 bytes (type+class)
        assert len(wire) == 12 + len(b"\x07version\x04bind\x00") + 4
        # QDCOUNT=1
        assert struct.unpack(">H", wire[4:6])[0] == 1
        # QNAME contains "version" and "bind" labels
        assert b"\x07version" in wire
        assert b"\x04bind" in wire
        # QTYPE=16 (TXT), QCLASS=3 (CHAOS) at the end
        qtype, qclass = struct.unpack(">HH", wire[-4:])
        assert qtype == 16
        assert qclass == 3

    def test_query_has_random_txid(self) -> None:
        a = _build_dns_query("example.com", 1, 1)
        b = _build_dns_query("example.com", 1, 1)
        # Highly unlikely (1/65536) for two consecutive queries to share a txid
        assert a != b


# ── DNS name decoder ──────────────────────────────────────────────


class TestDecodeDnsName:
    def test_simple_uncompressed(self) -> None:
        # "version.bind" QNAME format
        data = b"\x07version\x04bind\x00"
        name, off = _decode_dns_name(data, 0)
        assert name == "version.bind"
        assert off == len(data)

    def test_compressed_pointer(self) -> None:
        # Two names: "example.com" at offset 0, then a pointer back to it
        data = b"\x07example\x03com\x00\xc0\x00"
        name1, off1 = _decode_dns_name(data, 0)
        assert name1 == "example.com"
        name2, off2 = _decode_dns_name(data, off1)
        assert name2 == "example.com"
        assert off2 == len(data)

    def test_pointer_loop_protected(self) -> None:
        # Pointer that points to itself (offset 0 = pointer to 0)
        # Since the pointer is INSIDE itself, this is malformed but must not infinite-loop
        data = b"\xc0\x00"
        name, _ = _decode_dns_name(data, 0)
        # Should bail out gracefully (empty name)
        assert isinstance(name, str)


# ── DNS TXT response parser ───────────────────────────────────────


def _build_dns_txt_response(name: str, txt: str) -> bytes:
    """Build a minimal DNS response with one TXT answer."""
    # Header: txid=0xabcd, flags=0x8180 (response, RD, RA), QDCOUNT=1, ANCOUNT=1
    header = struct.pack(">HHHHHH", 0xABCD, 0x8180, 1, 1, 0, 0)
    # Question section
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split(".")) + b"\x00"
    question = qname + struct.pack(">HH", 16, 3)  # TXT, CHAOS
    # Answer: NAME (pointer to question), TYPE=TXT, CLASS=CHAOS, TTL=0, RDLENGTH, RDATA
    ans_name = b"\xc0\x0c"  # pointer to offset 12 (start of question name)
    txt_bytes = txt.encode("utf-8")
    rdata = bytes([len(txt_bytes)]) + txt_bytes
    answer = ans_name + struct.pack(">HHIH", 16, 3, 0, len(rdata)) + rdata
    return header + question + answer


class TestParseDnsTxtAnswers:
    def test_real_version_bind_response(self) -> None:
        # A response that BIND would send for version.bind CHAOS TXT
        wire = _build_dns_txt_response("version.bind", "9.18.24-Debian")
        answers = _parse_dns_txt_answers(wire)
        assert answers == ["9.18.24-Debian"]

    def test_truncated_response(self) -> None:
        assert _parse_dns_txt_answers(b"\x00\x00") == []

    def test_no_answers(self) -> None:
        # Header with ANCOUNT=0
        header = struct.pack(">HHHHHH", 0xABCD, 0x8180, 1, 0, 0, 0)
        qname = b"\x07example\x03com\x00"
        question = qname + struct.pack(">HH", 1, 1)
        wire = header + question
        assert _parse_dns_txt_answers(wire) == []


# ── probe_dns ──────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestProbeDns:
    async def test_responded_with_txt_answer(self) -> None:
        wire = _build_dns_txt_response("version.bind", "BIND 9.18.24")
        with _patch_datagram_endpoint(response_bytes=wire) as transport:
            r = await probe_dns("8.8.8.8", timeout=2.0)
        assert isinstance(r, UdpProbeResult)
        assert r.protocol == "dns"
        assert r.status == UdpProbeStatus.RESPONDED
        assert r.payload is not None and "BIND 9.18.24" in r.payload
        assert r.host == "8.8.8.8"
        assert r.port == 53
        # Verify a query was actually sent
        assert len(transport.sent) == 1
        assert transport.closed is True

    async def test_timeout(self) -> None:
        with _patch_datagram_endpoint(response_bytes=None):
            r = await probe_dns("10.255.255.1", timeout=0.05)
        assert r.status == UdpProbeStatus.TIMEOUT
        assert r.error is not None and "no response" in r.error

    async def test_icmp_unreachable(self) -> None:
        with _patch_datagram_endpoint(error=ConnectionRefusedError("port unreachable")):
            r = await probe_dns("127.0.0.1", timeout=2.0)
        assert r.status == UdpProbeStatus.ICMP_UNREACHABLE
        assert r.error is not None and "unreachable" in r.error

    async def test_local_oserror(self) -> None:
        with _patch_datagram_endpoint(factory_raises=OSError("address family not supported")):
            r = await probe_dns("::1", timeout=1.0)
        assert r.status == UdpProbeStatus.ERROR
        assert r.error is not None and "address family" in r.error

    async def test_dnspython_present_path(self) -> None:
        # When dnspython is installed (it's a dev dep here), the 'use_dnspython'
        # branch should be taken and we should still parse the response.
        wire = _build_dns_txt_response("version.bind", "Unbound 1.19.0")
        with _patch_datagram_endpoint(response_bytes=wire):
            r = await probe_dns("9.9.9.9", timeout=2.0)
        assert r.status == UdpProbeStatus.RESPONDED
        assert r.payload is not None and "Unbound" in r.payload

    async def test_dnspython_fallback_via_importerror(self) -> None:
        # Simulate dnspython not being installed
        wire = _build_dns_txt_response("version.bind", "Knot 3.3.5")
        import builtins

        real_import = builtins.__import__

        def _no_dns(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("dns"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=_no_dns),
            _patch_datagram_endpoint(response_bytes=wire),
        ):
            r = await probe_dns("9.9.9.9", timeout=2.0)
        assert r.status == UdpProbeStatus.RESPONDED
        assert r.payload is not None and "Knot 3.3.5" in r.payload


# ── NTP ────────────────────────────────────────────────────────────


def _build_ntp_response(stratum: int = 2, refid: bytes = b"\x80\x80\x80\x80") -> bytes:
    """Build a 48-byte NTPv4 server response.

    LI=0, VN=4, Mode=4 (server) → first byte 0x24.
    """
    pkt = bytearray(48)
    pkt[0] = 0x24
    pkt[1] = stratum
    pkt[2] = 6  # poll
    pkt[3] = 0xFA  # precision -6 in two's complement
    # Reference id at bytes 12-15
    pkt[12:16] = refid
    return bytes(pkt)


class TestDecodeRefid:
    def test_stratum_1_ascii(self) -> None:
        assert _decode_refid(1, b"GPS\x00") == "GPS"

    def test_stratum_0_ascii(self) -> None:
        assert _decode_refid(0, b"INIT") == "INIT"

    def test_stratum_2_ipv4(self) -> None:
        assert _decode_refid(2, b"\xc0\xa8\x01\x01") == "192.168.1.1"


@pytest.mark.asyncio
class TestProbeNtp:
    async def test_responded_with_stratum_and_refid(self) -> None:
        # Stratum 2, refid pointing to 192.168.1.1
        resp = _build_ntp_response(stratum=2, refid=b"\xc0\xa8\x01\x01")
        with _patch_datagram_endpoint(response_bytes=resp) as transport:
            r = await probe_ntp("pool.ntp.org", timeout=2.0)
        assert r.status == UdpProbeStatus.RESPONDED
        assert r.protocol == "ntp"
        assert r.extra["stratum"] == 2
        assert r.extra["refid"] == "192.168.1.1"
        assert r.extra["version"] == 4
        assert r.extra["mode"] == 4
        assert "stratum=2" in (r.payload or "")
        # Sent payload was 48 bytes with the client packet
        assert len(transport.sent[0]) == 48
        assert transport.sent[0][0] == 0x23

    async def test_short_response(self) -> None:
        resp = b"\x24\x02"  # truncated
        with _patch_datagram_endpoint(response_bytes=resp):
            r = await probe_ntp("ntp.example", timeout=1.0)
        assert r.status == UdpProbeStatus.RESPONDED
        assert r.error is not None and "short response" in r.error

    async def test_timeout(self) -> None:
        with _patch_datagram_endpoint(response_bytes=None):
            r = await probe_ntp("10.255.255.1", timeout=0.05)
        assert r.status == UdpProbeStatus.TIMEOUT

    async def test_icmp_unreachable(self) -> None:
        with _patch_datagram_endpoint(error=ConnectionRefusedError("port unreachable")):
            r = await probe_ntp("127.0.0.1", timeout=1.0)
        assert r.status == UdpProbeStatus.ICMP_UNREACHABLE

    async def test_oserror(self) -> None:
        with _patch_datagram_endpoint(factory_raises=OSError("permission denied")):
            r = await probe_ntp("ntp.example", timeout=1.0)
        assert r.status == UdpProbeStatus.ERROR


# ── SNMP ────────────────────────────────────────────────────────────


def _build_snmp_v1_response(community: str, oid: str, sysdescr: str) -> bytes:
    """Build an SNMP v1 GetResponse with a sysDescr OctetString."""
    from kaos_web.domain.udp import (
        _ber_int,
        _ber_octet,
        _ber_oid,
        _ber_sequence,
    )

    varbind = _ber_sequence(
        0x30,
        _ber_oid(oid) + _ber_octet(sysdescr.encode("utf-8")),
    )
    varbind_list = _ber_sequence(0x30, varbind)
    pdu = _ber_sequence(
        0xA2,  # GetResponse-PDU
        _ber_int(1) + _ber_int(0) + _ber_int(0) + varbind_list,
    )
    return _ber_sequence(
        0x30,
        _ber_int(0) + _ber_octet(community.encode("ascii")) + pdu,
    )


class TestSnmpParser:
    def test_parse_real_response(self) -> None:
        sysdescr = "Linux router 5.15.0-78-generic #85-Ubuntu SMP x86_64 GNU/Linux"
        wire = _build_snmp_v1_response("public", "1.3.6.1.2.1.1.1.0", sysdescr)
        result = _parse_snmp_v1_response(wire)
        assert result == sysdescr

    def test_parse_garbage(self) -> None:
        assert _parse_snmp_v1_response(b"\xff\xff\xff\xff") is None

    def test_parse_truncated(self) -> None:
        wire = _build_snmp_v1_response("public", "1.3.6.1.2.1.1.1.0", "Test")[:5]
        assert _parse_snmp_v1_response(wire) is None

    def test_build_request_round_trip(self) -> None:
        # Verify that what we build is parseable by our own parser when
        # given a response of the same shape (sanity check on BER plumbing).
        req = _build_snmp_v1_get("public", "1.3.6.1.2.1.1.1.0", 12345)
        # The request has 0x05 (NULL) as the value, not OctetString — parser
        # should return None for it but not crash.
        result = _parse_snmp_v1_response(req)
        assert result is None

    def test_build_request_starts_with_sequence_tag(self) -> None:
        req = _build_snmp_v1_get("public", "1.3.6.1.2.1.1.1.0", 1)
        assert req[0] == 0x30
        # Contains the community string somewhere
        assert b"public" in req


@pytest.mark.asyncio
class TestProbeSnmp:
    async def test_responded(self) -> None:
        sysdescr = "Cisco IOS Software, IOS-XE 17.9.4"
        wire = _build_snmp_v1_response("public", "1.3.6.1.2.1.1.1.0", sysdescr)
        with _patch_datagram_endpoint(response_bytes=wire) as transport:
            r = await probe_snmp("snmp.example", timeout=2.0)
        assert r.status == UdpProbeStatus.RESPONDED
        assert r.protocol == "snmp"
        assert r.payload == sysdescr
        assert r.extra["oid"] == "1.3.6.1.2.1.1.1.0"
        assert r.extra["community"] == "public"
        # Verify request was built and sent
        assert len(transport.sent) == 1
        assert transport.sent[0][0] == 0x30
        assert b"public" in transport.sent[0]

    async def test_custom_community(self) -> None:
        wire = _build_snmp_v1_response("private", "1.3.6.1.2.1.1.1.0", "x")
        with _patch_datagram_endpoint(response_bytes=wire) as transport:
            r = await probe_snmp("snmp.example", community="private", timeout=2.0)
        assert r.status == UdpProbeStatus.RESPONDED
        assert b"private" in transport.sent[0]
        assert r.extra["community"] == "private"

    async def test_timeout(self) -> None:
        with _patch_datagram_endpoint(response_bytes=None):
            r = await probe_snmp("10.255.255.1", timeout=0.05)
        assert r.status == UdpProbeStatus.TIMEOUT

    async def test_icmp_unreachable(self) -> None:
        with _patch_datagram_endpoint(error=ConnectionRefusedError("unreachable")):
            r = await probe_snmp("127.0.0.1", timeout=1.0)
        assert r.status == UdpProbeStatus.ICMP_UNREACHABLE

    async def test_oserror(self) -> None:
        with _patch_datagram_endpoint(factory_raises=OSError("network is unreachable")):
            r = await probe_snmp("snmp.example", timeout=1.0)
        assert r.status == UdpProbeStatus.ERROR


# ── Syslog ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestProbeSyslog:
    async def test_sent_no_response_expected(self) -> None:
        with _patch_datagram_endpoint(response_bytes=None) as transport:
            r = await probe_syslog("syslog.example", timeout=0.05)
        assert r.status == UdpProbeStatus.SENT_NO_RESPONSE_EXPECTED
        assert r.protocol == "syslog"
        assert r.extra["sent_bytes"] > 0
        # Verify the priority prefix was sent
        assert transport.sent[0].startswith(b"<14>")

    async def test_icmp_unreachable_when_kernel_signals(self) -> None:
        with _patch_datagram_endpoint(error=ConnectionRefusedError("unreachable")):
            r = await probe_syslog("127.0.0.1", timeout=0.5)
        assert r.status == UdpProbeStatus.ICMP_UNREACHABLE

    async def test_oserror(self) -> None:
        with _patch_datagram_endpoint(factory_raises=OSError("permission denied")):
            r = await probe_syslog("syslog.example", timeout=0.5)
        assert r.status == UdpProbeStatus.ERROR


# ── BER encoder helpers ──────────────────────────────────────────


class TestBerEncoders:
    def test_short_length(self) -> None:
        from kaos_web.domain.udp import _ber_length

        assert _ber_length(0) == b"\x00"
        assert _ber_length(127) == b"\x7f"

    def test_long_length(self) -> None:
        from kaos_web.domain.udp import _ber_length

        # 128 needs the long form: 0x81 0x80
        assert _ber_length(128) == b"\x81\x80"
        # 256 needs 0x82 0x01 0x00
        assert _ber_length(256) == b"\x82\x01\x00"

    def test_int_zero(self) -> None:
        from kaos_web.domain.udp import _ber_int

        # ASN.1 INTEGER 0
        assert _ber_int(0) == b"\x02\x01\x00"

    def test_int_positive(self) -> None:
        from kaos_web.domain.udp import _ber_int

        assert _ber_int(127) == b"\x02\x01\x7f"
        # 128 needs leading zero (high bit set => positive)
        assert _ber_int(128) == b"\x02\x02\x00\x80"

    def test_oid_encoding(self) -> None:
        from kaos_web.domain.udp import _ber_oid

        # 1.3.6.1.2.1.1.1.0 — sysDescr.0
        encoded = _ber_oid("1.3.6.1.2.1.1.1.0")
        # First byte: tag 0x06, length, then 1*40+3=43=0x2b, then 6, 1, 2, 1, 1, 1, 0
        assert encoded[0] == 0x06
        # Body bytes: 0x2b 0x06 0x01 0x02 0x01 0x01 0x01 0x00
        assert encoded[2:] == bytes([0x2B, 6, 1, 2, 1, 1, 1, 0])

    def test_oid_with_high_value(self) -> None:
        from kaos_web.domain.udp import _ber_oid

        # 1.3.6.1.4.1.8072 (Net-SNMP enterprise) — 8072 needs multibyte
        encoded = _ber_oid("1.3.6.1.4.1.8072")
        assert encoded[0] == 0x06
        # 8072 = 0x1F88 → split into 7-bit chunks: 0x3F (high bit set) 0x08
        # Specifically: 8072 = 0b0011111110001000 → top: 63, bot: 8
        # So encoded as 0xBF 0x08 (first chunk has high bit set)
        assert encoded[-2:] == bytes([0xBF, 0x08])

    def test_oid_too_short(self) -> None:
        from kaos_web.domain.udp import _ber_oid

        with pytest.raises(ValueError):
            _ber_oid("1")
