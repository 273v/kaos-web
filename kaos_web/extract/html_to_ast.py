"""Convert HTML element trees to kaos-content AST nodes.

This module walks an lxml HTML element tree and produces a
``ContentDocument`` composed of Block and Inline AST nodes from
``kaos_content.model``.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from urllib.parse import urljoin

from lxml import html as lxml_html
from lxml.html import HtmlElement

from kaos_content.model.attr import Attr, Caption, Provenance, SourceRef
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
from kaos_web.extract.readability_l3 import extract_content_l3

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

# Block-level tags — used to decide if <li> has block vs inline content.
_BLOCK_LEVEL_TAGS = frozenset(
    {
        "p",
        "div",
        "blockquote",
        "pre",
        "ul",
        "ol",
        "dl",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "figure",
        "section",
        "article",
    }
)
_LANG_RE = re.compile(r"\b(?:language|lang|highlight)-(\S+)")

# Dangerous URI schemes to reject.
_UNSAFE_SCHEMES = frozenset({"javascript", "data", "vbscript"})

# CSS classes whose elements should be filtered entirely.
# These are well-known noise patterns from major sites.
_SKIP_CLASSES = frozenset(
    {
        "mw-editsection",  # Wikipedia [edit] section links
        "mw-jump-link",  # Wikipedia "jump to" navigation
        "mw-cite-backlink",  # Wikipedia citation back-links
        "sr-only",  # Bootstrap screen-reader only
        "visually-hidden",  # Modern screen-reader only
        "screen-reader-text",  # WordPress screen-reader only
        "noprint",  # Wikipedia print-hide elements
    }
)

# Link href patterns that indicate UI action controls, not content links.
_ACTION_LINK_RE = re.compile(
    r"(?:^|[?&/])"
    r"(?:vote|upvote|downvote|flag|hide|collapse|fav|unfav)"
    r"(?:[?&=]|$)",
    re.IGNORECASE,
)

# Minimum words from readability before we accept its result.
# Below this threshold, we try semantic container fallback.
_MIN_READABILITY_WORDS = 50

# --- Inline XBRL preprocessing -----------------------------------------
#
# SEC EDGAR filings use Inline XBRL (iXBRL): standard HTML wrapped in
# ``ix:`` namespace elements.  lxml parses namespace-prefixed tags as
# ``{uri}localname`` which the AST builder doesn't recognize.  The fix:
# strip the XBRL wrapper before AST conversion.
#
# Reference: https://www.xbrl.org/Specification/inlineXBRL-part1/REC-2013-11-18/inlineXBRL-part1-REC-2013-11-18.html

_XBRL_HIDDEN_RE = re.compile(
    r"<ix:header\b[^>]*>.*?</ix:header>",
    re.DOTALL | re.IGNORECASE,
)
_XBRL_TAG_RE = re.compile(r"</?ix:[^>]*>", re.IGNORECASE)
_DISPLAY_NONE_RE = re.compile(
    r'<div\b[^>]*style\s*=\s*"[^"]*display\s*:\s*none[^"]*"[^>]*>.*?</div>',
    re.DOTALL | re.IGNORECASE,
)
_XML_DECL_RE = re.compile(r"<\?xml[^?]*\?>")
_XMLNS_RE = re.compile(r'\s+xmlns:[a-z_-]+="[^"]*"', re.IGNORECASE)


def _strip_inline_xbrl(html: str) -> str:
    """Remove Inline XBRL markup from an HTML string.

    Inline XBRL (iXBRL) wraps standard HTML in ``ix:`` namespace
    elements.  This function:

    1. Removes ``<ix:header>`` blocks (XBRL metadata, not visible).
    2. Removes ``<div style="display:none">`` blocks (hidden XBRL data).
    3. Unwraps all remaining ``ix:`` tags, keeping their text content.
       For example ``<ix:nonNumeric ...>42</ix:nonNumeric>`` → ``42``.
    4. Strips the XML declaration and XBRL namespace attributes so
       lxml can parse the result as plain HTML.

    The returned string is standard HTML that the rest of the
    kaos-web pipeline (readability extraction, AST conversion) can
    process normally.
    """
    # Order matters: remove hidden blocks before unwrapping tags,
    # so we don't accidentally keep hidden metadata text.
    result = _XBRL_HIDDEN_RE.sub("", html)
    result = _DISPLAY_NONE_RE.sub("", result)
    result = _XBRL_TAG_RE.sub("", result)
    result = _XML_DECL_RE.sub("", result)
    result = _XMLNS_RE.sub("", result)
    return result


def _looks_like_xbrl(html: str) -> bool:
    """Heuristic: does this HTML contain Inline XBRL markup?"""
    # Check the first 2000 chars for XBRL signatures.
    head = html[:2000]
    return "ix:" in head or "inlineXBRL" in head or "xbrl" in head.lower()

# Shared default Attr instance (frozen, safe to reuse).
_DEFAULT_ATTR = Attr()


def _fast_id() -> str:
    """Generate a fast unique ID (UUID4 hex — standard, faster than UUID7)."""
    return uuid.uuid4().hex


def _should_skip_element(el: HtmlElement) -> bool:
    """Check if an element should be skipped based on its CSS classes."""
    cls = el.get("class", "")
    if not cls:
        return False
    return bool(_SKIP_CLASSES.intersection(cls.split()))


def _is_action_link(href: str) -> bool:
    """Check if a link href is a UI action control, not a content link."""
    if not href:
        return False
    return _ACTION_LINK_RE.search(href) is not None


def _empty_document() -> ContentDocument:
    """Return an empty ContentDocument."""
    return ContentDocument.model_construct(
        metadata=DocumentMetadata.model_construct(
            title=None,
            authors=(),
            date=None,
            language=None,
            source=None,
            document_type=None,
            extra={},
        ),
        body=(),
        footnotes={},
        definitions={},
        annotations=(),
    )


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


# ---------------------------------------------------------------------------
# Fast node constructors — bypass Pydantic validation for trusted code.
# Uses model_construct() which skips schema validation and deepcopy.
# ---------------------------------------------------------------------------


def _mk_text(value: str) -> Text:
    return Text.model_construct(
        id=_fast_id(), attr=_DEFAULT_ATTR, provenance=None, node_type="text", value=value
    )


def _mk_strong(children: tuple[Inline, ...]) -> Strong:
    return Strong.model_construct(
        id=_fast_id(), attr=_DEFAULT_ATTR, provenance=None, node_type="strong", children=children
    )


def _mk_emphasis(children: tuple[Inline, ...]) -> Emphasis:
    return Emphasis.model_construct(
        id=_fast_id(), attr=_DEFAULT_ATTR, provenance=None, node_type="emphasis", children=children
    )


def _mk_code(value: str) -> Code:
    return Code.model_construct(
        id=_fast_id(), attr=_DEFAULT_ATTR, provenance=None, node_type="code", value=value
    )


def _mk_link(url: str, children: tuple[Inline, ...], title: str | None = None) -> Link:
    return Link.model_construct(
        id=_fast_id(),
        attr=_DEFAULT_ATTR,
        provenance=None,
        node_type="link",
        url=url,
        title=title,
        children=children,
    )


def _mk_image(src: str, alt: str | None = None, title: str | None = None) -> Image:
    return Image.model_construct(
        id=_fast_id(),
        attr=_DEFAULT_ATTR,
        provenance=None,
        node_type="image",
        src=src,
        alt=alt,
        title=title,
    )


def _mk_linebreak() -> LineBreak:
    return LineBreak.model_construct(
        id=_fast_id(), attr=_DEFAULT_ATTR, provenance=None, node_type="line_break"
    )


def _mk_paragraph(children: tuple[Inline, ...], prov: Provenance | None) -> Paragraph:
    return Paragraph.model_construct(
        id=_fast_id(),
        attr=_DEFAULT_ATTR,
        provenance=prov,
        node_type="paragraph",
        children=children,
    )


def _mk_heading(depth: int, children: tuple[Inline, ...], prov: Provenance | None) -> Heading:
    return Heading.model_construct(
        id=_fast_id(),
        attr=_DEFAULT_ATTR,
        provenance=prov,
        node_type="heading",
        depth=depth,
        children=children,
    )


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve a relative URL against a base URL.

    Fast path for common cases (absolute-path URLs like /about) avoids
    the full urljoin RFC parsing which is ~17x slower.
    """
    if not href or not base_url:
        return href
    # Already absolute
    if href.startswith(("http://", "https://", "//")):
        return href
    # Absolute-path relative (most common case): /about, /page/foo
    if href.startswith("/"):
        # Extract scheme + netloc from base
        idx = base_url.find("/", 8)  # skip past https://
        if idx > 0:
            return base_url[:idx] + href
        return base_url + href
    # Fall back to full RFC urljoin for relative paths, query strings, etc.
    return urljoin(base_url, href)


