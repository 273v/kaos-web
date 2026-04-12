"""Run the Level 1-3 readability experiment suite.

Examples:
    ./scripts/run_readability_experiments.py
    ./.venv/bin/python scripts/run_readability_experiments.py --scope 0.65
"""

from __future__ import annotations

import argparse
from pathlib import Path

from readability_experiments import (
    DEFAULT_CORPUS_PATH,
    format_summary_table,
    load_corpus,
    run_leave_one_page_out,
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
    results = run_leave_one_page_out(pages, scope=args.scope)

    print(f"Corpus: {args.corpus}")
    print(f"Pages: {len(pages)}")
    print(f"Scope: {args.scope:.2f}")
    print()
    print(format_summary_table(results))
    print()
    for name in ("level1", "level2", "level3"):
        result = results[name]
        print(f"{name} per-page F1")
        for page_name, value in sorted(result.per_page_f1.items()):
            print(f"  {page_name:24s} {value:.3f}")
        print()


if __name__ == "__main__":
    main()
