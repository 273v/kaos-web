"""Convert HTML element trees to kaos-content AST nodes.

This module walks an lxml HTML element tree and produces a
``ContentDocument`` composed of Block and Inline AST nodes from
``kaos_content.model``.
"""

from __future__ import annotations

import contextlib
import re
from urllib.parse import urljoin

from lxml import html as lxml_html
from lxml.html import HtmlElement

from kaos_content.model.attr import Caption, Provenance, SourceRef
from kaos_content.model.blocks import (
    Block,
    BlockQuote,
    BulletList,
    CodeBlock,
    DefinitionItem,
    DefinitionList,
    Figure,
    Heading,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    ThematicBreak,
)
from kaos_content.model.document import ContentDocument
from kaos_content.model.inlines import (
    Code,
    Emphasis,
    Image,
    Inline,
    LineBreak,
    Link,
    Strikethrough,
    Strong,
    Subscript,
    Superscript,
    Text,
)
from kaos_content.model.metadata import DocumentMetadata
from kaos_content.model.table import Cell, Row, TableSection
from kaos_web.extract.readability import extract_content as readability_extract

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "nav",
        "footer",
        "header",
        "form",
        "iframe",
        "embed",
        "object",
        "button",
        "svg",
        "noscript",
        "template",
    }
)

_INLINE_FORMATTING_TAGS = frozenset(
    {
        "strong",
        "b",
        "em",
        "i",
        "s",
        "del",
        "strike",
        "code",
        "a",
        "img",
        "br",
        "sub",
        "sup",
        "u",
        "mark",
        "abbr",
        "time",
        "span",
        "small",
    }
)

# Tags whose children are processed transparently as blocks.
_TRANSPARENT_BLOCK_TAGS = frozenset({"div", "section", "article", "main", "aside", "details"})

_WS_RE = re.compile(r"[ \t\n\r]+")
_LANG_RE = re.compile(r"\b(?:language|lang|highlight)-(\S+)")

# Dangerous URI schemes to reject.
_UNSAFE_SCHEMES = frozenset({"javascript", "data", "vbscript"})


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve a relative URL against a base URL."""
    if not href or not base_url:
        return href
    return urljoin(base_url, href)


def _is_safe_url(url: str) -> bool:
    """Reject javascript:, data:, vbscript: URIs."""
    stripped = url.strip().lower()
    return all(not stripped.startswith(f"{scheme}:") for scheme in _UNSAFE_SCHEMES)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace into single spaces."""
    return _WS_RE.sub(" ", text)


def _strip_or_empty(text: str | None) -> str:
    """Return stripped text or empty string."""
    if text is None:
        return ""
    return text


# ---------------------------------------------------------------------------
# Language / image helpers
# ---------------------------------------------------------------------------


def _extract_language(el: HtmlElement) -> str | None:
    """Extract code language from class attribute (e.g. ``language-python``)."""
    cls = el.get("class", "")
    m = _LANG_RE.search(cls)
    if m:
        return m.group(1)
    # Also check bare class names like "python", "json".
    for c in cls.split():
        if c and c not in ("highlight", "code", "sourceCode", "source"):
            return c
    return None


def _get_image_src(el: HtmlElement) -> str | None:
    """Get image source, preferring lazy-load attributes."""
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        val = el.get(attr)
        if val and val.strip():
            return val.strip()
    return None


# ---------------------------------------------------------------------------
# Provenance factory
# ---------------------------------------------------------------------------


def _make_provenance(url: str) -> Provenance | None:
    """Create a Provenance for block nodes."""
    if not url:
        return None
    return Provenance(
        source=SourceRef(uri=url, mime_type="text/html"),
        extractor="kaos-web",
    )


# ---------------------------------------------------------------------------
# Inline processing
# ---------------------------------------------------------------------------


