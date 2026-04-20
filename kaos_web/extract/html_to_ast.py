"""Convert HTML element trees to kaos-content AST nodes.

This module is a thin wrapper around ``kaos_content.parsers.html`` that
adds readability-based content extraction (L3 learned model with
heuristic fallback).  The core HTML-to-AST walker lives in kaos-content
so that sibling packages (kaos-source, kaos-pdf, etc.) can convert HTML
without depending on kaos-web.

For raw (no-readability) conversion, use
``kaos_content.parsers.html.parse_html`` directly.
"""

from __future__ import annotations

from lxml import html as lxml_html
from lxml.html import HtmlElement

from kaos_content.model.attr import SourceRef
from kaos_content.model.document import ContentDocument
from kaos_content.model.metadata import DocumentMetadata
from kaos_content.parsers.html import (
    empty_document,
    extractor_scope,
    looks_like_xbrl,
    parse_html,
    pre_content_scope,
    process_children_as_blocks,
    strip_inline_xbrl,
)
from kaos_core.logging import get_logger
from kaos_web.extract.readability import extract_content as readability_extract
from kaos_web.extract.readability_l3 import extract_content_l3

logger = get_logger(__name__)

# Minimum words from readability before we accept its result.
# Below this threshold, we try semantic container fallback.
_MIN_READABILITY_WORDS = 50


def _find_semantic_container(body: HtmlElement) -> HtmlElement | None:
    """Find the best semantic container when readability returns too little.

    Tries <main> first, then the common parent of multiple <article> elements,
    then [role=main]. Returns None if no semantic container has enough content.
    """
    # Try <main> first (most semantically correct)
    main_el = body.find(".//main")
    if main_el is not None:
        word_count = len((main_el.text_content() or "").split())
        if word_count >= _MIN_READABILITY_WORDS:
            return main_el

    # Try role="main"
    for el in body.iter():
        if isinstance(el.tag, str) and el.get("role") == "main":
            word_count = len((el.text_content() or "").split())
            if word_count >= _MIN_READABILITY_WORDS:
                return el

    # Try the common parent of multiple <article> elements (listing pattern)
    articles = body.findall(".//article")
    if len(articles) > 1:
        parent = articles[0].getparent()
        if parent is not None:
            word_count = len((parent.text_content() or "").split())
            if word_count >= _MIN_READABILITY_WORDS:
                return parent
    elif len(articles) == 1:
        word_count = len((articles[0].text_content() or "").split())
        if word_count >= _MIN_READABILITY_WORDS:
            return articles[0]

    return None


