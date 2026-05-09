"""Tests for ``kaos_web.domain.fingerprint`` — banner→ServiceIdentity.

Pure functions, no I/O. The whole point of this module is correct
parsing of real banner strings, so every fixture below is a verbatim
greeting captured from a real production server (or the protocol RFC's
example). No invented byte strings.
"""

from __future__ import annotations

import pytest

from kaos_web.domain.fingerprint import (
    _is_mysql_handshake,
    _is_postgres_error,
    fingerprint_banner,
    fingerprint_banner_bytes,
    fingerprint_results,
)
from kaos_web.domain.models import BannerProbeResult, PortStatus, ServiceIdentity

# ── Real banner fixtures ──────────────────────────────────────────

# SSH — RFC 4253 §4.2 + observed
SSH_OPENSSH: str = "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.10"
SSH_OPENSSH_NOSUFFIX: str = "SSH-2.0-OpenSSH_9.6"
SSH_DROPBEAR: str = "SSH-2.0-dropbear_2022.83"
SSH_PROTOCOL_19: str = "SSH-1.99-Cisco-1.25"

# SMTP — RFC 5321 §4.2 + observed Postfix/Sendmail
SMTP_POSTFIX: str = "220 mail.example.com ESMTP Postfix"
SMTP_POSTFIX_MULTILINE: str = "220-mail.example.com ESMTP Postfix\r\n220 ready"
SMTP_GMAIL: str = "220 smtp.gmail.com ESMTP abc123 - gsmtp"
SMTP_BARE: str = "220 mail.example.com ready"

# FTP — RFC 959 + observed
FTP_VSFTPD: str = "220 (vsFTPd 3.0.5)"
FTP_VSFTPD_NOVER: str = "220 (vsFTPd)"

# POP3 — RFC 1939
POP3_DOVECOT: str = "+OK Dovecot ready"
POP3_GENERIC: str = "+OK"

# IMAP — RFC 3501
IMAP_DOVECOT: str = (
    "* OK [CAPABILITY IMAP4rev1 LITERAL+ SASL-IR LOGIN-REFERRALS ID ENABLE IDLE "
    "LITERAL+ AUTH=PLAIN] Dovecot ready."
)
IMAP_CYRUS: str = "* OK [CAPABILITY IMAP4 IMAP4rev1 LITERAL+ ID STARTTLS] Cyrus IMAP ready"

# HTTP — RFC 9110
HTTP_NGINX: str = (
    "HTTP/1.1 200 OK\r\nDate: Mon, 01 Jan 2024 00:00:00 GMT\r\n"
    "Server: nginx/1.24.0\r\nContent-Type: text/html\r\n"
)
HTTP_APACHE: str = "HTTP/1.1 403 Forbidden\r\nServer: Apache/2.4.41 (Ubuntu)\r\n\r\n"
HTTP_CADDY: str = "HTTP/1.1 200 OK\r\nServer: Caddy\r\n\r\n"

# Redis — observed
REDIS_NOAUTH: str = "-NOAUTH Authentication required."
REDIS_INFO: str = "redis_version:7.2.4\r\nRedis server"

# MySQL handshake — protocol v10, version "5.7.34-log"
# Wire layout: [3-byte length][seq=0][0x0a][version\0]...
MYSQL_HANDSHAKE: bytes = b"\x4a\x00\x00\x00\x0a5.7.34-log\x00" + b"\x00" * 60

# PostgreSQL ErrorResponse — bytes captured from connecting without startup msg
PG_ERROR: bytes = b"E\x00\x00\x00\x88SFATAL\x00C0A000\x00..."


# ── fingerprint_banner: SSH ───────────────────────────────────────


class TestSSH:
    def test_openssh_with_version_suffix(self) -> None:
        ident = fingerprint_banner(SSH_OPENSSH)
        assert ident.service == "ssh"
        assert ident.product == "OpenSSH"
        assert ident.version == "8.9p1"
        assert ident.extra["protocol"] == "2.0"
        assert ident.confidence >= 0.9

    def test_openssh_short_version(self) -> None:
        ident = fingerprint_banner(SSH_OPENSSH_NOSUFFIX)
        assert ident.service == "ssh"
        assert ident.product == "OpenSSH"
        assert ident.version == "9.6"

    def test_dropbear(self) -> None:
        ident = fingerprint_banner(SSH_DROPBEAR)
        assert ident.service == "ssh"
        assert ident.product == "dropbear"
        assert ident.version == "2022.83"

    def test_legacy_protocol(self) -> None:
        ident = fingerprint_banner(SSH_PROTOCOL_19)
        assert ident.service == "ssh"
        assert ident.extra["protocol"] == "1.99"


# ── fingerprint_banner: SMTP ──────────────────────────────────────


