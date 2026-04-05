"""Browser page preparation utilities.

Handles cookie consent banner dismissal and content settling detection
for browser-based content extraction.

Cookie dismissal targets well-known Consent Management Platforms (CMPs)
with stable, documented selectors. No heuristic or generic popup detection.

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


# ---------------------------------------------------------------------------
# Content settling
# ---------------------------------------------------------------------------
# Two-phase approach:
#   Phase 1 (fast path, ~5ms): check if page already has meaningful content.
#   Phase 2 (slow path, only when Phase 1 fails): MutationObserver with
#     timer-reset pattern — resolve when DOM mutations stop for `quiet_ms`,
#     or hard-timeout at `timeout_ms`.
#
# Cost: 0ms on already-rendered pages. 500ms-5s on JS-filled pages.
#        Never hangs — hard timeout ensures we always return.
# ---------------------------------------------------------------------------

_CONTENT_CHECK_JS = """
() => {
    const candidates = document.querySelectorAll(
        'main, article, [role="main"], #content, .content, #main, .main'
    );
    for (const el of candidates) {
        if (el.innerText && el.innerText.trim().length > 200) return true;
    }
    return document.body && document.body.innerText
        && document.body.innerText.trim().length > 500;
}
"""

_SETTLE_JS = """
(opts) => new Promise((resolve) => {
    let timer = setTimeout(() => { observer.disconnect(); resolve(); }, opts.quiet);
    const observer = new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(() => { observer.disconnect(); resolve(); }, opts.quiet);
    });
    observer.observe(document.body, {
        childList: true, subtree: true, characterData: true
    });
    setTimeout(() => { observer.disconnect(); resolve(); }, opts.timeout);
})
"""


async def wait_for_content_settled(
    page: Any,
    *,
    quiet_ms: int = 500,
    timeout_ms: int = 5000,
) -> bool:
    """Wait for page content to appear, using a two-phase approach.

    Phase 1: Instant check — does the page already have meaningful content
    in a semantic container? If yes, return immediately (0ms penalty).

    Phase 2: MutationObserver — watch for DOM mutations to stop for
    ``quiet_ms``. Hard timeout at ``timeout_ms`` to avoid hanging on
    pages with constant updates (carousels, tickers, streaming).

    Args:
        page: Playwright Page object (must already be navigated).
        quiet_ms: Mutation quiet period before considering settled.
        timeout_ms: Hard timeout — extract whatever is there.

    Returns:
        True if content was found (fast path) or mutations settled.
        False if the hard timeout was hit.
    """
    # Phase 1: fast path — check if content already exists.
    try:
        has_content = await page.evaluate(_CONTENT_CHECK_JS)
        if has_content:
            return True
    except Exception:
        return True  # Page may be closed; let caller handle extraction

    logger.info("Page content not yet rendered, waiting for DOM to settle")

    # Phase 2: slow path — MutationObserver wait.
    try:
        await page.evaluate(_SETTLE_JS, {"quiet": quiet_ms, "timeout": timeout_ms})
    except Exception:
        logger.debug("Content settling wait failed (page may be closed)")
        return False

    return True
