"""Service banner fingerprinting — pure functions, zero network I/O.

Maps a raw banner string (typically from :func:`kaos_web.domain.tcp.probe_banner`)
to a :class:`ServiceIdentity`: generic service name, product, version, and
extra protocol-specific fields with a confidence score in ``[0.0, 1.0]``.

The signature table is curated from real protocol RFCs and observed
greetings — never guess. Banner regexes encode the prefix + product + version
extraction in a single pass; the binary handshake protocols (MySQL,
PostgreSQL, Redis) are handled with byte-level checks before the text path.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from kaos_web.domain.models import BannerProbeResult, ServiceIdentity

# ── Text banner signatures ─────────────────────────────────────────

# SSH per RFC 4253 §4.2 — "SSH-protoversion-softwareversion SP comments\r\n"
# softwareversion is printable US-ASCII without whitespace, '-', or comma.
# Conventionally the product/version are joined by '_' (OpenSSH_8.9p1) or
# the entire thing is the product (Sun_SSH_2.0).
_RE_SSH = re.compile(
    r"^SSH-(?P<protocol>\d+\.\d+)-(?P<product>[^\s_]+)(?:[_\s](?P<version>\S+))?",
)
# SMTP greeting: "220 host ESMTP Postfix" or "220-host ESMTP Sendmail 8.15.2"
# Use a non-greedy product but require the version group when present so
# the matcher commits to the correct token boundary.
_RE_SMTP = re.compile(
    r"^220[\s\-](?P<host>\S+)\s+(?:ESMTP\s+)?(?P<product>\S+?)(?:\s+(?P<version>[\d.]+))?(?:\s|$)",
    re.IGNORECASE,
)
# FTP greeting: "220 (vsFTPd 3.0.5)" or "220-FileZilla Server 0.9.60"
_RE_FTP = re.compile(
    r"^220[\s\-].*?\((?P<product>[\w\s\-]+?)(?:\s+(?P<version>[\d.]+))?\)",
)
# POP3 greeting: "+OK Dovecot ready" or "+OK POP3 server ready"
_RE_POP3 = re.compile(
    r"^\+OK\s+(?P<product>\S+?)(?:\s+(?P<version>[\d.]+))?(?:\s|$)",
)
# IMAP greeting: "* OK [CAPABILITY IMAP4rev1 ...] Dovecot ready"
_RE_IMAP = re.compile(
    r"^\*\s+OK\s+\[CAPABILITY[^\]]+\]\s+(?P<product>\S+)(?:\s+(?P<version>[\d.]+))?",
)
# HTTP "Server:" header line within a banner
_RE_HTTP_SERVER = re.compile(
    r"^Server:\s*(?P<product>[\w\-./]+?)(?:/(?P<version>[\w.\-]+))?(?:\s|$)",
    re.MULTILINE | re.IGNORECASE,
)
# Redis: -NOAUTH or banner contains "Redis server"
_RE_REDIS_NOAUTH = re.compile(r"^-NOAUTH")
_RE_REDIS_VER = re.compile(r"redis_version:(?P<version>[\d.]+)", re.IGNORECASE)


# ── Port hint table (low-confidence guesses for portless banners) ──

_KNOWN_PORTS: dict[int, str] = {
    20: "ftp-data",
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    67: "dhcp",
    68: "dhcp",
    69: "tftp",
    80: "http",
    110: "pop3",
    111: "rpcbind",
    119: "nntp",
    123: "ntp",
    135: "msrpc",
    139: "netbios-ssn",
    143: "imap",
    161: "snmp",
    162: "snmp-trap",
    179: "bgp",
    389: "ldap",
    443: "https",
    445: "microsoft-ds",
    465: "smtps",
    514: "syslog",
    515: "lpd",
    587: "submission",
    636: "ldaps",
    873: "rsync",
    993: "imaps",
    995: "pop3s",
    1080: "socks",
    1433: "mssql",
    1521: "oracle",
    2049: "nfs",
    2375: "docker",
    3306: "mysql",
    3389: "rdp",
    5432: "postgresql",
    5672: "amqp",
    5900: "vnc",
    5984: "couchdb",
    6379: "redis",
    8080: "http-proxy",
    8443: "https-alt",
    9092: "kafka",
    9200: "elasticsearch",
    11211: "memcached",
    27017: "mongodb",
    27018: "mongodb",
    50000: "db2",
}


def _port_hint(port: int | None) -> ServiceIdentity:
    """Return a low-confidence identity from a port number alone."""
    if port is None:
        return ServiceIdentity(service="unknown", confidence=0.0)
    name = _KNOWN_PORTS.get(port)
    if name is None:
        return ServiceIdentity(service="unknown", confidence=0.0)
    return ServiceIdentity(service=name, confidence=0.3, extra={"source": "port"})


def _is_mysql_handshake(banner_bytes: bytes) -> tuple[bool, str | None]:
    """Detect MySQL initial handshake packet (protocol v10).

    Wire format: ``[3-byte length][1-byte sequence_id=0x00][0x0a][server_ver\\0]...``.
    The 5th byte (index 4) is the protocol-version 0x0a; the server version
    string follows as a NUL-terminated ASCII string.
    """
    if len(banner_bytes) < 6:
        return False, None
    if banner_bytes[4] != 0x0A:
        return False, None
    # Find NUL terminator after the protocol byte
    nul = banner_bytes.find(b"\x00", 5)
    if nul == -1:
        return True, None
    try:
        version = banner_bytes[5:nul].decode("utf-8", errors="replace")
    except Exception:
        return True, None
    return True, version


def _is_postgres_error(banner_bytes: bytes) -> bool:
    """Detect a PostgreSQL ErrorResponse on raw connect.

    PostgreSQL returns an ErrorResponse with type byte 'E', followed by a
    4-byte length, then SFATAL... when the startup packet is malformed (or
    just absent). Detecting the prefix at low confidence is enough.
    """
    return banner_bytes.startswith(b"E\x00\x00")


def fingerprint_banner(
    banner: str,
    *,
    port: int | None = None,
) -> ServiceIdentity:
    """Identify a service from its banner string.

    The function never fails — it always returns a :class:`ServiceIdentity`,
    falling back to a port-based hint if the banner is empty/unrecognised
    and ``port`` is known, or to ``service="unknown", confidence=0.0`` as a
    last resort.

    Args:
        banner: The banner text (e.g. UTF-8 decoded from
            :class:`BannerProbeResult.banner`). May be empty.
        port: Optional TCP port number, used as a low-confidence fallback
            hint when the banner does not match any signature.

    Returns:
        ServiceIdentity with service / product / version / extra / confidence.
    """
    if not banner:
        return _port_hint(port)

    # SSH — high-confidence prefix
    if (m := _RE_SSH.match(banner)) is not None:
        return ServiceIdentity(
            service="ssh",
            product=m.group("product"),
            version=m.group("version"),
            extra={"protocol": m.group("protocol")},
            confidence=0.95,
        )

    # IMAP — must be checked before SMTP/FTP since "* OK" is distinct
    if (m := _RE_IMAP.match(banner)) is not None:
        return ServiceIdentity(
            service="imap",
            product=m.group("product"),
            version=m.group("version"),
            confidence=0.9,
        )

    # POP3
    if (m := _RE_POP3.match(banner)) is not None:
        return ServiceIdentity(
            service="pop3",
            product=m.group("product"),
            version=m.group("version"),
            confidence=0.85,
        )

    # FTP — check before SMTP because the parenthesised form is distinctive
    if (
        banner.startswith(("220 ", "220-"))
        and "(" in banner.split("\n", 1)[0]
        and (m := _RE_FTP.match(banner)) is not None
    ):
        return ServiceIdentity(
            service="ftp",
            product=(m.group("product") or "").strip() or None,
            version=m.group("version"),
            confidence=0.9,
        )

    # SMTP
    if (m := _RE_SMTP.match(banner)) is not None:
        product_raw = m.group("product")
        product = product_raw if product_raw and product_raw.upper() != "ESMTP" else None
        extra: dict[str, str] = {}
        if m.group("host"):
            extra["host"] = m.group("host")
        return ServiceIdentity(
            service="smtp",
            product=product,
            version=m.group("version"),
            extra=extra,
            confidence=0.9,
        )

    # HTTP — search anywhere in the banner for "Server:" header line
    if (m := _RE_HTTP_SERVER.search(banner)) is not None:
        return ServiceIdentity(
            service="http",
            product=m.group("product"),
            version=m.group("version"),
            confidence=0.9,
        )

    # Redis: -NOAUTH on auth-required, or info-style banners
    if _RE_REDIS_NOAUTH.match(banner) or "Redis server" in banner:
        version: str | None = None
        if (vm := _RE_REDIS_VER.search(banner)) is not None:
            version = vm.group("version")
        return ServiceIdentity(
            service="redis",
            product="Redis",
            version=version,
            confidence=0.8,
        )

    # No banner-text match — fall back to port-based hint
    return _port_hint(port)


def fingerprint_banner_bytes(
    banner_bytes: bytes,
    *,
    port: int | None = None,
) -> ServiceIdentity:
    """Identify a service from raw banner bytes.

    Handles binary handshakes (MySQL, PostgreSQL) before delegating to the
    text-based :func:`fingerprint_banner` for everything else.

    Args:
        banner_bytes: Raw bytes captured from the wire.
        port: Optional TCP port for fallback hinting.

    Returns:
        ServiceIdentity with the best identification possible.
    """
    if not banner_bytes:
        return _port_hint(port)

    # MySQL handshake — port-aware (only treat as MySQL on 3306-style ports
    # to avoid false positives on arbitrary 0x0a-prefixed payloads).
    is_mysql, mysql_version = _is_mysql_handshake(banner_bytes)
    if is_mysql and (port is None or port in {3306, 33060}):
        return ServiceIdentity(
            service="mysql",
            product="MySQL",
            version=mysql_version,
            confidence=0.85 if mysql_version else 0.6,
        )

    # PostgreSQL ErrorResponse
    if _is_postgres_error(banner_bytes):
        return ServiceIdentity(
            service="postgresql",
            product="PostgreSQL",
            confidence=0.5,
        )

    # Fall back to text path (with the same UTF-8 → latin-1 decode policy
    # as :func:`kaos_web.domain.tcp._decode_banner`).
    try:
        text = banner_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = banner_bytes.decode("latin-1")
        except UnicodeDecodeError:
            text = ""
    return fingerprint_banner(text, port=port)


def fingerprint_results(
    results: Sequence[BannerProbeResult],
) -> list[tuple[BannerProbeResult, ServiceIdentity]]:
    """Apply :func:`fingerprint_banner_bytes` (preferred) to a sequence of probes.

    For each result we prefer the raw bytes (handles binary handshakes)
    and fall back to the decoded ``banner`` string. Results with no
    captured data still yield a port-based hint.
    """
    out: list[tuple[BannerProbeResult, ServiceIdentity]] = []
    for r in results:
        if r.banner_bytes:
            ident = fingerprint_banner_bytes(r.banner_bytes, port=r.port)
        elif r.banner:
            ident = fingerprint_banner(r.banner, port=r.port)
        else:
            ident = _port_hint(r.port)
        out.append((r, ident))
    return out
