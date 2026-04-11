"""Composite domain profiling — one-shot domain intelligence.

Combines DNS enumeration, WHOIS, service detection, and mail security
analysis into a single ``DomainProfile``.  Also discovers robots.txt
and sitemap URLs using existing kaos-web capabilities.
"""

from __future__ import annotations

import asyncio

from kaos_web.domain.dns import enumerate_dns
from kaos_web.domain.http import analyze_headers
from kaos_web.domain.models import DomainProfile
from kaos_web.domain.security import analyze_mail_security
from kaos_web.domain.service import detect_services
from kaos_web.domain.whois import whois_lookup


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
    tasks: dict[str, asyncio.Task[object]] = {}

    async with asyncio.TaskGroup() as tg:
        tasks["dns"] = tg.create_task(enumerate_dns(domain, timeout=timeout))
        tasks["services"] = tg.create_task(detect_services(domain, timeout=timeout))
        if include_whois:
            tasks["whois"] = tg.create_task(whois_lookup(domain, timeout=timeout))
        if include_mail_security:
            tasks["mail"] = tg.create_task(analyze_mail_security(domain, timeout=timeout))

    dns_result = tasks["dns"].result()
    services_result = tasks["services"].result()
    whois_result = tasks["whois"].result() if "whois" in tasks else None
    mail_result = tasks["mail"].result() if "mail" in tasks else None

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
        pass

    return DomainProfile(
        domain=domain,
        dns=dns_result,
        whois=whois_result,
        services=services_result,
        mail_security=mail_result,
        sitemap_urls=sitemap_urls,
        robots_txt=robots_url,
    )
