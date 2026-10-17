"""``html_to_document(boilerplate=...)``: skip regions known to be site boilerplate.

Pipelines that extract many pages of one site can see a region whose text
repeats, identically, on many different pages (a translation widget, a
grant sidebar, a facet list). Passing those regions' fingerprints makes the
extractor select the next-best region, then the declared ``<main>``, then
the whole body. A fingerprint matches a region's whole text only, so content
that merely contains a shared block (a disclaimer) is never rejected.
"""

from __future__ import annotations

from kaos_content import serialize_text
from kaos_web import html_to_document
from kaos_web.extract import extract_content_l3, text_fingerprint

_ARTICLE = " ".join(
    f"Paragraph {i} of the inspection narrative describes the cited hazard and the abatement date."
    for i in range(14)
)
_SIDEBAR = " ".join(
    f"Grant program {i} funds youth mentoring in rural counties." for i in range(12)
)


def _page(article: str = _ARTICLE, extra: str = "") -> str:
    return (
        "<html><body>"
        f"<div class='content'><h1>Inspection Detail</h1><p>{article}</p>{extra}</div>"
        f"<div class='sidebar'><h2>Funding</h2><p>{_SIDEBAR}</p></div>"
        "</body></html>"
    )


def _text(html: str, boilerplate: set[str] | None = None) -> str:
    return serialize_text(
        html_to_document(html, url="https://www.example.gov/p", boilerplate=boilerplate)
    )


class TestFingerprint:
    def test_layout_does_not_change_it(self) -> None:
        assert text_fingerprint("A list:\n- one\n- two") == text_fingerprint("a list one two")
        assert text_fingerprint("x | y\nz") == text_fingerprint("X Y Z")

    def test_words_do(self) -> None:
        assert text_fingerprint("grant program") != text_fingerprint("grant programs")


class TestRegionRejection:
    def test_default_picks_the_article(self) -> None:
        assert "inspection narrative" in _text(_page())

    def test_region_source_is_reported(self) -> None:
        bp = {text_fingerprint("unrelated text")}
        doc = html_to_document(_page(), url="https://www.example.gov/p", boilerplate=bp)
        assert doc.metadata.extra["region_source"] in (
            "l3",
            "main",
            "readability",
            "semantic",
            "body",
        )
        assert serialize_text(doc) == _text(_page())

    def test_sidebar_boilerplate_never_wins(self) -> None:
        # A page whose article is short, so the sidebar would otherwise be chosen.
        html = _page(article="Inspection opened. Case closed with no citations issued today.")
        sidebar_text = "Funding\n\n" + _SIDEBAR
        bp = {text_fingerprint(sidebar_text)}
        doc = html_to_document(html, url="https://www.example.gov/p", boilerplate=bp)
        text = serialize_text(doc)
        assert "Inspection opened" in text
        assert text_fingerprint(text) != text_fingerprint(sidebar_text)

    def test_everything_boilerplate_falls_back_to_body(self) -> None:
        html = "<html><body><div><p>" + _SIDEBAR + "</p></div></body></html>"
        bp = {text_fingerprint(_SIDEBAR)}
        doc = html_to_document(html, url="https://www.example.gov/p", boilerplate=bp)
        assert doc.metadata.extra["region_source"] == "body"
        assert "Grant program 3" in serialize_text(doc)

    def test_declared_main_used_when_regions_are_boilerplate(self) -> None:
        main_text = " ".join(f"Main sentence {i} explains the transmittal." for i in range(12))
        html = (
            "<html><body><aside><p>" + _SIDEBAR + "</p></aside>"
            "<main><p>" + main_text + "</p></main></body></html>"
        )
        default = _text(html)
        bp = (
            {text_fingerprint(default)}
            if "Grant program" in default
            else {text_fingerprint(_SIDEBAR)}
        )
        doc = html_to_document(html, url="https://www.example.gov/p", boilerplate=bp)
        assert "Main sentence 3" in serialize_text(doc)

    def test_shared_disclaimer_inside_content_is_not_rejected(self) -> None:
        disclaimer = (
            "<p>Disclaimer: this information is provided for general guidance only and does not "
            "create any rights, substantive or procedural, enforceable by law.</p>"
        )
        html = _page(extra=disclaimer)
        bp = {
            text_fingerprint(
                "Disclaimer: this information is provided for general guidance only "
                "and does not create any rights, substantive or procedural, "
                "enforceable by law."
            )
        }
        assert _text(html, boilerplate=bp) == _text(html)
        assert "inspection narrative" in _text(html, boilerplate=bp)

    def test_no_boilerplate_keeps_metadata_unchanged(self) -> None:
        assert html_to_document(_page(), url="https://www.example.gov/p").metadata.extra == {}

    def test_l3_reject_all_returns_none(self) -> None:
        assert extract_content_l3(_page(), reject=lambda el: True) is None