def _is_safe_url(url: str) -> bool:
    """Reject javascript:, data:, vbscript: URIs."""
    stripped = url.strip().lower()
    return all(not stripped.startswith(f"{scheme}:") for scheme in _UNSAFE_SCHEMES)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace into single spaces.

    Uses str.split()/join() which is ~4x faster than re.sub for this pattern.
    Note: split() also strips leading/trailing whitespace, so we re-add a
    single space if the original had leading/trailing whitespace.
    """
    if not text:
        return text
    leading = text[0] in " \t\n\r"
    trailing = text[-1] in " \t\n\r"
    collapsed = " ".join(text.split())
    if leading and collapsed:
        collapsed = " " + collapsed
    if trailing and collapsed:
        collapsed = collapsed + " "
    return collapsed


def _strip_or_empty(text: str | None) -> str:
    """Return stripped text or empty string."""
    if text is None:
        return ""
    return text


def _trim_inline_whitespace(inlines: list[Inline]) -> list[Inline]:
    """Strip leading/trailing whitespace from a list of inline nodes.

    Trims whitespace from leading/trailing Text nodes and removes empty ones.
    """
    # Trim leading
    while inlines and isinstance(inlines[0], Text):
        stripped = inlines[0].value.lstrip()
        if stripped:
            inlines[0] = _mk_text(stripped)
            break
        inlines.pop(0)
    # Trim trailing
    while inlines and isinstance(inlines[-1], Text):
        stripped = inlines[-1].value.rstrip()
        if stripped:
            inlines[-1] = _mk_text(stripped)
            break
        inlines.pop()
    return inlines


def _merge_adjacent_text(inlines: list[Inline]) -> list[Inline]:
    """Merge adjacent Text nodes and adjacent same-type inline formatting nodes.

    - Adjacent Text nodes: merge and collapse double spaces.
    - Adjacent Strong+Strong, Emphasis+Emphasis, Strikethrough+Strikethrough:
      merge children into a single node.
    """
    if not inlines:
        return inlines
    result: list[Inline] = []
    for node in inlines:
        if not result:
            result.append(node)
            continue
        prev = result[-1]
        # Merge adjacent Text nodes
        if isinstance(node, Text) and isinstance(prev, Text):
            merged = _WS_RE.sub(" ", prev.value + node.value)
            result[-1] = _mk_text(merged)
        # Merge adjacent Strong nodes
        elif isinstance(node, Strong) and isinstance(prev, Strong):
            result[-1] = _mk_strong(prev.children + node.children)
        # Merge adjacent Emphasis nodes
        elif isinstance(node, Emphasis) and isinstance(prev, Emphasis):
            result[-1] = _mk_emphasis(prev.children + node.children)
        # Merge adjacent Strikethrough nodes
        elif isinstance(node, Strikethrough) and isinstance(prev, Strikethrough):
            result[-1] = Strikethrough.model_construct(
                id=_fast_id(),
                attr=_DEFAULT_ATTR,
                provenance=None,
                node_type="strikethrough",
                children=prev.children + node.children,
            )
        else:
            result.append(node)
    return result


def _is_whitespace_only_inlines(inlines: tuple[Inline, ...] | list[Inline]) -> bool:
    """Check if inline nodes contain only whitespace text."""
    for c in inlines:
        if isinstance(c, Text):
            if c.value.strip():
                return False
        else:
            return False  # Non-text node means not whitespace-only
    return True


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


_PROVENANCE_CACHE: dict[str, Provenance] = {}


def _make_provenance(url: str) -> Provenance | None:
    """Create a Provenance for block nodes. Cached per URL (frozen, safe to share)."""
    if not url:
        return None
    cached = _PROVENANCE_CACHE.get(url)
    if cached is not None:
        return cached
    prov = Provenance.model_construct(
        source=SourceRef.model_construct(uri=url, mime_type="text/html", artifact_id=None),
        page=None,
        bbox=None,
        char_span=None,
        confidence=None,
        extractor="kaos-web",
    )
    _PROVENANCE_CACHE[url] = prov
    return prov


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
            result.append(_mk_text(collapsed))

    for child in el:
        if not isinstance(child.tag, str):
            # Processing instruction or comment — skip, but grab tail.
            tail = _strip_or_empty(child.tail)
            if tail:
                collapsed = _collapse_whitespace(tail)
                if collapsed:
                    result.append(_mk_text(collapsed))
            continue

        tag = child.tag.lower()

        # Skip elements that should not produce inline content.
        if tag in _SKIP_TAGS:
            tail = _strip_or_empty(child.tail)
            if tail:
                collapsed = _collapse_whitespace(tail)
                if collapsed:
                    result.append(_mk_text(collapsed))
            continue

        # Skip elements with well-known noise classes (e.g. mw-editsection).
        if _should_skip_element(child):
            tail = _strip_or_empty(child.tail)
            if tail:
                collapsed = _collapse_whitespace(tail)
                if collapsed:
                    result.append(_mk_text(collapsed))
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
                result.append(_mk_text(collapsed))

    return _merge_adjacent_text(result)


def _element_to_inline(el: HtmlElement, url: str) -> Inline | None:
    """Convert a single element to an Inline node (or None to skip)."""
    tag = el.tag.lower() if isinstance(el.tag, str) else ""

    if tag in ("strong", "b"):
        children = tuple(_process_inlines(el, url))
        if not children or _is_whitespace_only_inlines(children):
            return None
        # Collapse redundant nesting: <b><b>text</b></b> → Strong(text)
        if len(children) == 1 and isinstance(children[0], Strong):
            return children[0]
        return _mk_strong(children)

    if tag in ("em", "i"):
        children = tuple(_process_inlines(el, url))
        if not children or _is_whitespace_only_inlines(children):
            return None
        # Collapse redundant nesting
        if len(children) == 1 and isinstance(children[0], Emphasis):
            return children[0]
        return _mk_emphasis(children)

    if tag in ("s", "del", "strike"):
        children = tuple(_process_inlines(el, url))
        if not children:
            return None
        return Strikethrough.model_construct(
            id=_fast_id(),
            attr=_DEFAULT_ATTR,
            provenance=None,
            node_type="strikethrough",
            children=children,
        )

    if tag == "code":
        # Inline code: use text_content to flatten children.
        value = el.text_content() or ""
        if not value:
            return None
        return _mk_code(value)

    if tag == "a":
        href = el.get("href", "")
        # Skip UI action links (vote, hide, flag, etc.)
        if href and _is_action_link(href):
            return None
        resolved = _resolve_url(href, url) if href else ""
        if resolved and not _is_safe_url(resolved):
            # Dangerous URL — return children as plain text, drop the link
            return None
        children = tuple(_process_inlines(el, url))
        if not children and not resolved:
            return None
        if not children:
            # Link with no visible text — use href as text.
            children = (_mk_text(resolved),)
        title = el.get("title")
        return _mk_link(resolved, children, title or None)

    if tag == "img":
        src = _get_image_src(el)
        if not src:
            return None
        resolved = _resolve_url(src, url)
        if not _is_safe_url(resolved):
            return None
        alt = el.get("alt", "")
        title = el.get("title")
        return _mk_image(resolved, alt or None, title or None)

    if tag == "br":
        return _mk_linebreak()

    if tag == "sub":
        children = tuple(_process_inlines(el, url))
        if not children:
            return None
        return Subscript.model_construct(
            id=_fast_id(),
            attr=_DEFAULT_ATTR,
            provenance=None,
            node_type="subscript",
            children=children,
        )

    if tag == "sup":
        children = tuple(_process_inlines(el, url))
        if not children:
            return None
        return Superscript.model_construct(
            id=_fast_id(),
            attr=_DEFAULT_ATTR,
            provenance=None,
            node_type="superscript",
            children=children,
        )

    # Unknown inline-ish tag — flatten text content.
    text = el.text_content() or ""
    if text.strip():
        return _mk_text(_collapse_whitespace(text))
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

    # Skip elements with well-known noise classes (e.g. mw-editsection).
    if _should_skip_element(el):
        return []

    # Headings.
    if tag in _HEADING_TAGS:
        depth = int(tag[1])
        children = _trim_inline_whitespace(list(_process_inlines(el, url)))
        if not children:
            return []
        return [_mk_heading(depth, tuple(children), prov)]

    # Paragraph.
    if tag == "p":
        children = tuple(_process_inlines(el, url))
        if not children:
            return []
        # Skip whitespace-only paragraphs (only Text children, all whitespace)
        if all(isinstance(c, Text) for c in children):
            text = "".join(c.value for c in children if isinstance(c, Text)).strip()
            if not text:
                return []
        return [_mk_paragraph(children, prov)]

    # Blockquote.
    if tag == "blockquote":
        blocks = tuple(_process_children_as_blocks(el, url))
        if not blocks:
            # Try as inline content wrapped in a paragraph.
            inlines = tuple(_process_inlines(el, url))
            if inlines:
                blocks = (_mk_paragraph(inlines, prov),)
        if not blocks:
            return []
        return [
            BlockQuote.model_construct(
                id=_fast_id(),
                attr=_DEFAULT_ATTR,
                provenance=prov,
                node_type="blockquote",
                children=blocks,
            )
        ]

    # Preformatted / code blocks.
    if tag == "pre":
        return _process_pre(el, url, prov)

    # Lists.
    if tag == "ul":
        items = _process_list_items(el, url)
        if not items:
            return []
        return [
            BulletList.model_construct(
                id=_fast_id(),
                attr=_DEFAULT_ATTR,
                provenance=prov,
                node_type="bullet_list",
                children=tuple(items),
            )
        ]

    if tag == "ol":
        items = _process_list_items(el, url)
        if not items:
            return []
        start = 1
        start_attr = el.get("start")
        if start_attr is not None:
            with contextlib.suppress(ValueError):
                start = int(start_attr)
        return [
            OrderedList.model_construct(
                id=_fast_id(),
                attr=_DEFAULT_ATTR,
                provenance=prov,
                node_type="ordered_list",
                start=start,
                children=tuple(items),
            )
        ]

    # Definition list.
    if tag == "dl":
        return _process_definition_list(el, url, prov)

    # Table.
    if tag == "table":
        return _process_table(el, url, prov)

    # Horizontal rule.
    if tag == "hr":
        return [
            ThematicBreak.model_construct(
                id=_fast_id(), attr=_DEFAULT_ATTR, provenance=prov, node_type="thematic_break"
            )
        ]

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
            return [_mk_paragraph(tuple(inlines), prov)]
        return []

    # Unknown tags — try to process children as blocks.
    blocks = _process_children_as_blocks(el, url)
    if blocks:
        return blocks

    # Last resort: try as inline content.
    inlines = tuple(_process_inlines(el, url))
    if inlines:
        return [_mk_paragraph(inlines, prov)]

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
    After the initial pass, runs a merge pass that collapses "orphan inline"
    paragraphs into the preceding paragraph (see :func:`_merge_orphan_inlines`).
    """
    result: list[Block] = []
    prov = _make_provenance(url)

    # Leading text in the element.
    text = _strip_or_empty(el.text)
    if text:
        collapsed = _collapse_whitespace(text).strip()
        if collapsed:
            result.append(_mk_paragraph((_mk_text(collapsed),), prov))

    for child in el:
        if not isinstance(child.tag, str):
            # Comment/PI — grab tail text.
            tail = _strip_or_empty(child.tail)
            if tail:
                collapsed = _collapse_whitespace(tail).strip()
                if collapsed:
                    result.append(_mk_paragraph((_mk_text(collapsed),), prov))
            continue

        blocks = _process_element(child, url)
        result.extend(blocks)

        # Tail text after this child element.
        tail = _strip_or_empty(child.tail)
        if tail:
            collapsed = _collapse_whitespace(tail).strip()
            if collapsed:
                result.append(_mk_paragraph((_mk_text(collapsed),), prov))

    return _merge_orphan_inlines(result)


