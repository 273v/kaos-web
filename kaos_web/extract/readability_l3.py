"""Level 3 readability: learned content extraction via logistic regression.

Replaces the heuristic readability algorithm with a pre-trained logistic
regression model that scores DOM nodes on 35 structural, textual, and
semantic features. The model was trained on a 10-page labeled corpus
covering articles, directories, search results, dockets, and listings.

No ML runtime dependency — inference is a dot product + sigmoid (~1 μs/node).
Weights are baked in as constants from the training run.

The ``content_scope`` parameter (0.0-1.0) controls the classification
threshold: 0.0 = strict (article-only), 0.5 = balanced, 1.0 = permissive.

Usage::

    from kaos_web.extract.readability_l3 import extract_content_l3

    element = extract_content_l3(html, content_scope=0.5)
"""

from __future__ import annotations

import math
import re

from lxml import html as lxml_html
from lxml.html import HtmlElement

from kaos_web.extract.readability import (
    _NEGATIVE_RE,
    _POSITIVE_RE,
    _SCORE_TAGS,
    _STRIP_TAGS,
    _class_weight,
    _has_block_child,
    _inner_text_length,
    _link_density,
    _tag_weight,
    _text_content,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"[.!?]+")

# Block-level tags eligible for scoring.
_CANDIDATE_TAGS = frozenset(
    {
        "article",
        "main",
        "section",
        "div",
        "p",
        "pre",
        "td",
        "ul",
        "ol",
        "table",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "form",
        "aside",
        "nav",
        "header",
        "footer",
    }
)

# Feature order must exactly match training. Do not reorder.
_FEATURE_ORDER: tuple[str, ...] = (
    "log_text_length",
    "log_text_density",
    "log_descendant_count",
    "link_density",
    "non_link_text_ratio",
    "class_weight",
    "tag_weight",
    "depth",
    "position_centrality",
    "sibling_log_text_density",
    "same_tag_sibling_ratio",
    "same_class_sibling_ratio",
    "sentence_score",
    "comma_density",
    "has_block_children",
    "paragraph_score_density",
    "anchor_count_ratio",
    "interactive_ratio",
    "heading_ratio",
    "paragraph_ratio",
    "list_item_ratio",
    "repeated_child_tag_ratio",
    "repeated_child_signature_ratio",
    "positive_class",
    "negative_class",
    "ancestor_positive_bias",
    "ancestor_negative_bias",
    "is_article_like",
    "is_paragraph_like",
    "is_list_like",
    "is_boilerplate_tag",
    "is_form_tag",
    "under_content_landmark",
    "under_boilerplate_landmark",
    "has_repeated_children",
)

# ---------------------------------------------------------------------------
# Pre-trained model weights
# Trained on 10-page corpus (279 records, 150 positive, 129 negative)
# Training F1: 0.987 | Leave-one-page-out F1: 0.903
# ---------------------------------------------------------------------------

_BIAS = -0.486090

_WEIGHTS: tuple[float, ...] = (
    -0.162834,  # log_text_length
    +0.012019,  # log_text_density
    +0.002070,  # log_descendant_count
    +0.072390,  # link_density
    -0.072390,  # non_link_text_ratio
    +0.338445,  # class_weight
    -0.111737,  # tag_weight
    +0.654090,  # depth
    +0.840166,  # position_centrality
    +0.124704,  # sibling_log_text_density
    +0.070097,  # same_tag_sibling_ratio
    +0.394895,  # same_class_sibling_ratio
    +0.438460,  # sentence_score
    -0.239501,  # comma_density
    -0.391829,  # has_block_children
    +0.333655,  # paragraph_score_density
    -0.062324,  # anchor_count_ratio
    -0.261470,  # interactive_ratio
    +0.126789,  # heading_ratio
    +0.243455,  # paragraph_ratio
    -0.073112,  # list_item_ratio
    -0.066230,  # repeated_child_tag_ratio
    -0.064363,  # repeated_child_signature_ratio
    -0.081514,  # positive_class
    -0.468961,  # negative_class
    +0.333316,  # ancestor_positive_bias
    -0.332848,  # ancestor_negative_bias
    +0.537384,  # is_article_like
    +0.901563,  # is_paragraph_like
    +0.250385,  # is_list_like
    -0.624699,  # is_boilerplate_tag
    -0.312407,  # is_form_tag
    +0.147389,  # under_content_landmark
    -1.903748,  # under_boilerplate_landmark
    -0.221445,  # has_repeated_children
)

