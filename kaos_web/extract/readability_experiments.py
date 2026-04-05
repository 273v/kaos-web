"""Experiment harness for evaluating parametric readability approaches.

This module is intentionally separate from the production extractor. It lets us
compare three approaches on a labeled DOM-node corpus:

- Level 1: threshold-scaled version of the current heuristic extractor
- Level 2: fixed continuous heuristic scoring
- Level 3: logistic regression trained on DOM-node features

The goal is to answer a practical question before changing runtime behavior:
does a lightweight learned model outperform the hand-tuned heuristics on
directory, listing, and multi-section layouts?
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

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

DEFAULT_CORPUS_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "readability" / "corpus.json"
)

_NODE_ID_ATTR = "data-kaos-exp-id"
_SENTENCE_RE = re.compile(r"[.!?]+")
_CONTENT_TAGS = frozenset(
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
_TAG_FEATURES = (
    "is_article_like",
    "is_paragraph_like",
    "is_list_like",
    "is_boilerplate_tag",
    "is_form_tag",
    "under_content_landmark",
    "under_boilerplate_landmark",
    "has_repeated_children",
)
_NUMERIC_FEATURES = (
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
)
FEATURE_ORDER = _NUMERIC_FEATURES + _TAG_FEATURES
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "text_content": (
        "log_text_length",
        "log_text_density",
        "log_descendant_count",
        "sentence_score",
        "comma_density",
        "paragraph_score_density",
        "paragraph_ratio",
    ),
    "link_interactive": (
        "link_density",
        "non_link_text_ratio",
        "anchor_count_ratio",
        "interactive_ratio",
        "heading_ratio",
    ),
    "structure_context": (
        "depth",
        "position_centrality",
        "sibling_log_text_density",
        "same_tag_sibling_ratio",
        "same_class_sibling_ratio",
        "has_block_children",
        "under_content_landmark",
        "under_boilerplate_landmark",
    ),
    "template_repetition": (
        "list_item_ratio",
        "repeated_child_tag_ratio",
        "repeated_child_signature_ratio",
        "has_repeated_children",
        "is_list_like",
    ),
    "semantic_priors": (
        "class_weight",
        "tag_weight",
        "positive_class",
        "negative_class",
        "ancestor_positive_bias",
        "ancestor_negative_bias",
        "is_article_like",
        "is_paragraph_like",
        "is_boilerplate_tag",
        "is_form_tag",
    ),
}


@dataclass(frozen=True)
class NodeRecord:
    """A labeled candidate node."""

    page_name: str
    node_id: str
    xpath: str
    tag: str
    label: int
    features: dict[str, float]

    def vector(self) -> list[float]:
        """Return the feature vector in a stable order."""
        return [self.features[name] for name in FEATURE_ORDER]

    def masked_vector(self, enabled_features: set[str] | None = None) -> list[float]:
        """Return a feature vector with disabled features zeroed out."""
        if enabled_features is None:
            return self.vector()
        return [
            self.features[name] if name in enabled_features else 0.0
            for name in FEATURE_ORDER
        ]


@dataclass(frozen=True)
class LabeledPage:
    """A parsed HTML page with DOM-node labels."""

    name: str
    fixture_path: Path
    html: str
    records: tuple[NodeRecord, ...]
    positive_region_ids: frozenset[str]
    negative_region_ids: frozenset[str]


@dataclass(frozen=True)
class BinaryMetrics:
    """Aggregate binary classification metrics."""

    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    f1: float
    accuracy: float


@dataclass(frozen=True)
class ExperimentSummary:
    """Aggregated results for one model across held-out pages."""

    name: str
    scope: float
    threshold: float
    metrics: BinaryMetrics
    top_hit_rate: float
    per_page_f1: dict[str, float]


@dataclass(frozen=True)
class AblationSummary:
    """Result of a leave-one-feature-group-out Level 3 experiment."""

    name: str
    dropped_group: str | None
    enabled_features: tuple[str, ...]
    metrics: BinaryMetrics
    top_hit_rate: float
    delta_f1: float


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _safe_mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def _class_id_text(el: HtmlElement) -> str:
    return " ".join(filter(None, [el.get("class", ""), el.get("id", "")]))


def _assign_node_ids(root: HtmlElement) -> None:
    for index, el in enumerate(root.iter()):
        if isinstance(el.tag, str):
            el.set(_NODE_ID_ATTR, f"n{index}")


def _candidate_elements(root: HtmlElement) -> list[HtmlElement]:
    return [
        el
        for el in root.iter()
        if isinstance(el.tag, str) and el.tag in _CONTENT_TAGS and el.get(_NODE_ID_ATTR)
    ]


def _expand_region_ids(root: HtmlElement, selectors: list[str]) -> set[str]:
    region_ids: set[str] = set()
    for selector in selectors:
        for node in root.xpath(selector):
            if not isinstance(node, HtmlElement):
                continue
            for el in node.iter():
                node_id = el.get(_NODE_ID_ATTR)
                if node_id:
                    region_ids.add(node_id)
    return region_ids


def _paragraph_score_density(el: HtmlElement) -> float:
    total = 0.0
    for child in el.iter():
        if not isinstance(child.tag, str) or child.tag not in _SCORE_TAGS:
            continue
        text = _text_content(child).strip()
        if not text:
            continue
        score = 1.0 + min(text.count(","), 6) * 0.4 + min(math.floor(len(text) / 120), 3)
        total += score
    return total / max(_inner_text_length(el), 1)


def _descendant_tag_count(el: HtmlElement, tags: set[str]) -> int:
    return sum(1 for node in el.iterdescendants() if isinstance(node.tag, str) and node.tag in tags)


def _max_bucket_ratio(values: list[str]) -> float:
    if not values:
        return 0.0
    buckets: dict[str, int] = {}
    for value in values:
        buckets[value] = buckets.get(value, 0) + 1
    return max(buckets.values()) / len(values)


def _node_signature(el: HtmlElement) -> str:
    class_value = " ".join(sorted(filter(None, el.get("class", "").split())))
    return f"{el.tag}|{class_value}"


def _build_records(page_name: str, root: HtmlElement, labels: dict[str, int]) -> tuple[NodeRecord, ...]:
    candidates = _candidate_elements(root)
    total_candidates = len(candidates)
    densities_by_id: dict[str, float] = {}
    candidate_ids = {el.get(_NODE_ID_ATTR, "") for el in candidates}

    for el in candidates:
        text_len = _inner_text_length(el)
        descendants = sum(1 for node in el.iterdescendants() if isinstance(node.tag, str))
        text_density = text_len / max(descendants, 1)
        densities_by_id[el.get(_NODE_ID_ATTR, "")] = text_density

    records: list[NodeRecord] = []
    tree = root.getroottree()
    for index, el in enumerate(candidates):
        node_id = el.get(_NODE_ID_ATTR, "")
        if node_id not in labels:
            continue

        text = _text_content(el).strip()
        text_len = len(text)
        sentence_count = len(_SENTENCE_RE.findall(text))
        sibling_densities = []
        parent = el.getparent()
        siblings: list[HtmlElement] = []
        if parent is not None:
            for sibling in parent:
                if sibling is el or not isinstance(sibling.tag, str):
                    continue
                siblings.append(sibling)
                sibling_id = sibling.get(_NODE_ID_ATTR, "")
                if sibling_id in candidate_ids:
                    sibling_densities.append(densities_by_id.get(sibling_id, 0.0))

        descendants = [node for node in el.iterdescendants() if isinstance(node.tag, str)]
        descendant_count = len(descendants)
        anchor_count = sum(1 for node in descendants if node.tag == "a")
        interactive_count = sum(
            1
            for node in descendants
            if node.tag in {"input", "button", "select", "option", "textarea", "label"}
        )
        heading_count = sum(1 for node in descendants if node.tag in {"h1", "h2", "h3", "h4"})
        paragraph_count = sum(1 for node in descendants if node.tag in {"p", "pre", "blockquote"})
        list_item_count = sum(1 for node in descendants if node.tag == "li")
        direct_children = [child for child in el if isinstance(child.tag, str)]
        child_tags = [child.tag for child in direct_children]
        child_signatures = [_node_signature(child) for child in direct_children]
        sibling_count = len(siblings)
        same_tag_siblings = sum(1 for sibling in siblings if sibling.tag == el.tag)
        class_value = el.get("class", "")
        same_class_siblings = sum(
            1 for sibling in siblings if class_value and sibling.get("class", "") == class_value
        )
        ancestors = [ancestor for ancestor in el.iterancestors() if isinstance(ancestor.tag, str)]
        ancestor_class_values = [_class_id_text(ancestor) for ancestor in ancestors]
        ancestor_positive_bias = 1.0 if any(_POSITIVE_RE.search(value) for value in ancestor_class_values) else 0.0
        ancestor_negative_bias = 1.0 if any(_NEGATIVE_RE.search(value) for value in ancestor_class_values) else 0.0
        under_content_landmark = 1.0 if any(ancestor.tag in {"main", "article", "section"} for ancestor in ancestors) else 0.0
        under_boilerplate_landmark = 1.0 if any(
            ancestor.tag in {"aside", "nav", "header", "footer", "form"} for ancestor in ancestors
        ) else 0.0
        class_weight = _class_weight(el) / 25.0
        tag_weight = _tag_weight(el) / 5.0
        tag = el.tag
        features = {
            "log_text_length": math.log1p(text_len),
            "log_text_density": math.log1p(densities_by_id[node_id]),
            "log_descendant_count": math.log1p(descendant_count),
            "link_density": _link_density(el),
            "non_link_text_ratio": 1.0 - _link_density(el),
            "class_weight": class_weight,
            "tag_weight": tag_weight,
            "depth": len(list(el.iterancestors())) / 10.0,
            "position_centrality": (
                1.0
                if total_candidates <= 1
                else 1.0 - abs((index / (total_candidates - 1)) - 0.5) * 2.0
            ),
            "sibling_log_text_density": math.log1p(_safe_mean(sibling_densities)),
            "same_tag_sibling_ratio": same_tag_siblings / max(sibling_count, 1),
            "same_class_sibling_ratio": same_class_siblings / max(sibling_count, 1),
            "sentence_score": min(sentence_count, 12) / 12.0,
            "comma_density": min(text.count(","), 12) / max(text_len, 1),
            "has_block_children": 1.0 if _has_block_child(el) else 0.0,
            "paragraph_score_density": _paragraph_score_density(el),
            "anchor_count_ratio": anchor_count / max(descendant_count, 1),
            "interactive_ratio": interactive_count / max(descendant_count, 1),
            "heading_ratio": heading_count / max(descendant_count, 1),
            "paragraph_ratio": paragraph_count / max(descendant_count, 1),
            "list_item_ratio": list_item_count / max(descendant_count, 1),
            "repeated_child_tag_ratio": _max_bucket_ratio(child_tags),
            "repeated_child_signature_ratio": _max_bucket_ratio(child_signatures),
            "positive_class": 1.0 if _POSITIVE_RE.search(_class_id_text(el)) else 0.0,
            "negative_class": 1.0 if _NEGATIVE_RE.search(_class_id_text(el)) else 0.0,
            "ancestor_positive_bias": ancestor_positive_bias,
            "ancestor_negative_bias": ancestor_negative_bias,
            "is_article_like": 1.0 if tag in {"article", "main", "section"} else 0.0,
            "is_paragraph_like": 1.0 if tag in {"p", "pre", "blockquote", "td", "h1", "h2", "h3"} else 0.0,
            "is_list_like": 1.0 if tag in {"ul", "ol", "table"} else 0.0,
            "is_boilerplate_tag": 1.0 if tag in {"nav", "aside", "header", "footer"} else 0.0,
            "is_form_tag": 1.0 if tag == "form" else 0.0,
            "under_content_landmark": under_content_landmark,
            "under_boilerplate_landmark": under_boilerplate_landmark,
            "has_repeated_children": 1.0 if _max_bucket_ratio(child_signatures) >= 0.5 else 0.0,
        }
        records.append(
            NodeRecord(
                page_name=page_name,
                node_id=node_id,
                xpath=tree.getpath(el),
                tag=tag,
                label=labels[node_id],
                features=features,
            )
        )

    return tuple(records)


def load_corpus(corpus_path: str | Path = DEFAULT_CORPUS_PATH) -> list[LabeledPage]:
    """Load the labeled readability corpus."""
    corpus_path = Path(corpus_path)
    data = json.loads(corpus_path.read_text())
    pages: list[LabeledPage] = []

    for page_data in data["pages"]:
        fixture_path = (corpus_path.parent / page_data["fixture"]).resolve()
        html = fixture_path.read_text()
        doc = lxml_html.document_fromstring(html)
        body = doc.body
        if body is None:
            continue
        _assign_node_ids(body)

        positive_ids = _expand_region_ids(body, page_data.get("positive_regions", []))
        negative_ids = _expand_region_ids(body, page_data.get("negative_regions", []))

        labels = dict.fromkeys(positive_ids, 1)
        for node_id in negative_ids:
            labels[node_id] = 0

        records = _build_records(page_data["name"], body, labels)
        pages.append(
            LabeledPage(
                name=page_data["name"],
                fixture_path=fixture_path,
                html=html,
                records=records,
                positive_region_ids=frozenset(positive_ids),
                negative_region_ids=frozenset(negative_ids),
            )
        )

    return pages


def _interpolate(scope: float, strict: float, default: float, permissive: float) -> float:
    scope = max(0.0, min(1.0, scope))
    if scope <= 0.5:
        ratio = scope / 0.5
        return strict + (default - strict) * ratio
    ratio = (scope - 0.5) / 0.5
    return default + (permissive - default) * ratio


def _annotated_body(html: str) -> HtmlElement:
    doc = lxml_html.document_fromstring(html)
    body = doc.body
    if body is None:
        raise ValueError("HTML document has no body")
    _assign_node_ids(body)
    return body


def _strip_unlikely_level1(root: HtmlElement, strip_text_threshold: float) -> None:
    to_remove: list[HtmlElement] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag in ("html", "body", "article", "main"):
            continue
        if el.tag in _STRIP_TAGS:
            to_remove.append(el)
            continue
        class_id = " ".join(filter(None, [el.get("class", ""), el.get("id", "")]))
        if not class_id:
            continue
        if (
            _NEGATIVE_RE.search(class_id)
            and not _POSITIVE_RE.search(class_id)
            and _inner_text_length(el) < strip_text_threshold
        ):
            to_remove.append(el)

    for el in to_remove:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)


def _transform_divs_level1(root: HtmlElement) -> None:
    for div in list(root.iter("div")):
        if not _has_block_child(div):
            div.tag = "p"


def _score_candidates_level1(root: HtmlElement, min_paragraph_length: int) -> dict[HtmlElement, float]:
    candidates: dict[HtmlElement, float] = {}
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag not in _SCORE_TAGS:
            continue
        text = _text_content(el).strip()
        if len(text) < min_paragraph_length:
            continue

        parent = el.getparent()
        grandparent = parent.getparent() if parent is not None else None
        score = 1.0 + min(text.count(","), 8) + min(math.floor(len(text) / 100), 3)

        if parent is not None:
            candidates[parent] = candidates.get(parent, _class_weight(parent) + _tag_weight(parent)) + score
        if grandparent is not None:
            candidates[grandparent] = candidates.get(
                grandparent, _class_weight(grandparent) + _tag_weight(grandparent)
            ) + score * 0.5

    return candidates


def _collect_siblings_level1(
    best: HtmlElement,
    best_score: float,
    candidates: dict[HtmlElement, float],
    sibling_threshold_factor: float,
) -> list[HtmlElement]:
    parent = best.getparent()
    if parent is None:
        return [best]

    threshold = max(10.0, best_score * sibling_threshold_factor)
    siblings: list[HtmlElement] = []
    for sibling in parent:
        if sibling is best:
            siblings.append(sibling)
            continue
        if not isinstance(sibling.tag, str):
            continue

        sibling_score = candidates.get(sibling, 0.0)
        if sibling.get("class") and sibling.get("class") == best.get("class"):
            sibling_score += best_score * 0.2

        adjusted = sibling_score * (1.0 - _link_density(sibling))
        if adjusted >= threshold:
            siblings.append(sibling)
            continue

        if sibling.tag == "p":
            text_len = _inner_text_length(sibling)
            if text_len > max(60, threshold * 4) and _link_density(sibling) < 0.35:
                siblings.append(sibling)

    return siblings


def level1_scores(page: LabeledPage, scope: float) -> tuple[dict[str, float], str | None]:
    """Run the threshold-scaled heuristic and return binary node scores."""
    body = _annotated_body(page.html)
    strip_text_threshold = _interpolate(scope, 50.0, 200.0, 500.0)
    sibling_threshold_factor = _interpolate(scope, 0.4, 0.2, 0.05)
    min_paragraph_length = round(_interpolate(scope, 50.0, 25.0, 10.0))

    _strip_unlikely_level1(body, strip_text_threshold)
    _transform_divs_level1(body)
    candidates = _score_candidates_level1(body, min_paragraph_length)
    if not candidates:
        return ({record.node_id: 0.0 for record in page.records}, None)

    best = max(candidates, key=lambda el: candidates[el] * (1.0 - _link_density(el)))
    best_adjusted = candidates[best] * (1.0 - _link_density(best))
    selected = _collect_siblings_level1(best, best_adjusted, candidates, sibling_threshold_factor)

    selected_ids: set[str] = set()
    for node in selected:
        for el in node.iter():
            node_id = el.get(_NODE_ID_ATTR)
            if node_id:
                selected_ids.add(node_id)

    top_id = best.get(_NODE_ID_ATTR)
    scores = {record.node_id: (1.0 if record.node_id in selected_ids else 0.0) for record in page.records}
    return scores, top_id


_LEVEL2_WEIGHTS = {
    "log_text_length": 0.55,
    "log_text_density": 0.7,
    "log_descendant_count": 0.15,
    "link_density": -2.4,
    "non_link_text_ratio": 0.55,
    "class_weight": 0.45,
    "tag_weight": 0.55,
    "depth": 0.15,
    "position_centrality": 0.2,
    "sibling_log_text_density": 0.25,
    "same_tag_sibling_ratio": 0.35,
    "same_class_sibling_ratio": 0.2,
    "sentence_score": 0.55,
    "comma_density": 12.0,
    "has_block_children": 0.35,
    "paragraph_score_density": 18.0,
    "anchor_count_ratio": -0.6,
    "interactive_ratio": -2.0,
    "heading_ratio": 0.3,
    "paragraph_ratio": 0.25,
    "list_item_ratio": 0.3,
    "repeated_child_tag_ratio": 0.35,
    "repeated_child_signature_ratio": 0.45,
    "positive_class": 0.45,
    "negative_class": -1.0,
    "ancestor_positive_bias": 0.2,
    "ancestor_negative_bias": -0.6,
    "is_article_like": 0.85,
    "is_paragraph_like": 0.45,
    "is_list_like": 0.25,
    "is_boilerplate_tag": -1.1,
    "is_form_tag": -0.8,
    "under_content_landmark": 0.4,
    "under_boilerplate_landmark": -0.8,
    "has_repeated_children": 0.45,
}
_LEVEL2_INTERCEPT = -2.2


def level2_scores(page: LabeledPage) -> tuple[dict[str, float], str | None]:
    """Continuous heuristic scoring with a sigmoid output."""
    scores: dict[str, float] = {}
    top_id: str | None = None
    top_score = -1.0
    for record in page.records:
        raw = _LEVEL2_INTERCEPT
        for name, weight in _LEVEL2_WEIGHTS.items():
            raw += record.features[name] * weight
        score = _sigmoid(raw)
        scores[record.node_id] = score
        if score > top_score:
            top_score = score
            top_id = record.node_id
    return scores, top_id


class LogisticRegressionModel:
    """Small no-dependency logistic regression for Level 3 experiments."""

    def __init__(
        self,
        learning_rate: float = 0.35,
        iterations: int = 600,
        regularization: float = 0.01,
    ) -> None:
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.regularization = regularization
        self.means: list[float] = []
        self.scales: list[float] = []
        self.weights: list[float] = []
        self.bias = 0.0

    def fit(self, records: list[NodeRecord]) -> None:
        if not records:
            raise ValueError("Need training records for Level 3")

        vectors = [record.vector() for record in records]
        labels = [record.label for record in records]
        self.fit_vectors(vectors, labels)

    def fit_vectors(self, vectors: list[list[float]], labels: list[int]) -> None:
        """Train on explicit vectors and binary labels."""
        if not vectors or not labels:
            raise ValueError("Need training vectors for Level 3")
        feature_count = len(vectors[0])
        self.means = [0.0] * feature_count
        self.scales = [1.0] * feature_count

        for index in range(feature_count):
            column = [row[index] for row in vectors]
            column_mean = _safe_mean(column)
            variance = _safe_mean([(value - column_mean) ** 2 for value in column])
            self.means[index] = column_mean
            self.scales[index] = math.sqrt(variance) or 1.0

        normalized = [self._normalize(row) for row in vectors]
        self.weights = [0.0] * feature_count
        self.bias = 0.0

        pos_count = sum(labels)
        neg_count = len(labels) - pos_count
        pos_weight = len(labels) / (2.0 * pos_count) if pos_count else 1.0
        neg_weight = len(labels) / (2.0 * neg_count) if neg_count else 1.0

        for _ in range(self.iterations):
            grad_w = [0.0] * feature_count
            grad_b = 0.0
            for row, label in zip(normalized, labels, strict=True):
                prediction = _sigmoid(self.bias + sum(weight * value for weight, value in zip(self.weights, row, strict=True)))
                error = prediction - label
                sample_weight = pos_weight if label == 1 else neg_weight
                grad_b += error * sample_weight
                for index, value in enumerate(row):
                    grad_w[index] += error * value * sample_weight

            scale = 1.0 / len(labels)
            grad_b *= scale
            for index in range(feature_count):
                grad_w[index] = grad_w[index] * scale + self.regularization * self.weights[index]
                self.weights[index] -= self.learning_rate * grad_w[index]
            self.bias -= self.learning_rate * grad_b

    def _normalize(self, vector: list[float]) -> list[float]:
        return [
            (value - mean) / scale
            for value, mean, scale in zip(vector, self.means, self.scales, strict=True)
        ]

    def predict_proba(self, vector: list[float]) -> float:
        normalized = self._normalize(vector)
        return _sigmoid(self.bias + sum(weight * value for weight, value in zip(self.weights, normalized, strict=True)))


def level3_scores(
    train_pages: list[LabeledPage],
    test_page: LabeledPage,
    *,
    enabled_features: set[str] | None = None,
    iterations: int = 600,
) -> tuple[dict[str, float], str | None]:
    """Train on held-in pages and score the held-out page."""
    model = LogisticRegressionModel(iterations=iterations)
    train_records = [record for page in train_pages for record in page.records]
    train_vectors = [record.masked_vector(enabled_features) for record in train_records]
    train_labels = [record.label for record in train_records]
    model.fit_vectors(train_vectors, train_labels)

    scores: dict[str, float] = {}
    top_id: str | None = None
    top_score = -1.0
    for record in test_page.records:
        score = model.predict_proba(record.masked_vector(enabled_features))
        scores[record.node_id] = score
        if score > top_score:
            top_score = score
            top_id = record.node_id
    return scores, top_id


def _binary_metrics(records: list[NodeRecord], scores: dict[str, float], threshold: float) -> BinaryMetrics:
    tp = fp = fn = tn = 0
    for record in records:
        predicted = 1 if scores[record.node_id] >= threshold else 0
        actual = record.label
        if predicted == 1 and actual == 1:
            tp += 1
        elif predicted == 1 and actual == 0:
            fp += 1
        elif predicted == 0 and actual == 1:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / max(tp + fp + fn + tn, 1)
    return BinaryMetrics(
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        accuracy=accuracy,
    )


def run_leave_one_page_out(
    pages: list[LabeledPage] | None = None,
    *,
    scope: float = 0.5,
    level3_iterations: int = 600,
) -> dict[str, ExperimentSummary]:
    """Run leave-one-page-out evaluation for Levels 1-3."""
    pages = pages or load_corpus()
    threshold = 1.0 - max(0.0, min(1.0, scope))

    model_scores: dict[str, list[tuple[list[NodeRecord], dict[str, float], str, str | None]]] = {
        "level1": [],
        "level2": [],
        "level3": [],
    }

    for test_page in pages:
        train_pages = [page for page in pages if page.name != test_page.name]
        level1_page_scores, level1_top = level1_scores(test_page, scope)
        level2_page_scores, level2_top = level2_scores(test_page)
        level3_page_scores, level3_top = level3_scores(
            train_pages,
            test_page,
            iterations=level3_iterations,
        )

        model_scores["level1"].append((list(test_page.records), level1_page_scores, test_page.name, level1_top))
        model_scores["level2"].append((list(test_page.records), level2_page_scores, test_page.name, level2_top))
        model_scores["level3"].append((list(test_page.records), level3_page_scores, test_page.name, level3_top))

    summaries: dict[str, ExperimentSummary] = {}
    for name, folds in model_scores.items():
        all_records: list[NodeRecord] = []
        merged_scores: dict[str, float] = {}
        per_page_f1: dict[str, float] = {}
        top_hits = 0

        for records, scores, page_name, top_node_id in folds:
            all_records.extend(records)
            for record in records:
                merged_scores[f"{record.page_name}:{record.node_id}"] = scores[record.node_id]

            page_metrics = _binary_metrics(records, scores, threshold)
            per_page_f1[page_name] = page_metrics.f1
            if top_node_id is not None:
                top_record = next((record for record in records if record.node_id == top_node_id), None)
                if top_record is not None and top_record.label == 1:
                    top_hits += 1

        keyed_records = [
            NodeRecord(
                page_name=record.page_name,
                node_id=f"{record.page_name}:{record.node_id}",
                xpath=record.xpath,
                tag=record.tag,
                label=record.label,
                features=record.features,
            )
            for record in all_records
        ]
        metrics = _binary_metrics(keyed_records, merged_scores, threshold)
        summaries[name] = ExperimentSummary(
            name=name,
            scope=scope,
            threshold=threshold,
            metrics=metrics,
            top_hit_rate=top_hits / max(len(folds), 1),
            per_page_f1=per_page_f1,
        )

    return summaries


def format_summary_table(results: dict[str, ExperimentSummary]) -> str:
    """Render the experiment results as a compact plain-text table."""
    lines = [
        "model   precision  recall  f1     acc    top-hit",
        "------  ---------  ------  -----  -----  -------",
    ]
    for key in ("level1", "level2", "level3"):
        result = results[key]
        metrics = result.metrics
        lines.append(
            f"{key:6s}  {metrics.precision:9.3f}  {metrics.recall:6.3f}  "
            f"{metrics.f1:5.3f}  {metrics.accuracy:5.3f}  {result.top_hit_rate:7.3f}"
        )
    return "\n".join(lines)


def run_level3_feature_ablations(
    pages: list[LabeledPage] | None = None,
    *,
    scope: float = 0.5,
    level3_iterations: int = 600,
) -> list[AblationSummary]:
    """Run leave-one-group-out ablations for Level 3."""
    pages = pages or load_corpus()
    threshold = 1.0 - max(0.0, min(1.0, scope))
    all_features = set(FEATURE_ORDER)

    def evaluate(enabled_features: set[str], dropped_group: str | None) -> AblationSummary:
        all_records: list[NodeRecord] = []
        merged_scores: dict[str, float] = {}
        top_hits = 0

        for test_page in pages:
            train_pages = [page for page in pages if page.name != test_page.name]
            page_scores, top_node_id = level3_scores(
                train_pages,
                test_page,
                enabled_features=enabled_features,
                iterations=level3_iterations,
            )
            all_records.extend(test_page.records)
            for record in test_page.records:
                merged_scores[f"{record.page_name}:{record.node_id}"] = page_scores[record.node_id]
            if top_node_id is not None:
                top_record = next(
                    (record for record in test_page.records if record.node_id == top_node_id),
                    None,
                )
                if top_record is not None and top_record.label == 1:
                    top_hits += 1

        keyed_records = [
            NodeRecord(
                page_name=record.page_name,
                node_id=f"{record.page_name}:{record.node_id}",
                xpath=record.xpath,
                tag=record.tag,
                label=record.label,
                features=record.features,
            )
            for record in all_records
        ]
        metrics = _binary_metrics(keyed_records, merged_scores, threshold)
        return AblationSummary(
            name="level3",
            dropped_group=dropped_group,
            enabled_features=tuple(name for name in FEATURE_ORDER if name in enabled_features),
            metrics=metrics,
            top_hit_rate=top_hits / max(len(pages), 1),
            delta_f1=0.0,
        )

    baseline = evaluate(all_features, None)
    summaries = [baseline]

    for group_name, features in FEATURE_GROUPS.items():
        enabled = all_features - set(features)
        summary = evaluate(enabled, group_name)
        summaries.append(
            AblationSummary(
                name=summary.name,
                dropped_group=summary.dropped_group,
                enabled_features=summary.enabled_features,
                metrics=summary.metrics,
                top_hit_rate=summary.top_hit_rate,
                delta_f1=summary.metrics.f1 - baseline.metrics.f1,
            )
        )

    return summaries


def format_ablation_table(results: list[AblationSummary]) -> str:
    """Render feature-group ablations as a compact table."""
    lines = [
        "drop-group           f1     delta   precision  recall  top-hit",
        "-------------------  -----  ------  ---------  ------  -------",
    ]
    for result in results:
        label = result.dropped_group or "none"
        metrics = result.metrics
        lines.append(
            f"{label:19s}  {metrics.f1:5.3f}  {result.delta_f1:+6.3f}  "
            f"{metrics.precision:9.3f}  {metrics.recall:6.3f}  {result.top_hit_rate:7.3f}"
        )
    return "\n".join(lines)
