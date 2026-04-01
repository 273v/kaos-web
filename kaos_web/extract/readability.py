"""Readability algorithm for extracting main content from HTML.

Port of Mozilla's Readability.js / readability-lxml approach. Takes raw HTML and
returns the cleaned main-content element subtree.
"""

from __future__ import annotations

import math
import re

from lxml import html as lxml_html
from lxml.html import HtmlElement

# ---------------------------------------------------------------------------
# Regex patterns for candidate classification
# ---------------------------------------------------------------------------

_NEGATIVE_RE = re.compile(
    r"comment|sidebar|footer|footnote|nav|ad[-_]?|sponsor|social|share|widget"
    r"|popup|banner|cookie|modal|menu|breadcrumb|pager|pagination|promo|related"
    r"|shoutbox|combx|masthead|media|meta|outbrain|taboola|disqus",
    re.IGNORECASE,
)

_POSITIVE_RE = re.compile(
    r"article|body|content|entry|main|post|text|blog|story|hentry|page",
    re.IGNORECASE,
)

# Block-level elements that indicate a <div> is truly structural.
_BLOCK_LEVEL_TAGS = frozenset(
    {
        "p",
        "ul",
        "ol",
        "table",
        "pre",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "dl",
        "figure",
        "figcaption",
        "details",
        "address",
        "fieldset",
        "form",
        "aside",
        "main",
        "nav",
        "div",
    }
)

# Tags that are always removed before scoring.
_STRIP_TAGS = frozenset(
    {
        "script",
        "style",
        "link",
        "noscript",
        "iframe",
        "embed",
        "object",
        "applet",
        "svg",
    }
)

