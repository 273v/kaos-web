"""Composite domain profiling — one-shot domain intelligence.

Combines DNS enumeration, WHOIS, service detection, and mail security
analysis into a single ``DomainProfile``.  Also discovers robots.txt
and sitemap URLs using existing kaos-web capabilities.
"""

from __future__ import annotations

import asyncio

from kaos_core.logging import get_logger
from kaos_web.domain.dns import enumerate_dns
from kaos_web.domain.http import analyze_headers
from kaos_web.domain.models import (
    DnsProfile,
    DomainProfile,
    MailSecurityReport,
    ServiceProfile,
    WhoisRecord,
)
from kaos_web.domain.security import analyze_mail_security
from kaos_web.domain.service import detect_services
from kaos_web.domain.whois import whois_lookup

logger = get_logger(__name__)


async def profile_domain(
    domain: str,
    *,
    timeout: float = 15.0,
    include_whois: bool = True,
    include_mail_security: bool = True,
) -> DomainProfile:
    """Build a comprehensive domain profile.

    Runs DNS enumeration, WHOIS, service detection, and mail security
    analysis concurrently.  Discovers robots.txt and sitemap URLs from
    the HTTP response.

    Args:
        domain: Domain to profile.
        timeout: Per-operation timeout.
        include_whois: Include WHOIS lookup.
        include_mail_security: Include SPF/DKIM/DMARC analysis.

    Returns:
        DomainProfile with all gathered intelligence.
    """
    dns_task: asyncio.Task[DnsProfile] | None = None
    services_task: asyncio.Task[ServiceProfile] | None = None
    whois_task: asyncio.Task[WhoisRecord] | None = None
    mail_task: asyncio.Task[MailSecurityReport] | None = None

    async with asyncio.TaskGroup() as tg:
        dns_task = tg.create_task(enumerate_dns(domain, timeout=timeout))
        services_task = tg.create_task(detect_services(domain, timeout=timeout))
        if include_whois:
            whois_task = tg.create_task(whois_lookup(domain, timeout=timeout))
        if include_mail_security:
            mail_task = tg.create_task(analyze_mail_security(domain, timeout=timeout))

    assert dns_task is not None  # always set above
    assert services_task is not None  # always set above
    dns_result = dns_task.result()
    services_result = services_task.result()
    whois_result = whois_task.result() if whois_task is not None else None
    mail_result = mail_task.result() if mail_task is not None else None

    # Discover robots.txt and sitemaps from the HTTPS endpoint
    sitemap_urls: list[str] = []
    robots_url: str | None = None
    try:
        robots_resp = await analyze_headers(
            f"https://{domain}/robots.txt",
            timeout=timeout,
            follow_redirects=True,
        )
        if robots_resp.status_code == 200:
            robots_url = f"https://{domain}/robots.txt"
    except Exception:
        logger.debug("robots.txt check failed for %s", domain, exc_info=True)

    return DomainProfile(
        domain=domain,
        dns=dns_result,
        whois=whois_result,
        services=services_result,
        mail_security=mail_result,
        sitemap_urls=sitemap_urls,
        robots_txt=robots_url,
    )
