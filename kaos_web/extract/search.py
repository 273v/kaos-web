"""Search within a ContentDocument using BM25 or term frequency.

Paragraph-level and sentence-level search grounded to the kaos-content
AST (block_refs). Uses kaos-nlp-core BM25 when available, falls back
to simple term frequency scoring.

This is the kaos-web equivalent of kaos_pdf.search — identical API
but without the kaos-pdf dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kaos_content.model.document import ContentDocument
from kaos_content.views import DocumentView


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search result with context."""

    text: str
    score: float
    block_ref: str
    page: int | None
    section_ref: str | None
    section_title: str | None


@dataclass(frozen=True, slots=True)
class SearchResults:
    """Search results with pagination metadata."""

    results: list[SearchResult]
    total_matches: int
    has_more: bool
    query: str


def search_document(
    document: ContentDocument,
    query: str,
    *,
    top_k: int = 10,
    preview_length: int = 200,
    level: Literal["paragraph", "sentence"] = "paragraph",
) -> SearchResults:
    """Search within a ContentDocument by text query.

    Uses BM25 via kaos-nlp-core when available, falls back to TF scoring.

    Args:
        document: The ContentDocument to search.
        query: Search query text (must not be empty).
        top_k: Maximum number of results to return.
        preview_length: Maximum characters in result text. 0 = full text.
        level: Search granularity — "paragraph" or "sentence".

    Returns:
        SearchResults with matching results and pagination metadata.
    """
    if not query or not query.strip():
        msg = "Query must not be empty"
        raise ValueError(msg)

    try:
        from kaos_nlp_core.search import search_paragraphs, search_sentences  # noqa: F401

        return _search_bm25(
            document, query, top_k=top_k, preview_length=preview_length, level=level
        )
    except ImportError:
        pass

    if level == "sentence":
        msg = (
            "Sentence-level search requires kaos-nlp-core. Install with: pip install kaos-web[nlp]"
        )
        raise ImportError(msg)
    return _search_tf(document, query, top_k=top_k, preview_length=preview_length)


def _search_bm25(
    document: ContentDocument,
    query: str,
    *,
    top_k: int,
    preview_length: int,
    level: Literal["paragraph", "sentence"],
) -> SearchResults:
    """BM25 search via kaos-nlp-core, grounded to AST block_refs."""
    if level == "sentence":
        return _search_bm25_sentences(document, query, top_k=top_k, preview_length=preview_length)
    return _search_bm25_paragraphs(document, query, top_k=top_k, preview_length=preview_length)


def _search_bm25_paragraphs(
    document: ContentDocument,
    query: str,
    *,
    top_k: int,
    preview_length: int,
) -> SearchResults:
    """BM25 paragraph search per-paragraph (AST-grounded)."""
    from kaos_nlp_core.search import search_paragraphs

    view = DocumentView(document)
    all_scored: list[SearchResult] = []

    for pv in view.paragraphs:
        if not pv.text or not pv.text.strip():
            continue

        hits = search_paragraphs(pv.text, query, top_k=1)
        if not hits:
            continue

        score = hits[0]["score"]
        text = pv.text
        if preview_length > 0 and len(text) > preview_length:
            text = text[:preview_length] + "..."

        section_title = _resolve_section(view, pv.section_ref)

        all_scored.append(
            SearchResult(
                text=text,
                score=score,
                block_ref=pv.block_ref,
                page=pv.page,
                section_ref=pv.section_ref,
                section_title=section_title,
            )
        )

    all_scored.sort(key=lambda r: r.score, reverse=True)
    total = len(all_scored)
    results = all_scored[:top_k]
    return SearchResults(
        results=results, total_matches=total, has_more=total > len(results), query=query
    )


def _search_bm25_sentences(
    document: ContentDocument,
    query: str,
    *,
    top_k: int,
    preview_length: int,
) -> SearchResults:
    """BM25 sentence search per-paragraph (AST-grounded)."""
    from kaos_nlp_core.search import search_sentences

    view = DocumentView(document)
    all_scored: list[SearchResult] = []

    for pv in view.paragraphs:
        if not pv.text or not pv.text.strip():
            continue

        hits = search_sentences(pv.text, query, top_k=top_k)
        if not hits:
            continue

        section_title = _resolve_section(view, pv.section_ref)

        for r in hits:
            text = r["text"]
            if preview_length > 0 and len(text) > preview_length:
                text = text[:preview_length] + "..."

            all_scored.append(
                SearchResult(
                    text=text,
                    score=r["score"],
                    block_ref=pv.block_ref,
                    page=pv.page,
                    section_ref=pv.section_ref,
                    section_title=section_title,
                )
            )

    all_scored.sort(key=lambda r: r.score, reverse=True)
    total = len(all_scored)
    results = all_scored[:top_k]
    return SearchResults(
        results=results, total_matches=total, has_more=total > len(results), query=query
    )


def _search_tf(
    document: ContentDocument,
    query: str,
    *,
    top_k: int,
    preview_length: int,
) -> SearchResults:
    """Simple term frequency search (fallback without kaos-nlp-core)."""
    view = DocumentView(document)
    query_lower = query.lower()
    query_words = [w for w in query_lower.split() if len(w) > 2]
    all_scored: list[SearchResult] = []

    for pv in view.paragraphs:
        text = pv.text
        if not text or not text.strip():
            continue

        text_lower = text.lower()
        score = float(text_lower.count(query_lower))
        if score <= 0 and query_words:
            score = float(sum(text_lower.count(w) for w in query_words))
        if score <= 0:
            continue

        display = text
        if preview_length > 0 and len(display) > preview_length:
            display = display[:preview_length] + "..."

        section_title = _resolve_section(view, pv.section_ref)

        all_scored.append(
            SearchResult(
                text=display,
                score=score,
                block_ref=pv.block_ref,
                page=pv.page,
                section_ref=pv.section_ref,
                section_title=section_title,
            )
        )

    all_scored.sort(key=lambda r: r.score, reverse=True)
    total = len(all_scored)
    results = all_scored[:top_k]
    return SearchResults(
        results=results, total_matches=total, has_more=total > len(results), query=query
    )


def _resolve_section(view: DocumentView, section_ref: str | None) -> str | None:
    """Resolve section heading text from a section ref."""
    if not section_ref:
        return None
    sec = view.section_by_ref(section_ref)
    return sec.heading_text if sec is not None else None