def _process_inlines(el: HtmlElement, url: str) -> list[Inline]:
    """Process an element's children as inline content.

    Handles the element's own text, child elements, and tail text.
    This returns the inline nodes for the *children* of ``el`` (including
    el.text), but NOT el's own tail text — that belongs to the parent.
    """
    result: list[Inline] = []

    # Leading text of the element itself.
    text = _strip_or_empty(el.text)
    if text:
        collapsed = _collapse_whitespace(text)
        if collapsed:
            result.append(Text(value=collapsed))

    for child in el:
        if not isinstance(child.tag, str):
            # Processing instruction or comment — skip, but grab tail.
            tail = _strip_or_empty(child.tail)
            if tail:
                collapsed = _collapse_whitespace(tail)
                if collapsed:
                    result.append(Text(value=collapsed))
            continue

        tag = child.tag.lower()

        # Skip elements that should not produce inline content.
        if tag in _SKIP_TAGS:
            tail = _strip_or_empty(child.tail)
            if tail:
                collapsed = _collapse_whitespace(tail)
                if collapsed:
                    result.append(Text(value=collapsed))
            continue

        # Transparent inline elements: expand their children directly.
        if tag in ("span", "u", "mark", "abbr", "time", "small", "font"):
            result.extend(_process_inlines(child, url))
        else:
            inline = _element_to_inline(child, url)
            if inline is not None:
                result.append(inline)

        # Tail text after the child element.
        tail = _strip_or_empty(child.tail)
        if tail:
            collapsed = _collapse_whitespace(tail)
            if collapsed:
                result.append(Text(value=collapsed))

    return result


def _element_to_inline(el: HtmlElement, url: str) -> Inline | None:
    """Convert a single element to an Inline node (or None to skip)."""
    tag = el.tag.lower() if isinstance(el.tag, str) else ""

    if tag in ("strong", "b"):
        children = tuple(_process_inlines(el, url))
        if not children:
            return None
        return Strong(children=children)

    if tag in ("em", "i"):
        children = tuple(_process_inlines(el, url))
        if not children:
            return None
        return Emphasis(children=children)

    if tag in ("s", "del", "strike"):
        children = tuple(_process_inlines(el, url))
        if not children:
            return None
        return Strikethrough(children=children)

    if tag == "code":
        # Inline code: use text_content to flatten children.
        value = el.text_content() or ""
        if not value:
            return None
        return Code(value=value)

    if tag == "a":
        href = el.get("href", "")
        resolved = _resolve_url(href, url) if href else ""
        if resolved and not _is_safe_url(resolved):
            # Dangerous URL — return children as plain text, drop the link
            return None
        children = tuple(_process_inlines(el, url))
        if not children and not resolved:
            return None
        if not children:
            # Link with no visible text — use href as text.
            children = (Text(value=resolved),)
        title = el.get("title")
        return Link(url=resolved, title=title or None, children=children)

    if tag == "img":
        src = _get_image_src(el)
        if not src:
            return None
        resolved = _resolve_url(src, url)
        if not _is_safe_url(resolved):
            return None
        alt = el.get("alt", "")
        title = el.get("title")
        return Image(src=resolved, alt=alt or None, title=title or None)

    if tag == "br":
        return LineBreak()

    if tag == "sub":
        children = tuple(_process_inlines(el, url))
        if not children:
            return None
        return Subscript(children=children)

    if tag == "sup":
        children = tuple(_process_inlines(el, url))
        if not children:
            return None
        return Superscript(children=children)

    # Unknown inline-ish tag — flatten text content.
    text = el.text_content() or ""
    if text.strip():
        return Text(value=_collapse_whitespace(text))
    return None


# ---------------------------------------------------------------------------
# Block processing
# ---------------------------------------------------------------------------


