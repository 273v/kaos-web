# Readability Experiments: Level 1 vs Level 2 vs Level 3

Companion document to `BROWSER_CONTENT_EXTRACTION.md`.

This file documents the experiment harness, labeled corpus, feature engineering, results, and current recommendations for parametric readability in `kaos-web`.

**Status:** 2026-04-04 experiment pass completed  
**Primary question:** does a lightweight Level 3 learned model actually outperform the heuristic approaches on the page types that matter to us?

## Executive Summary

Yes, Level 3 works on the current labeled corpus.

On the expanded 10-page leave-one-page-out corpus at `content_scope=0.5`:

| Model | Precision | Recall | F1 | Accuracy | Top-hit |
|-------|----------:|-------:|---:|---------:|--------:|
| Level 1 | 0.933 | 0.653 | 0.769 | 0.789 | 0.600 |
| Level 2 | 0.582 | 0.993 | 0.734 | 0.613 | 1.000 |
| Level 3 | 0.880 | 0.927 | 0.903 | 0.892 | 1.000 |

This is the current conclusion:

1. Level 3 is materially better than Levels 1 and 2 on the corpus we built.
2. The gain comes primarily from **structure/context features**, not from a sophisticated optimizer.
3. We do **not** need a more advanced logistic-regression variant yet.
4. We do need more corpus growth, and we should probably prune or redesign some text-heavy feature groups before moving Level 3 into production.

## Why We Ran This

The original design discussion in `BROWSER_CONTENT_EXTRACTION.md` identified a key problem: classic Readability-style heuristics are tuned for article pages and break on:

- directory listings
- search result pages
- legal/statutory pages
- multi-section landing pages
- structured tables and docket-like layouts

The specific recurring failure mode was:

- the heuristic selects a search form, filter rail, or navigation block instead of the actual results/content region

We wanted to answer this concretely rather than philosophically:

1. Does Level 3 actually improve extraction quality?
2. If it does, is the improvement coming from better features or just more model complexity?
3. Which feature families matter enough to justify keeping?

## What Was Implemented

### Experiment Code

The experiment harness lives in:

- [readability_experiments.py](/home/mjbommar/projects/273v/kaos-modules/kaos-web/kaos_web/extract/readability_experiments.py)

Runnable scripts:

- [run_readability_experiments.py](/home/mjbommar/projects/273v/kaos-modules/kaos-web/scripts/run_readability_experiments.py)
- [run_readability_ablations.py](/home/mjbommar/projects/273v/kaos-modules/kaos-web/scripts/run_readability_ablations.py)

Tests:

- [test_readability_experiments.py](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/unit/test_readability_experiments.py)

### Corpus

The labeled corpus lives in:

- [corpus.json](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/readability/corpus.json)

Fixtures currently included:

- [article.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/article.html)
- [books_toscrape.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/books_toscrape.html)
- [cornell_law.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/cornell_law.html)
- [httpbin.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/httpbin.html)
- [directory_listing.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/readability/directory_listing.html)
- [multi_section_landing.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/readability/multi_section_landing.html)
- [search_results_page.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/readability/search_results_page.html)
- [docket_report.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/readability/docket_report.html)
- [category_listing.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/readability/category_listing.html)
- [team_directory_cards.html](/home/mjbommar/projects/273v/kaos-modules/kaos-web/tests/fixtures/readability/team_directory_cards.html)

### Labeling Scheme

Each page defines:

- `positive_regions`: DOM regions that should count as extracted content
- `negative_regions`: DOM regions that are important hard negatives

Labels are expanded to the descendants of those regions so the experiment operates on node-level classification rather than full-page string matching.

## The Three Implemented Levels

### Level 1

Level 1 is the current readability heuristic with parametric threshold scaling.

It varies:

- strip threshold
- sibling inclusion threshold
- minimum paragraph length

This is the closest experiment to "just expose the current knobs."

### Level 2

Level 2 uses a fixed continuous heuristic score with a sigmoid output.

It keeps the idea of handcrafted features, but instead of relying on hard binary cutoffs at each stage, it turns node scoring into a continuous probability-like value.

This is a cleaner heuristic baseline, but it is still manually weighted.

### Level 3

Level 3 is a no-dependency logistic regression trained on DOM-node features.

Important design constraints:

- no `scikit-learn` runtime dependency
- no neural stack
- no tree runtime
- weights are learned by a small local implementation
- inference stays cheap

The experiment uses leave-one-page-out evaluation so every page is tested as held-out data.

## Feature Engineering

The strongest version of Level 3 came from adding richer structural and contextual features, not from changing the optimizer.

### Feature Families

Defined in `FEATURE_GROUPS` inside [readability_experiments.py](/home/mjbommar/projects/273v/kaos-modules/kaos-web/kaos_web/extract/readability_experiments.py).

