"""Tests for ``kaos_web.domain.whois`` — WHOIS client + parser.

The parser ``_parse_whois_text`` is exercised against real WHOIS
response text drawn from public registry/registrar formats (Verisign
.com, Public Interest Registry .org, .uk, .de).  The async network
surface is covered by mocking ``asyncio.open_connection`` to feed
canned response bytes through a fake ``StreamReader``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.domain.models import WhoisRecord
from kaos_web.domain.whois import (
    IANA_WHOIS,
    WHOIS_SERVERS,
    _follow_referral,
    _get_whois_server,
    _parse_date,
    _parse_whois_text,
    _raw_whois_query,
    whois_lookup,
)

# ── Real-shaped WHOIS responses (based on actual public data) ────────

_VERISIGN_COM_RESPONSE = """\
   Domain Name: EXAMPLE.COM
   Registry Domain ID: 2336799_DOMAIN_COM-VRSN
   Registrar WHOIS Server: whois.iana.org
   Registrar URL: http://res-dom.iana.org
   Updated Date: 2024-08-14T07:01:38Z
   Creation Date: 1995-08-14T04:00:00Z
   Registry Expiry Date: 2025-08-13T04:00:00Z
   Registrar: RESERVED-Internet Assigned Numbers Authority
   Registrar IANA ID: 376
   Registrar Abuse Contact Email:
   Registrar Abuse Contact Phone:
   Domain Status: clientDeleteProhibited https://icann.org/epp#clientDeleteProhibited
   Domain Status: clientTransferProhibited https://icann.org/epp#clientTransferProhibited
   Domain Status: clientUpdateProhibited https://icann.org/epp#clientUpdateProhibited
   Name Server: A.IANA-SERVERS.NET
   Name Server: B.IANA-SERVERS.NET
   DNSSEC: signedDelegation
   URL of the ICANN Whois Inaccuracy Complaint Form: https://www.icann.org/wicf/
>>> Last update of whois database: 2024-12-01T12:00:00Z <<<
"""

_PIR_ORG_RESPONSE = """\
Domain Name: WIKIPEDIA.ORG
Registry Domain ID: 4caedab78b5745b5868fc7f0e36a1e7c-LROR
Registrar WHOIS Server: whois.markmonitor.com
Registrar URL: http://www.markmonitor.com
Updated Date: 2024-12-13T19:32:01Z
Creation Date: 2001-01-13T00:12:14Z
Registry Expiry Date: 2027-01-13T00:12:14Z
Registrar: MarkMonitor Inc.
Registrar IANA ID: 292
Domain Status: clientDeleteProhibited https://icann.org/epp#clientDeleteProhibited
Domain Status: clientTransferProhibited https://icann.org/epp#clientTransferProhibited
Domain Status: clientUpdateProhibited https://icann.org/epp#clientUpdateProhibited
Registrant Organization: Wikimedia Foundation, Inc.
Registrant State/Province: CA
Registrant Country: US
Name Server: NS0.WIKIMEDIA.ORG
Name Server: NS1.WIKIMEDIA.ORG
Name Server: NS2.WIKIMEDIA.ORG
DNSSEC: unsigned
"""

_DENIC_DE_RESPONSE = """\
% Restricted rights.
%
Domain: example.de
Nserver: ns1.example.de
Nserver: ns2.example.de
Status: connect
Changed: 2024-01-15T12:00:00+02:00
"""

_THIN_REFERRAL_RESPONSE = """\
   Domain Name: EXAMPLE.COM
   Registry Domain ID: 2336799_DOMAIN_COM-VRSN
   Registrar WHOIS Server: whois.markmonitor.com
   Registrar URL: http://www.markmonitor.com
   Updated Date: 2024-01-01T00:00:00Z