_MEANS: tuple[float, ...] = (
    3.797387,
    2.979457,
    1.100273,
    0.132191,
    0.867809,
    -0.082437,
    0.222939,
    0.539785,
    0.416594,
    2.672409,
    0.311167,
    0.066985,
    0.111708,
    0.004013,
    0.340502,
    0.038935,
    0.239405,
    0.030271,
    0.036028,
    0.054054,
    0.058268,
    0.561862,
    0.539580,
    0.060932,
    0.143369,
    0.473118,
    0.146953,
    0.089606,
    0.405018,
    0.078853,
    0.125448,
    0.014337,
    0.731183,
    0.283154,
    0.580645,
)

_SCALES: tuple[float, ...] = (
    1.911634,
    1.436591,
    1.134662,
    0.247083,
    0.247083,
    0.427982,
    0.643281,
    0.287046,
    0.297674,
    1.735365,
    0.398042,
    0.221979,
    0.217161,
    0.010769,
    0.473878,
    0.143793,
    0.348313,
    0.147821,
    0.121167,
    0.169979,
    0.158559,
    0.454788,
    0.449100,
    0.239205,
    0.350449,
    0.499277,
    0.354059,
    0.285616,
    0.490896,
    0.269509,
    0.331226,
    0.118875,
    0.443345,
    0.450531,
    0.493454,
)

_FEATURE_COUNT = len(_FEATURE_ORDER)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _score_vector(features: list[float]) -> float:
    """Score a feature vector: normalize → dot product → sigmoid."""
    z = _BIAS
    for i in range(_FEATURE_COUNT):
        normalized = (features[i] - _MEANS[i]) / _SCALES[i]
        z += _WEIGHTS[i] * normalized
    return _sigmoid(z)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def _class_id_text(el: HtmlElement) -> str:
    return " ".join(filter(None, [el.get("class", ""), el.get("id", "")]))


def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _paragraph_score_density(el: HtmlElement) -> float:
    total = 0.0
    for child in el.iter():
        if not isinstance(child.tag, str) or child.tag not in _SCORE_TAGS:
            continue
        text = _text_content(child).strip()
        if not text:
            continue
        total += 1.0 + min(text.count(","), 6) * 0.4 + min(math.floor(len(text) / 120), 3)
    return total / max(_inner_text_length(el), 1)


def _max_bucket_ratio(values: list[str]) -> float:
    if not values:
        return 0.0
    buckets: dict[str, int] = {}
    for v in values:
        buckets[v] = buckets.get(v, 0) + 1
    return max(buckets.values()) / len(values)


def _node_signature(el: HtmlElement) -> str:
    cls = " ".join(sorted(filter(None, el.get("class", "").split())))
    return f"{el.tag}|{cls}"


