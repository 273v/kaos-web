"""Tests for CLI commands using direct main() invocation with captured output."""

from __future__ import annotations

import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from kaos_web.cli import main

FIXTURES = Path(__file__).parent.parent / "fixtures"
ARTICLE_PATH = str(FIXTURES / "article.html")

_has_kaos_nlp_core = importlib.util.find_spec("kaos_nlp_core") is not None


def _run_cli(argv: list[str]) -> tuple[str, str, int]:
    """Run the CLI main() and capture stdout, stderr, and exit code.

    Returns (stdout, stderr, exit_code).
    """
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = StringIO()
    sys.stderr = StringIO()
    exit_code = 0
    try:
        main(argv)
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
    finally:
        stdout_val = sys.stdout.getvalue()
        stderr_val = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return stdout_val, stderr_val, exit_code


class TestExtractCommand:
    def test_extract_from_file(self) -> None:
        """Extract markdown content from the fixture HTML file."""
        stdout, _stderr, exit_code = _run_cli(["extract", ARTICLE_PATH])

        assert exit_code == 0, f"CLI should exit 0, got {exit_code}"
        assert "Main Article Heading" in stdout, (
            "Extracted markdown should contain the main heading"
        )
        assert "Section One" in stdout, "Extracted markdown should contain section headings"

    def test_extract_json_envelope(self) -> None:
        """Verify JSON output has the correct envelope structure."""
        stdout, _stderr, exit_code = _run_cli(["extract", ARTICLE_PATH, "--json"])

        assert exit_code == 0, f"CLI should exit 0, got {exit_code}"
        data = json.loads(stdout)
        assert data["command"] == "extract", (
            f"Expected command='extract', got '{data.get('command')}'"
        )
        assert "url" in data, "JSON envelope should contain 'url'"
        assert "title" in data, "JSON envelope should contain 'title'"
        assert "block_count" in data, "JSON envelope should contain 'block_count'"
        assert isinstance(data["block_count"], int), "block_count should be an integer"
        assert data["block_count"] > 0, "Extracted document should have blocks"
        assert "content" in data, "JSON envelope should contain 'content'"
        assert "format" in data, "JSON envelope should contain 'format'"


class TestMetadataCommand:
    def test_metadata_from_file(self) -> None:
        """Extract metadata from the fixture HTML file."""
        stdout, _stderr, exit_code = _run_cli(["metadata", ARTICLE_PATH])

        assert exit_code == 0, f"CLI should exit 0, got {exit_code}"
        assert "Test Article OG Title" in stdout, "Metadata output should include the OG title"
        assert "Jane Doe" in stdout, "Metadata output should include the author"

    def test_metadata_json_envelope(self) -> None:
        """Verify JSON metadata output has the correct structure."""
        stdout, _stderr, exit_code = _run_cli(["metadata", ARTICLE_PATH, "--json"])

        assert exit_code == 0, f"CLI should exit 0, got {exit_code}"
        data = json.loads(stdout)
        assert data["command"] == "metadata", (
            f"Expected command='metadata', got '{data.get('command')}'"
        )
        assert data.get("title") == "Test Article OG Title", (
            f"Expected OG title, got: {data.get('title')}"
        )
        assert data.get("author") == "Jane Doe"
        assert "opengraph" in data, "JSON output should contain opengraph dict"
        assert "structured_data" in data, "JSON output should contain structured_data"


@pytest.mark.skipif(not _has_kaos_nlp_core, reason="kaos-nlp-core not installed")
class TestSearchCommand:
    def test_search_from_file(self) -> None:
        """Search within the fixture for a term and find results."""
        stdout, _stderr, exit_code = _run_cli(
            [
                "search",
                ARTICLE_PATH,
                "blockquote important",
            ]
        )

        assert exit_code == 0, f"CLI should exit 0, got {exit_code}"
        assert "Score:" in stdout or "matches" in stdout, (
            "Search output should contain score information or match counts"
        )

    def test_search_json_envelope(self) -> None:
        """Verify JSON search output has the correct structure."""
        stdout, _stderr, exit_code = _run_cli(
            [
                "search",
                ARTICLE_PATH,
                "blockquote important",
                "--json",
            ]
        )

        assert exit_code == 0, f"CLI should exit 0, got {exit_code}"
        data = json.loads(stdout)
        assert data["command"] == "search", (
            f"Expected command='search', got '{data.get('command')}'"
        )
        assert "query" in data, "JSON envelope should contain 'query'"
        assert data["query"] == "blockquote important"
        assert "results" in data, "JSON envelope should contain 'results'"
        assert isinstance(data["results"], list), "results should be a list"
        assert "total_matches" in data, "JSON envelope should contain 'total_matches'"
        assert "has_more" in data, "JSON envelope should contain 'has_more'"