def _process_element(el: HtmlElement, url: str) -> list[Block]:
    """Convert a single HTML element into Block AST nodes."""
    if not isinstance(el.tag, str):
        return []

    tag = el.tag.lower()
    prov = _make_provenance(url)

    # Skip non-content elements.
    if tag in _SKIP_TAGS:
        return []

    # Headings.
    if tag in _HEADING_TAGS:
        depth = int(tag[1])
        children = tuple(_process_inlines(el, url))
        if not children:
            return []
        return [Heading(depth=depth, children=children, provenance=prov)]

    # Paragraph.
    if tag == "p":
        children = tuple(_process_inlines(el, url))
        if not children:
            return []
        # Skip whitespace-only paragraphs (only Text children, all whitespace)
        if all(hasattr(c, "value") and isinstance(c, Text) for c in children):
            text = "".join(c.value for c in children).strip()
            if not text:
                return []
        return [Paragraph(children=children, provenance=prov)]

    # Blockquote.
    if tag == "blockquote":
        blocks = tuple(_process_children_as_blocks(el, url))
        if not blocks:
            # Try as inline content wrapped in a paragraph.
            inlines = tuple(_process_inlines(el, url))
            if inlines:
                blocks = (Paragraph(children=inlines, provenance=prov),)
        if not blocks:
            return []
        return [BlockQuote(children=blocks, provenance=prov)]

    # Preformatted / code blocks.
    if tag == "pre":
        return _process_pre(el, url, prov)

    # Lists.
    if tag == "ul":
        items = _process_list_items(el, url)
        if not items:
            return []
        return [BulletList(children=tuple(items), provenance=prov)]

    if tag == "ol":
        items = _process_list_items(el, url)
        if not items:
            return []
        start = 1
        start_attr = el.get("start")
        if start_attr is not None:
            with contextlib.suppress(ValueError):
                start = int(start_attr)
        return [OrderedList(start=start, children=tuple(items), provenance=prov)]

    # Definition list.
    if tag == "dl":
        return _process_definition_list(el, url, prov)

    # Table.
    if tag == "table":
        return _process_table(el, url, prov)

    # Horizontal rule.
    if tag == "hr":
        return [ThematicBreak(provenance=prov)]

    # Figure.
    if tag == "figure":
        return _process_figure(el, url, prov)

    # Transparent block containers.
    if tag in _TRANSPARENT_BLOCK_TAGS:
        blocks = _process_children_as_blocks(el, url)
        return blocks

    # Inline-level elements encountered at block level — wrap in Paragraph.
    if tag in _INLINE_FORMATTING_TAGS:
        inlines = _element_to_inline_list(el, url)
        if inlines:
            return [Paragraph(children=tuple(inlines), provenance=prov)]
        return []

    # Unknown tags — try to process children as blocks.
    blocks = _process_children_as_blocks(el, url)
    if blocks:
        return blocks

    # Last resort: try as inline content.
    inlines = tuple(_process_inlines(el, url))
    if inlines:
        return [Paragraph(children=inlines, provenance=prov)]

    return []


def _element_to_inline_list(el: HtmlElement, url: str) -> list[Inline]:
    """Convert an inline-level element to a list of Inline nodes.

    Unlike _element_to_inline which returns a single node, this returns a list
    so transparent elements can contribute multiple children.
    """
    tag = el.tag.lower() if isinstance(el.tag, str) else ""

    if tag in ("span", "u", "mark", "abbr", "time", "small", "font"):
        return _process_inlines(el, url)

    node = _element_to_inline(el, url)
    if node is not None:
        return [node]
    return []


def _process_children_as_blocks(el: HtmlElement, url: str) -> list[Block]:
    """Process all children of an element as block nodes.

    Also handles stray text between child elements by wrapping it in Paragraphs.
    """
    result: list[Block] = []
    prov = _make_provenance(url)

    # Leading text in the element.
    text = _strip_or_empty(el.text)
    if text:
        collapsed = _collapse_whitespace(text).strip()
        if collapsed:
            result.append(Paragraph(children=(Text(value=collapsed),), provenance=prov))

    for child in el:
        if not isinstance(child.tag, str):
            # Comment/PI — grab tail text.
            tail = _strip_or_empty(child.tail)
            if tail:
                collapsed = _collapse_whitespace(tail).strip()
                if collapsed:
                    result.append(Paragraph(children=(Text(value=collapsed),), provenance=prov))
            continue

        blocks = _process_element(child, url)
        result.extend(blocks)

        # Tail text after this child element.
        tail = _strip_or_empty(child.tail)
        if tail:
            collapsed = _collapse_whitespace(tail).strip()
            if collapsed:
                result.append(Paragraph(children=(Text(value=collapsed),), provenance=prov))

    return result


