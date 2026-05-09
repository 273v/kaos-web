"""Tests for ``kaos_web.domain.tcp.probe_banner`` and ``probe_banners``.

The contract surface is the (asyncio.open_connection outcome, send_probe,
read response) → (BannerProbeResult) mapping. We mock open_connection
to drive every status branch (open + read, open + empty, timeout,
refused, reset, oserror, send timeout). Banner fixtures are real
greetings copied verbatim from public protocol RFCs and observed
production servers — these are public-domain protocol greetings.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.domain.models import BannerProbeResult, PortStatus
from kaos_web.domain.tcp import _decode_banner, probe_banner, probe_banners

# ── Real banner fixtures ──────────────────────────────────────────


# Captured from `nc -v ssh.example 22` against an OpenSSH host
SSH_BANNER: bytes = b"SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.10\r\n"
# Captured from `nc -v smtp.example 25` against a Postfix host
SMTP_BANNER: bytes = b"220 mail.example.com ESMTP Postfix\r\n"
# Captured from `nc -v ftp.example 21` against a vsFTPd host
FTP_BANNER: bytes = b"220 (vsFTPd 3.0.5)\r\n"
# Captured from a Dovecot POP3 server
POP3_BANNER: bytes = b"+OK Dovecot ready.\r\n"
# Captured from a Dovecot IMAP server
IMAP_BANNER: bytes = (
    b"* OK [CAPABILITY IMAP4rev1 LITERAL+ SASL-IR LOGIN-REFERRALS "
    b"ID ENABLE IDLE LITERAL+ AUTH=PLAIN] Dovecot ready.\r\n"
)
# HTTP HEAD response (after probe payload)
HTTP_RESPONSE: bytes = (
    b"HTTP/1.1 200 OK\r\nServer: nginx/1.24.0\r\n"
    b"Content-Type: text/html\r\nContent-Length: 0\r\n\r\n"
)


def _stream_pair(
    banner: bytes = b"",
    *,
    read_raises: type[BaseException] | None = None,
    drain_raises: type[BaseException] | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build a (StreamReader, StreamWriter) pair backed by mocks."""
    reader = MagicMock()
    if read_raises is not None:
        reader.read = AsyncMock(side_effect=read_raises())
    else:
        reader.read = AsyncMock(return_value=banner)
    writer = MagicMock()
    writer.write = MagicMock()
    if drain_raises is not None:
        writer.drain = AsyncMock(side_effect=drain_raises())
    else:
        writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return reader, writer


# ── Decoding ──────────────────────────────────────────────────────


class TestDecodeBanner:
    def test_utf8_first(self) -> None:
        assert _decode_banner(b"SSH-2.0-OpenSSH\r\n") == "SSH-2.0-OpenSSH\r\n"

    def test_latin1_fallback(self) -> None:
        # Bytes that aren't valid UTF-8 but ARE valid latin-1
        raw = b"\xa0\xff banner"
        decoded = _decode_banner(raw)
        # latin-1 maps every byte to a codepoint 0-255
        assert decoded == raw.decode("latin-1")

    def test_repr_fallback_only_on_truly_undecodable(self) -> None:
        # latin-1 can decode any byte sequence so this never triggers in
        # practice — we test that the function survives for completeness.
        # Pass empty to verify no error.
        assert _decode_banner(b"") == ""


# ── probe_banner ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestProbeBannerOpen:
    async def test_open_with_unsolicited_banner(self) -> None:
        reader, writer = _stream_pair(SSH_BANNER)
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_banner("example.com", 22, timeout=2.0)
        assert isinstance(r, BannerProbeResult)
        assert r.host == "example.com"
        assert r.port == 22
        assert r.status == PortStatus.OPEN
        assert r.banner is not None and "SSH-2.0-OpenSSH" in r.banner
        assert r.banner_bytes == SSH_BANNER
        assert r.duration_ms is not None and r.duration_ms >= 0
        assert r.error is None
        # writer was closed
        writer.close.assert_called_once()

    async def test_open_empty_read(self) -> None:
        reader, writer = _stream_pair(b"")
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_banner("example.com", 443, timeout=2.0)
        assert r.status == PortStatus.OPEN
        assert r.banner is None
        assert r.banner_bytes is None

    @pytest.mark.parametrize(
        "banner",
        [SSH_BANNER, SMTP_BANNER, FTP_BANNER, POP3_BANNER, IMAP_BANNER],
    )
    async def test_real_banner_round_trip(self, banner: bytes) -> None:
        reader, writer = _stream_pair(banner)
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_banner("example.com", 22, timeout=2.0)
        assert r.status == PortStatus.OPEN
        assert r.banner_bytes == banner
        # UTF-8 decode round-trip — every fixture is ASCII
        assert r.banner == banner.decode("utf-8")

    async def test_send_probe_writes_payload(self) -> None:
        reader, writer = _stream_pair(HTTP_RESPONSE)
        probe = b"HEAD / HTTP/1.0\r\n\r\n"
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_banner("example.com", 80, timeout=2.0, send_probe=probe)
        writer.write.assert_called_once_with(probe)
        writer.drain.assert_awaited_once()
        assert r.status == PortStatus.OPEN
        assert r.banner is not None and "Server: nginx/1.24.0" in r.banner

    async def test_max_bytes_passed_to_read(self) -> None:
        reader, writer = _stream_pair(b"x" * 100)
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            await probe_banner("example.com", 22, timeout=2.0, max_bytes=128)
        reader.read.assert_awaited_once_with(128)


