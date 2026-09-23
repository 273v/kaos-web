"""Tests for readability content extraction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from lxml.html import HtmlElement, tostring

from kaos_web.extract.html_to_ast import html_to_document
from kaos_web.extract.readability import document_body, extract_content

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestReadability:
    def test_extracts_article(self):
        html = (FIXTURES / "article.html").read_text()
        result = extract_content(html)
        assert result is not None
        text = tostring(result, encoding="unicode", method="text")
        assert "Main Article Heading" in text

    def test_removes_nav(self):
        html = (FIXTURES / "article.html").read_text()
        result = extract_content(html)
        assert result is not None
        text = tostring(result, encoding="unicode", method="text")
        # Nav links should be stripped by readability
        assert "Privacy" not in text or "Copyright" not in text

    def test_preserves_code(self):
        html = (FIXTURES / "article.html").read_text()
        result = extract_content(html)
        assert result is not None
        text = tostring(result, encoding="unicode", method="text")
        assert "extract" in text  # from the code block

    def test_empty_html_returns_none(self):
        result = extract_content("")
        assert result is None

    def test_no_content_returns_none(self):
        html = "<html><body><nav><a href='/'>Home</a></nav></body></html>"
        result = extract_content(html)
        # With only nav, there's minimal content — may return None or a tiny element
        if result is not None:
            text = tostring(result, encoding="unicode", method="text").strip()
            assert len(text) < 50  # Minimal content

    def test_simple_article(self):
        html = """
        <html><body>
            <nav><a href="/">Home</a></nav>
            <article>
                <h1>Title</h1>
                <p>First paragraph of the article with enough content to score well
                in the readability algorithm. More text here for scoring.</p>
                <p>Second paragraph with additional content, commas, and substance
                to ensure this section is selected as the main content.</p>
            </article>
            <footer><p>Copyright notice</p></footer>
        </body></html>
        """
        result = extract_content(html)
        assert result is not None
        text = tostring(result, encoding="unicode", method="text")
        assert "First paragraph" in text
        assert "Second paragraph" in text


class _BodylessDoc:
    """Stand-in for lxml 5.x, whose ``.body`` raises on a body-less document."""

    @property
    def body(self) -> None:
        raise IndexError("list index out of range")


class TestDocumentBody:
    def test_bodyless_doc_returns_none(self) -> None:
        assert document_body(cast("HtmlElement", _BodylessDoc())) is None

    def test_empty_html_extracts_to_empty_document(self) -> None:
        doc = html_to_document("<html></html>", url="https://example.com")
        assert not doc.body