#### 1. `text_content`

- `log_text_length`
- `log_text_density`
- `log_descendant_count`
- `sentence_score`
- `comma_density`
- `paragraph_score_density`
- `paragraph_ratio`

#### 2. `link_interactive`

- `link_density`
- `non_link_text_ratio`
- `anchor_count_ratio`
- `interactive_ratio`
- `heading_ratio`

#### 3. `structure_context`

- `depth`
- `position_centrality`
- `sibling_log_text_density`
- `same_tag_sibling_ratio`
- `same_class_sibling_ratio`
- `has_block_children`
- `under_content_landmark`
- `under_boilerplate_landmark`

#### 4. `template_repetition`

- `list_item_ratio`
- `repeated_child_tag_ratio`
- `repeated_child_signature_ratio`
- `has_repeated_children`
- `is_list_like`

#### 5. `semantic_priors`

- `class_weight`
- `tag_weight`
- `positive_class`
- `negative_class`
- `ancestor_positive_bias`
- `ancestor_negative_bias`
- `is_article_like`
- `is_paragraph_like`
- `is_boilerplate_tag`
- `is_form_tag`

## Experiment Methodology

### Evaluation Setup

We used leave-one-page-out evaluation:

1. Hold out one labeled page.
2. Train Level 3 on the remaining pages.
3. Score every labeled node on the held-out page.
4. Repeat for all pages.
5. Aggregate precision, recall, F1, accuracy, and "top-hit" rate.

### `content_scope`

The shared API assumption remains:

- `threshold = 1.0 - content_scope`

For the reported runs here, we focused primarily on:

- `content_scope = 0.5`

We also ran scope sweeps earlier to confirm that Level 3 did not depend on a single lucky threshold.

## Results

### Phase 1: Initial 6-page Seed Corpus

The initial seed corpus showed:

- Level 1 strong on easy article-like cases
- Level 3 clearly better on directory/search/statute failures
- Level 3 not yet strong enough to beat Level 1 overall

This was the signal that feature quality, not model family, was the bottleneck.

### Phase 2: Expanded Features, 7-page Corpus

After adding richer structure/context/template features, Level 3 jumped substantially.

At 7 pages and `scope=0.5`:

| Model | F1 |
|-------|---:|
| Level 1 | 0.753 |
| Level 2 | 0.701 |
| Level 3 | 0.922 |

This was the turning point.

### Phase 3: Broader 10-page Corpus

After adding three more listing/docket/team-directory fixtures, Level 3 remained strong.

At 10 pages and `scope=0.5`:

| Model | Precision | Recall | F1 | Accuracy | Top-hit |
|-------|----------:|-------:|---:|---------:|--------:|
| Level 1 | 0.933 | 0.653 | 0.769 | 0.789 | 0.600 |
| Level 2 | 0.582 | 0.993 | 0.734 | 0.613 | 1.000 |
| Level 3 | 0.880 | 0.927 | 0.903 | 0.892 | 1.000 |

### Per-Page F1 on the 10-page Corpus

#### Level 1

| Page | F1 |
|------|---:|
| `article_fixture` | 1.000 |
| `books_product` | 1.000 |
| `category_listing` | 0.444 |
| `cornell_statute` | 0.000 |
| `directory_listing` | 0.000 |
| `docket_report` | 0.882 |
| `httpbin_article` | 1.000 |
| `multi_section_landing` | 1.000 |
| `search_results_page` | 0.000 |
| `team_directory_cards` | 0.960 |

#### Level 2

| Page | F1 |
|------|---:|
| `article_fixture` | 0.881 |
| `books_product` | 0.900 |
| `category_listing` | 0.824 |
| `cornell_statute` | 0.341 |
| `directory_listing` | 0.813 |
| `docket_report` | 0.826 |
| `httpbin_article` | 1.000 |
| `multi_section_landing` | 0.786 |
| `search_results_page` | 0.690 |
| `team_directory_cards` | 0.813 |

#### Level 3

| Page | F1 |
|------|---:|
| `article_fixture` | 0.939 |
| `books_product` | 0.833 |
| `category_listing` | 1.000 |
| `cornell_statute` | 0.636 |
| `directory_listing` | 1.000 |
| `docket_report` | 1.000 |
| `httpbin_article` | 1.000 |
| `multi_section_landing` | 1.000 |
| `search_results_page` | 1.000 |
| `team_directory_cards` | 0.897 |

## Interpretation of the Main Results

The main findings are:

1. Level 1 remains very strong on obvious article-like pages.
2. Level 1 is unreliable on the exact layouts we care most about for browser extraction: forms next to results, structured listings, and statutory content.
3. Level 2 improves recall but remains too permissive and too noisy.
4. Level 3 is the first approach that is strong both on easy pages and on the hard structured layouts.

