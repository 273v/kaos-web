"""Fuzz / invariant tests for HTML-to-AST extraction.

Tests structural invariants across multiple real HTML fixtures.
Modeled after kaos-pdf's fuzz test suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaos_content.model.blocks import (
    BulletList,
    Heading,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
)
from kaos_content.model.inlines import Image, Link, Text
from kaos_content.serializers.markdown import serialize_markdown
from kaos_content.serializers.text import serialize_text
from kaos_web.extract import extract_images, extract_links, html_to_document

FIXTURES = Path(__file__).parent.parent / "fixtures"
HTML_FILES = sorted(FIXTURES.glob("*.html"))


@pytest.fixture(params=[f.name for f in HTML_FILES], ids=[f.stem for f in HTML_FILES])
def html_fixture(request: pytest.FixtureRequest) -> tuple[str, str]:
    """Load an HTML fixture. Returns (html_content, filename)."""
    path = FIXTURES / request.param
    return path.read_text(encoding="utf-8"), request.param


# ─── Structural invariants ───────────────────────────────────────────────────


class TestInvariants:
    """Invariants that must hold for ALL HTML fixtures."""

    def test_produces_content(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}")
        assert len(doc.body) > 0, f"{name}: should produce at least one block"

    def test_no_empty_paragraphs(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}", extract_content=False)
        for block in doc.body:
            if isinstance(block, Paragraph):
                text = "".join(c.value for c in block.children if isinstance(c, Text)).strip()
                has_non_text = any(not isinstance(c, Text) for c in block.children)
                assert text or has_non_text, f"{name}: empty paragraph found"

    def test_headings_have_valid_depth(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}", extract_content=False)
        for block in doc.body:
            if isinstance(block, Heading):
                assert 1 <= block.depth <= 6, f"{name}: heading depth {block.depth} out of range"

    def test_all_blocks_have_provenance(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}")
        for block in doc.body:
            assert block.provenance is not None, (
                f"{name}: block {block.node_type} missing provenance"
            )
            assert block.provenance.source is not None
            assert block.provenance.extractor == "kaos-web"

    def test_provenance_url_matches(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        url = f"https://example.com/{name}"
        doc = html_to_document(html, url=url)
        for block in doc.body:
            if block.provenance and block.provenance.source:
                assert block.provenance.source.uri == url

    def test_no_javascript_uris_in_links(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}", extract_content=False)

        def _check_inlines(children: tuple) -> None:
            for child in children:
                if isinstance(child, Link):
                    assert not child.url.startswith("javascript:"), (
                        f"{name}: javascript: URI in link: {child.url}"
                    )
                    assert not child.url.startswith("data:text"), (
                        f"{name}: data: URI in link: {child.url[:50]}"
                    )
                if hasattr(child, "children"):
                    _check_inlines(child.children)

        for block in doc.body:
            children = getattr(block, "children", ())
            if children:
                _check_inlines(children)

    def test_images_have_src(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}", extract_content=False)

        def _check_images(children: tuple) -> None:
            for child in children:
                if isinstance(child, Image):
                    assert child.src, f"{name}: image with empty src"
                if hasattr(child, "children"):
                    _check_images(child.children)

        for block in doc.body:
            children = getattr(block, "children", ())
            if children:
                _check_images(children)

    def test_lists_have_items(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}", extract_content=False)
        for block in doc.body:
            if isinstance(block, (BulletList, OrderedList)):
                assert len(block.children) > 0, f"{name}: empty list"
                for item in block.children:
                    assert isinstance(item, ListItem), f"{name}: non-ListItem in list"

    def test_tables_have_content(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}", extract_content=False)
        for block in doc.body:
            if isinstance(block, Table):
                has_content = (
                    block.head is not None or len(block.bodies) > 0 or block.foot is not None
                )
                assert has_content, f"{name}: empty table"


# ─── Serialization invariants ────────────────────────────────────────────────


class TestSerializationInvariants:
    """Serialization must work on all extracted documents."""

    def test_markdown_serialization(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}")
        md = serialize_markdown(doc)
        assert isinstance(md, str)
        assert len(md) > 0, f"{name}: empty markdown output"

    def test_text_serialization(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}")
        text = serialize_text(doc)
        assert isinstance(text, str)
        assert len(text) > 0, f"{name}: empty text output"

    def test_markdown_no_crash_without_readability(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        doc = html_to_document(html, url=f"https://example.com/{name}", extract_content=False)
        md = serialize_markdown(doc)
        assert isinstance(md, str)


# ─── Link / image extraction invariants ──────────────────────────────────────


class TestExtractionInvariants:
    """Link and image extraction invariants."""

    def test_links_have_urls(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        links = extract_links(html, url=f"https://example.com/{name}")
        for link in links:
            assert link.url, f"{name}: link with empty URL"
            assert not link.url.startswith("javascript:"), (
                f"{name}: javascript: URI in extracted link"
            )

    def test_images_have_srcs(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        images = extract_images(html, url=f"https://example.com/{name}")
        for img in images:
            assert img.src, f"{name}: image with empty src"

    def test_link_types_valid(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        links = extract_links(html, url=f"https://example.com/{name}")
        valid_types = {
            "navigation",
            "content",
            "pagination",
            "social",
            "download",
            "anchor",
            "other",
        }
        for link in links:
            assert link.link_type in valid_types, f"{name}: invalid link type {link.link_type}"

    def test_image_types_valid(self, html_fixture: tuple[str, str]) -> None:
        html, name = html_fixture
        images = extract_images(html, url=f"https://example.com/{name}")
        valid_types = {"content", "decorative", "icon", "social_card", "tracking"}
        for img in images:
            assert img.image_type in valid_types, f"{name}: invalid image type {img.image_type}"
