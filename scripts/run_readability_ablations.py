"""Run Level 3 feature-family ablations for readability experiments."""

from __future__ import annotations

import argparse
from pathlib import Path

from kaos_web.extract.readability_experiments import (
    DEFAULT_CORPUS_PATH,
    format_ablation_table,
    load_corpus,
    run_level3_feature_ablations,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS_PATH,
        help="Path to the labeled readability corpus JSON",
    )
    parser.add_argument(
        "--scope",
        type=float,
        default=0.5,
        help="content_scope to evaluate, from 0.0 (strict) to 1.0 (permissive)",
    )
    args = parser.parse_args()

    pages = load_corpus(args.corpus)
    results = run_level3_feature_ablations(pages, scope=args.scope)

    print(f"Corpus: {args.corpus}")
    print(f"Pages: {len(pages)}")
    print(f"Scope: {args.scope:.2f}")
    print()
    print(format_ablation_table(results))


if __name__ == "__main__":
    main()