def _extract_features(
    el: HtmlElement,
    *,
    candidate_densities: dict[int, float],
    candidate_ids: set[int],
    total_candidates: int,
    candidate_index: int,
) -> list[float]:
    """Compute the 35-feature vector for a single DOM element."""
    text = _text_content(el).strip()
    text_len = len(text)
    el_id = id(el)

    # Descendants
    descendants = [n for n in el.iterdescendants() if isinstance(n.tag, str)]
    descendant_count = len(descendants)
    anchor_count = sum(1 for n in descendants if n.tag == "a")
    interactive_count = sum(
        1
        for n in descendants
        if n.tag in {"input", "button", "select", "option", "textarea", "label"}
    )
    heading_count = sum(1 for n in descendants if n.tag in {"h1", "h2", "h3", "h4"})
    paragraph_count = sum(1 for n in descendants if n.tag in {"p", "pre", "blockquote"})
    list_item_count = sum(1 for n in descendants if n.tag == "li")

    # Siblings
    parent = el.getparent()
    siblings: list[HtmlElement] = []
    sibling_densities: list[float] = []
    if parent is not None:
        for sib in parent:
            if sib is el or not isinstance(sib.tag, str):
                continue
            siblings.append(sib)
            sib_id = id(sib)
            if sib_id in candidate_ids:
                sibling_densities.append(candidate_densities.get(sib_id, 0.0))

    sibling_count = len(siblings)
    same_tag_siblings = sum(1 for s in siblings if s.tag == el.tag)
    cls = el.get("class", "")
    same_class_siblings = sum(1 for s in siblings if cls and s.get("class", "") == cls)

    # Children
    direct_children = [c for c in el if isinstance(c.tag, str)]
    child_tags = [c.tag for c in direct_children]
    child_sigs = [_node_signature(c) for c in direct_children]

    # Ancestors
    ancestors = [a for a in el.iterancestors() if isinstance(a.tag, str)]
    ancestor_texts = [_class_id_text(a) for a in ancestors]
    ancestor_pos = 1.0 if any(_POSITIVE_RE.search(t) for t in ancestor_texts) else 0.0
    ancestor_neg = 1.0 if any(_NEGATIVE_RE.search(t) for t in ancestor_texts) else 0.0
    under_content = 1.0 if any(a.tag in {"main", "article", "section"} for a in ancestors) else 0.0
    under_boilerplate = (
        1.0
        if any(a.tag in {"aside", "nav", "header", "footer", "form"} for a in ancestors)
        else 0.0
    )

    sentence_count = len(_SENTENCE_RE.findall(text))
    tag = el.tag
    repeated_sig_ratio = _max_bucket_ratio(child_sigs)

    return [
        math.log1p(text_len),  # log_text_length
        math.log1p(candidate_densities.get(el_id, 0.0)),  # log_text_density
        math.log1p(descendant_count),  # log_descendant_count
        _link_density(el),  # link_density
        1.0 - _link_density(el),  # non_link_text_ratio
        _class_weight(el) / 25.0,  # class_weight
        _tag_weight(el) / 5.0,  # tag_weight
        len(list(el.iterancestors())) / 10.0,  # depth
        (
            1.0
            if total_candidates <= 1  # position_centrality
            else 1.0 - abs((candidate_index / (total_candidates - 1)) - 0.5) * 2.0
        ),
        math.log1p(_safe_mean(sibling_densities)),  # sibling_log_text_density
        same_tag_siblings / max(sibling_count, 1),  # same_tag_sibling_ratio
        same_class_siblings / max(sibling_count, 1),  # same_class_sibling_ratio
        min(sentence_count, 12) / 12.0,  # sentence_score
        min(text.count(","), 12) / max(text_len, 1),  # comma_density
        1.0 if _has_block_child(el) else 0.0,  # has_block_children
        _paragraph_score_density(el),  # paragraph_score_density
        anchor_count / max(descendant_count, 1),  # anchor_count_ratio
        interactive_count / max(descendant_count, 1),  # interactive_ratio
        heading_count / max(descendant_count, 1),  # heading_ratio
        paragraph_count / max(descendant_count, 1),  # paragraph_ratio
        list_item_count / max(descendant_count, 1),  # list_item_ratio
        _max_bucket_ratio(child_tags),  # repeated_child_tag_ratio
        repeated_sig_ratio,  # repeated_child_signature_ratio
        1.0 if _POSITIVE_RE.search(_class_id_text(el)) else 0.0,  # positive_class
        1.0 if _NEGATIVE_RE.search(_class_id_text(el)) else 0.0,  # negative_class
        ancestor_pos,  # ancestor_positive_bias
        ancestor_neg,  # ancestor_negative_bias
        1.0 if tag in {"article", "main", "section"} else 0.0,  # is_article_like
        1.0
        if tag in {"p", "pre", "blockquote", "td", "h1", "h2", "h3"}
        else 0.0,  # is_paragraph_like
        1.0 if tag in {"ul", "ol", "table"} else 0.0,  # is_list_like
        1.0 if tag in {"nav", "aside", "header", "footer"} else 0.0,  # is_boilerplate_tag
        1.0 if tag == "form" else 0.0,  # is_form_tag
        under_content,  # under_content_landmark
        under_boilerplate,  # under_boilerplate_landmark
        1.0 if repeated_sig_ratio >= 0.5 else 0.0,  # has_repeated_children
    ]


# ---------------------------------------------------------------------------
# Content region selection
# ---------------------------------------------------------------------------


def _strip_non_content_tags(root: HtmlElement) -> None:
    """Remove script, style, and other non-content tags."""
    to_remove = [el for el in root.iter() if isinstance(el.tag, str) and el.tag in _STRIP_TAGS]
    for el in to_remove:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)


