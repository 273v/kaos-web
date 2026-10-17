"""``html_to_document``: declared main region guard and word spacing.

The declared-main guard is kaos-web behaviour: when the learned extractor
returns a region that lies outside the page's ``<main>`` / ``role="main"``
(a navigation drawer, a sidebar, a footer), the declared region is used.

The word-spacing cases run through the public entry point and need the
kaos-content walker that keeps whitespace at inline element boundaries
(whitespace-only text and tails, ``&nbsp;``, block-level elements inside
inline content, paragraphs inside table cells). Snippets are reduced from
public U.S. government pages where words were glued together.
"""

from __future__ import annotations

import pytest
from lxml import html as lxml_html
from lxml.html import HtmlElement

from kaos_content import serialize_text
from kaos_content.parsers.html import collapse_whitespace
from kaos_web import html_to_document
from kaos_web.extract import html_to_ast

_NAV_ITEMS = "".join(f"<li><a href='/topic/{i}'>Topic number {i}</a></li>" for i in range(40))
_ARTICLE = " ".join(
    f"Sentence {i} of the media note explains the signing of the accords." for i in range(12)
)
_PAGE = (
    "<html><body><div class='nav__drawer'><ul>"
    + _NAV_ITEMS
    + "</ul></div><main><h1>Media Note</h1><p>"
    + _ARTICLE
    + "</p></main></body></html>"
)


def _nav_region(html: str, content_scope: float = 0.5) -> HtmlElement | None:
    del content_scope
    doc = lxml_html.document_fromstring(html)
    found = doc.find_class("nav__drawer")
    return found[0] if found else None


def _main_region(html: str, content_scope: float = 0.5) -> HtmlElement | None:
    del content_scope
    doc = lxml_html.document_fromstring(html)
    return doc.find(".//main")


class TestDeclaredMainGuard:
    def test_region_outside_main_is_replaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(html_to_ast, "extract_content_l3", _nav_region)
        text = serialize_text(html_to_document(_PAGE))
        assert "Sentence 0 of the media note" in text
        assert "Topic number 3" not in text

    def test_region_inside_main_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(html_to_ast, "extract_content_l3", _main_region)
        text = serialize_text(html_to_document(_PAGE))
        assert text.startswith("Media Note")
        assert "Topic number" not in text

    def test_no_declared_main_keeps_model_choice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(html_to_ast, "extract_content_l3", _nav_region)
        page = _PAGE.replace("<main>", "<div>").replace("</main>", "</div>")
        text = serialize_text(html_to_document(page))
        assert "Topic number 3" in text

    def test_role_main_counts_as_declared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(html_to_ast, "extract_content_l3", _nav_region)
        page = _PAGE.replace("<main>", "<div role='main'>").replace("</main>", "</div>")
        text = serialize_text(html_to_document(page))
        assert "Sentence 0 of the media note" in text

    def test_short_main_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(html_to_ast, "extract_content_l3", _nav_region)
        page = _PAGE.replace(_ARTICLE, "Too short.")
        text = serialize_text(html_to_document(page))
        assert "Topic number 3" in text


_WALKER_KEEPS_SPACES = collapse_whitespace(" ") == " "
needs_walker_fix = pytest.mark.skipif(
    not _WALKER_KEEPS_SPACES,
    reason="needs kaos-content with whitespace kept at inline element boundaries",
)


def _doc_text(body: str) -> str:
    return serialize_text(
        html_to_document(f"<html><body>{body}</body></html>", extract_content=False)
    )


@needs_walker_fix
class TestWordSpacing:
    def test_word_spaces_in_spans(self) -> None:
        # OSHRC decision exported from Word.
        body = (
            '<p><span style="font-size:12pt">For<span>\r\n\t</span></span>'
            '<span style="font-size:12pt">the<span>\r\n\t</span></span>'
            '<span style="font-size:12pt">Respondent</span></p>'
        )
        assert _doc_text(body).strip() == "For the Respondent"

    def test_nbsp_between_strong_elements(self) -> None:
        body = "<p><strong>health problems.</strong>&nbsp;<strong>(It won't.)</strong></p>"
        assert _doc_text(body).strip() == "health problems. (It won't.)"

    def test_divs_inside_link(self) -> None:
        body = '<p><a href="/staff"><div>Michael Sarich</div><div>Eric Stein</div></a></p>'
        assert _doc_text(body).strip() == "Michael Sarich\nEric Stein"

    def test_cell_paragraphs(self) -> None:
        body = (
            "<table><tr><td><p>Respondents:</p><p>Agri-Fintech Holdings, Inc.</p></td>"
            "<td>Release No. 34-98921</td></tr></table>"
        )
        assert "Respondents:\nAgri-Fintech Holdings, Inc." in _doc_text(body)

    def test_extracted_article_keeps_spaces(self) -> None:
        para = " ".join(
            f"Birdsboro Kosher Farms (Birdsboro or Respondent)<span>\r\n</span>operates "
            f"facility {i} at <b>1100</b> <i>Lincoln Road</i>."
            for i in range(10)
        )
        html = (
            "<html><body><nav><a href='/'>Home</a></nav>"
            f"<article><p>{para}</p></article></body></html>"
        )
        text = serialize_text(html_to_document(html))
        assert "Respondent) operates facility 0 at 1100 Lincoln Road." in text
        assert "Respondent)operates" not in text
