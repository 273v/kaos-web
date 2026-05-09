"""URL / host policy gate for kaos-web outbound network calls.

Wraps ``kaos_core.security`` so every kaos-web fetch site has a single
``validate_url(...)`` / ``validate_host(...)`` call to check before
opening a socket. Translates ``kaos_core.exceptions.UnsafeURLError``
into ``kaos_web.errors.UrlPolicyError`` with the 3-part agent
recovery message.

Strict by default — block private networks, loopback, link-local,
metadata services, and non-(http|https) schemes. Operators relax via
``KAOS_SECURITY_*`` env vars (see ``kaos_core.security.KaosSecuritySettings``).

Threat model: a misconfigured caller (especially the HTTP-mode MCP
server fronting multiple agents) MUST NOT be able to reach link-local
metadata services (``169.254.169.254``), loopback, RFC1918 private
networks, or block-listed schemes (``file://``, ``javascript:``).
WEB5-001 wires the gate at every URL/host-bearing fetch site so a
single misconfigured caller cannot bypass it by routing through a
sibling tool.
"""

from __future__ import annotations

import ipaddress

from kaos_core.exceptions import UnsafeURLError
from kaos_core.security import (
    KaosSecuritySettings,
    is_loopback,
    is_metadata_service,
    is_private_ip,
    validate_outbound_url,
)
from kaos_web.errors import UrlPolicyError


def validate_url(url: str, *, settings: KaosSecuritySettings | None = None) -> str:
    """Gate an outbound URL through kaos-core's SSRF check.

    Returns the URL unchanged on success; raises ``UrlPolicyError``
    with an agent-friendly recovery message on rejection.

    Note on redirects: callers that follow redirects (httpx
    ``follow_redirects=True``) gate only the original URL — the
    redirect target is NOT re-validated, which is a known gap. Closing
    it requires a connect-time hook on the HTTP client (kaos-core
    follow-up). Until then, callers concerned about redirect-based
    SSRF should disable redirect-following and re-call ``validate_url``
    on each ``Location`` response.
    """
    try:
        return validate_outbound_url(url, settings=settings)
    except UnsafeURLError as exc:
        raise UrlPolicyError(
            f"URL {url!r} blocked by KAOS security policy: {exc}. "
            "If this URL is genuinely safe (intranet host, allowlist target), "
            "set KAOS_SECURITY_ALLOWED_HOSTS to include the host (or its CIDR), "
            "or set KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS=0 / "
            "KAOS_SECURITY_BLOCK_LOOPBACK=0 / KAOS_SECURITY_BLOCK_METADATA_SERVICES=0 "
            "as appropriate. See kaos-core's KaosSecuritySettings docstring "
            "for the full policy surface.",
            url=url,
        ) from exc


def validate_host(host: str, *, settings: KaosSecuritySettings | None = None) -> str:
    """Host-only variant for tools that take ``host[:port]`` instead of a URL.

    Used by TCP / UDP / TLS / DNS / WHOIS probes in ``kaos_web.domain``.
    Same policy posture as :func:`validate_url` — rejects metadata
    services, loopback, private networks (per the configured
    ``KaosSecuritySettings`` flags). Hostname-only inputs that don't
    parse as IP literals pass the IP-range checks (no DNS resolution
    here — that would race the actual connect).

    Allowlist matches (exact hostname, suffix, or CIDR) bypass the
    IP-range gates exactly as ``validate_outbound_url`` does for full
    URLs.
    """
    if settings is None:
        settings = KaosSecuritySettings()
    # Allowlist short-circuit: an exact hostname / suffix / CIDR match
    # bypasses the IP-range gates.
    for entry in settings.allowed_hosts:
        if _host_matches_allow_entry(host, entry):
            return host
    if settings.block_metadata_services and is_metadata_service(host):
        raise UrlPolicyError(
            f"Host {host!r} is a cloud metadata-service endpoint; blocked by "
            "KAOS_SECURITY_BLOCK_METADATA_SERVICES. Set the env var to 0 or "
            "add the host to KAOS_SECURITY_ALLOWED_HOSTS to bypass.",
            url=host,
        )
    if settings.block_loopback and is_loopback(host):
        raise UrlPolicyError(
            f"Host {host!r} is a loopback address; blocked by "
            "KAOS_SECURITY_BLOCK_LOOPBACK. Set the env var to 0 or add the "
            "host to KAOS_SECURITY_ALLOWED_HOSTS to bypass.",
            url=host,
        )
    if settings.block_private_networks and is_private_ip(host):
        raise UrlPolicyError(
            f"Host {host!r} is in a private/link-local IP range; blocked by "
            "KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS. Set the env var to 0 or "
            "add the host (or its CIDR) to KAOS_SECURITY_ALLOWED_HOSTS to bypass.",
            url=host,
        )
    return host


def _host_matches_allow_entry(host: str, entry: str) -> bool:
    """Allowlist matcher mirroring ``kaos_core.security.url._host_matches_allow_entry``.

    Three entry shapes (same as kaos-core):
      * exact hostname or IP literal
      * suffix hostname (``.example.com`` matches subdomains)
      * CIDR network (``10.0.0.0/24``)

    TODO(kaos-core): the upstream helper is module-private. Once kaos-
    core exposes ``_host_matches_allow_entry`` as public API, drop this
    duplicate and import from there. Tracked as a follow-up to WEB5-001.
    """
    entry = entry.strip()
    if not entry:
        return False
    if entry == host:
        return True
    if entry.startswith(".") and host.endswith(entry):
        return True
    try:
        net = ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr in net