# Tags scored as content paragraphs.
_SCORE_TAGS = frozenset({"p", "pre", "td"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _class_id_string(el: HtmlElement) -> str:
    """Combine class and id into a single string for regex matching."""
    parts: list[str] = []
    cls = el.get("class", "")
    if cls:
        parts.append(cls)
    eid = el.get("id", "")
    if eid:
        parts.append(eid)
    return " ".join(parts)


def _class_weight(el: HtmlElement) -> float:
    """Score +25 for positive class/id, -25 for negative."""
    weight = 0.0
    ci = _class_id_string(el)
    if not ci:
        return weight
    if _NEGATIVE_RE.search(ci):
        weight -= 25.0
    if _POSITIVE_RE.search(ci):
        weight += 25.0
    return weight


def _tag_weight(el: HtmlElement) -> float:
    """Base score adjustment by tag name."""
    tag = el.tag
    if tag in ("article", "div"):
        return 5.0
    if tag in ("pre", "blockquote"):
        return 3.0
    if tag in ("form", "aside"):
        return -3.0
    if tag in ("nav", "footer", "header"):
        return -5.0
    return 0.0


def _text_content(el: HtmlElement) -> str:
    """Get all text content of an element (including children)."""
    return el.text_content() or ""


def _link_density(el: HtmlElement) -> float:
    """Fraction of text inside <a> tags relative to total text."""
    total = len(_text_content(el))
    if total == 0:
        return 0.0
    link_len = 0
    for a in el.iter("a"):
        link_len += len(_text_content(a))
    return link_len / total


def _inner_text_length(el: HtmlElement) -> int:
    """Length of text content after stripping whitespace."""
    return len(_text_content(el).strip())


def _has_block_child(el: HtmlElement) -> bool:
    """Check if the element has any block-level child elements."""
    return any(isinstance(child.tag, str) and child.tag in _BLOCK_LEVEL_TAGS for child in el)


# ---------------------------------------------------------------------------
# Main algorithm
# ---------------------------------------------------------------------------


def _strip_unlikely(root: HtmlElement) -> None:
    """Remove elements that are unlikely to be main content."""
    to_remove: list[HtmlElement] = []
    for el in root.iter():
        # Skip non-element nodes and key structural tags.
        if not isinstance(el.tag, str):
            continue
        tag = el.tag
        if tag in ("html", "body", "article", "main"):
            continue
        if tag in _STRIP_TAGS:
            to_remove.append(el)
            continue
        ci = _class_id_string(el)
        if not ci:
            continue
        if _NEGATIVE_RE.search(ci) and not _POSITIVE_RE.search(ci) and _inner_text_length(el) < 200:
            to_remove.append(el)

    for el in to_remove:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)


def _transform_divs(root: HtmlElement) -> None:
    """Convert phrasing-only divs to <p> tags."""
    for div in list(root.iter("div")):
        if not _has_block_child(div):
            div.tag = "p"


def _score_candidates(root: HtmlElement) -> dict[HtmlElement, float]:
    """Score all candidate containers by content density."""
    candidates: dict[HtmlElement, float] = {}

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag not in _SCORE_TAGS:
            continue

        text = _text_content(el).strip()
        if len(text) < 25:
            continue

        parent = el.getparent()
        grandparent = parent.getparent() if parent is not None else None

        # Initialize parent/grandparent if needed.
        if parent is not None and parent not in candidates:
            candidates[parent] = _class_weight(parent) + _tag_weight(parent)
        if grandparent is not None and grandparent not in candidates:
            candidates[grandparent] = _class_weight(grandparent) + _tag_weight(grandparent)

        # Base score: 1 point per scored paragraph.
        score = 1.0

        # Comma bonus.
        score += text.count(",")

        # Length bonus: min(floor(len/100), 3).
        score += min(math.floor(len(text) / 100), 3)

        # Add to parent and grandparent.
        if parent is not None:
            candidates[parent] = candidates.get(parent, 0.0) + score
        if grandparent is not None:
            candidates[grandparent] = candidates.get(grandparent, 0.0) + score * 0.5

    return candidates


def _select_best(candidates: dict[HtmlElement, float]) -> HtmlElement | None:
    """Select the highest-scoring candidate, adjusted by link density."""
    if not candidates:
        return None

    best: HtmlElement | None = None
    best_score = -1e9

    for el, score in candidates.items():
        adjusted = score * (1.0 - _link_density(el))
        if adjusted > best_score:
            best_score = adjusted
            best = el

    return best


def _collect_siblings(
    best: HtmlElement,
    best_score: float,
    candidates: dict[HtmlElement, float],
) -> list[HtmlElement]:
    """Collect qualifying sibling elements around the best candidate."""
    parent = best.getparent()
    if parent is None:
        return [best]

    threshold = max(10.0, best_score * 0.2)
    result: list[HtmlElement] = []

    for sibling in parent:
        if sibling is best:
            result.append(sibling)
            continue
        if not isinstance(sibling.tag, str):
            continue

        sib_score = candidates.get(sibling, 0.0)
        # Boost siblings with matching class.
        if sibling.get("class") and sibling.get("class") == best.get("class"):
            sib_score += best_score * 0.2

        adjusted = sib_score * (1.0 - _link_density(sibling))
        if adjusted >= threshold:
            result.append(sibling)
        elif sibling.tag == "p":
            # Include sibling paragraphs with minimal link density and some text.
            ld = _link_density(sibling)
            text_len = _inner_text_length(sibling)
            if text_len > 80 and ld < 0.25:
                result.append(sibling)

    return result


def extract_content(html: str) -> HtmlElement | None:
    """Extract main content from HTML using a readability algorithm.

    Returns the cleaned content element tree, or ``None`` if no content found.
    The result is an ``lxml.html.HtmlElement`` containing the extracted content
    subtree.  Callers can serialize with ``lxml.html.tostring(el)`` or pass it
    directly to ``html_to_document``.

    Args:
        html: Raw HTML string (may be a full page or a fragment).

    Returns:
        Cleaned content element, or ``None`` if extraction fails.
    """
    if not html or not html.strip():
        return None

    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return None

    body = doc.body
    if body is None:
        return None

    # Step 1: Strip unlikely candidates.
    _strip_unlikely(body)

    # Step 2: Transform phrasing-only divs to <p>.
    _transform_divs(body)

    # Step 3: Score candidates.
    candidates = _score_candidates(body)

    if not candidates:
        # Fallback: return body if it has meaningful text.
        if _inner_text_length(body) > 50:
            return body
        return None

    # Step 4: Select best candidate.
    best = _select_best(candidates)
    if best is None:
        return None

    # Compute the adjusted best score for sibling threshold.
    best_adjusted = candidates.get(best, 0.0) * (1.0 - _link_density(best))

    # Step 5: Collect qualifying siblings.
    siblings = _collect_siblings(best, best_adjusted, candidates)

    if len(siblings) == 1:
        return siblings[0]

    # Wrap siblings in a <div>.
    wrapper = lxml_html.fragment_fromstring("<div></div>", create_parent=False)
    for sib in siblings:
        parent = sib.getparent()
        if parent is not None:
            parent.remove(sib)
        wrapper.append(sib)

    return wrapper