# Characters that are inline decorations when they appear as the sole
# content of a block-level element.  These are typically trademark,
# copyright, or footnote symbols that styled HTML (EDGAR, PDF-to-HTML)
# places in separate <div> or <span> elements at block level.
_ORPHAN_INLINE_CHARS = frozenset("®™©℠¹²³⁴⁵⁶⁷⁸⁹⁰*†‡§¶")

# Maximum length of a paragraph's text for it to be considered an orphan.
_ORPHAN_MAX_LEN = 4


def _is_orphan_inline(block: Block) -> bool:
    """True if ``block`` is a short paragraph that's really inline content.

    Detects paragraphs that contain only:
    - Trademark/copyright symbols (®, ™, ©)
    - Superscript numbers (¹, ², ³, etc.)
    - Footnote markers (*, †, ‡)
    - Other single characters that styled HTML placed in their own block

    These arise from EDGAR/XBRL, PDF-to-HTML converters, and other tools
    that use ``<div>`` or ``<span>`` at block level for what should be
    inline content.
    """
    if block.node_type != "paragraph":
        return False
    children = block.children
    if not children:
        return False
    # Extract the text content of the paragraph.
    text = "".join(
        c.value for c in children if hasattr(c, "value") and isinstance(c.value, str)
    ).strip()
    if not text or len(text) > _ORPHAN_MAX_LEN:
        return False
    # All characters must be in the orphan set.
    return all(ch in _ORPHAN_INLINE_CHARS for ch in text)


