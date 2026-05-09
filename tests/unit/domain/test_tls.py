"""Tests for ``kaos_web.domain.tls`` — TLS certificate inspection.

Mocks ``socket.create_connection`` + ``ssl.SSLContext.wrap_socket``.
The pure helpers (``_flatten_cert_field``, ``_parse_cert_date``) are
exercised directly with realistic certificate-shaped data.
"""

from __future__ import annotations

import datetime as dt
import ssl
from unittest.mock import patch

import pytest

from kaos_web.domain.models import TlsCertInfo
from kaos_web.domain.tls import (
    _extract_cert_info,
    _flatten_cert_field,
    _parse_cert_date,
    inspect_tls,
)

# ── Real-shaped peer certificate (matches stdlib ssl.getpeercert format) ──

_REAL_CERT: dict[str, object] = {
    "subject": (
        (("countryName", "US"),),
        (("stateOrProvinceName", "California"),),
        (("localityName", "Los Angeles"),),
        (
            (
                "organizationName",
                "Internet Corporation for Assigned Names and Numbers",
            ),
        ),
        (("commonName", "www.example.org"),),
    ),
    "issuer": (
        (("countryName", "US"),),
        (("organizationName", "DigiCert Inc"),),
        (("commonName", "DigiCert Global G3 TLS ECC SHA384 2020 CA1"),),
    ),
    "version": 3,
    "serialNumber": "0FE19FB7BD3DA1A4FC8D3F87B6F3D1F5",
    "notBefore": "Jan 30 00:00:00 2024 GMT",
    "notAfter": "Mar  1 23:59:59 2026 GMT",
    "subjectAltName": (
        ("DNS", "www.example.org"),
        ("DNS", "example.com"),
        ("DNS", "example.net"),
        ("DNS", "example.org"),
        ("DNS", "www.example.com"),
        ("DNS", "www.example.net"),
    ),
    "OCSP": ("http://ocsp.digicert.com",),
    "caIssuers": ("http://cacerts.digicert.com/DigiCertGlobalG3TLSECCSHA3842020CA1-1.crt",),
}


class TestFlattenCertField:
    def test_empty(self) -> None:
        assert _flatten_cert_field(()) == {}
        assert _flatten_cert_field(None) == {}

    def test_subject(self) -> None:
        assert _flatten_cert_field(_REAL_CERT["subject"]) == {  # type: ignore[arg-type]
            "countryName": "US",
            "stateOrProvinceName": "California",
            "localityName": "Los Angeles",
            "organizationName": "Internet Corporation for Assigned Names and Numbers",
            "commonName": "www.example.org",
        }

    def test_issuer(self) -> None:
        result = _flatten_cert_field(_REAL_CERT["issuer"])  # type: ignore[arg-type]
        assert result["organizationName"] == "DigiCert Inc"

    def test_skips_non_tuple(self) -> None:
        assert _flatten_cert_field([1, 2, "x"]) == {}


class TestParseCertDate:
    def test_none(self) -> None:
        assert _parse_cert_date(None) is None

    def test_empty(self) -> None:
        assert _parse_cert_date("") is None

    def test_real_format(self) -> None:
        d = _parse_cert_date("Jan 30 00:00:00 2024 GMT")
        assert d is not None
        assert d.year == 2024
        assert d.month == 1
        assert d.day == 30
        assert d.tzinfo is dt.UTC

    def test_invalid(self) -> None:
        assert _parse_cert_date("not-a-real-date") is None


class _MockSSLSocket:
    """A context manager that pretends to be an SSL-wrapped socket."""

    def __init__(
        self,
        cert: dict | None = None,
        cipher: tuple[str, str, int] | None = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256),
        version: str | None = "TLSv1.3",
    ) -> None:
        self._cert = cert
        self._cipher = cipher
        self._version = version

    def __enter__(self) -> _MockSSLSocket:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getpeercert(self) -> dict | None:
        return self._cert

    def cipher(self) -> tuple[str, str, int] | None:
        return self._cipher

    def version(self) -> str | None:
        return self._version


