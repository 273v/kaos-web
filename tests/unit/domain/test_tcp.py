"""Tests for ``kaos_web.domain.tcp`` — TCP port probing.

Mocks ``asyncio.open_connection`` directly: the underlying stdlib socket
behaviour is already trusted, and the unit test surface here is the
mapping of (open_connection outcome) → (PortStatus + latency).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.domain.models import COMMON_PORTS, PortStatus, TcpProbeResult
from kaos_web.domain.tcp import probe_port, probe_ports


def _mock_open_connection_success(banner: bytes = b""):
    reader = MagicMock()
    reader.read = AsyncMock(return_value=banner)
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return AsyncMock(return_value=(reader, writer))


@pytest.mark.asyncio
class TestProbePort:
    async def test_open_no_banner(self) -> None:
        with patch("asyncio.open_connection", _mock_open_connection_success(b"")):
            r = await probe_port("example.com", 443, timeout=1.0)
        assert r.status == PortStatus.OPEN
        assert r.port == 443
        assert r.latency_ms is not None and r.latency_ms >= 0
        assert r.banner is None

    async def test_open_with_banner(self) -> None:
        banner = b"SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.4\r\n"
        with patch("asyncio.open_connection", _mock_open_connection_success(banner)):
            r = await probe_port("example.com", 22, timeout=1.0, read_banner=True)
        assert r.status == PortStatus.OPEN
        assert r.banner is not None
        assert "SSH-2.0" in r.banner

    async def test_banner_empty_read(self) -> None:
        with patch("asyncio.open_connection", _mock_open_connection_success(b"")):
            r = await probe_port("example.com", 443, timeout=1.0, read_banner=True)
        assert r.status == PortStatus.OPEN
        assert r.banner is None  # empty banner stays None

    async def test_banner_timeout(self) -> None:
        # Connection succeeds but banner read times out -> port still OPEN
        reader = MagicMock()
        reader.read = AsyncMock(side_effect=TimeoutError())
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_port("example.com", 22, timeout=1.0, read_banner=True)
        assert r.status == PortStatus.OPEN
        assert r.banner is None

    async def test_banner_unexpected_error(self) -> None:
        # Banner read raises unexpected error -> still OPEN, banner None
        reader = MagicMock()
        reader.read = AsyncMock(side_effect=RuntimeError("boom"))
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            r = await probe_port("example.com", 22, timeout=1.0, read_banner=True)
        assert r.status == PortStatus.OPEN

    async def test_timeout(self) -> None:
        # asyncio.wait_for raises TimeoutError when open_connection takes too long
        async def _slow(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise TimeoutError()

        with patch("asyncio.open_connection", side_effect=_slow):
            r = await probe_port("10.255.255.1", 443, timeout=0.01)
        assert r.status == PortStatus.TIMEOUT
        assert r.latency_ms is not None

    async def test_refused(self) -> None:
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError()):
            r = await probe_port("127.0.0.1", 1, timeout=1.0)
        assert r.status == PortStatus.CLOSED
        assert r.latency_ms is not None

    async def test_oserror(self) -> None:
        with patch("asyncio.open_connection", side_effect=OSError("network unreachable")):
            r = await probe_port("0.0.0.0", 80, timeout=1.0)
        assert r.status == PortStatus.ERROR
        assert r.error is not None
        assert "network unreachable" in r.error


@pytest.mark.asyncio
class TestProbePorts:
    async def test_explicit_ports(self) -> None:
        with patch("asyncio.open_connection", _mock_open_connection_success(b"")):
            t = await probe_ports("example.com", ports=[80, 443], timeout=1.0)
        assert isinstance(t, TcpProbeResult)
        assert t.host == "example.com"
        assert t.open_count == 2
        assert t.closed_count == 0
        assert len(t.ports) == 2

    async def test_default_preset(self) -> None:
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError()):
            t = await probe_ports("example.com", timeout=0.1)
        assert len(t.ports) == len(COMMON_PORTS["default"])
        assert t.closed_count == len(COMMON_PORTS["default"])

    async def test_named_preset(self) -> None:
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError()):
            t = await probe_ports("example.com", preset="ssh", timeout=0.1)
        assert len(t.ports) == 1
        assert t.ports[0].port == 22

    async def test_unknown_preset_falls_back_to_default(self) -> None:
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError()):
            t = await probe_ports("example.com", preset="not-a-real-preset", timeout=0.1)
        assert len(t.ports) == len(COMMON_PORTS["default"])

    async def test_mixed_results(self) -> None:
        # Port 80 timeout, 443 open, 22 closed
        async def _conn(host, port):  # type: ignore[no-untyped-def]
            if port == 80:
                raise TimeoutError()
            if port == 22:
                raise ConnectionRefusedError()
            # 443 succeeds
            reader = MagicMock()
            reader.read = AsyncMock(return_value=b"")
            writer = MagicMock()
            writer.close = MagicMock()
            writer.wait_closed = AsyncMock()
            return (reader, writer)

        with patch("asyncio.open_connection", side_effect=_conn):
            t = await probe_ports("example.com", ports=[80, 443, 22], timeout=0.1)
        assert t.open_count == 1
        assert t.closed_count == 1
        assert t.timeout_count == 1
