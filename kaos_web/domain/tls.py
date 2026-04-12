"""TLS certificate inspection via stdlib ``ssl`` — no external deps.

Connects to a host:port, performs a TLS handshake, and extracts
certificate metadata: subject, issuer, SAN, validity, cipher info.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import socket
import ssl
from typing import Any

from kaos_web.domain.models import TlsCertInfo


def _extract_cert_info(
    host: str,
    port: int,
    *,
    timeout: float = 10.0,
) -> TlsCertInfo:
    """Synchronous TLS certificate extraction (runs in thread pool).

    Uses default CA verification so ``getpeercert()`` returns the full
    structured dict.  Falls back to CERT_NONE if the cert can't be
    validated (self-signed, expired), capturing cipher/protocol only.
    """
    cert: dict[str, Any] | None = None
    cipher_info: tuple[str, str, int] | None = None
    protocol_version: str | None = None

    # First try: validate cert to get the full structured dict.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False

    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=host) as ssock,
        ):
            cert = ssock.getpeercert()
            cipher_info = ssock.cipher()
            protocol_version = ssock.version()
    except ssl.SSLCertVerificationError:
        # Cert invalid — retry without verification for cipher/protocol.
        ctx2 = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx2.check_hostname = False
        ctx2.verify_mode = ssl.CERT_NONE
        try:
            with (
                socket.create_connection((host, port), timeout=timeout) as sock,
                ctx2.wrap_socket(sock, server_hostname=host) as ssock,
            ):
                cipher_info = ssock.cipher()
                protocol_version = ssock.version()
        except Exception:
            pass
    except TimeoutError:
        return TlsCertInfo(host=host, port=port, error=f"Connection timed out after {timeout}s")
    except ConnectionRefusedError:
        return TlsCertInfo(host=host, port=port, error=f"Connection refused on port {port}")
    except ssl.SSLError as exc:
        return TlsCertInfo(host=host, port=port, error=f"SSL error: {exc}")
    except OSError as exc:
        return TlsCertInfo(host=host, port=port, error=f"Network error: {exc}")

    # If cert validation failed, return partial info.
    if not cert:
        return TlsCertInfo(
            host=host,
            port=port,
            protocol=protocol_version,
            cipher=cipher_info[0] if cipher_info else None,
            cipher_bits=cipher_info[2] if cipher_info and len(cipher_info) > 2 else None,
            error="Certificate could not be validated (self-signed or expired)",
        )

    # Parse the validated cert.
    subject = _flatten_cert_field(cert.get("subject", ()))
    issuer = _flatten_cert_field(cert.get("issuer", ()))

    raw_not_before = cert.get("notBefore")
    raw_not_after = cert.get("notAfter")
    not_before = _parse_cert_date(str(raw_not_before) if raw_not_before else None)
    not_after = _parse_cert_date(str(raw_not_after) if raw_not_after else None)

    days_until_expiry = None
    if not_after:
        delta = not_after - dt.datetime.now(dt.UTC)
        days_until_expiry = delta.days

    san_dns: list[str] = []
    for san_type, san_value in cert.get("subjectAltName", ()):
        if san_type == "DNS":
            san_dns.append(str(san_value))

    raw_serial = cert.get("serialNumber")
    return TlsCertInfo(
        host=host,
        port=port,
        subject=subject,
        issuer=issuer,
        serial_number=str(raw_serial) if raw_serial else None,
        not_before=not_before.isoformat() if not_before else None,
        not_after=not_after.isoformat() if not_after else None,
        days_until_expiry=days_until_expiry,
        san_dns=san_dns,
        protocol=protocol_version,
        cipher=cipher_info[0] if cipher_info else None,
        cipher_bits=cipher_info[2] if cipher_info and len(cipher_info) > 2 else None,
    )


async def inspect_tls(
    host: str,
    port: int = 443,
    *,
    timeout: float = 10.0,
) -> TlsCertInfo:
    """Async TLS certificate inspection.

    Runs the blocking SSL handshake in a thread pool.

    Args:
        host: Target hostname.
        port: TLS port (default 443).
        timeout: Socket timeout.

    Returns:
        TlsCertInfo with certificate details or error.
    """
    return await asyncio.to_thread(_extract_cert_info, host, port, timeout=timeout)


def _flatten_cert_field(field: Any) -> dict[str, str]:
    """Flatten a certificate subject/issuer tuple-of-tuples to a dict."""
    result: dict[str, str] = {}
    if not field:
        return result
    for entry in field:
        if isinstance(entry, tuple):
            for pair in entry:
                if isinstance(pair, tuple) and len(pair) == 2:
                    result[pair[0]] = str(pair[1])
    return result


def _parse_cert_date(date_str: str | None) -> dt.datetime | None:
    """Parse certificate date string (e.g., 'Jan  5 12:00:00 2025 GMT')."""
    if not date_str:
        return None
    try:
        return dt.datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.UTC)
    except ValueError:
        return None