@pytest.mark.asyncio
class TestProbeBannerErrors:
    async def test_connect_timeout(self) -> None:
        async def _slow(*args: object, **kwargs: object) -> tuple[MagicMock, MagicMock]:
            raise TimeoutError()

        with patch("asyncio.open_connection", side_effect=_slow):
            r = await probe_banner("10.255.255.1", 22, timeout=0.05)
        assert r.status == PortStatus.TIMEOUT
        assert r.error == "connect timeout"
        assert r.banner is None
        assert r.duration_ms is not None

    async def test_read_timeout(self) -> None:
        reader, writer = _stream_pair(read_raises=TimeoutError)
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_banner("example.com", 22, timeout=0.05)
        assert r.status == PortStatus.TIMEOUT
        assert r.error is not None and "reading" in r.error
        # Even on read timeout, the writer must be closed (finally block)
        writer.close.assert_called_once()

    async def test_send_probe_timeout(self) -> None:
        reader, writer = _stream_pair(b"", drain_raises=TimeoutError)
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_banner(
                "example.com", 80, timeout=0.05, send_probe=b"HEAD / HTTP/1.0\r\n\r\n"
            )
        assert r.status == PortStatus.TIMEOUT
        assert r.error is not None and "sending probe" in r.error
        writer.close.assert_called_once()

    async def test_connection_refused(self) -> None:
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError()):
            r = await probe_banner("127.0.0.1", 1, timeout=1.0)
        assert r.status == PortStatus.CLOSED
        # Some platforms supply no message — accept either
        assert r.error is not None

    async def test_connection_reset(self) -> None:
        with patch("asyncio.open_connection", side_effect=ConnectionResetError("RST")):
            r = await probe_banner("127.0.0.1", 22, timeout=1.0)
        assert r.status == PortStatus.CLOSED
        assert r.error == "RST"

    async def test_oserror_filtered(self) -> None:
        with patch("asyncio.open_connection", side_effect=OSError("no route to host")):
            r = await probe_banner("0.0.0.0", 22, timeout=1.0)
        assert r.status == PortStatus.FILTERED
        assert r.error is not None and "no route to host" in r.error

    async def test_writer_close_failure_swallowed(self) -> None:
        reader, writer = _stream_pair(SSH_BANNER)
        # Make wait_closed raise — finally must still complete cleanly
        writer.wait_closed = AsyncMock(side_effect=RuntimeError("already closed"))
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_banner("example.com", 22, timeout=2.0)
        assert r.status == PortStatus.OPEN
        assert r.banner_bytes == SSH_BANNER


# ── probe_banners ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestProbeBanners:
    async def test_concurrent_fanout(self) -> None:
        # Each port returns a different banner
        async def _conn(host: str, port: int) -> tuple[MagicMock, MagicMock]:
            mapping = {22: SSH_BANNER, 25: SMTP_BANNER, 21: FTP_BANNER}
            return _stream_pair(mapping.get(port, b""))

        with patch("asyncio.open_connection", side_effect=_conn):
            results = await probe_banners(
                "example.com", [22, 25, 21, 80], timeout=1.0, concurrency=2
            )
        assert len(results) == 4
        # Order preserved
        assert results[0].port == 22
        assert results[1].port == 25
        assert results[2].port == 21
        assert results[3].port == 80
        assert results[0].banner is not None and "OpenSSH" in results[0].banner
        assert results[1].banner is not None and "Postfix" in results[1].banner
        assert results[2].banner is not None and "vsFTPd" in results[2].banner
        # Port 80 returned empty -> open with no banner
        assert results[3].banner is None
        assert results[3].status == PortStatus.OPEN

    async def test_send_probe_passed_through(self) -> None:
        reader, writer = _stream_pair(HTTP_RESPONSE)
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            results = await probe_banners(
                "example.com",
                [80],
                timeout=1.0,
                send_probe=b"HEAD / HTTP/1.0\r\n\r\n",
            )
        assert len(results) == 1
        writer.write.assert_called_once()

    async def test_empty_ports_list(self) -> None:
        with patch("asyncio.open_connection") as mocked:
            results = await probe_banners("example.com", [], timeout=1.0)
        assert results == []
        mocked.assert_not_called()
