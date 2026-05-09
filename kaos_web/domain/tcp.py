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

from kaos_core.logging import get_logger
from kaos_web.domain.models import (
    COMMON_PORTS,
    BannerProbeResult,
    PortResult,
    PortStatus,
    TcpProbeResult,
)

logger = get_logger(__name__)


def _decode_banner(raw: bytes) -> str:
    """Decode a banner byte string with progressive fallback.

    Tries UTF-8 first (most modern protocols), then latin-1 (legacy ASCII
    extended), and finally Python ``repr()`` of the bytes (lossless,
    always succeeds, e.g. for binary handshakes like MySQL).
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("latin-1")
    except UnicodeDecodeError:  # pragma: no cover - latin-1 is total
        return repr(raw)


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
    # WEB5-001: gate the target host BEFORE opening the socket. Strict
    # by default — blocks link-local metadata, loopback, RFC1918
    # private ranges (when the host is an IP literal). Hostname-only
    # inputs that don't parse as IP literals fall through to the actual
    # connect; closing that gap requires connect-time DNS interception.
    from kaos_web.security import validate_host

    validate_host(host)
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
                logger.debug("Banner read failed on port %d", port, exc_info=True)

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
    # WEB5-001: gate once at the front of the fan-out (probe_port also
    # gates per-call but doing it here too short-circuits the whole
    # batch on policy rejection rather than spawning tasks that all
    # raise the same UrlPolicyError).
    from kaos_web.security import validate_host

    validate_host(host)
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


# ── Banner grabbing ─────────────────────────────────────────────────


async def probe_banner(
    host: str,
    port: int,
    *,
    timeout: float = 5.0,
    send_probe: bytes | None = None,
    max_bytes: int = 4096,
) -> BannerProbeResult:
    """Open a TCP connection and capture the service's banner.

    Many services greet on connect (SSH, SMTP, FTP, POP3, IMAP) — leave
    ``send_probe`` as ``None`` to wait for the unsolicited banner. For
    request/response protocols (HTTP, Redis), pass a probe payload such
    as ``b"HEAD / HTTP/1.0\\r\\n\\r\\n"``.

    The connect+read budget is bounded by ``timeout`` seconds:
    ``asyncio.wait_for`` is applied to both the ``open_connection`` and the
    subsequent ``reader.read(max_bytes)`` calls.

    The decoded banner uses UTF-8 → latin-1 → ``repr()`` fallback so this
    function never raises on undecodable bytes.

    Args:
        host: Target hostname or IP.
        port: TCP port number.
        timeout: Per-stage timeout (connect, send, read) in seconds.
        send_probe: Optional probe bytes to send after connection.
        max_bytes: Maximum bytes to read from the socket.

    Returns:
        BannerProbeResult — always a value, never raises for normal
        connection errors. ``status`` is OPEN on successful read,
        CLOSED on RST/refused, TIMEOUT on connect or read timeout,
        FILTERED on ``OSError`` that suggests a firewall.

    Raises:
        UrlPolicyError: WEB5-001 gate rejection (private/loopback/
        metadata host, when the input is an IP literal). Other
        connection errors produce a value, not an exception.
    """
    # WEB5-001: gate the target host before opening the socket.
    from kaos_web.security import validate_host

    validate_host(host)
    start = time.perf_counter()
    writer = None

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )

        if send_probe:
            writer.write(send_probe)
            try:
                await asyncio.wait_for(writer.drain(), timeout=timeout)
            except TimeoutError:
                duration_ms = (time.perf_counter() - start) * 1000
                return BannerProbeResult(
                    host=host,
                    port=port,
                    status=PortStatus.TIMEOUT,
                    duration_ms=round(duration_ms, 2),
                    error="timeout sending probe payload",
                )

        try:
            raw = await asyncio.wait_for(reader.read(max_bytes), timeout=timeout)
        except TimeoutError:
            duration_ms = (time.perf_counter() - start) * 1000
            return BannerProbeResult(
                host=host,
                port=port,
                status=PortStatus.TIMEOUT,
                duration_ms=round(duration_ms, 2),
                error="timeout reading banner",
            )

        duration_ms = (time.perf_counter() - start) * 1000

        if not raw:
            # Connection succeeded but peer sent nothing (and we didn't probe)
            return BannerProbeResult(
                host=host,
                port=port,
                status=PortStatus.OPEN,
                banner=None,
                banner_bytes=None,
                duration_ms=round(duration_ms, 2),
            )

        return BannerProbeResult(
            host=host,
            port=port,
            status=PortStatus.OPEN,
            banner=_decode_banner(raw),
            banner_bytes=raw,
            duration_ms=round(duration_ms, 2),
        )

    except TimeoutError:
        duration_ms = (time.perf_counter() - start) * 1000
        return BannerProbeResult(
            host=host,
            port=port,
            status=PortStatus.TIMEOUT,
            duration_ms=round(duration_ms, 2),
            error="connect timeout",
        )

    except ConnectionRefusedError as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return BannerProbeResult(
            host=host,
            port=port,
            status=PortStatus.CLOSED,
            duration_ms=round(duration_ms, 2),
            error=str(exc) or "connection refused",
        )

    except ConnectionResetError as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return BannerProbeResult(
            host=host,
            port=port,
            status=PortStatus.CLOSED,
            duration_ms=round(duration_ms, 2),
            error=str(exc) or "connection reset",
        )

    except OSError as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        # EHOSTUNREACH / ENETUNREACH commonly indicate a firewall drop
        # rather than an explicit RST. Map to FILTERED.
        return BannerProbeResult(
            host=host,
            port=port,
            status=PortStatus.FILTERED,
            duration_ms=round(duration_ms, 2),
            error=str(exc),
        )

    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def probe_banners(
    host: str,
    ports: Sequence[int],
    *,
    timeout: float = 5.0,
    concurrency: int = 10,
    send_probe: bytes | None = None,
    max_bytes: int = 4096,
) -> list[BannerProbeResult]:
    """Run :func:`probe_banner` against many ports concurrently.

    Args:
        host: Target hostname or IP.
        ports: Sequence of TCP ports to probe.
        timeout: Per-port connect/read timeout.
        concurrency: Maximum concurrent probes (asyncio.Semaphore).
        send_probe: Optional probe payload (same for every port).
        max_bytes: Maximum bytes to read per port.

    Returns:
        List of BannerProbeResult in the same order as ``ports``.
    """
    # WEB5-001: gate once at the front of the fan-out — probe_banner
    # also gates per-call, but short-circuiting here avoids spawning
    # N tasks that all raise the same UrlPolicyError.
    from kaos_web.security import validate_host

    validate_host(host)
    semaphore = asyncio.Semaphore(concurrency)

    async def _limited(p: int) -> BannerProbeResult:
        async with semaphore:
            return await probe_banner(
                host,
                p,
                timeout=timeout,
                send_probe=send_probe,
                max_bytes=max_bytes,
            )

    return list(await asyncio.gather(*[_limited(p) for p in ports]))
