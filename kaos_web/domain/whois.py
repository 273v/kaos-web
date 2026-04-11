"""WHOIS client with built-in parsing — pure stdlib, no external deps.

Implements the WHOIS protocol (RFC 3912) using ``asyncio.open_connection``
on port 43.  Includes a TLD-to-server mapping and regex-based response
parser for common registrar formats.

Parsing patterns adapted from python-whois (MIT license,
https://github.com/richardpenman/whois).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import datetime

from kaos_web.domain.models import WhoisRecord

# ── TLD to WHOIS server mapping ─────────────────────────────────────

WHOIS_SERVERS: dict[str, str] = {
    "com": "whois.verisign-grs.com",
    "net": "whois.verisign-grs.com",
    "org": "whois.pir.org",
    "info": "whois.afilias.net",
    "biz": "whois.biz",
    "us": "whois.nic.us",
    "co": "whois.nic.co",
    "io": "whois.nic.io",
    "ai": "whois.nic.ai",
    "dev": "whois.nic.google",
    "app": "whois.nic.google",
    "me": "whois.nic.me",
    "tv": "whois.nic.tv",
    "cc": "ccwhois.verisign-grs.com",
    "uk": "whois.nic.uk",
    "co.uk": "whois.nic.uk",
    "org.uk": "whois.nic.uk",
    "de": "whois.denic.de",
    "fr": "whois.nic.fr",
    "nl": "whois.sidn.nl",
    "eu": "whois.eu",
    "be": "whois.dns.be",
    "ch": "whois.nic.ch",
    "at": "whois.nic.at",
    "it": "whois.nic.it",
    "es": "whois.nic.es",
    "pt": "whois.dns.pt",
    "se": "whois.iis.se",
    "no": "whois.norid.no",
    "dk": "whois.dk-hostmaster.dk",
    "fi": "whois.fi",
    "pl": "whois.dns.pl",
    "cz": "whois.nic.cz",
    "ru": "whois.tcinet.ru",
    "au": "whois.auda.org.au",
    "com.au": "whois.auda.org.au",
    "nz": "whois.srs.net.nz",
    "ca": "whois.cira.ca",
    "jp": "whois.jprs.jp",
    "kr": "whois.kr",
    "cn": "whois.cnnic.cn",
    "in": "whois.registry.in",
    "br": "whois.registro.br",
    "mx": "whois.mx",
    "za": "whois.registry.net.za",
    "xyz": "whois.nic.xyz",
    "online": "whois.nic.online",
    "site": "whois.nic.site",
    "tech": "whois.nic.tech",
    "law": "whois.nic.law",
    "legal": "whois.nic.legal",
}

# Fallback: IANA root server
IANA_WHOIS = "whois.iana.org"


# ── Date parsing ────────────────────────────────────────────────────

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d-%b-%Y",
    "%d-%b-%Y %H:%M:%S %Z",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%Y.%m.%d",
    "%d.%m.%Y",
    "%Y%m%d",
    "%B %d, %Y",
    "%d %B %Y",
    "%d-%b-%Y %H:%M:%S",
    "%a %b %d %H:%M:%S %Z %Y",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%d %H:%M:%S %Z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S (%z)",
)


def _parse_date(value: str) -> str | None:
    """Try multiple date formats, return ISO 8601 or None."""
    value = value.strip().rstrip(".")
    if not value:
        return None

    # Handle "before " prefix (some registrars)
    value = re.sub(r"^before\s+", "", value, flags=re.IGNORECASE)

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return None


# ── Response parsing ────────────────────────────────────────────────

# Each tuple: (field_name, list of regex patterns to try)
# Patterns are case-insensitive and match the value after the label.
_FIELD_PATTERNS: dict[str, list[str]] = {
    "registrar": [
        r"registrar:\s*(.+)",
        r"registrar name:\s*(.+)",
        r"sponsoring registrar:\s*(.+)",
        r"registrar organization:\s*(.+)",
    ],
    "whois_server": [
        r"registrar whois server:\s*(.+)",
        r"whois server:\s*(.+)",
    ],
    "creation_date": [
        r"creat(?:ion|ed)\s*(?:date|on)?:\s*(.+)",
        r"registration\s*(?:date|time):\s*(.+)",
        r"registered\s*(?:date|on)?:\s*(.+)",
        r"domain registration date:\s*(.+)",
    ],
    "expiration_date": [
        r"expir(?:y|ation|es)\s*(?:date|on)?:\s*(.+)",
        r"registry expiry date:\s*(.+)",
        r"registrar registration expiration date:\s*(.+)",
        r"paid-till:\s*(.+)",
        r"validity:\s*(.+)",
        r"renewal date:\s*(.+)",
    ],
    "updated_date": [
        r"updated?\s*(?:date|on)?:\s*(.+)",
        r"last[\s-]*(?:modified|updated|changed)\s*(?:date|on)?:\s*(.+)",
        r"last update of whois database:\s*(.+)",
    ],
    "registrant_name": [
        r"registrant\s*(?:name|contact name):\s*(.+)",
    ],
    "registrant_org": [
        r"registrant\s*(?:organization|org|organisation):\s*(.+)",
    ],
    "registrant_country": [
        r"registrant\s*(?:country|country/economy):\s*(.+)",
    ],
    "dnssec": [
        r"dnssec:\s*(.+)",
    ],
}

_STATUS_PATTERNS: list[str] = [
    r"(?:domain )?status:\s*(.+)",
    r"state:\s*(.+)",
]

_NAMESERVER_PATTERNS: list[str] = [
    r"name\s*server:\s*(.+)",
    r"nserver:\s*(.+)",
    r"nameservers?:\s*(.+)",
    r"dns:\s*(.+)",
]


def _parse_whois_text(text: str, domain: str) -> WhoisRecord:
    """Parse raw WHOIS response text into a WhoisRecord."""
    lines = text.splitlines()
    fields: dict[str, str | None] = {}
    name_servers: list[str] = []
    statuses: list[str] = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith("%") or line.startswith("#"):
            continue

        # Extract single-value fields
        for field_name, patterns in _FIELD_PATTERNS.items():
            if field_name in fields:
                continue
            for pattern in patterns:
                m = re.match(pattern, line, re.IGNORECASE)
                if m:
                    fields[field_name] = m.group(1).strip()
                    break

        # Extract multi-value fields
        for pattern in _NAMESERVER_PATTERNS:
            m = re.match(pattern, line, re.IGNORECASE)
            if m:
                ns = m.group(1).strip().rstrip(".").lower()
                if ns and ns not in name_servers:
                    name_servers.append(ns)
                break

        for pattern in _STATUS_PATTERNS:
            m = re.match(pattern, line, re.IGNORECASE)
            if m:
                status_val = m.group(1).strip()
                if status_val and status_val not in statuses:
                    statuses.append(status_val)
                break

    return WhoisRecord(
        domain=domain,
        registrar=fields.get("registrar"),
        whois_server=fields.get("whois_server"),
        creation_date=_parse_date(fields.get("creation_date", "")),
        expiration_date=_parse_date(fields.get("expiration_date", "")),
        updated_date=_parse_date(fields.get("updated_date", "")),
        name_servers=name_servers,
        status=statuses,
        registrant_name=fields.get("registrant_name"),
        registrant_org=fields.get("registrant_org"),
        registrant_country=fields.get("registrant_country"),
        dnssec=fields.get("dnssec"),
        raw_text=text,
    )


# ── WHOIS protocol ──────────────────────────────────────────────────


def _get_whois_server(domain: str) -> str:
    """Determine WHOIS server for a domain based on TLD."""
    parts = domain.rstrip(".").lower().split(".")

    # Try progressively shorter suffix matches: co.uk, then uk
    for i in range(len(parts) - 1):
        suffix = ".".join(parts[i:])
        if suffix in WHOIS_SERVERS:
            return WHOIS_SERVERS[suffix]

    # Just the TLD
    tld = parts[-1] if parts else ""
    return WHOIS_SERVERS.get(tld, IANA_WHOIS)


async def _raw_whois_query(
    domain: str,
    server: str,
    *,
    timeout: float = 10.0,
) -> str:
    """Send a raw WHOIS query and return the response text."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(server, 43),
        timeout=timeout,
    )

    # Some servers (like DENIC) need specific query formats
    query = domain
    if server == "whois.denic.de":
        query = f"-T dn,ace {domain}"
    elif server == "whois.jprs.jp":
        query = f"{domain}/e"  # English output

    writer.write(f"{query}\r\n".encode())
    await writer.drain()

    chunks: list[bytes] = []
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not chunk:
                break
            chunks.append(chunk)
    except TimeoutError:
        pass

    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()

    raw = b"".join(chunks)
    # Try UTF-8, fall back to latin-1
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