The most important Level 3 wins are:

- `directory_listing`: 1.000
- `search_results_page`: 1.000
- `category_listing`: 1.000
- `docket_report`: 1.000
- `cornell_statute`: 0.636, still imperfect but far better than Level 1

## Feature Ablation Results

We then ran leave-one-feature-group-out ablations on the 10-page corpus.

At `scope=0.5`:

| Dropped Group | F1 | Delta |
|--------------|---:|------:|
| none | 0.903 | +0.000 |
| text_content | 0.951 | +0.049 |
| link_interactive | 0.913 | +0.011 |
| structure_context | 0.806 | -0.096 |
| template_repetition | 0.919 | +0.016 |
| semantic_priors | 0.904 | +0.002 |

### What This Means

#### `structure_context` matters the most

This group is doing the real work.

Removing it causes the largest degradation:

- `0.903 -> 0.806`

This supports the underlying hypothesis that the hard cases are not mainly about "better text understanding." They are about understanding where a node sits in the DOM and how it relates to its neighbors.

#### `semantic_priors` are now low-value

These include the classic Readability-style class/id and tag priors.

On this corpus, dropping them barely changes the result:

- `0.903 -> 0.904`

That does not mean they are useless globally, but it does mean the current Level 3 win is not coming primarily from inherited Readability-style regex priors.

#### `text_content` is likely noisy or redundant

This was the most surprising result:

- dropping `text_content` improved F1 from `0.903` to `0.951`

Most likely explanations:

1. those features are partially redundant with stronger structure/context signals
2. they may be overemphasizing article-shaped content patterns
3. they may be hurting structured result/table/list layouts

#### `template_repetition` is not yet pulling its weight

This group sounded attractive conceptually, but on the current corpus it is not providing a positive marginal contribution.

Possible reasons:

1. the repetition features are too crude
2. the current corpus is still too small for them to stabilize
3. structure/context already captures most of the same information

## Current Recommendation

### Short Version

Build toward Level 3, but keep it experimental for now.

### Specific Recommendation

1. Do not spend time on a more advanced logistic regression technique yet.
2. Do not jump to boosted trees or neural models yet.
3. Continue with the current simple Level 3 model.
4. Expand the labeled corpus.
5. Prune or redesign feature groups that do not help.

### Immediate Next Step

The next rational move is:

1. create a reduced Level 3 feature set centered on `structure_context`
2. optionally keep a minimal subset of `semantic_priors` and `link_interactive`
3. rerun the same corpus and ablations
4. compare the simpler model against the full current model

If the smaller feature set performs as well or better, it becomes a much cleaner candidate for production integration.

## Why We Are Not Recommending "Fancier Regression"

The experiments so far do not point to the optimizer as the bottleneck.

The evidence points to feature quality:

- first Level 3 version: promising but not clearly dominant
- better feature set: large jump in performance
- same simple learner: still sufficient

So the current technical bar is:

- better labels
- better corpus coverage
- better feature selection

not:

- second-order optimization
- advanced regularization tricks
- different solver family

## Limitations

This experiment harness is useful, but the corpus is still small.

Known limitations:

1. Many fixtures are synthetic or semi-synthetic.
2. We do not yet have site-level splits across many unrelated real sites.
3. We are labeling regions, not full gold-standard cleaned text output.
4. Some current pages are biased toward the exact failure modes we care about, which is good for development but not enough for a general benchmark.

So these results are strong enough to justify continued investment, but not strong enough to declare Level 3 production-ready.

## Suggested Future Corpus Additions

The highest-value new pages would be:

- more law firm people directories
- more search-result pages with side filters
- more statute/regulation pages
- corporate insight landing pages with mixed cards and rails
- ecommerce category pages
- government index pages
- knowledge-base pages with left navigation + main article
- pages with cookie banners or modals still present in DOM

## How To Reproduce

Run the main comparison:

```bash
./.venv/bin/python scripts/run_readability_experiments.py --scope 0.5
```

Run the feature ablations:

```bash
./.venv/bin/python scripts/run_readability_ablations.py --scope 0.5
```

Run the tests:

```bash
./.venv/bin/pytest tests/unit/test_readability_experiments.py -q
```

## Validation Status

At the time this document was written:

- `ruff check` passed
- `pytest tests/unit/test_readability_experiments.py -q` passed
- the experiment runner completed successfully
- the ablation runner completed successfully

## Final Position

Level 3 is now justified as the medium-term direction for parametric readability.

The experiments show:

1. the learned model is genuinely better than the heuristic baselines on the current corpus
2. the win is mostly structural/contextual
3. the model does not yet need more sophistication
4. the next engineering task is feature pruning plus corpus expansion, not algorithm escalation

That is the strongest statement supported by the current evidence.