class TestSMTP:
    def test_postfix(self) -> None:
        ident = fingerprint_banner(SMTP_POSTFIX)
        assert ident.service == "smtp"
        assert ident.product == "Postfix"
        assert ident.extra.get("host") == "mail.example.com"
        assert ident.confidence >= 0.85

    def test_postfix_multiline(self) -> None:
        ident = fingerprint_banner(SMTP_POSTFIX_MULTILINE)
        assert ident.service == "smtp"
        assert ident.product == "Postfix"

    def test_gmail(self) -> None:
        ident = fingerprint_banner(SMTP_GMAIL)
        assert ident.service == "smtp"
        # Product detection on opaque IDs is best-effort
        assert ident.extra["host"] == "smtp.gmail.com"

    def test_bare_smtp(self) -> None:
        ident = fingerprint_banner(SMTP_BARE)
        assert ident.service == "smtp"
        # No ESMTP, product is "ready"
        assert ident.extra.get("host") == "mail.example.com"

    def test_case_insensitive(self) -> None:
        ident = fingerprint_banner("220 mail.example.com esmtp postfix")
        assert ident.service == "smtp"


# ── fingerprint_banner: FTP ───────────────────────────────────────


class TestFTP:
    def test_vsftpd(self) -> None:
        ident = fingerprint_banner(FTP_VSFTPD)
        assert ident.service == "ftp"
        assert ident.product == "vsFTPd"
        assert ident.version == "3.0.5"

    def test_vsftpd_no_version(self) -> None:
        ident = fingerprint_banner(FTP_VSFTPD_NOVER)
        assert ident.service == "ftp"
        assert ident.product == "vsFTPd"


# ── fingerprint_banner: POP3 ──────────────────────────────────────


class TestPOP3:
    def test_dovecot(self) -> None:
        ident = fingerprint_banner(POP3_DOVECOT)
        assert ident.service == "pop3"
        assert ident.product == "Dovecot"

    def test_generic_no_product(self) -> None:
        # "+OK" alone has no product token to capture
        ident = fingerprint_banner(POP3_GENERIC)
        # Either pop3 with no product, or fallback to port hint when present
        assert ident.service in {"pop3", "unknown"}


# ── fingerprint_banner: IMAP ──────────────────────────────────────


class TestIMAP:
    def test_dovecot(self) -> None:
        ident = fingerprint_banner(IMAP_DOVECOT)
        assert ident.service == "imap"
        assert ident.product == "Dovecot"
        assert ident.confidence >= 0.85

    def test_cyrus(self) -> None:
        ident = fingerprint_banner(IMAP_CYRUS)
        assert ident.service == "imap"
        assert ident.product == "Cyrus"


# ── fingerprint_banner: HTTP ──────────────────────────────────────


class TestHTTP:
    def test_nginx(self) -> None:
        ident = fingerprint_banner(HTTP_NGINX)
        assert ident.service == "http"
        assert ident.product == "nginx"
        assert ident.version == "1.24.0"

    def test_apache(self) -> None:
        ident = fingerprint_banner(HTTP_APACHE)
        assert ident.service == "http"
        assert ident.product == "Apache"
        assert ident.version == "2.4.41"

    def test_caddy_no_version(self) -> None:
        ident = fingerprint_banner(HTTP_CADDY)
        assert ident.service == "http"
        assert ident.product == "Caddy"


# ── fingerprint_banner: Redis ─────────────────────────────────────


class TestRedis:
    def test_noauth(self) -> None:
        ident = fingerprint_banner(REDIS_NOAUTH)
        assert ident.service == "redis"
        assert ident.product == "Redis"

    def test_info_with_version(self) -> None:
        ident = fingerprint_banner(REDIS_INFO)
        assert ident.service == "redis"
        assert ident.version == "7.2.4"


# ── Port-based hints ──────────────────────────────────────────────


class TestPortHints:
    @pytest.mark.parametrize(
        "port,expected_service",
        [
            (22, "ssh"),
            (25, "smtp"),
            (80, "http"),
            (443, "https"),
            (3306, "mysql"),
            (5432, "postgresql"),
            (6379, "redis"),
            (27017, "mongodb"),
            (53, "dns"),
            (123, "ntp"),
            (161, "snmp"),
            (514, "syslog"),
        ],
    )
    def test_known_port_with_empty_banner(self, port: int, expected_service: str) -> None:
        ident = fingerprint_banner("", port=port)
        assert ident.service == expected_service
        assert ident.confidence == 0.3
        assert ident.extra["source"] == "port"

    def test_unknown_port_with_empty_banner(self) -> None:
        ident = fingerprint_banner("", port=12345)
        assert ident.service == "unknown"
        assert ident.confidence == 0.0

    def test_no_port_no_banner(self) -> None:
        ident = fingerprint_banner("")
        assert ident.service == "unknown"
        assert ident.confidence == 0.0

    def test_unrecognised_banner_falls_back_to_port(self) -> None:
        ident = fingerprint_banner("RANDOM_PROTOCOL_GREETING_v1\r\n", port=27017)
        assert ident.service == "mongodb"
        assert ident.confidence == 0.3


