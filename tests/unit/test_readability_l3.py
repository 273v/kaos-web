"""Tests for Level 3 learned readability model.

Tests content_scope boundary values, extraction quality on fixtures,
and integration with html_to_document.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaos_web.extract.readability_l3 import extract_content_l3

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
READABILITY_FIXTURES = FIXTURES / "readability"


class TestContentScopeBoundaries:
    """Verify content_scope=0.0, 0.5, 1.0 all produce sensible results."""

    @pytest.fixture
    def article_html(self) -> str:
        return (FIXTURES / "article.html").read_text()

    def test_scope_zero_strict(self, article_html: str):
        """scope=0.0 (strict) should extract content, not the full body."""
        result = extract_content_l3(article_html, content_scope=0.0)
        assert result is not None
        text = result.text_content() or ""
        # Strict mode should still extract the article
        assert "Main Article" in text
        # Should NOT include navigation/sidebar boilerplate
        assert "Home" not in text or "Privacy" not in text

    def test_scope_half_default(self, article_html: str):
        """scope=0.5 (default) extracts the article content."""
        result = extract_content_l3(article_html, content_scope=0.5)
        assert result is not None
        text = result.text_content() or ""
        assert "Main Article" in text
        assert "Section One" in text

    def test_scope_one_permissive(self, article_html: str):
        """scope=1.0 (permissive) extracts more content without error."""
        result = extract_content_l3(article_html, content_scope=1.0)
        assert result is not None
        text = result.text_content() or ""
        # Should still have article content
        assert "Main Article" in text

    def test_scope_strict_has_less_or_equal_content(self, article_html: str):
        """Strict scope should extract <= permissive scope content."""
        strict = extract_content_l3(article_html, content_scope=0.0)
        permissive = extract_content_l3(article_html, content_scope=1.0)
        assert strict is not None
        assert permissive is not None
        strict_len = len(strict.text_content() or "")
        permissive_len = len(permissive.text_content() or "")
        assert strict_len <= permissive_len

    def test_threshold_clamped_below_one(self, article_html: str):
        """scope=0.0 should not fall back to full body due to threshold=1.0."""
        result = extract_content_l3(article_html, content_scope=0.0)
        assert result is not None
        text = result.text_content() or ""
        # Full body includes nav items like "Home", "About", "Privacy"
        # Strict extraction should not include all of them
        nav_items = sum(1 for term in ["Home", "About", "Privacy"] if term in text)
        assert nav_items < 3, "Strict mode should not include full nav bar"


class TestContentScopeActuallyVaries:
    """Verify content_scope produces different output on multi-section pages."""

    def test_team_directory_varies_at_three_levels(self):
        """team_directory_cards.html has multiple content regions."""
        html = (READABILITY_FIXTURES / "team_directory_cards.html").read_text()
        strict = extract_content_l3(html, content_scope=0.0)
        default = extract_content_l3(html, content_scope=0.5)
        permissive = extract_content_l3(html, content_scope=1.0)
        assert strict is not None and default is not None and permissive is not None

        s = len(strict.text_content() or "")
        d = len(default.text_content() or "")
        p = len(permissive.text_content() or "")

        # Strict < default < permissive
        assert s < d < p, f"Expected monotonic increase: strict={s}, default={d}, permissive={p}"

    def test_directory_listing_strict_vs_permissive(self):
        """directory_listing.html should differ between strict and permissive."""
        html = (READABILITY_FIXTURES / "directory_listing.html").read_text()
        strict = extract_content_l3(html, content_scope=0.0)
        permissive = extract_content_l3(html, content_scope=1.0)
        assert strict is not None and permissive is not None

        s = len(strict.text_content() or "")
        p = len(permissive.text_content() or "")
        assert s < p, f"Expected permissive > strict: strict={s}, permissive={p}"

    def test_search_results_strict_vs_permissive(self):
        """search_results_page.html should differ between strict and permissive."""
        html = (READABILITY_FIXTURES / "search_results_page.html").read_text()
        strict = extract_content_l3(html, content_scope=0.0)
        permissive = extract_content_l3(html, content_scope=1.0)
        assert strict is not None and permissive is not None

        s = len(strict.text_content() or "")
        p = len(permissive.text_content() or "")
        assert s < p, f"Expected permissive > strict: strict={s}, permissive={p}"

    def test_multi_section_landing_varies_by_scope(self):
        """multi_section_landing.html should broaden as scope increases."""
        html = (READABILITY_FIXTURES / "multi_section_landing.html").read_text()
        strict = extract_content_l3(html, content_scope=0.0)
        default = extract_content_l3(html, content_scope=0.5)
        permissive = extract_content_l3(html, content_scope=1.0)
        assert strict is not None and default is not None and permissive is not None

        strict_text = strict.text_content() or ""
        default_text = default.text_content() or ""
        permissive_text = permissive.text_content() or ""

        assert "FTC updates non-compete enforcement priorities" in strict_text
        assert "Regulatory Insights" not in strict_text
        assert "Regulatory Insights" in default_text
        assert "Popular Resources" not in default_text
        assert "Popular Resources" in permissive_text

        s = len(strict_text)
        d = len(default_text)
        p = len(permissive_text)
        assert s < d < p, f"Expected monotonic increase: strict={s}, default={d}, permissive={p}"

    def test_scope_monotonic_on_all_fixtures(self):
        """Across all fixtures, strict <= default <= permissive."""
        fixtures = [
            FIXTURES / "article.html",
            READABILITY_FIXTURES / "directory_listing.html",
            READABILITY_FIXTURES / "search_results_page.html",
            READABILITY_FIXTURES / "team_directory_cards.html",
            READABILITY_FIXTURES / "multi_section_landing.html",
        ]
        for path in fixtures:
            if not path.exists():
                continue
            html = path.read_text()
            s = extract_content_l3(html, content_scope=0.0)
            d = extract_content_l3(html, content_scope=0.5)
            p = extract_content_l3(html, content_scope=1.0)
            s_len = len(s.text_content() or "") if s is not None else 0
            d_len = len(d.text_content() or "") if d is not None else 0
            p_len = len(p.text_content() or "") if p is not None else 0
            assert s_len <= d_len <= p_len, (
                f"{path.name}: not monotonic: strict={s_len}, default={d_len}, permissive={p_len}"
            )


class TestL3ExtractionQuality:
    """Verify L3 extracts the right content from diverse fixtures."""

    def test_article_extracts_article(self):
        html = (FIXTURES / "article.html").read_text()
        result = extract_content_l3(html)
        assert result is not None
        text = result.text_content() or ""
        assert "Main Article" in text
        assert "Section One" in text

    def test_books_extracts_product(self):
        html = (FIXTURES / "books_toscrape.html").read_text()
        result = extract_content_l3(html)
        assert result is not None
        text = result.text_content() or ""
        assert "Light in the Attic" in text

    @pytest.mark.skipif(
        not (READABILITY_FIXTURES / "directory_listing.html").exists(),
        reason="Fixture not found",
    )
    def test_directory_extracts_listings(self):
        html = (READABILITY_FIXTURES / "directory_listing.html").read_text()
        result = extract_content_l3(html)
        assert result is not None
        text = result.text_content() or ""
        # Should extract directory entries, not nav/sidebar
        assert "Jane Doe" in text or "John Smith" in text or len(text) > 100

    @pytest.mark.skipif(
        not (READABILITY_FIXTURES / "search_results_page.html").exists(),
        reason="Fixture not found",
    )
    def test_search_results_extracts_results(self):
        html = (READABILITY_FIXTURES / "search_results_page.html").read_text()
        result = extract_content_l3(html)
        assert result is not None
        text = result.text_content() or ""
        # Should extract search results, not the filter sidebar
        assert len(text) > 50

    def test_empty_html_returns_none(self):
        assert extract_content_l3("") is None
        assert extract_content_l3("   ") is None

    def test_minimal_html_returns_body_or_none(self):
        result = extract_content_l3("<html><body><p>Hello</p></body></html>")
        # Very short page — may return the body or None depending on scoring
        if result is not None:
            assert "Hello" in (result.text_content() or "")


class TestHtmlToDocumentIntegration:
    """Verify content_scope threads through html_to_document."""

    def test_content_scope_default(self):
        from kaos_web.extract import html_to_document

        html = (FIXTURES / "article.html").read_text()
        doc = html_to_document(html, url="https://example.com/article")
        assert len(doc.body) > 0

    def test_content_scope_passed_to_l3(self):
        """content_scope must survive through html_to_document into the AST."""
        from kaos_content.serializers.text import serialize_text
        from kaos_web.extract import html_to_document

        # Use multi_section_landing which is known to vary by scope
        html = (READABILITY_FIXTURES / "multi_section_landing.html").read_text()
        doc_strict = html_to_document(html, url="https://example.com/ms", content_scope=0.0)
        doc_permissive = html_to_document(html, url="https://example.com/ms", content_scope=1.0)

        assert len(doc_strict.body) > 0
        assert len(doc_permissive.body) > 0

        strict_text = serialize_text(doc_strict)
        permissive_text = serialize_text(doc_permissive)
        assert len(strict_text) < len(permissive_text), (
            f"html_to_document must honor content_scope: strict={len(strict_text)}, "
            f"permissive={len(permissive_text)}"
        )

    def test_extract_content_false_ignores_scope(self):
        from kaos_web.extract import html_to_document

        html = (FIXTURES / "article.html").read_text()
        doc = html_to_document(html, url="https://example.com/article", extract_content=False)
        # raw mode returns full body regardless of content_scope
        assert len(doc.body) > 0


class TestPublicApiConsistency:
    """Verify package-level extract_content matches html_to_document behavior."""

    def test_extract_content_is_l3(self):
        from kaos_web.extract import extract_content, extract_content_l3

        assert extract_content is extract_content_l3

    def test_heuristic_still_available(self):
        from kaos_web.extract import extract_content_heuristic

        html = (FIXTURES / "article.html").read_text()
        result = extract_content_heuristic(html)
        assert result is not None
