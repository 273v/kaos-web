"""Top-level ``parse_*`` API naming for kaos-web.

Per docs/guides/python-api-naming.md (PA3), kaos-web re-exports
``parse_html`` from kaos-content so agents can reach the canonical
"raw HTML → AST" entry point via ``from kaos_web import parse_html``.

The richer ``html_to_document`` (readability) and the structured-data
``extract_*`` functions are NOT renamed: they keep their meaning under
the ``extract`` verb rule (pull data from an already-loaded source).
"""

from __future__ import annotations

from kaos_content.model.document import ContentDocument


def test_parse_html_importable_from_top_level() -> None:
    """``from kaos_web import parse_html`` is the canonical raw-HTML entry."""
    from kaos_web import parse_html

    assert callable(parse_html)


def test_parse_html_in_all() -> None:
    import kaos_web

    assert "parse_html" in kaos_web.__all__


def test_parse_html_returns_content_document() -> None:
    from kaos_web import parse_html

    doc = parse_html("<html><body><p>Hello PA3</p></body></html>")
    assert isinstance(doc, ContentDocument)


def test_parse_html_matches_kaos_content_function() -> None:
    """kaos-web re-exports the kaos-content function, not a fork."""
    from kaos_content.parsers.html import parse_html as kc_parse_html
    from kaos_web import parse_html as kw_parse_html

    assert kw_parse_html is kc_parse_html


def test_html_to_document_still_exported() -> None:
    """The richer readability pipeline keeps its name (no rename)."""
    from kaos_web import html_to_document

    assert callable(html_to_document)


def test_extract_content_still_exported() -> None:
    """extract_<thing> keeps its name — data pull from an already-loaded source."""
    from kaos_web import extract_content, extract_metadata

    assert callable(extract_content)
    assert callable(extract_metadata)