def _merge_orphan_inlines(blocks: list[Block]) -> list[Block]:
    """Merge orphan-inline paragraphs into the preceding paragraph.

    After the main block-processing pass, the block list may contain
    sequences like::

        Paragraph("iPhone")
        Paragraph("®")           ← orphan inline
        Paragraph(" is the ...")

    This function collapses them into::

        Paragraph("iPhone® is the ...")

    Only paragraphs whose entire text content is in
    :data:`_ORPHAN_INLINE_CHARS` and is ≤ :data:`_ORPHAN_MAX_LEN`
    characters are merged.  All other blocks pass through unchanged.
    """
    if len(blocks) < 2:
        return blocks

    merged: list[Block] = [blocks[0]]
    for block in blocks[1:]:
        if _is_orphan_inline(block) and merged and merged[-1].node_type == "paragraph":
            # Merge: append the orphan's children to the preceding paragraph.
            prev = merged[-1]
            new_children = tuple(prev.children) + tuple(block.children)
            merged[-1] = _mk_paragraph(new_children, prev.provenance)
        elif (
            merged
            and merged[-1].node_type == "paragraph"
            and block.node_type == "paragraph"
            and _is_orphan_inline(merged[-1])
        ):
            # Edge case: the PREVIOUS block was an orphan that didn't
            # have a predecessor to merge into — merge forward instead.
            new_children = tuple(merged[-1].children) + tuple(block.children)
            merged[-1] = _mk_paragraph(new_children, block.provenance)
        else:
            merged.append(block)
    return merged


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
    # Strip leading newline per HTML spec (browsers do this for <pre>)
    if value.startswith("\n"):
        value = value[1:]
    return [
        CodeBlock.model_construct(
            id=_fast_id(),
            attr=_DEFAULT_ATTR,
            provenance=prov,
            node_type="codeblock",
            language=language,
            value=value,
        )
    ]


