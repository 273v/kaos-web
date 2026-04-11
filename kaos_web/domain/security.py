"""Mail authentication analysis — SPF, DKIM, and DMARC.

Fetches relevant DNS TXT records and parses them to assess a domain's
mail authentication posture.  Uses ``dnspython`` for DNS queries.
"""

from __future__ import annotations

import re

from kaos_web.domain.dns import lookup
from kaos_web.domain.models import (
    DnsRecordStatus,
    MailAuthMechanism,
    MailAuthRecord,
    MailAuthStatus,
    MailSecurityReport,
)

# ── Common DKIM selectors to check ──────────────────────────────────

_DKIM_SELECTORS: tuple[str, ...] = (
    "default",
    "google",
    "selector1",  # Microsoft 365
    "selector2",  # Microsoft 365
    "k1",  # Mailchimp
    "s1",
    "s2",
    "mail",
    "dkim",
    "smtp",
    "mandrill",  # Mailchimp Transactional
    "mailjet",
)


# ── SPF parsing ─────────────────────────────────────────────────────


def _parse_spf(txt_records: list[str]) -> MailAuthRecord:
    """Parse SPF from TXT records."""
    spf_record = None
    for txt in txt_records:
        if txt.startswith("v=spf1"):
            spf_record = txt
            break

    if not spf_record:
        return MailAuthRecord(
            mechanism=MailAuthMechanism.SPF,
            status=MailAuthStatus.MISSING,
            issues=["No SPF record found. Mail spoofing is possible."],
        )

    issues: list[str] = []
    policy: str | None = None

    # Extract the terminal qualifier
    if spf_record.rstrip().endswith("-all"):
        policy = "-all (hard fail)"
    elif spf_record.rstrip().endswith("~all"):
        policy = "~all (soft fail)"
    elif spf_record.rstrip().endswith("?all"):
        policy = "?all (neutral)"
        issues.append("SPF uses ?all (neutral) — effectively no enforcement.")
    elif "+all" in spf_record:
        policy = "+all (pass all)"
        issues.append("SPF uses +all — allows any server to send mail. This is dangerous.")
    elif "all" not in spf_record.lower():
        issues.append("SPF record has no 'all' mechanism — default is pass.")

    # Check for too many DNS lookups (limit is 10)
    lookup_mechanisms = re.findall(r"\b(include:|a:|mx:|ptr:|redirect=)", spf_record)
    if len(lookup_mechanisms) > 10:
        issues.append(
            f"SPF has {len(lookup_mechanisms)} lookup mechanisms (limit is 10). "
            "Exceeding this causes SPF permerror."
        )

    status = MailAuthStatus.CONFIGURED
    if "+all" in spf_record or "?all" in spf_record:
        status = MailAuthStatus.WEAK

    return MailAuthRecord(
        mechanism=MailAuthMechanism.SPF,
        status=status,
        raw_record=spf_record,
        policy=policy,
        details={"lookup_mechanisms": len(lookup_mechanisms)},
        issues=issues,
    )


# ── DMARC parsing ───────────────────────────────────────────────────


