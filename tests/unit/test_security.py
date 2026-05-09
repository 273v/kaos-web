"""Tests for ``kaos_web.security`` — URL/host SSRF gate (WEB5-001).

The gate wraps ``kaos_core.security`` so every kaos-web fetch site has
a uniform pre-flight check that blocks link-local cloud-metadata
endpoints, loopback, RFC1918 private ranges, and non-(http|https)
schemes by default. These tests cover the policy surface of
``validate_url`` / ``validate_host`` independently of the wiring at
each fetch site (those are covered by per-site regression tests in
``test_http_client.py``, ``test_browser_client.py``, ``domain/test_*.py``,
and ``test_domain_tools.py``).

Regression intent: WEB5-001 — a misconfigured caller (especially the
HTTP-mode MCP server fronting multiple agents) MUST NOT be able to
fetch ``169.254.169.254/latest/meta-data/`` or pivot through
loopback / RFC1918 to internal services.
"""

from __future__ import annotations

import pytest

from kaos_core.security import KaosSecuritySettings
from kaos_web.errors import UrlPolicyError
from kaos_web.security import validate_host, validate_url

# ── validate_url: happy path ────────────────────────────────────────


class TestValidateUrlHappyPath:
    """Public URLs and explicit allowlist entries pass through untouched."""

    def test_public_https_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Strict-default policy still admits a normal public URL."""
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_LOOPBACK", "1")
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_METADATA_SERVICES", "1")
        url = "https://example.com/page?x=1"
        assert validate_url(url) == url

    def test_public_http_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        url = "http://example.com/"
        assert validate_url(url) == url


# ── validate_url: blocked classes ──────────────────────────────────


class TestValidateUrlBlocked:
    """Each blocked class fires under the strict default policy."""

    def test_blocks_rfc1918_private(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """10.0.0.0/8 — classic SSRF pivot target."""
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        with pytest.raises(UrlPolicyError) as info:
            validate_url("http://10.0.0.1/")
        assert "10.0.0.1" in str(info.value)
        # Recovery hint must point the operator at the env var.
        assert "KAOS_SECURITY_" in str(info.value)

    def test_blocks_loopback_v4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_LOOPBACK", "1")
        with pytest.raises(UrlPolicyError):
            validate_url("http://127.0.0.1/")

    def test_blocks_loopback_v6(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_LOOPBACK", "1")
        with pytest.raises(UrlPolicyError):
            validate_url("http://[::1]/")

    def test_blocks_aws_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ground-zero SSRF target — AWS / Azure / GCP IMDS."""
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_METADATA_SERVICES", "1")
        with pytest.raises(UrlPolicyError) as info:
            validate_url("http://169.254.169.254/latest/meta-data/")
        assert "169.254.169.254" in str(info.value)

    def test_blocks_ecs_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AWS ECS task metadata — second of the well-known IMDS endpoints."""
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_METADATA_SERVICES", "1")
        with pytest.raises(UrlPolicyError):
            validate_url("http://169.254.170.2/v2/credentials")

    def test_blocks_link_local_v4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """169.254.0.0/16 link-local minus IMDS still blocks via private gate."""
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        with pytest.raises(UrlPolicyError):
            validate_url("http://169.254.1.1/")

    def test_blocks_file_scheme(self) -> None:
        """``file://`` is rejected by the XSS-shape blocklist regardless of host."""
        with pytest.raises(UrlPolicyError):
            validate_url("file:///etc/passwd")

    def test_blocks_javascript_scheme(self) -> None:
        with pytest.raises(UrlPolicyError):
            validate_url("javascript:alert(1)")

    def test_blocks_data_scheme(self) -> None:
        with pytest.raises(UrlPolicyError):
            validate_url("data:text/html,<script>alert(1)</script>")

    def test_blocks_unknown_scheme(self) -> None:
        """``ftp://`` is not in the default ``allowed_schemes`` tuple."""
        with pytest.raises(UrlPolicyError):
            validate_url("ftp://example.com/")


# ── validate_url: allowlist + per-call relaxation ──────────────────