def extract_content_l3(html: str, content_scope: float = 0.5) -> HtmlElement | None:
    """Extract main content using the Level 3 learned model.

    Scores every candidate block-level element in the DOM, then selects
    the best content region based on scores and the ``content_scope``
    threshold.

    Args:
        html: Raw HTML string.
        content_scope: Extraction breadth from 0.0 (strict) to 1.0
            (permissive). Default 0.5 matches balanced precision/recall.

    Returns:
        Content element subtree, or ``None`` if no content found.
    """
    if not html or not html.strip():
        return None

    content_scope = max(0.0, min(1.0, content_scope))
    threshold = 1.0 - content_scope

    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return None

    body = doc.body
    if body is None:
        return None

    # Step 1: Strip non-content tags.
    _strip_non_content_tags(body)

    # Step 2: Collect candidate elements and pre-compute text densities.
    candidates: list[HtmlElement] = [
        el for el in body.iter() if isinstance(el.tag, str) and el.tag in _CANDIDATE_TAGS
    ]
    if not candidates:
        return body if _inner_text_length(body) > 50 else None

    candidate_ids: set[int] = set()
    candidate_densities: dict[int, float] = {}
    for el in candidates:
        el_id = id(el)
        candidate_ids.add(el_id)
        text_len = _inner_text_length(el)
        desc_count = sum(1 for n in el.iterdescendants() if isinstance(n.tag, str))
        candidate_densities[el_id] = text_len / max(desc_count, 1)

    # Step 3: Score every candidate.
    scored: list[tuple[HtmlElement, float]] = []
    total = len(candidates)
    for idx, el in enumerate(candidates):
        features = _extract_features(
            el,
            candidate_densities=candidate_densities,
            candidate_ids=candidate_ids,
            total_candidates=total,
            candidate_index=idx,
        )
        score = _score_vector(features)
        scored.append((el, score))

    # Step 4: Find the best container using readability-style parent accumulation.
    # For each high-scoring leaf element, propagate its contribution to parent
    # and grandparent containers. This selects the container that wraps the most
    # content by volume, not the element with the highest individual score.
    container_scores: dict[int, float] = {}
    container_map: dict[int, HtmlElement] = {}

    for el, score in scored:
        if score < threshold:
            continue

        # Weight by text length so large paragraphs contribute more than
        # tiny table cells. log1p prevents a single huge block from dominating.
        text_weight = math.log1p(_inner_text_length(el))
        contribution = score * text_weight

        parent = el.getparent()
        if parent is not None:
            pid = id(parent)
            container_scores[pid] = container_scores.get(pid, 0.0) + contribution
            container_map[pid] = parent

            grandparent = parent.getparent()
            if grandparent is not None:
                gpid = id(grandparent)
                container_scores[gpid] = container_scores.get(gpid, 0.0) + contribution * 0.5
                container_map[gpid] = grandparent

    if not container_scores:
        return body if _inner_text_length(body) > 50 else None

    # Select the best container, penalized by link density.
    best_el: HtmlElement | None = None
    best_aggregate = -1.0
    for cid, raw_score in container_scores.items():
        el = container_map[cid]
        adjusted = raw_score * (1.0 - _link_density(el))
        if adjusted > best_aggregate:
            best_aggregate = adjusted
            best_el = el

    if best_el is None:
        return body if _inner_text_length(body) > 50 else None

    # Step 5: Collect qualifying siblings of the best element.
    parent = best_el.getparent()
    if parent is None:
        return best_el

    # Sibling inclusion threshold: 20% of best score, minimum 10
    sib_threshold = max(10.0, best_aggregate * 0.2)
    siblings: list[HtmlElement] = []
    for sib in parent:
        if sib is best_el:
            siblings.append(sib)
            continue
        if not isinstance(sib.tag, str):
            continue
        sib_cid = id(sib)
        sib_score = container_scores.get(sib_cid, 0.0)
        # Boost siblings with matching class
        if sib.get("class") and sib.get("class") == best_el.get("class"):
            sib_score += best_aggregate * 0.2
        sib_adjusted = sib_score * (1.0 - _link_density(sib))
        if sib_adjusted >= sib_threshold:
            siblings.append(sib)

    if len(siblings) == 1:
        return siblings[0]

    # Wrap siblings in a div.
    wrapper = lxml_html.fragment_fromstring("<div></div>", create_parent=False)
    for sib in siblings:
        sib_parent = sib.getparent()
        if sib_parent is not None:
            sib_parent.remove(sib)
        wrapper.append(sib)

    return wrapper