async def _fetch_dmarc(domain: str, timeout: float) -> MailAuthRecord:
    """Fetch and parse DMARC record from _dmarc.{domain}."""
    result = await lookup(f"_dmarc.{domain}", "TXT", timeout=timeout)

    if result.status != DnsRecordStatus.SUCCESS or not result.records:
        return MailAuthRecord(
            mechanism=MailAuthMechanism.DMARC,
            status=MailAuthStatus.MISSING,
            issues=["No DMARC record found at _dmarc." + domain],
        )

    dmarc_record = None
    for rec in result.records:
        val = rec.value.strip('"')
        if val.startswith("v=DMARC1"):
            dmarc_record = val
            break

    if not dmarc_record:
        return MailAuthRecord(
            mechanism=MailAuthMechanism.DMARC,
            status=MailAuthStatus.MISSING,
            issues=["TXT record at _dmarc." + domain + " does not contain v=DMARC1"],
        )

    issues: list[str] = []
    policy: str | None = None
    details: dict[str, str] = {}

    # Parse p= (policy)
    p_match = re.search(r"\bp=(\w+)", dmarc_record)
    if p_match:
        p_val = p_match.group(1).lower()
        policy = p_val
        details["policy"] = p_val
        if p_val == "none":
            issues.append("DMARC policy is 'none' — failures are only reported, not enforced.")
    else:
        issues.append("DMARC record missing 'p=' tag (policy).")

    # Parse sp= (subdomain policy)
    sp_match = re.search(r"\bsp=(\w+)", dmarc_record)
    if sp_match:
        details["subdomain_policy"] = sp_match.group(1).lower()

    # Parse rua= (aggregate reports)
    rua_match = re.search(r"\brua=([^;\s]+)", dmarc_record)
    if rua_match:
        details["aggregate_report"] = rua_match.group(1)
    else:
        issues.append("No rua= (aggregate report URI). You won't receive DMARC reports.")

    # Parse pct= (percentage)
    pct_match = re.search(r"\bpct=(\d+)", dmarc_record)
    if pct_match:
        pct = int(pct_match.group(1))
        details["pct"] = str(pct)
        if pct < 100:
            issues.append(f"DMARC pct={pct} — only {pct}% of mail is subject to policy.")

    status = MailAuthStatus.CONFIGURED
    if policy == "none":
        status = MailAuthStatus.WEAK

    return MailAuthRecord(
        mechanism=MailAuthMechanism.DMARC,
        status=status,
        raw_record=dmarc_record,
        policy=policy,
        details=details,
        issues=issues,
    )


# ── DKIM checking ───────────────────────────────────────────────────


async def _check_dkim(domain: str, timeout: float) -> MailAuthRecord:
    """Check for DKIM records at common selectors."""
    import asyncio

    found_selectors: list[str] = []

    async def _try_selector(sel: str) -> str | None:
        qname = f"{sel}._domainkey.{domain}"
        result = await lookup(qname, "TXT", timeout=timeout)
        if result.status == DnsRecordStatus.SUCCESS and result.records:
            for rec in result.records:
                val = rec.value.strip('"')
                if "v=DKIM1" in val or "p=" in val:
                    return sel
        return None

    results = await asyncio.gather(*[_try_selector(s) for s in _DKIM_SELECTORS])
    found_selectors = [s for s in results if s is not None]

    if not found_selectors:
        return MailAuthRecord(
            mechanism=MailAuthMechanism.DKIM,
            status=MailAuthStatus.MISSING,
            issues=[
                "No DKIM records found at common selectors. "
                "DKIM may still be configured with a non-standard selector."
            ],
            details={"selectors_checked": list(_DKIM_SELECTORS)},
        )

    return MailAuthRecord(
        mechanism=MailAuthMechanism.DKIM,
        status=MailAuthStatus.CONFIGURED,
        details={"selectors_found": found_selectors},
    )


# ── Composite analysis ──────────────────────────────────────────────


async def analyze_mail_security(
    domain: str,
    *,
    timeout: float = 10.0,
) -> MailSecurityReport:
    """Analyze mail authentication posture for a domain.

    Checks SPF, DKIM (common selectors), and DMARC.

    Args:
        domain: Domain to analyze.
        timeout: Per-query DNS timeout.

    Returns:
        MailSecurityReport with per-mechanism analysis and overall posture.
    """
    import asyncio

    # Fetch TXT records for SPF
    txt_result = await lookup(domain, "TXT", timeout=timeout)
    txt_values: list[str] = []
    if txt_result.status == DnsRecordStatus.SUCCESS:
        txt_values = [rec.value.strip('"') for rec in txt_result.records]

    # Run SPF (sync parse), DKIM, DMARC concurrently
    spf_record = _parse_spf(txt_values)
    dkim_record, dmarc_record = await asyncio.gather(
        _check_dkim(domain, timeout),
        _fetch_dmarc(domain, timeout),
    )

    records = [spf_record, dkim_record, dmarc_record]

    # Overall posture
    configured = sum(1 for r in records if r.status == MailAuthStatus.CONFIGURED)
    weak = sum(1 for r in records if r.status == MailAuthStatus.WEAK)

    if configured == 3:
        posture = "strong"
    elif configured + weak >= 2:
        posture = "moderate"
    elif configured + weak >= 1:
        posture = "weak"
    else:
        posture = "missing"

    return MailSecurityReport(
        domain=domain,
        records=records,
        overall_posture=posture,
    )
