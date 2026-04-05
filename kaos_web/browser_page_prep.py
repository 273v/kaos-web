"""Browser page preparation utilities.

Handles cookie consent banner dismissal for browser-based content extraction.
Only targets well-known Consent Management Platforms (CMPs) with stable,
documented selectors. No heuristic or generic popup detection.

Each CMP entry is tested against major deployments and uses official element IDs
where possible. The list is intentionally conservative — better to miss an
unknown banner than to break a page by clicking the wrong thing.

Detection uses a single ``page.evaluate()`` call that checks all CMP selectors
synchronously in the browser. Cost is ~5ms regardless of how many CMPs are
registered, so adding entries does not degrade no-banner performance.

Usage::

    from kaos_web.browser_page_prep import dismiss_cookie_banners

    # After page.goto(), before page.content():
    dismissed = await dismiss_cookie_banners(page)
    # dismissed = ["OneTrust"] or []
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kaos_core.logging import get_logger

if TYPE_CHECKING:
    pass  # Playwright Page type is only used at runtime

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Known Consent Management Platforms
# ---------------------------------------------------------------------------
# Each CMP has:
#   detect  — CSS selector that identifies the banner element
#   dismiss — CSS selector for the "accept" / "close" button
#   name    — Human-readable CMP name (for logging)
#   note    — Brief documentation of the platform
#
# Selectors use official element IDs where possible (stable across deployments).
# We click "accept all" rather than "reject" because:
#   1. Accept buttons are more reliably present across deployments
#   2. We're a headless scraper — cookie preferences don't persist
#   3. Some CMPs hide content until cookies are accepted
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KnownCMP:
    """A known Consent Management Platform with detection and dismissal selectors."""

    name: str
    detect: str
    dismiss: str
    note: str = ""


KNOWN_CMPS: tuple[KnownCMP, ...] = (
    KnownCMP(
        name="OneTrust",
        detect="#onetrust-banner-sdk",
        dismiss="#onetrust-accept-btn-handler",
        note="Largest CMP globally. Banner ID and button ID are stable across deployments.",
    ),
    KnownCMP(
        name="CookieBot",
        detect="#CybotCookiebotDialog",
        dismiss="#CybotCookiebotDialogBodyButtonAccept",
        note="Second most popular CMP. Uses Cybot prefix consistently.",
    ),
    KnownCMP(
        name="TrustArc",
        detect="#truste-consent-track",
        dismiss="#truste-consent-button",
        note="Common on enterprise/legal sites. Uses truste- prefix.",
    ),
    KnownCMP(
        name="Quantcast Choice",
        detect=".qc-cmp2-summary-buttons",
        dismiss=".qc-cmp2-summary-buttons button[mode='primary']",
        note="Common on media sites. Uses qc-cmp2 class prefix.",
    ),
    KnownCMP(
        name="Complianz",
        detect=".cmplz-cookiebanner",
        dismiss=".cmplz-btn.cmplz-accept",
        note="Popular WordPress cookie consent plugin.",
    ),
    KnownCMP(
        name="Osano",
        detect=".osano-cm-dialog",
        dismiss=".osano-cm-accept-all",
        note="US-focused CMP with consistent class naming.",
    ),
    KnownCMP(
        name="Didomi",
        detect="#didomi-notice",
        dismiss="#didomi-notice-agree-button",
        note="European CMP. Uses didomi- ID prefix.",
    ),
    KnownCMP(
        name="Termly",
        detect="[data-tid='banner-accept']",
        dismiss="[data-tid='banner-accept']",
        note="Small/medium business CMP. Detect and dismiss are the same element.",
    ),
)

# JavaScript executed in a single page.evaluate() call. Checks all detect
# selectors synchronously — returns the index of the first visible CMP, or -1.
# Visibility check: element exists, is not display:none/visibility:hidden,
# and has non-zero dimensions.
_DETECT_JS = """
(selectors) => {
    for (let i = 0; i < selectors.length; i++) {
        const el = document.querySelector(selectors[i]);
        if (!el) continue;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        if (el.offsetWidth === 0 && el.offsetHeight === 0) continue;
        return i;
    }
    return -1;
}
"""


async def dismiss_cookie_banners(
    page: Any,
    *,
    timeout_ms: int = 2000,
    cmps: tuple[KnownCMP, ...] | None = None,
) -> list[str]:
    """Detect and dismiss known cookie consent banners on a Playwright page.

    Detection runs in a single ``page.evaluate()`` call that checks all CMP
    selectors synchronously in the browser (~5ms). Only if a banner is found
    does a second round-trip happen to click the dismiss button. No-banner
    pages pay negligible cost.

    Args:
        page: Playwright Page object (must already be navigated).
        timeout_ms: Maximum time in ms to wait for the dismiss click.
        cmps: Override the CMP list. Defaults to ``KNOWN_CMPS``.

    Returns:
        List of CMP names that were successfully dismissed.
    """
    if cmps is None:
        cmps = KNOWN_CMPS

    if not cmps:
        return []

    # Phase 1: single JS call to find which CMP (if any) is visible.
    detect_selectors = [cmp.detect for cmp in cmps]
    try:
        matched_index: int = await page.evaluate(_DETECT_JS, detect_selectors)
    except Exception:
        logger.debug("Cookie banner detection failed (page may be closed)")
        return []

    if matched_index < 0:
        return []

    cmp = cmps[matched_index]
    logger.info("Cookie banner detected: %s (%s)", cmp.name, cmp.detect)

    # Phase 2: click the dismiss button for the matched CMP.
    try:
        button = page.locator(cmp.dismiss).first
        await button.click(timeout=timeout_ms)
        logger.info("Cookie banner dismissed: %s (%s)", cmp.name, cmp.dismiss)
        return [cmp.name]
    except Exception:
        logger.debug("Cookie banner dismiss click failed: %s", cmp.name)
        return []