def html_to_document(
    html_content: str,
    *,
    url: str = "",
    extract_content: bool = True,
    content_scope: float = 0.5,
    strip_xbrl: bool | None = None,
    pre_content_mode: str = "code",
) -> ContentDocument:
    """Convert HTML to a ContentDocument AST.

    Args:
        html_content: Raw HTML string.
        url: Source URL for provenance and relative URL resolution.
        extract_content: If True, run content extraction first.
            If False, convert the entire HTML body.
        content_scope: Extraction breadth from 0.0 (strict, article-only)
            to 1.0 (permissive, include more surrounding content).
            Default 0.5. Only applies when ``extract_content=True``.
        strip_xbrl: If ``True``, preprocess the HTML to strip Inline
            XBRL (iXBRL) markup before parsing.  If ``None``
            (default), auto-detect XBRL and strip if present.  If
            ``False``, skip XBRL stripping even if detected.

            SEC EDGAR filings use iXBRL which wraps HTML in ``ix:``
            namespace elements that lxml and readability models
            cannot process.  This parameter enables the kaos-web
            pipeline to handle EDGAR filings natively.
        pre_content_mode: How to interpret ``<pre>`` tag content.
            ``"code"`` (default) emits a ``CodeBlock`` preserving
            whitespace. ``"prose"`` treats the inner text as
            blank-line-separated prose and emits ``Paragraph`` blocks.
            Use ``"prose"`` for sources (e.g. Federal Register
            ``raw_text_url``) that abuse ``<pre>`` as a plain-text
            container.

    Returns:
        ContentDocument with Block/Inline AST nodes and provenance.
    """
    # For raw (no-readability) conversion, delegate to kaos-content.
    if not extract_content:
        with extractor_scope("kaos-web"):
            return parse_html(
                html_content,
                url=url,
                strip_xbrl=strip_xbrl,
                pre_content_mode=pre_content_mode,
            )

    if not html_content or not html_content.strip():
        return empty_document()

    # Strip Inline XBRL if requested or auto-detected.
    if strip_xbrl is True or (strip_xbrl is None and looks_like_xbrl(html_content)):
        html_content = strip_inline_xbrl(html_content)

    root: HtmlElement | None = None
    full_doc: HtmlElement | None = None  # Parsed once, reused if needed

    # Try Level 3 learned model first, fall back to heuristic readability.
    try:
        root = extract_content_l3(html_content, content_scope=content_scope)
    except Exception as exc:
        logger.debug("html_to_ast: fallback from L3 extraction to L3 retry/heuristic: %s", exc)
        root = None

    # If L3 returned nothing and scope was strict, retry with default scope
    # before falling back to the heuristic (which ignores content_scope).
    if root is None and content_scope < 0.4:
        try:
            root = extract_content_l3(html_content, content_scope=0.5)
        except Exception as exc:
            logger.debug(
                "html_to_ast: fallback from L3 retry (scope=0.5) to heuristic readability: %s",
                exc,
            )

    if root is None:
        root = readability_extract(html_content)

    # Guard: if extraction returned a suspiciously small fragment,
    # fall back to semantic container extraction.
    if root is not None:
        readability_words = len((root.text_content() or "").split())
        if readability_words < _MIN_READABILITY_WORDS:
            try:
                full_doc = lxml_html.document_fromstring(html_content)
            except Exception as exc:
                logger.debug(
                    "html_to_ast: fallback from semantic container parsing to body: %s",
                    exc,
                )
                full_doc = None
            if full_doc is not None and full_doc.body is not None:
                body_words = len((full_doc.body.text_content() or "").split())
                if body_words > _MIN_READABILITY_WORDS * 4:
                    # Extraction returned too little -- try semantic containers.
                    semantic = _find_semantic_container(full_doc.body)
                    root = semantic if semantic is not None else full_doc.body

    if root is None:
        # Parse full document and use <body>.
        if full_doc is None:
            try:
                full_doc = lxml_html.document_fromstring(html_content)
            except Exception as exc:
                logger.debug(
                    "html_to_ast: fallback from full-body parsing to empty document: %s",
                    exc,
                )
                return empty_document()
        root = full_doc.body if full_doc is not None else None
        if root is None:
            return empty_document()

    # Set extractor to "kaos-web" for provenance since this path applies
    # kaos-web's readability extraction on top of the kaos-content walker.
    # Nest ``pre_content_scope`` so the chosen ``<pre>`` interpretation
    # applies equally when readability extraction surfaces a ``<pre>``.
    with extractor_scope("kaos-web"), pre_content_scope(pre_content_mode):
        blocks = process_children_as_blocks(root, url)

    # Extract title from the original HTML for metadata.
    title: str | None = None
    try:
        full_doc = lxml_html.document_fromstring(html_content)
        title_el = full_doc.find(".//title")
        if title_el is not None and title_el.text:
            title = title_el.text.strip() or None
    except Exception as exc:
        logger.debug("html_to_ast: title extraction failed, skipping: %s", exc)

    metadata = DocumentMetadata.model_construct(
        title=title,
        authors=(),
        date=None,
        language=None,
        source=SourceRef.model_construct(uri=url, mime_type="text/html", artifact_id=None)
        if url
        else None,
        document_type=None,
        extra={},
    )

    return ContentDocument.model_construct(
        metadata=metadata,
        body=tuple(blocks),
        footnotes={},
        definitions={},
        annotations=(),
    )
