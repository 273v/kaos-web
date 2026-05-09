"""Tests for ``kaos_web.domain.dns`` — DNS queries via dnspython.

Mocks ``dns.asyncresolver.Resolver`` directly. The pure helper
``_derive_apex_domain`` is exercised against real public-suffix examples.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import dns.exception  # type: ignore[import-untyped]
import dns.flags  # type: ignore[import-untyped]
import dns.name  # type: ignore[import-untyped]
import dns.rdata  # type: ignore[import-untyped]
import dns.rdataclass  # type: ignore[import-untyped]
import dns.rdataset  # type: ignore[import-untyped]
import dns.rdatatype  # type: ignore[import-untyped]
import dns.rrset  # type: ignore[import-untyped]
import pytest
from dns.resolver import NXDOMAIN, NoAnswer, NoNameservers  # type: ignore[import-untyped]

from kaos_web.domain.dns import (
    DEFAULT_RECORD_TYPES,
    DNSSEC_RECORD_TYPES,
    _derive_apex_domain,
    enumerate_dns,
    lookup,
    lookup_many,
    reverse_ptr,
)
from kaos_web.domain.models import DnsProfile, DnsQueryResult, DnsRecordStatus

# ── Apex domain derivation ──────────────────────────────────────────


class TestDeriveApexDomain:
    def test_simple_two_label(self) -> None:
        assert _derive_apex_domain("example.com") == "example.com"

    def test_subdomain(self) -> None:
        assert _derive_apex_domain("www.example.com") == "example.com"

    def test_deep_subdomain(self) -> None:
        assert _derive_apex_domain("a.b.c.example.com") == "example.com"

    def test_single_label(self) -> None:
        assert _derive_apex_domain("localhost") == "localhost"

    def test_co_uk(self) -> None:
        assert _derive_apex_domain("www.example.co.uk") == "example.co.uk"
        assert _derive_apex_domain("example.co.uk") == "example.co.uk"

    def test_com_au(self) -> None:
        assert _derive_apex_domain("subdomain.example.com.au") == "example.com.au"

    def test_co_jp(self) -> None:
        assert _derive_apex_domain("foo.example.co.jp") == "example.co.jp"

    def test_trailing_dot(self) -> None:
        assert _derive_apex_domain("example.com.") == "example.com"


# ── DNS resolver mocking helpers ─────────────────────────────────────


def _make_rrset(
    name: str,
    rtype: str,
    values: list[str],
    ttl: int = 300,
) -> Any:
    """Build a real dnspython RRset for a record type + list of textual rdata."""
    rdtype = dns.rdatatype.from_text(rtype)
    rds = dns.rdataset.from_text_list(dns.rdataclass.IN, rdtype, ttl, values)
    rrset = dns.rrset.from_rdata_list(dns.name.from_text(name), ttl, list(rds))
    return rrset


def _make_answer(rrsets: list[Any]) -> Any:
    """Construct a fake answer object with .response.answer = rrsets."""
    response = MagicMock()
    response.answer = rrsets
    answer = MagicMock()
    answer.response = response
    answer.rrset = rrsets[0] if rrsets else None
    answer.__iter__ = lambda self: iter(rrsets[0]) if rrsets else iter([])
    return answer


def _patch_resolver(answer_or_exc: Any) -> Any:
    """Patch dns.asyncresolver.Resolver to return / raise the given thing."""
    fake_resolver = MagicMock()
    if isinstance(answer_or_exc, BaseException) or (
        isinstance(answer_or_exc, type) and issubclass(answer_or_exc, BaseException)
    ):
        fake_resolver.resolve = AsyncMock(side_effect=answer_or_exc)
    else:
        fake_resolver.resolve = AsyncMock(return_value=answer_or_exc)
    fake_resolver.lifetime = 0
    fake_resolver.timeout = 0
    fake_resolver.nameservers = []
    return patch("dns.asyncresolver.Resolver", return_value=fake_resolver)


# ── lookup() ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestLookup:
    async def test_a_record_success(self) -> None:
        answer = _make_answer([_make_rrset("example.com.", "A", ["93.184.216.34"], ttl=300)])
        with _patch_resolver(answer):
            result = await lookup("example.com", "A", timeout=1.0)
        assert isinstance(result, DnsQueryResult)
        assert result.status == DnsRecordStatus.SUCCESS
        assert len(result.records) == 1
        assert result.records[0].value == "93.184.216.34"
        assert result.records[0].ttl == 300
        assert result.records[0].record_type == "A"
        assert result.duration_ms is not None

    async def test_mx_record_success(self) -> None:
        answer = _make_answer(
            [_make_rrset("example.com.", "MX", ["10 mail.example.com."], ttl=600)]
        )
        with _patch_resolver(answer):
            result = await lookup("example.com", "MX", timeout=1.0)
        assert result.status == DnsRecordStatus.SUCCESS
        assert "mail.example.com" in result.records[0].value

    async def test_txt_record_success(self) -> None:
        answer = _make_answer([_make_rrset("example.com.", "TXT", ['"v=spf1 -all"'], ttl=300)])
        with _patch_resolver(answer):
            result = await lookup("example.com", "TXT")
        assert result.status == DnsRecordStatus.SUCCESS
        assert "spf1" in result.records[0].value

    async def test_nxdomain(self) -> None:
        with _patch_resolver(NXDOMAIN):
            result = await lookup("does-not-exist.invalid", "A", timeout=1.0)
        assert result.status == DnsRecordStatus.NXDOMAIN
        assert result.error is not None and "NXDOMAIN" in result.error
        assert result.records == []

    async def test_no_answer(self) -> None:
        with _patch_resolver(NoAnswer):
            result = await lookup("example.com", "AAAA", timeout=1.0)
        assert result.status == DnsRecordStatus.NO_ANSWER

    async def test_no_nameservers(self) -> None:
        with _patch_resolver(NoNameservers):
            result = await lookup("example.com", "A", timeout=1.0)
        assert result.status == DnsRecordStatus.TIMEOUT

    async def test_dns_timeout(self) -> None:
        with _patch_resolver(dns.exception.Timeout):
            result = await lookup("example.com", "A", timeout=1.0)
        assert result.status == DnsRecordStatus.TIMEOUT

    async def test_other_error(self) -> None:
        with _patch_resolver(RuntimeError("boom")):
            result = await lookup("example.com", "A", timeout=1.0)
        assert result.status == DnsRecordStatus.ERROR
        assert result.error is not None and "RuntimeError" in result.error

    async def test_empty_answer_section(self) -> None:
        # response.answer is empty list — counts as NO_ANSWER
        empty = MagicMock()
        empty.response = MagicMock()
        empty.response.answer = []
        empty.rrset = None
        with _patch_resolver(empty):
            result = await lookup("example.com", "A")
        assert result.status == DnsRecordStatus.NO_ANSWER

    async def test_with_custom_nameservers(self) -> None:
        answer = _make_answer([_make_rrset("example.com.", "A", ["1.2.3.4"])])
        with _patch_resolver(answer):
            result = await lookup(
                "example.com", "A", timeout=1.0, nameservers=["8.8.8.8", "1.1.1.1"]
            )
        assert result.status == DnsRecordStatus.SUCCESS

    async def test_lowercase_record_type(self) -> None:
        answer = _make_answer([_make_rrset("example.com.", "A", ["1.2.3.4"])])
        with _patch_resolver(answer):
            result = await lookup("example.com", "a")
        assert result.record_type == "A"  # uppercased


# ── lookup_many() ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestLookupMany:
    async def test_multiple_types(self) -> None:
        answer = _make_answer([_make_rrset("example.com.", "A", ["1.2.3.4"])])
        with _patch_resolver(answer):
            results = await lookup_many("example.com", ["A", "AAAA", "MX"], timeout=1.0)
        assert len(results) == 3
        assert all(isinstance(r, DnsQueryResult) for r in results)


# ── reverse_ptr() ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestReversePtr:
    async def test_success(self) -> None:
        # Build a PTR rrset
        rrset = _make_rrset("34.216.184.93.in-addr.arpa.", "PTR", ["example.com."], ttl=300)
        answer = MagicMock()
        answer.rrset = rrset
        answer.__iter__ = lambda self: iter(rrset)
        with _patch_resolver(answer):
            ptr = await reverse_ptr("93.184.216.34", timeout=1.0)
        assert ptr is not None
        assert ptr.value == "example.com"
        assert ptr.record_type == "PTR"

    async def test_invalid_ip(self) -> None:
        ptr = await reverse_ptr("not-an-ip", timeout=1.0)
        assert ptr is None

    async def test_resolver_failure(self) -> None:
        with _patch_resolver(NXDOMAIN):
            ptr = await reverse_ptr("93.184.216.34", timeout=1.0)
        assert ptr is None


# ── enumerate_dns() ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestEnumerateDns:
    async def test_full_enumeration(self) -> None:
        # Resolver returns A=1.2.3.4 for every query type.
        answer = _make_answer([_make_rrset("example.com.", "A", ["1.2.3.4"])])
        with _patch_resolver(answer):
            profile = await enumerate_dns(
                "example.com", timeout=1.0, include_reverse_ptr=False, include_dnssec=False
            )
        assert isinstance(profile, DnsProfile)
        assert profile.domain == "example.com"
        assert profile.apex_domain == "example.com"
        assert len(profile.queries) == len(DEFAULT_RECORD_TYPES)

    async def test_dnssec_present(self) -> None:
        # DNSKEY succeeds → dnssec True
        answer = _make_answer([_make_rrset("example.com.", "A", ["1.2.3.4"])])
        with _patch_resolver(answer):
            profile = await enumerate_dns(
                "example.com", timeout=1.0, include_reverse_ptr=False, include_dnssec=True
            )
        # All queries return A-style success → DNSKEY also "succeeds" → dnssec True
        assert profile.dnssec is True
        # Includes the DNSSEC record types in queries
        recorded_types = [q.record_type for q in profile.queries]
        for t in DNSSEC_RECORD_TYPES:
            assert t in recorded_types

    async def test_dnssec_disabled(self) -> None:
        answer = _make_answer([_make_rrset("example.com.", "A", ["1.2.3.4"])])
        with _patch_resolver(answer):
            profile = await enumerate_dns(
                "example.com", timeout=1.0, include_reverse_ptr=False, include_dnssec=False
            )
        assert profile.dnssec is None
        recorded_types = [q.record_type for q in profile.queries]
        assert "DNSKEY" not in recorded_types

    async def test_extracts_nameservers_and_mx(self) -> None:
        # All queries return the same canned answer — but we want NS and MX
        # to actually come back differently. Build a specialized resolver.

        async def _resolve(qname: object, **kwargs: Any) -> Any:
            rt = kwargs.get("rdtype")
            if isinstance(rt, str):
                rt_text = rt.upper()
            elif rt is None:
                rt_text = ""
            else:
                rt_text = dns.rdatatype.to_text(rt)
            if rt_text == "NS":
                return _make_answer(
                    [
                        _make_rrset(
                            "example.com.",
                            "NS",
                            ["ns1.example.com.", "ns2.example.com."],
                            ttl=300,
                        )
                    ]
                )
            if rt_text == "MX":
                return _make_answer(
                    [
                        _make_rrset(
                            "example.com.",
                            "MX",
                            ["10 mail1.example.com.", "20 mail2.example.com."],
                            ttl=300,
                        )
                    ]
                )
            if rt_text in ("A", "AAAA"):
                return _make_answer([_make_rrset("example.com.", "A", ["93.184.216.34"], ttl=300)])
            return _make_answer([])

        fake_resolver = MagicMock()
        fake_resolver.resolve = AsyncMock(side_effect=_resolve)
        with (
            patch("dns.asyncresolver.Resolver", return_value=fake_resolver),
            patch("kaos_web.domain.dns.reverse_ptr", AsyncMock(return_value=None)),
        ):
            profile = await enumerate_dns(
                "example.com", timeout=1.0, include_reverse_ptr=True, include_dnssec=False
            )
        assert "ns1.example.com" in profile.nameservers
        assert "ns2.example.com" in profile.nameservers
        assert "mail1.example.com" in profile.mx_hosts
        assert "mail2.example.com" in profile.mx_hosts

    async def test_co_uk_apex(self) -> None:
        answer = _make_answer([_make_rrset("example.co.uk.", "A", ["1.2.3.4"])])
        with _patch_resolver(answer):
            profile = await enumerate_dns(
                "www.example.co.uk",
                timeout=1.0,
                include_reverse_ptr=False,
                include_dnssec=False,
            )
        assert profile.apex_domain == "example.co.uk"