class TestValidateUrlAllowlist:
    """Allowlist entries (env or per-call) bypass the IP-range gates."""

    def test_env_allowed_hosts_exact_bypass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``KAOS_SECURITY_ALLOWED_HOSTS`` admits otherwise-blocked private hosts.

        pydantic-settings expects JSON for list-typed env vars, so the
        operator-facing form is ``["10.0.0.5"]`` not ``10.0.0.5``.
        """
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        monkeypatch.setenv("KAOS_SECURITY_ALLOWED_HOSTS", '["10.0.0.5"]')
        url = "http://10.0.0.5/internal"
        assert validate_url(url) == url

    def test_env_allowed_hosts_cidr_bypass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CIDR-shape entries (``10.0.0.0/24``) match by IP membership."""
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        monkeypatch.setenv("KAOS_SECURITY_ALLOWED_HOSTS", '["10.0.0.0/24"]')
        assert validate_url("http://10.0.0.42/") == "http://10.0.0.42/"

    def test_per_call_settings_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Passing a custom settings instance bypasses env-derived defaults."""
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        relaxed = KaosSecuritySettings(allowed_hosts=["10.0.0.0/8"])
        assert validate_url("http://10.5.5.5/", settings=relaxed) == "http://10.5.5.5/"

    def test_relaxing_one_field_keeps_others(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Allowing private networks does NOT also relax the metadata block."""
        relaxed = KaosSecuritySettings(block_private_networks=False)
        # Private now passes:
        assert validate_url("http://10.0.0.1/", settings=relaxed) == "http://10.0.0.1/"
        # Metadata is still blocked (different field):
        with pytest.raises(UrlPolicyError):
            validate_url("http://169.254.169.254/latest/meta-data/", settings=relaxed)


# ── validate_host: blocked + allowlist ─────────────────────────────


class TestValidateHostBlocked:
    """Host-only inputs (TCP / UDP / TLS / DNS / WHOIS probes)."""

    def test_blocks_metadata_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_METADATA_SERVICES", "1")
        with pytest.raises(UrlPolicyError) as info:
            validate_host("169.254.169.254")
        assert "metadata-service" in str(info.value)

    def test_blocks_loopback_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_LOOPBACK", "1")
        with pytest.raises(UrlPolicyError):
            validate_host("127.0.0.1")

    def test_blocks_private_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        with pytest.raises(UrlPolicyError):
            validate_host("10.0.0.5")

    def test_blocks_v6_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(UrlPolicyError):
            validate_host("::1")


class TestValidateHostHostnamePassThrough:
    """Hostnames (no IP literal) pass — no DNS resolution at this layer."""

    def test_plain_hostname_passes(self) -> None:
        """``example.com`` doesn't parse as an IP literal, so the IP-range
        gates can't fire. The actual SSRF defense for hostname-based
        rebinding requires a connect-time hook (out of scope for this
        commit).
        """
        assert validate_host("example.com") == "example.com"

    def test_subdomain_passes(self) -> None:
        assert validate_host("api.example.com") == "api.example.com"


class TestValidateHostAllowlist:
    """Allowlist matching mirrors ``validate_url`` (exact / suffix / CIDR)."""

    def test_exact_hostname_bypass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SECURITY_ALLOWED_HOSTS", '["10.0.0.5"]')
        assert validate_host("10.0.0.5") == "10.0.0.5"

    def test_cidr_bypass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAOS_SECURITY_ALLOWED_HOSTS", '["10.0.0.0/24"]')
        assert validate_host("10.0.0.42") == "10.0.0.42"

    def test_suffix_match_bypass(self) -> None:
        """A ``.example.com`` suffix entry matches subdomain hostnames."""
        relaxed = KaosSecuritySettings(allowed_hosts=[".example.com"])
        # Pure hostname test — pick something that would otherwise pass anyway,
        # so this asserts the suffix-match code path activates rather than
        # changing the outcome.
        assert validate_host("api.example.com", settings=relaxed) == "api.example.com"

    def test_per_call_settings_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Passing relaxed settings bypasses the env-derived block."""
        monkeypatch.setenv("KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS", "1")
        relaxed = KaosSecuritySettings(block_private_networks=False)
        assert validate_host("10.0.0.5", settings=relaxed) == "10.0.0.5"
