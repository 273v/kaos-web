"""Live end-to-end test for the experimental rustwright browser backend.

Drives a real fetch through ``BrowserClient(backend="rustwright")`` — the
native-Rust CDP engine — to prove the Playwright-compatible drop-in works
end to end, not just in the selection unit tests.

Gated: skips when the ``[browser-rust]`` extra isn't installed, or when no
Chromium executable can be found (rustwright ships no browser). CI that runs
``playwright install chromium`` satisfies both.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kaos_web.clients.browser import BrowserClient
from kaos_web.clients.config import BrowserClientConfig
from kaos_web.models import WebRequest

pytest.importorskip("rustwright", reason="requires the [browser-rust] extra")


def _find_chromium() -> str | None:
    """Locate a Chromium/Chrome executable for rustwright (which bundles none)."""
    env = os.environ.get("RUSTWRIGHT_CHROMIUM")
    if env:
        return env
    ms_playwright = Path.home() / ".cache" / "ms-playwright"
    # Playwright's chromium dir is chrome-linux on some builds, chrome-linux64 on others.
    bundled = sorted(ms_playwright.glob("chromium*/chrome-linux*/chrome"))
    if bundled:
        return str(bundled[0])
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


_CHROMIUM = _find_chromium()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_CHROMIUM is None, reason="no Chromium executable for rustwright"),
]


@pytest.mark.asyncio
async def test_rustwright_backend_fetches_a_real_page() -> None:
    cfg = BrowserClientConfig(
        backend="rustwright",
        headless=True,
        executable_path=_CHROMIUM,
    )
    async with BrowserClient(cfg) as client:
        resp = await client.fetch(WebRequest(url="https://example.com"))

    assert resp.status_code == 200
    assert resp.title is not None
    assert "example" in resp.title.lower()
    assert "Example Domain" in (resp.html or "")