def _process_list_items(el: HtmlElement, url: str) -> list[ListItem]:
    """Process <li> children of a list element."""
    items: list[ListItem] = []
    prov = _make_provenance(url)

    for child in el:
        if not isinstance(child.tag, str):
            continue
        if child.tag.lower() != "li":
            continue

        # Check if <li> has block-level children (p, ul, ol, blockquote, etc.)
        has_block_children = any(
            isinstance(c.tag, str) and c.tag.lower() in _BLOCK_LEVEL_TAGS for c in child
        )

        if has_block_children:
            blocks = _process_children_as_blocks(child, url)
        else:
            # Inline-only content — wrap in a single paragraph
            inlines = tuple(_process_inlines(child, url))
            if inlines and not _is_whitespace_only_inlines(inlines):
                inlines = tuple(_trim_inline_whitespace(list(inlines)))
                blocks = [_mk_paragraph(inlines, prov)]
            else:
                blocks = []

        if blocks:
            items.append(
                ListItem.model_construct(
                    id=_fast_id(),
                    attr=_DEFAULT_ATTR,
                    provenance=prov,
                    node_type="list_item",
                    checked=None,
                    children=tuple(blocks),
                )
            )

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
                        DefinitionItem.model_construct(
                            id=_fast_id(),
                            attr=_DEFAULT_ATTR,
                            provenance=prov,
                            node_type="definition_item",
                            term=term,
                            definitions=tuple(current_defs),
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
                    blocks = [_mk_paragraph(inlines, prov)]
            if blocks:
                current_defs.append(tuple(blocks))

    # Flush remaining.
    if current_terms:
        for term in current_terms:
            items.append(
                DefinitionItem.model_construct(
                    id=_fast_id(),
                    attr=_DEFAULT_ATTR,
                    provenance=prov,
                    node_type="definition_item",
                    term=term,
                    definitions=tuple(current_defs) if current_defs else (),
                )
            )

    if not items:
        return []
    return [
        DefinitionList.model_construct(
            id=_fast_id(),
            attr=_DEFAULT_ATTR,
            provenance=prov,
            node_type="definition_list",
            children=tuple(items),
        )
    ]


def _process_table(el: HtmlElement, url: str, prov: Provenance | None) -> list[Block]:
    """Process <table> into Table AST node."""
    head: TableSection | None = None
    bodies: list[TableSection] = []

    # Process <thead>.
    thead = el.find("thead")
    if thead is not None:
        rows = _process_table_rows(thead, url, is_header=True)
        if rows:
            head = TableSection.model_construct(
                id=_fast_id(),
                attr=_DEFAULT_ATTR,
                provenance=None,
                node_type="table_section",
                rows=tuple(rows),
            )

    # Process <tbody> elements.
    tbodies = el.findall("tbody")
    if tbodies:
        for tbody in tbodies:
            rows = _process_table_rows(tbody, url, is_header=False)
            if rows:
                bodies.append(
                    TableSection.model_construct(
                        id=_fast_id(),
                        attr=_DEFAULT_ATTR,
                        provenance=None,
                        node_type="table_section",
                        rows=tuple(rows),
                    )
                )
    else:
        # No explicit <tbody>: process direct <tr> children.
        rows = _process_table_rows(el, url, is_header=False)
        if rows:
            bodies.append(
                TableSection.model_construct(
                    id=_fast_id(),
                    attr=_DEFAULT_ATTR,
                    provenance=None,
                    node_type="table_section",
                    rows=tuple(rows),
                )
            )

    # Process <tfoot>.
    foot: TableSection | None = None
    tfoot = el.find("tfoot")
    if tfoot is not None:
        rows = _process_table_rows(tfoot, url, is_header=False)
        if rows:
            foot = TableSection.model_construct(
                id=_fast_id(),
                attr=_DEFAULT_ATTR,
                provenance=None,
                node_type="table_section",
                rows=tuple(rows),
            )

    # Extract caption.
    caption: Caption | None = None
    cap_el = el.find("caption")
    if cap_el is not None:
        inlines = tuple(_process_inlines(cap_el, url))
        if inlines:
            caption = Caption.model_construct(short=None, body=(_mk_paragraph(inlines, prov),))

    if not head and not bodies and not foot:
        return []

    return [
        Table.model_construct(
            id=_fast_id(),
            attr=_DEFAULT_ATTR,
            provenance=prov,
            node_type="table",
            caption=caption,
            col_specs=(),
            head=head,
            bodies=tuple(bodies),
            foot=foot,
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
                    blocks = [_mk_paragraph(inlines, _make_provenance(url))]

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
                Cell.model_construct(
                    id=_fast_id(),
                    attr=_DEFAULT_ATTR,
                    provenance=None,
                    node_type="cell",
                    alignment=None,
                    content=tuple(blocks),
                    col_span=col_span,
                    row_span=row_span,
                )
            )

        if cells:
            rows.append(
                Row.model_construct(
                    id=_fast_id(),
                    attr=_DEFAULT_ATTR,
                    provenance=None,
                    node_type="row",
                    cells=tuple(cells),
                )
            )

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
                caption = Caption.model_construct(short=None, body=(_mk_paragraph(inlines, prov),))
        elif tag == "img":
            src = _get_image_src(child)
            if src:
                resolved = _resolve_url(src, url)
                if _is_safe_url(resolved):
                    alt = child.get("alt", "")
                    img = _mk_image(resolved, alt or None)
                    children.append(_mk_paragraph((img,), prov))
        else:
            blocks = _process_element(child, url)
            children.extend(blocks)

    if not children and not caption:
        return []

    return [
        Figure.model_construct(
            id=_fast_id(),
            attr=_DEFAULT_ATTR,
            provenance=prov,
            node_type="figure",
            caption=caption,
            children=tuple(children),
        )
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def html_to_document(
    html_content: str,
    *,
    url: str = "",
    extract_content: bool = True,
    content_scope: float = 0.5,
    strip_xbrl: bool | None = None,
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

    Returns:
        ContentDocument with Block/Inline AST nodes and provenance.
    """
    if not html_content or not html_content.strip():
        return _empty_document()

    # Strip Inline XBRL if requested or auto-detected.
    if strip_xbrl is True or (strip_xbrl is None and _looks_like_xbrl(html_content)):
        html_content = _strip_inline_xbrl(html_content)

    root: HtmlElement | None = None
    full_doc: HtmlElement | None = None  # Parsed once, reused if needed

    if extract_content:
        # Try Level 3 learned model first, fall back to heuristic readability.
        try:
            root = extract_content_l3(html_content, content_scope=content_scope)
        except Exception:
            root = None

        # If L3 returned nothing and scope was strict, retry with default scope
        # before falling back to the heuristic (which ignores content_scope).
        if root is None and content_scope < 0.4:
            with contextlib.suppress(Exception):
                root = extract_content_l3(html_content, content_scope=0.5)

        if root is None:
            root = readability_extract(html_content)

        # Guard: if extraction returned a suspiciously small fragment,
        # fall back to semantic container extraction.
        if root is not None:
            readability_words = len((root.text_content() or "").split())
            if readability_words < _MIN_READABILITY_WORDS:
                try:
                    full_doc = lxml_html.document_fromstring(html_content)
                except Exception:
                    full_doc = None
                if full_doc is not None and full_doc.body is not None:
                    body_words = len((full_doc.body.text_content() or "").split())
                    if body_words > _MIN_READABILITY_WORDS * 4:
                        # Extraction returned too little — try semantic containers.
                        semantic = _find_semantic_container(full_doc.body)
                        root = semantic if semantic is not None else full_doc.body

    if root is None:
        # Parse full document and use <body>.
        if full_doc is None:
            try:
                full_doc = lxml_html.document_fromstring(html_content)
            except Exception:
                return _empty_document()
        root = full_doc.body if full_doc is not None else None
        if root is None:
            return _empty_document()

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
