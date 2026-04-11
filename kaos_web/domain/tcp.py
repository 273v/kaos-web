"""TCP port probing via asyncio — pure stdlib, no external deps.

Probes one or more TCP ports on a host using ``asyncio.open_connection()``
with configurable timeout.  Reports open/closed/timeout status with
connection latency.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence

from kaos_web.domain.models import COMMON_PORTS, PortResult, PortStatus, TcpProbeResult


async def probe_port(
    host: str,
    port: int,
    *,
    timeout: float = 5.0,
    read_banner: bool = False,
    banner_bytes: int = 256,
) -> PortResult:
    """Probe a single TCP port.

    Args:
        host: Target hostname or IP.
        port: TCP port number.
        timeout: Connection timeout in seconds.
        read_banner: If True, attempt to read initial bytes from the connection.
        banner_bytes: Max bytes to read for banner grabbing.

    Returns:
        PortResult with status and latency.
    """
    start = time.perf_counter()
    banner: str | None = None

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        latency_ms = (time.perf_counter() - start) * 1000

        if read_banner:
            try:
                raw = await asyncio.wait_for(reader.read(banner_bytes), timeout=2.0)
                if raw:
                    banner = raw.decode("utf-8", errors="replace").strip()
            except TimeoutError:
                pass
            except Exception:
                pass

        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

        return PortResult(
            port=port,
            status=PortStatus.OPEN,
            latency_ms=round(latency_ms, 2),
            banner=banner,
        )

    except TimeoutError:
        latency_ms = (time.perf_counter() - start) * 1000
        return PortResult(
            port=port,
            status=PortStatus.TIMEOUT,
            latency_ms=round(latency_ms, 2),
        )

    except ConnectionRefusedError:
        latency_ms = (time.perf_counter() - start) * 1000
        return PortResult(
            port=port,
            status=PortStatus.CLOSED,
            latency_ms=round(latency_ms, 2),
        )

    except OSError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return PortResult(
            port=port,
            status=PortStatus.ERROR,
            latency_ms=round(latency_ms, 2),
            error=str(exc),
        )


async def probe_ports(
    host: str,
    ports: Sequence[int] | None = None,
    *,
    preset: str | None = None,
    timeout: float = 5.0,
    concurrency: int = 20,
    read_banner: bool = False,
) -> TcpProbeResult:
    """Probe multiple TCP ports concurrently.

    Args:
        host: Target hostname or IP.
        ports: Explicit list of ports. Overrides *preset*.
        preset: Named port preset from ``COMMON_PORTS``
            (web, mail, ssh, dns, ftp, database, default).
        timeout: Per-port connection timeout.
        concurrency: Max concurrent probes.
        read_banner: Attempt banner read on open ports.

    Returns:
        TcpProbeResult with per-port results.
    """
    if ports is None:
        preset_name = preset or "default"
        ports = COMMON_PORTS.get(preset_name, COMMON_PORTS["default"])

    semaphore = asyncio.Semaphore(concurrency)

    async def _limited(p: int) -> PortResult:
        async with semaphore:
            return await probe_port(host, p, timeout=timeout, read_banner=read_banner)

    results = await asyncio.gather(*[_limited(p) for p in ports])

    open_count = sum(1 for r in results if r.status == PortStatus.OPEN)
    closed_count = sum(1 for r in results if r.status == PortStatus.CLOSED)
    timeout_count = sum(1 for r in results if r.status == PortStatus.TIMEOUT)

    return TcpProbeResult(
        host=host,
        ports=list(results),
        open_count=open_count,
        closed_count=closed_count,
        timeout_count=timeout_count,
    )