"""


# ── _parse_date ─────────────────────────────────────────────────────


class TestParseDate:
    def test_none(self) -> None:
        assert _parse_date(None) is None

    def test_empty(self) -> None:
        assert _parse_date("") is None

    def test_iso_z(self) -> None:
        d = _parse_date("2024-08-14T07:01:38Z")
        assert d is not None
        assert d.startswith("2024-08-14T07:01:38")

    def test_with_offset(self) -> None:
        d = _parse_date("2024-01-15T12:00:00+02:00")
        assert d is not None

    def test_simple_date(self) -> None:
        assert _parse_date("2024-08-14") is not None

    def test_dd_mon_yyyy(self) -> None:
        assert _parse_date("14-Aug-2024") is not None

    def test_with_before_prefix(self) -> None:
        assert _parse_date("before 2020-01-01") is not None

    def test_unknown_format(self) -> None:
        assert _parse_date("not-a-date-at-all") is None


# ── _parse_whois_text ───────────────────────────────────────────────


class TestParseWhoisText:
    def test_verisign_com(self) -> None:
        r = _parse_whois_text(_VERISIGN_COM_RESPONSE, "example.com")
        assert r.domain == "example.com"
        assert r.registrar == "RESERVED-Internet Assigned Numbers Authority"
        assert r.whois_server == "whois.iana.org"
        assert r.creation_date is not None and r.creation_date.startswith("1995-08-14")
        assert r.expiration_date is not None and r.expiration_date.startswith("2025-08-13")
        assert r.dnssec == "signedDelegation"
        assert "a.iana-servers.net" in r.name_servers
        assert "b.iana-servers.net" in r.name_servers
        assert any("clientDeleteProhibited" in s for s in r.status)
        assert r.raw_text is not None and "EXAMPLE.COM" in r.raw_text

    def test_pir_org(self) -> None:
        r = _parse_whois_text(_PIR_ORG_RESPONSE, "wikipedia.org")
        assert r.registrar == "MarkMonitor Inc."
        assert r.registrant_org == "Wikimedia Foundation, Inc."
        assert r.registrant_country == "US"
        assert "ns0.wikimedia.org" in r.name_servers
        assert "ns1.wikimedia.org" in r.name_servers
        assert "ns2.wikimedia.org" in r.name_servers
        assert r.dnssec == "unsigned"

    def test_denic_de(self) -> None:
        r = _parse_whois_text(_DENIC_DE_RESPONSE, "example.de")
        # Comment lines (starting with %) are skipped
        assert "ns1.example.de" in r.name_servers
        assert "ns2.example.de" in r.name_servers
        # "connect" status comes through
        assert any("connect" in s.lower() for s in r.status)

    def test_empty_response(self) -> None:
        r = _parse_whois_text("", "missing.example")
        assert r.domain == "missing.example"
        assert r.registrar is None
        assert r.name_servers == []

    def test_throttle_response(self) -> None:
        # Many WHOIS servers respond with throttle messages — should parse
        # to an empty record without crashing.
        text = "% Excessive queries from your IP address.\n% Please slow down.\n"
        r = _parse_whois_text(text, "example.com")
        assert r.registrar is None
        assert r.creation_date is None
        # raw_text is preserved
        assert r.raw_text == text

    def test_dedupe_nameservers(self) -> None:
        text = (
            "Name Server: NS1.EXAMPLE.COM\n"
            "Name Server: ns1.example.com\n"  # case-insensitive duplicate
            "Name Server: ns2.example.com\n"
        )
        r = _parse_whois_text(text, "example.com")
        assert r.name_servers == ["ns1.example.com", "ns2.example.com"]

    def test_dedupe_status(self) -> None:
        text = "Status: connect\nStatus: connect\nStatus: ok\n"
        r = _parse_whois_text(text, "example.de")
        # Two unique values
        assert len(r.status) == 2

    def test_alt_creation_label(self) -> None:
        text = "Registered on: 2020-05-15\n"
        r = _parse_whois_text(text, "example.com")
        assert r.creation_date is not None and r.creation_date.startswith("2020-05-15")

    def test_paid_till_label(self) -> None:
        # Russian .ru registry uses "paid-till"
        text = "paid-till: 2025-06-01\n"
        r = _parse_whois_text(text, "example.ru")
        assert r.expiration_date is not None and r.expiration_date.startswith("2025-06-01")


# ── _get_whois_server ───────────────────────────────────────────────


class TestGetWhoisServer:
    def test_com(self) -> None:
        assert _get_whois_server("example.com") == "whois.verisign-grs.com"

    def test_subdomain_com(self) -> None:
        assert _get_whois_server("foo.bar.example.com") == "whois.verisign-grs.com"

    def test_org(self) -> None:
        assert _get_whois_server("wikipedia.org") == "whois.pir.org"

    def test_co_uk_takes_precedence(self) -> None:
        # co.uk should match before just uk
        assert _get_whois_server("example.co.uk") == WHOIS_SERVERS["co.uk"]

    def test_uk(self) -> None:
        assert _get_whois_server("example.uk") == WHOIS_SERVERS["uk"]

    def test_unknown_tld_iana(self) -> None:
        assert _get_whois_server("example.unknowntld") == IANA_WHOIS

    def test_trailing_dot(self) -> None:
        assert _get_whois_server("example.com.") == "whois.verisign-grs.com"


# ── _raw_whois_query ────────────────────────────────────────────────


class _FakeReader:
    """Fake StreamReader that returns canned bytes in chunks."""

    def __init__(self, payload: bytes, *, chunk: int = 4096) -> None:
        self._payload = payload
        self._chunk = chunk

    async def read(self, n: int) -> bytes:
        if not self._payload:
            return b""
        take = self._payload[:n]
        self._payload = self._payload[n:]
        return take


class _FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


@pytest.mark.asyncio
class TestRawWhoisQuery:
    async def test_basic_query(self) -> None:
        reader = _FakeReader(_VERISIGN_COM_RESPONSE.encode("utf-8"))
        writer = _FakeWriter()

        async def _fake_open(host: str, port: int) -> tuple[_FakeReader, _FakeWriter]:
            assert port == 43
            return reader, writer

        with patch("asyncio.open_connection", side_effect=_fake_open):
            text = await _raw_whois_query("example.com", "whois.verisign-grs.com", timeout=1.0)
        assert "EXAMPLE.COM" in text
        # Default query format is "<domain>\r\n"
        assert writer.written and writer.written[0] == b"example.com\r\n"

    async def test_denic_query_format(self) -> None:
        reader = _FakeReader(_DENIC_DE_RESPONSE.encode("utf-8"))
        writer = _FakeWriter()

        async def _fake_open(host: str, port: int) -> tuple[_FakeReader, _FakeWriter]:
            return reader, writer

        with patch("asyncio.open_connection", side_effect=_fake_open):
            await _raw_whois_query("example.de", "whois.denic.de", timeout=1.0)
        assert writer.written
        assert writer.written[0].startswith(b"-T dn,ace example.de")

    async def test_jp_query_format(self) -> None:
        reader = _FakeReader(b"")
        writer = _FakeWriter()

        async def _fake_open(host: str, port: int) -> tuple[_FakeReader, _FakeWriter]:
            return reader, writer

        with patch("asyncio.open_connection", side_effect=_fake_open):
            await _raw_whois_query("example.jp", "whois.jprs.jp", timeout=1.0)
        assert writer.written and writer.written[0] == b"example.jp/e\r\n"

    async def test_latin1_fallback(self) -> None:
        # Bytes that are invalid UTF-8 but valid latin-1
        bad_bytes = b"Registrar: M\xfcller GmbH\r\n"  # non-utf8
        reader = _FakeReader(bad_bytes)
        writer = _FakeWriter()

        async def _fake_open(host: str, port: int) -> tuple[_FakeReader, _FakeWriter]:
            return reader, writer

        with patch("asyncio.open_connection", side_effect=_fake_open):
            text = await _raw_whois_query("example.com", "whois.example.com", timeout=1.0)
        assert "Registrar:" in text

    async def test_read_timeout_returns_partial(self) -> None:
        # Reader returns one chunk then times out -> partial response is OK
        reader = MagicMock()
        reader.read = AsyncMock(side_effect=[b"partial response\r\n", TimeoutError()])
        writer = _FakeWriter()

        async def _fake_open(host: str, port: int) -> tuple[MagicMock, _FakeWriter]:
            return reader, writer

        with patch("asyncio.open_connection", side_effect=_fake_open):
            text = await _raw_whois_query("example.com", "whois.example.com", timeout=0.1)
        assert "partial response" in text


# ── _follow_referral ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestFollowReferral:
    async def test_no_referral(self) -> None:
        result = await _follow_referral("no referral here", "example.com", 1.0)
        assert result is None

    async def test_skips_http_referral(self) -> None:
        # Some Verisign-style records have HTTP-based WHOIS servers
        text = "Registrar WHOIS Server: https://whois.example.com\n"
        result = await _follow_referral(text, "example.com", 1.0)
        assert result is None

    async def test_follows_referral(self) -> None:
        text = "Registrar WHOIS Server: whois.markmonitor.com\n"
        with patch(
            "kaos_web.domain.whois._raw_whois_query",
            AsyncMock(return_value="Domain: example.com\n"),
        ) as mock:
            result = await _follow_referral(text, "example.com", 1.0)
        assert result is not None and "Domain:" in result
        mock.assert_awaited_once_with("example.com", "whois.markmonitor.com", timeout=1.0)

    async def test_referral_failure(self) -> None:
        text = "Registrar WHOIS Server: whois.example.com\n"
        with patch(
            "kaos_web.domain.whois._raw_whois_query",
            AsyncMock(side_effect=OSError("network")),
        ):
            result = await _follow_referral(text, "example.com", 1.0)
        assert result is None


# ── whois_lookup (public API) ───────────────────────────────────────


@pytest.mark.asyncio
class TestWhoisLookup:
    async def test_success_no_referral(self) -> None:
        with patch(
            "kaos_web.domain.whois._raw_whois_query",
            AsyncMock(return_value=_PIR_ORG_RESPONSE),
        ):
            r = await whois_lookup("wikipedia.org", follow_referrals=False)
        assert isinstance(r, WhoisRecord)
        assert r.domain == "wikipedia.org"
        assert r.registrar == "MarkMonitor Inc."

    async def test_success_with_referral(self) -> None:
        # First call returns thin response with referral; second returns full
        responses = iter([_THIN_REFERRAL_RESPONSE, "Registrar: Detailed Registrar Info\n"])

        async def _fake_query(domain: str, server: str, *, timeout: float = 10.0) -> str:
            return next(responses)

        with patch("kaos_web.domain.whois._raw_whois_query", side_effect=_fake_query):
            r = await whois_lookup("example.com", follow_referrals=True)
        # The referral response replaced the original
        assert r.registrar == "Detailed Registrar Info"

    async def test_timeout(self) -> None:
        with patch(
            "kaos_web.domain.whois._raw_whois_query",
            AsyncMock(side_effect=TimeoutError()),
        ):
            r = await whois_lookup("example.com", follow_referrals=False)
        assert r.error is not None and "timed out" in r.error

    async def test_connection_refused(self) -> None:
        with patch(
            "kaos_web.domain.whois._raw_whois_query",
            AsyncMock(side_effect=ConnectionRefusedError()),
        ):
            r = await whois_lookup("example.com", follow_referrals=False)
        assert r.error is not None and "Connection refused" in r.error

    async def test_oserror(self) -> None:
        with patch(
            "kaos_web.domain.whois._raw_whois_query",
            AsyncMock(side_effect=OSError("network down")),
        ):
            r = await whois_lookup("example.com", follow_referrals=False)
        assert r.error is not None and "Network error" in r.error

    async def test_normalizes_domain(self) -> None:
        # whois_lookup strips trailing dots and lowercases
        captured: list[str] = []

        async def _fake_query(domain: str, server: str, *, timeout: float = 10.0) -> str:
            captured.append(domain)
            return ""

        with patch("kaos_web.domain.whois._raw_whois_query", side_effect=_fake_query):
            await whois_lookup("EXAMPLE.COM.", follow_referrals=False)
        assert captured[0] == "example.com"

    async def test_url_policy_blocks_private_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WEB5-001: ``whois_lookup`` MUST refuse a private-IP literal
        (treats domain string as host) BEFORE opening a TCP socket.
        """
        from kaos_web.errors import UrlPolicyError

        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        with pytest.raises(UrlPolicyError) as info:
            await whois_lookup("10.0.0.1")
        assert "KAOS_SECURITY_" in str(info.value)