# ---------------------------------------------------------------------------
# Specialised element processors
# ---------------------------------------------------------------------------


def _process_pre(el: HtmlElement, url: str, prov: Provenance | None) -> list[Block]:
    """Process <pre> and <pre><code> elements into CodeBlock."""
    # Check for <pre><code>.
    code_el = el.find("code")
    if code_el is not None:
        language = _extract_language(code_el) or _extract_language(el)
        value = code_el.text_content() or ""
    else:
        language = _extract_language(el)
        value = el.text_content() or ""

    if not value:
        return []
    return [CodeBlock(language=language, value=value, provenance=prov)]


def _process_list_items(el: HtmlElement, url: str) -> list[ListItem]:
    """Process <li> children of a list element."""
    items: list[ListItem] = []
    prov = _make_provenance(url)

    for child in el:
        if not isinstance(child.tag, str):
            continue
        if child.tag.lower() != "li":
            continue

        blocks = _process_children_as_blocks(child, url)
        if not blocks:
            # Try inline content.
            inlines = tuple(_process_inlines(child, url))
            if inlines:
                blocks = [Paragraph(children=inlines, provenance=prov)]

        if blocks:
            items.append(ListItem(children=tuple(blocks), provenance=prov))

    return items


def _process_definition_list(el: HtmlElement, url: str, prov: Provenance | None) -> list[Block]:
    """Process <dl> into DefinitionList."""
    items: list[DefinitionItem] = []

    current_terms: list[tuple[Inline, ...]] = []
    current_defs: list[tuple[Block, ...]] = []

    for child in el:
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()

        if tag == "dt":
            # If we have accumulated terms+defs, flush.
            if current_terms and current_defs:
                for term in current_terms:
                    items.append(
                        DefinitionItem(
                            term=term,
                            definitions=tuple(current_defs),
                            provenance=prov,
                        )
                    )
                current_terms = []
                current_defs = []
            elif current_terms and not current_defs:
                # Multiple terms before any definition — keep accumulating.
                pass

            inlines = tuple(_process_inlines(child, url))
            if inlines:
                current_terms.append(inlines)

        elif tag == "dd":
            blocks = _process_children_as_blocks(child, url)
            if not blocks:
                inlines = tuple(_process_inlines(child, url))
                if inlines:
                    blocks = [Paragraph(children=inlines, provenance=prov)]
            if blocks:
                current_defs.append(tuple(blocks))

    # Flush remaining.
    if current_terms:
        for term in current_terms:
            items.append(
                DefinitionItem(
                    term=term,
                    definitions=tuple(current_defs) if current_defs else (),
                    provenance=prov,
                )
            )

    if not items:
        return []
    return [DefinitionList(children=tuple(items), provenance=prov)]


def _process_table(el: HtmlElement, url: str, prov: Provenance | None) -> list[Block]:
    """Process <table> into Table AST node."""
    head: TableSection | None = None
    bodies: list[TableSection] = []

    # Process <thead>.
    thead = el.find("thead")
    if thead is not None:
        rows = _process_table_rows(thead, url, is_header=True)
        if rows:
            head = TableSection(rows=tuple(rows))

    # Process <tbody> elements.
    tbodies = el.findall("tbody")
    if tbodies:
        for tbody in tbodies:
            rows = _process_table_rows(tbody, url, is_header=False)
            if rows:
                bodies.append(TableSection(rows=tuple(rows)))
    else:
        # No explicit <tbody>: process direct <tr> children.
        rows = _process_table_rows(el, url, is_header=False)
        if rows:
            bodies.append(TableSection(rows=tuple(rows)))

    # Process <tfoot>.
    foot: TableSection | None = None
    tfoot = el.find("tfoot")
    if tfoot is not None:
        rows = _process_table_rows(tfoot, url, is_header=False)
        if rows:
            foot = TableSection(rows=tuple(rows))

    # Extract caption.
    caption: Caption | None = None
    cap_el = el.find("caption")
    if cap_el is not None:
        inlines = tuple(_process_inlines(cap_el, url))
        if inlines:
            caption = Caption(body=(Paragraph(children=inlines, provenance=prov),))

    if not head and not bodies and not foot:
        return []

    return [
        Table(
            caption=caption,
            head=head,
            bodies=tuple(bodies),
            foot=foot,
            provenance=prov,
        )
    ]


