"""Regression test for audit-04: README count drift prevention.

audit-04/kaos-web.md flagged that README.md:118 said
``kaos_web.__all__`` had 8 symbols and 4 ``register_*_tools()``
entry points. Runtime inspection reported 10 and 5 respectively.

This test pins the exact count and the exact symbol set that the
README's Maturity row enumerates, so future drift fails this gate
instead of silently making the README false. Pairs with the README
copy update on the same branch — code and prose now have a single
source of truth.
"""

from __future__ import annotations

import kaos_web

# README.md Maturity row enumerates the full kaos_web.__all__ set
# inline. This test is the inverse pin: if the code-side __all__
# drifts, this fails and forces the docs update to ride along.
_EXPECTED_PUBLIC_API: frozenset[str] = frozenset(
    {
        "__version__",
        "extract_content",
        "extract_metadata",
        "html_to_document",
        "parse_html",
        "register_browser_tools",
        "register_crawl_tools",
        "register_domain_tools",
        "register_web_all_tools",
        "register_web_tools",
    }
)


def test_dunder_all_matches_readme_enumerated_set() -> None:
    """`kaos_web.__all__` must equal the set enumerated in README.md:118.

    Pre-2026-05-23 the README said 8 symbols / 4 register_*_tools
    entry points. Runtime returned 10 and 5. The README is now
    correct; this test keeps it that way.
    """
    actual = frozenset(kaos_web.__all__)
    assert actual == _EXPECTED_PUBLIC_API, (
        "audit-04 README drift regression: kaos_web.__all__ moved.\n"
        f"  expected: {sorted(_EXPECTED_PUBLIC_API)}\n"
        f"  actual:   {sorted(actual)}\n"
        f"  added:    {sorted(actual - _EXPECTED_PUBLIC_API)}\n"
        f"  removed:  {sorted(_EXPECTED_PUBLIC_API - actual)}\n"
        "Update README.md:118 Maturity row + CHANGELOG before changing __all__."
    )


def test_register_star_tools_count_matches_readme() -> None:
    """README.md:118 says 5 register_*_tools entry points. Pin it.

    Counted dynamically from `__all__` so future register_X_tools
    additions surface here rather than in a downstream consumer's
    broken auto-registration loop.
    """
    register_star = {
        n for n in kaos_web.__all__ if n.startswith("register_") and n.endswith("_tools")
    }
    assert len(register_star) == 5, (
        "audit-04 README drift regression: register_*_tools count moved "
        f"from the documented 5 (now {len(register_star)}: {sorted(register_star)}). "
        "Update README.md:118 Maturity row + CHANGELOG before changing this."
    )


def test_extraction_helpers_count_matches_readme() -> None:
    """README.md:118 lists 3 extraction helpers.

    The Maturity row enumerates `extract_content`, `extract_metadata`,
    `html_to_document` as "the three extraction helpers". Pin that.
    """
    extraction = {"extract_content", "extract_metadata", "html_to_document"}
    assert extraction.issubset(set(kaos_web.__all__)), (
        "audit-04 README drift regression: extraction helpers missing.\n"
        f"  documented: {sorted(extraction)}\n"
        f"  missing:    {sorted(extraction - set(kaos_web.__all__))}"
    )