async def _follow_referral(text: str, domain: str, timeout: float) -> str | None:
    """If the response contains a referral to another WHOIS server, follow it."""
    # Verisign responses contain "Registrar WHOIS Server: whois.example.com"
    m = re.search(r"registrar whois server:\s*(\S+)", text, re.IGNORECASE)
    if not m:
        return None
    referral_server = m.group(1).strip().rstrip(".")
    if not referral_server or "://" in referral_server:
        return None  # Skip HTTP-based WHOIS servers
    try:
        return await _raw_whois_query(domain, referral_server, timeout=timeout)
    except Exception:
        return None


# ── Public API ──────────────────────────────────────────────────────


async def whois_lookup(
    domain: str,
    *,
    timeout: float = 10.0,
    follow_referrals: bool = True,
) -> WhoisRecord:
    """Look up WHOIS registration data for a domain.

    Connects to the appropriate WHOIS server via TCP port 43,
    sends the domain query, parses the response.

    Args:
        domain: Domain name to look up.
        timeout: Socket timeout in seconds.
        follow_referrals: Follow referral servers (e.g., Verisign → registrar).

    Returns:
        WhoisRecord with parsed registration data.
    """
    domain = domain.strip().lower().rstrip(".")
    server = _get_whois_server(domain)

    try:
        text = await _raw_whois_query(domain, server, timeout=timeout)
    except TimeoutError:
        return WhoisRecord(domain=domain, error=f"WHOIS query timed out ({server})")
    except ConnectionRefusedError:
        return WhoisRecord(domain=domain, error=f"Connection refused by {server}")
    except OSError as exc:
        return WhoisRecord(domain=domain, error=f"Network error querying {server}: {exc}")

    # Follow referral if the initial response is a thin WHOIS
    if follow_referrals:
        referral_text = await _follow_referral(text, domain, timeout)
        if referral_text:
            # Use the referral response but keep original as fallback
            text = referral_text

    return _parse_whois_text(text, domain)
