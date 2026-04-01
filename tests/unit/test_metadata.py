"""Tests for metadata extraction."""

from __future__ import annotations

from pathlib import Path

from kaos_web.extract.metadata import extract_metadata

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestMetadataExtraction:
    def test_title_from_tag(self):
        html = "<html><head><title>Page Title</title></head><body></body></html>"
        meta = extract_metadata(html)
        assert meta.title == "Page Title"

    def test_opengraph_title_preferred(self):
        html = """
        <html><head>
            <title>HTML Title</title>
            <meta property="og:title" content="OG Title">
        </head><body></body></html>
        """
        meta = extract_metadata(html)
        assert meta.title == "OG Title"

    def test_opengraph_properties(self):
        html = """
        <html><head>
            <meta property="og:title" content="OG Title">
            <meta property="og:description" content="OG Desc">
            <meta property="og:url" content="https://example.com/og">
            <meta property="og:site_name" content="Example">
            <meta property="og:image" content="https://example.com/img.jpg">
        </head><body></body></html>
        """
        meta = extract_metadata(html)
        assert meta.title == "OG Title"
        assert meta.description == "OG Desc"
        assert meta.url == "https://example.com/og"
        assert meta.site_name == "Example"
        assert meta.image == "https://example.com/img.jpg"

    def test_json_ld_extraction(self):
        html = """
        <html><head>
            <script type="application/ld+json">
            {"@type": "Article", "headline": "JSON-LD Title", "author": {"name": "Jane"}}
            </script>
        </head><body></body></html>
        """
        meta = extract_metadata(html)
        assert len(meta.structured_data) == 1
        assert meta.structured_data[0]["@type"] == "Article"

    def test_json_ld_malformed_ignored(self):
        html = """
        <html><head>
            <script type="application/ld+json">not valid json{</script>
        </head><body></body></html>
        """
        meta = extract_metadata(html)
        assert len(meta.structured_data) == 0

    def test_meta_author(self):
        html = '<html><head><meta name="author" content="John Smith"></head><body></body></html>'
        meta = extract_metadata(html)
        assert meta.author == "John Smith"

    def test_meta_description(self):
        html = '<html><head><meta name="description" content="A page"></head><body></body></html>'
        meta = extract_metadata(html)
        assert meta.description == "A page"

    def test_canonical_url(self):
        html = '<html><head><link rel="canonical" href="https://example.com/canonical"></head><body></body></html>'
        meta = extract_metadata(html)
        assert meta.url == "https://example.com/canonical"

    def test_language_from_html_lang(self):
        html = '<html lang="fr"><head></head><body></body></html>'
        meta = extract_metadata(html)
        assert meta.language == "fr"

    def test_article_published_time(self):
        html = (
            "<html><head>"
            '<meta property="article:published_time" content="2026-01-15">'
            "</head><body></body></html>"
        )
        meta = extract_metadata(html)
        assert meta.date_published == "2026-01-15"

    def test_fixture_article_metadata(self):
        html = (FIXTURES / "article.html").read_text()
        meta = extract_metadata(html, url="https://example.com/article")
        assert meta.title == "Test Article OG Title"
        assert meta.author == "Jane Doe"
        assert meta.site_name == "Example Site"
        assert meta.language == "en"
        assert len(meta.structured_data) == 1
        assert meta.image == "https://example.com/image.jpg"

    def test_empty_html(self):
        meta = extract_metadata("")
        assert meta.title is None
        assert meta.author is None
