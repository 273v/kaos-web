"""Tests for the readability experiment harness."""

from __future__ import annotations

import sys
from pathlib import Path

# readability_experiments is a research script, not a production module
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from readability_experiments import (  # ty: ignore[unresolved-import]
    FEATURE_GROUPS,
    FEATURE_ORDER,
    LogisticRegressionModel,
    load_corpus,
    run_leave_one_page_out,
    run_level3_feature_ablations,
)


def test_load_corpus_contains_labeled_pages():
    pages = load_corpus()

    assert len(pages) >= 10
    assert all(page.records for page in pages)
    assert any(record.label == 1 for page in pages for record in page.records)
    assert any(record.label == 0 for page in pages for record in page.records)


def test_feature_vectors_are_stable():
    pages = load_corpus()
    first_record = pages[0].records[0]

    vector = first_record.vector()

    assert len(vector) == len(FEATURE_ORDER)
    assert all(isinstance(value, float) for value in vector)


def test_logistic_regression_learns_a_simple_boundary():
    pages = load_corpus()
    train_records = [record for page in pages[:-1] for record in page.records]
    eval_records = list(pages[-1].records)

    model = LogisticRegressionModel(iterations=250)
    model.fit(train_records)
    probabilities = [model.predict_proba(record.vector()) for record in eval_records]

    assert all(0.0 <= value <= 1.0 for value in probabilities)
    assert len({round(value, 4) for value in probabilities}) > 1


def test_leave_one_page_out_reports_all_levels():
    results = run_leave_one_page_out(scope=0.5, level3_iterations=120)

    assert set(results) == {"level1", "level2", "level3"}
    assert results["level3"].metrics.f1 >= 0.45
    assert results["level3"].top_hit_rate >= 0.5


def test_level3_feature_ablations_report_all_groups():
    results = run_level3_feature_ablations(scope=0.5, level3_iterations=120)

    assert len(results) == len(FEATURE_GROUPS) + 1
    assert results[0].dropped_group is None
    assert all(0.0 <= result.metrics.f1 <= 1.0 for result in results)