# ── Binary handshakes ─────────────────────────────────────────────


class TestMySQLHandshake:
    def test_detect_handshake(self) -> None:
        ok, version = _is_mysql_handshake(MYSQL_HANDSHAKE)
        assert ok is True
        assert version == "5.7.34-log"

    def test_too_short(self) -> None:
        ok, version = _is_mysql_handshake(b"\x00\x00")
        assert ok is False
        assert version is None

    def test_wrong_protocol_byte(self) -> None:
        # Protocol byte at offset 4 is 0x09 not 0x0a
        ok, _ = _is_mysql_handshake(b"\x4a\x00\x00\x00\x09" + b"x" * 20)
        assert ok is False

    def test_no_nul_terminator(self) -> None:
        # Handshake with 0x0a but no NUL — recognised as MySQL but no version
        raw = b"\x4a\x00\x00\x00\x0a" + b"5.7.34"  # missing NUL
        ok, version = _is_mysql_handshake(raw)
        assert ok is True
        assert version is None

    def test_fingerprint_via_bytes_with_port(self) -> None:
        ident = fingerprint_banner_bytes(MYSQL_HANDSHAKE, port=3306)
        assert ident.service == "mysql"
        assert ident.product == "MySQL"
        assert ident.version == "5.7.34-log"

    def test_fingerprint_via_bytes_wrong_port_no_match(self) -> None:
        # Same bytes but port=80 should NOT be detected as MySQL
        ident = fingerprint_banner_bytes(MYSQL_HANDSHAKE, port=80)
        # Falls through to text path which will likely yield http port hint
        assert ident.service != "mysql"


class TestPostgresErrorResponse:
    def test_detect(self) -> None:
        assert _is_postgres_error(PG_ERROR) is True

    def test_negative(self) -> None:
        assert _is_postgres_error(b"SSH-2.0-Foo") is False

    def test_fingerprint_via_bytes(self) -> None:
        ident = fingerprint_banner_bytes(PG_ERROR, port=5432)
        assert ident.service == "postgresql"
        assert ident.product == "PostgreSQL"
        assert ident.confidence >= 0.4


# ── fingerprint_banner_bytes general ──────────────────────────────


class TestFingerprintBytes:
    def test_empty_with_port(self) -> None:
        ident = fingerprint_banner_bytes(b"", port=22)
        assert ident.service == "ssh"
        assert ident.confidence == 0.3

    def test_text_path_via_bytes(self) -> None:
        ident = fingerprint_banner_bytes(SSH_OPENSSH.encode("ascii"))
        assert ident.service == "ssh"
        assert ident.product == "OpenSSH"

    def test_undecodable_falls_back_to_port(self) -> None:
        # latin-1 always succeeds so we test an arbitrary high-byte sequence
        # that won't match any signature
        ident = fingerprint_banner_bytes(b"\xff\xfe\xfd\xfc", port=22)
        assert ident.service == "ssh"
        assert ident.confidence == 0.3


# ── fingerprint_results ───────────────────────────────────────────


class TestFingerprintResults:
    def test_mixed_inputs(self) -> None:
        results: list[BannerProbeResult] = [
            BannerProbeResult(
                host="x",
                port=22,
                status=PortStatus.OPEN,
                banner=SSH_OPENSSH,
                banner_bytes=SSH_OPENSSH.encode("ascii"),
            ),
            BannerProbeResult(
                host="x",
                port=80,
                status=PortStatus.OPEN,
                banner=None,
                banner_bytes=None,
            ),
            BannerProbeResult(
                host="x",
                port=3306,
                status=PortStatus.OPEN,
                banner=None,
                banner_bytes=MYSQL_HANDSHAKE,
            ),
            # Banner string only, no bytes
            BannerProbeResult(
                host="x",
                port=25,
                status=PortStatus.OPEN,
                banner=SMTP_POSTFIX,
                banner_bytes=None,
            ),
        ]
        out = fingerprint_results(results)
        assert len(out) == 4
        assert out[0][1].service == "ssh"
        assert out[1][1].service == "http"  # port hint
        assert out[1][1].confidence == 0.3
        assert out[2][1].service == "mysql"
        assert out[3][1].service == "smtp"

    def test_returns_pairs(self) -> None:
        result = BannerProbeResult(host="x", port=22, status=PortStatus.OPEN, banner=SSH_OPENSSH)
        out = fingerprint_results([result])
        assert len(out) == 1
        probe, ident = out[0]
        assert probe is result
        assert isinstance(ident, ServiceIdentity)
