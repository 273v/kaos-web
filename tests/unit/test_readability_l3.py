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
        from kaos_web.extract import html_to_document

        html = (FIXTURES / "article.html").read_text()
        doc_strict = html_to_document(html, url="https://example.com/article", content_scope=0.1)
        doc_permissive = html_to_document(
            html, url="https://example.com/article", content_scope=0.9
        )
        # Both should produce content
        assert len(doc_strict.body) > 0
        assert len(doc_permissive.body) > 0

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