class _MockRawSocket:
    def __enter__(self) -> _MockRawSocket:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestExtractCertInfo:
    def test_validated_cert(self) -> None:
        with (
            patch("socket.create_connection", return_value=_MockRawSocket()),
            patch.object(
                ssl.SSLContext, "wrap_socket", return_value=_MockSSLSocket(cert=_REAL_CERT)
            ),
        ):
            info = _extract_cert_info("example.com", 443, timeout=1.0)
        assert isinstance(info, TlsCertInfo)
        assert info.host == "example.com"
        assert info.subject["commonName"] == "www.example.org"
        assert info.issuer["organizationName"] == "DigiCert Inc"
        assert info.serial_number == "0FE19FB7BD3DA1A4FC8D3F87B6F3D1F5"
        assert info.protocol == "TLSv1.3"
        assert info.cipher == "TLS_AES_256_GCM_SHA384"
        assert info.cipher_bits == 256
        assert "example.com" in info.san_dns
        assert "www.example.org" in info.san_dns
        assert info.not_before is not None and info.not_before.startswith("2024-01-30")
        assert info.not_after is not None and info.not_after.startswith("2026-03-01")
        # days_until_expiry is signed days from now
        assert info.days_until_expiry is not None

    def test_validation_error_falls_back(self) -> None:
        # First wrap_socket raises SSLCertVerificationError, second returns
        # cipher info but no cert.
        first = ssl.SSLCertVerificationError("self signed")
        second = _MockSSLSocket(cert=None, cipher=("AES", "TLSv1.2", 128), version="TLSv1.2")
        wrap_calls = iter([first, second])

        def _wrap(*args: object, **kwargs: object) -> _MockSSLSocket:
            v = next(wrap_calls)
            if isinstance(v, BaseException):
                raise v
            return v

        with (
            patch("socket.create_connection", return_value=_MockRawSocket()),
            patch.object(ssl.SSLContext, "wrap_socket", side_effect=_wrap),
        ):
            info = _extract_cert_info("self-signed.example", 443, timeout=1.0)
        assert info.error is not None and "self-signed" in info.error.lower()
        assert info.protocol == "TLSv1.2"
        assert info.cipher == "AES"

    def test_validation_error_then_fallback_fails(self) -> None:
        def _wrap(*args: object, **kwargs: object) -> object:
            raise ssl.SSLCertVerificationError("self signed")

        with (
            patch("socket.create_connection", return_value=_MockRawSocket()),
            patch.object(ssl.SSLContext, "wrap_socket", side_effect=_wrap),
        ):
            info = _extract_cert_info("self-signed.example", 443, timeout=1.0)
        # Fallback also fails -> still returns an error result
        assert info.error is not None

    def test_timeout(self) -> None:
        with patch("socket.create_connection", side_effect=TimeoutError()):
            info = _extract_cert_info("slow.example", 443, timeout=0.1)
        assert info.error is not None and "timed out" in info.error

    def test_refused(self) -> None:
        with patch("socket.create_connection", side_effect=ConnectionRefusedError()):
            info = _extract_cert_info("closed.example", 443, timeout=1.0)
        assert info.error is not None and "refused" in info.error

    def test_ssl_error(self) -> None:
        with (
            patch("socket.create_connection", return_value=_MockRawSocket()),
            patch.object(ssl.SSLContext, "wrap_socket", side_effect=ssl.SSLError("bad handshake")),
        ):
            info = _extract_cert_info("ssl-bad.example", 443, timeout=1.0)
        assert info.error is not None and "SSL error" in info.error

    def test_oserror(self) -> None:
        with patch("socket.create_connection", side_effect=OSError("network down")):
            info = _extract_cert_info("network-down.example", 443, timeout=1.0)
        assert info.error is not None and "Network error" in info.error

    def test_cert_with_minimal_fields(self) -> None:
        minimal = {"subject": (), "issuer": (), "subjectAltName": ()}
        with (
            patch("socket.create_connection", return_value=_MockRawSocket()),
            patch.object(ssl.SSLContext, "wrap_socket", return_value=_MockSSLSocket(cert=minimal)),
        ):
            info = _extract_cert_info("minimal.example", 443, timeout=1.0)
        assert info.serial_number is None
        assert info.not_before is None
        assert info.san_dns == []


@pytest.mark.asyncio
class TestInspectTls:
    async def test_async_wrapper(self) -> None:
        with (
            patch("socket.create_connection", return_value=_MockRawSocket()),
            patch.object(
                ssl.SSLContext, "wrap_socket", return_value=_MockSSLSocket(cert=_REAL_CERT)
            ),
        ):
            info = await inspect_tls("example.com", 443, timeout=1.0)
        assert info.host == "example.com"
        assert info.subject["commonName"] == "www.example.org"

    async def test_url_policy_blocks_private_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WEB5-001: ``inspect_tls`` MUST refuse a private-IP target
        BEFORE the TLS handshake. Gate fires before
        ``asyncio.to_thread``, so no socket / SSL mock is needed.
        """
        from kaos_web.errors import UrlPolicyError

        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        with pytest.raises(UrlPolicyError) as info:
            await inspect_tls("10.0.0.1", 443)
        assert "KAOS_SECURITY_" in str(info.value)
