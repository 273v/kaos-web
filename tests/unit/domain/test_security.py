"""Tests for ``kaos_web.domain.security`` — SPF/DKIM/DMARC analysis.

The pure parser ``_parse_spf`` is exercised against real-world TXT
records (drawn from public DNS records of large mail providers). The
async functions ``_check_dkim``, ``_fetch_dmarc``, and
``analyze_mail_security`` are tested by mocking ``kaos_web.domain.dns.lookup``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kaos_web.domain.models import (
    DnsQueryResult,
    DnsRecord,
    DnsRecordStatus,
    MailAuthMechanism,
    MailAuthStatus,
)
from kaos_web.domain.security import (
    _DKIM_SELECTORS,
    _check_dkim,
    _fetch_dmarc,
    _parse_spf,
    analyze_mail_security,
)

# ── _parse_spf ──────────────────────────────────────────────────────


class TestParseSpf:
    def test_missing(self) -> None:
        r = _parse_spf([])
        assert r.mechanism == MailAuthMechanism.SPF
        assert r.status == MailAuthStatus.MISSING
        assert "No SPF record found" in r.issues[0]

    def test_no_spf_in_txt_records(self) -> None:
        r = _parse_spf(["just a random TXT", "another"])
        assert r.status == MailAuthStatus.MISSING

    def test_hard_fail(self) -> None:
        # Real-shaped record (anonymized variant of typical Gmail SPF)
        r = _parse_spf(["v=spf1 include:_spf.google.com -all"])
        assert r.status == MailAuthStatus.CONFIGURED
        assert r.policy == "-all (hard fail)"
        assert r.raw_record is not None and "google.com" in r.raw_record

    def test_soft_fail(self) -> None:
        r = _parse_spf(["v=spf1 include:_spf.example.com ~all"])
        assert r.status == MailAuthStatus.CONFIGURED
        assert r.policy == "~all (soft fail)"

    def test_neutral(self) -> None:
        r = _parse_spf(["v=spf1 ?all"])
        # Per implementation: ?all is treated as WEAK because "no enforcement"
        assert r.status == MailAuthStatus.WEAK
        assert r.policy == "?all (neutral)"
        assert any("?all" in issue for issue in r.issues)

    def test_pass_all_dangerous(self) -> None:
        r = _parse_spf(["v=spf1 +all"])
        assert r.status == MailAuthStatus.WEAK
        assert r.policy == "+all (pass all)"
        assert any("dangerous" in issue.lower() for issue in r.issues)

    def test_no_all_mechanism(self) -> None:
        r = _parse_spf(["v=spf1 include:_spf.example.com"])
        assert any("no 'all' mechanism" in issue.lower() for issue in r.issues)

    def test_too_many_lookups(self) -> None:
        # >10 include: mechanisms triggers permerror warning
        includes = " ".join(f"include:provider{i}.example.com" for i in range(11))
        record = f"v=spf1 {includes} -all"
        r = _parse_spf([record])
        assert any("permerror" in issue.lower() for issue in r.issues)
        assert r.details["lookup_mechanisms"] == 11

    def test_lookup_count_under_limit(self) -> None:
        record = "v=spf1 include:_spf.google.com a:mail.example.com -all"
        r = _parse_spf([record])
        assert r.details["lookup_mechanisms"] == 2


# ── DNS lookup mocking helpers ───────────────────────────────────────


def _success_result(domain: str, values: list[str]) -> DnsQueryResult:
    return DnsQueryResult(
        query_name=domain,
        record_type="TXT",
        status=DnsRecordStatus.SUCCESS,
        records=[DnsRecord(name=domain, record_type="TXT", value=v, ttl=300) for v in values],
    )


def _empty_result(domain: str) -> DnsQueryResult:
    return DnsQueryResult(
        query_name=domain,
        record_type="TXT",
        status=DnsRecordStatus.NXDOMAIN,
    )


# ── _fetch_dmarc ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestFetchDmarc:
    async def test_quarantine_policy(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _success_result(
                qname,
                ["v=DMARC1; p=quarantine; sp=quarantine; rua=mailto:dmarc@example.com; pct=100"],
            )

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _fetch_dmarc("example.com", timeout=1.0)
        assert r.status == MailAuthStatus.CONFIGURED
        assert r.policy == "quarantine"
        assert r.details["aggregate_report"] == "mailto:dmarc@example.com"
        assert r.details["pct"] == "100"
        assert r.details["subdomain_policy"] == "quarantine"
        assert r.issues == []

    async def test_policy_none_is_weak(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _success_result(qname, ["v=DMARC1; p=none; rua=mailto:rua@example.com"])

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _fetch_dmarc("example.com", timeout=1.0)
        assert r.status == MailAuthStatus.WEAK
        assert r.policy == "none"
        assert any("none" in i.lower() for i in r.issues)

    async def test_pct_below_100(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _success_result(
                qname, ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com; pct=50"]
            )

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _fetch_dmarc("example.com", timeout=1.0)
        assert r.policy == "reject"
        assert any("pct=50" in i for i in r.issues)

    async def test_missing_rua(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _success_result(qname, ["v=DMARC1; p=reject"])

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _fetch_dmarc("example.com", timeout=1.0)
        assert any("rua=" in i for i in r.issues)

    async def test_missing_p_tag(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _success_result(qname, ["v=DMARC1; rua=mailto:rua@example.com"])

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _fetch_dmarc("example.com", timeout=1.0)
        assert any("missing 'p='" in i for i in r.issues)

    async def test_no_dmarc_record(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _empty_result(qname)

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _fetch_dmarc("example.com", timeout=1.0)
        assert r.status == MailAuthStatus.MISSING
        assert any("_dmarc" in i for i in r.issues)

    async def test_no_v_dmarc1(self) -> None:
        # TXT record exists but doesn't start with v=DMARC1
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _success_result(qname, ["just a random txt record"])

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _fetch_dmarc("example.com", timeout=1.0)
        assert r.status == MailAuthStatus.MISSING
        assert any("v=DMARC1" in i for i in r.issues)


# ── _check_dkim ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestCheckDkim:
    async def test_no_selectors_found(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _empty_result(qname)

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _check_dkim("example.com", timeout=1.0)
        assert r.mechanism == MailAuthMechanism.DKIM
        assert r.status == MailAuthStatus.MISSING
        assert "selectors_checked" in r.details
        assert isinstance(r.details["selectors_checked"], list)
        assert len(r.details["selectors_checked"]) == len(_DKIM_SELECTORS)

    async def test_one_selector_found(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            if qname.startswith("google._domainkey"):
                return _success_result(
                    qname, ["v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCB"]
                )
            return _empty_result(qname)

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _check_dkim("example.com", timeout=1.0)
        assert r.status == MailAuthStatus.CONFIGURED
        assert "google" in r.details["selectors_found"]

    async def test_p_only_record_counts(self) -> None:
        # Some providers omit v=DKIM1 but include p= -> should still be detected
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            if qname.startswith("default._domainkey"):
                return _success_result(qname, ['"k=rsa\\;p=MIGfMA0GCSqGSIb3"'])
            return _empty_result(qname)

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            r = await _check_dkim("example.com", timeout=1.0)
        assert r.status == MailAuthStatus.CONFIGURED


# ── analyze_mail_security ───────────────────────────────────────────


@pytest.mark.asyncio
class TestAnalyzeMailSecurity:
    async def test_strong_posture(self) -> None:
        # SPF, DKIM, DMARC all valid
        async def _fake_lookup(qname: str, rt: str, **__: Any) -> DnsQueryResult:
            if qname == "example.com" and rt == "TXT":
                return _success_result(qname, ["v=spf1 include:_spf.google.com -all"])
            if qname.startswith("_dmarc"):
                return _success_result(
                    qname, ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com; pct=100"]
                )
            if qname.startswith("google._domainkey"):
                return _success_result(qname, ["v=DKIM1; k=rsa; p=AAAA"])
            return _empty_result(qname)

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            report = await analyze_mail_security("example.com", timeout=1.0)
        assert report.overall_posture == "strong"
        assert len(report.records) == 3

    async def test_missing_posture(self) -> None:
        async def _fake_lookup(qname: str, _: str, **__: Any) -> DnsQueryResult:
            return _empty_result(qname)

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            report = await analyze_mail_security("example.com", timeout=1.0)
        assert report.overall_posture == "missing"

    async def test_moderate_posture(self) -> None:
        # Only SPF + DMARC; no DKIM
        async def _fake_lookup(qname: str, rt: str, **__: Any) -> DnsQueryResult:
            if qname == "example.com" and rt == "TXT":
                return _success_result(qname, ["v=spf1 include:_spf.google.com -all"])
            if qname.startswith("_dmarc"):
                return _success_result(qname, ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"])
            return _empty_result(qname)

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            report = await analyze_mail_security("example.com", timeout=1.0)
        assert report.overall_posture == "moderate"

    async def test_weak_posture(self) -> None:
        # Only DMARC with p=none -> WEAK
        async def _fake_lookup(qname: str, rt: str, **__: Any) -> DnsQueryResult:
            if qname.startswith("_dmarc"):
                return _success_result(qname, ["v=DMARC1; p=none"])
            return _empty_result(qname)

        with patch("kaos_web.domain.security.lookup", AsyncMock(side_effect=_fake_lookup)):
            report = await analyze_mail_security("example.com", timeout=1.0)
        assert report.overall_posture == "weak"