def _process_table_rows(container: HtmlElement, url: str, *, is_header: bool) -> list[Row]:
    """Process <tr> elements within a table section."""
    rows: list[Row] = []

    for tr in container:
        if not isinstance(tr.tag, str) or tr.tag.lower() != "tr":
            continue
        cells: list[Cell] = []

        for td in tr:
            if not isinstance(td.tag, str):
                continue
            tag = td.tag.lower()
            if tag not in ("td", "th"):
                continue

            # Parse content of cell.
            blocks = _process_children_as_blocks(td, url)
            if not blocks:
                inlines = tuple(_process_inlines(td, url))
                if inlines:
                    blocks = [Paragraph(children=inlines, provenance=_make_provenance(url))]

            col_span = 1
            row_span = 1
            cs = td.get("colspan")
            if cs:
                with contextlib.suppress(ValueError):
                    col_span = max(1, int(cs))
            rs = td.get("rowspan")
            if rs:
                with contextlib.suppress(ValueError):
                    row_span = max(1, int(rs))

            cells.append(
                Cell(
                    content=tuple(blocks),
                    col_span=col_span,
                    row_span=row_span,
                )
            )

        if cells:
            rows.append(Row(cells=tuple(cells)))

    return rows


def _process_figure(el: HtmlElement, url: str, prov: Provenance | None) -> list[Block]:
    """Process <figure> into Figure AST node."""
    caption: Caption | None = None
    children: list[Block] = []

    for child in el:
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()

        if tag == "figcaption":
            inlines = tuple(_process_inlines(child, url))
            if inlines:
                caption = Caption(body=(Paragraph(children=inlines, provenance=prov),))
        elif tag == "img":
            src = _get_image_src(child)
            if src:
                resolved = _resolve_url(src, url)
                if _is_safe_url(resolved):
                    alt = child.get("alt", "")
                    img = Image(src=resolved, alt=alt or None)
                    children.append(Paragraph(children=(img,), provenance=prov))
        else:
            blocks = _process_element(child, url)
            children.extend(blocks)

    if not children and not caption:
        return []

    return [Figure(caption=caption, children=tuple(children), provenance=prov)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def html_to_document(
    html_content: str,
    *,
    url: str = "",
    extract_content: bool = True,
) -> ContentDocument:
    """Convert HTML to a ContentDocument AST.

    Args:
        html_content: Raw HTML string.
        url: Source URL for provenance and relative URL resolution.
        extract_content: If True, run readability to extract main content first.
            If False, convert the entire HTML body.

    Returns:
        ContentDocument with Block/Inline AST nodes and provenance.
    """
    if not html_content or not html_content.strip():
        return ContentDocument()

    root: HtmlElement | None = None

    if extract_content:
        root = readability_extract(html_content)

    if root is None:
        # Parse full document and use <body>.
        try:
            doc = lxml_html.document_fromstring(html_content)
        except Exception:
            return ContentDocument()
        root = doc.body
        if root is None:
            return ContentDocument()

    # Convert the element tree to AST blocks.
    blocks = _process_children_as_blocks(root, url)

    # Extract title from the original HTML for metadata.
    title: str | None = None
    try:
        full_doc = lxml_html.document_fromstring(html_content)
        title_el = full_doc.find(".//title")
        if title_el is not None and title_el.text:
            title = title_el.text.strip() or None
    except Exception:
        pass

    metadata = DocumentMetadata(
        title=title,
        source=SourceRef(uri=url, mime_type="text/html") if url else None,
    )

    return ContentDocument(
        metadata=metadata,
        body=tuple(blocks),
    )
