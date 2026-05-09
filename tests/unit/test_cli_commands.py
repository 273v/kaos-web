"""Tests for CLI commands using direct main() invocation with captured output.

Coverage backfill (audit-03 WEB3-005): exercises each subcommand's happy path
and error path. URL-fetch and serve branches are mocked at the boundary so no
real network/MCP server is involved.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.cli import main
from kaos_web.models import WebResponse

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


# ── No-command / help branch ────────────────────────────────────────


class TestNoCommand:
    def test_no_command_prints_help_and_exits_1(self) -> None:
        stdout, _stderr, exit_code = _run_cli([])
        assert exit_code == 1
        # Help is printed to stdout via parser.print_help()
        assert "kaos-web" in stdout or "Web content extraction" in stdout


# ── extract command ─────────────────────────────────────────────────


class TestExtractCommand:
    def test_extract_from_file(self) -> None:
        stdout, _stderr, exit_code = _run_cli(["extract", ARTICLE_PATH])
        assert exit_code == 0
        assert "Main Article Heading" in stdout
        assert "Section One" in stdout

    def test_extract_json_envelope(self) -> None:
        stdout, _stderr, exit_code = _run_cli(["extract", ARTICLE_PATH, "--json"])
        assert exit_code == 0
        data = json.loads(stdout)
        assert data["command"] == "extract"
        assert "url" in data
        assert "title" in data
        assert "block_count" in data
        assert isinstance(data["block_count"], int)
        assert data["block_count"] > 0
        assert "content" in data
        assert "format" in data

    def test_extract_text_format(self) -> None:
        stdout, _stderr, exit_code = _run_cli(["extract", ARTICLE_PATH, "--format", "text"])
        assert exit_code == 0
        # Text output is plain (no markdown bullets)
        assert "Main Article Heading" in stdout

    def test_extract_json_format_to_stdout(self) -> None:
        """--format json (without --json) writes the doc model_dump as json text."""
        stdout, _stderr, exit_code = _run_cli(["extract", ARTICLE_PATH, "--format", "json"])
        assert exit_code == 0
        # The output is itself a JSON document of the ContentDocument
        parsed = json.loads(stdout)
        assert isinstance(parsed, dict)
        # ContentDocument has a body field
        assert "body" in parsed or "metadata" in parsed

    def test_extract_to_output_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "result.md"
        _stdout, stderr, exit_code = _run_cli(["extract", ARTICLE_PATH, "--output", str(out_file)])
        assert exit_code == 0
        assert out_file.exists()
        body = out_file.read_text(encoding="utf-8")
        assert "Main Article Heading" in body
        # Stderr should report the write
        assert str(out_file) in stderr

    def test_extract_file_not_found_exits_1(self) -> None:
        _stdout, stderr, exit_code = _run_cli(["extract", "/nonexistent/file.html"])
        assert exit_code == 1
        assert "File not found" in stderr

    def test_extract_url_uses_http_client(self) -> None:
        """URL source path goes through HttpClient.fetch under asyncio.run."""
        html = "<html><body><h1>Remote Title</h1><p>Remote body content here.</p></body></html>"

        # Mock the HttpClient context-manager-free path used in _get_html()
        mock_client = MagicMock()
        mock_client.fetch = AsyncMock(
            return_value=WebResponse(url="https://example.com", status_code=200, html=html)
        )
        mock_client.close = AsyncMock()
        with patch("kaos_web.clients.http.HttpClient", return_value=mock_client):
            stdout, _stderr, exit_code = _run_cli(["extract", "https://example.com"])
        assert exit_code == 0
        assert "Remote Title" in stdout


# ── metadata command ────────────────────────────────────────────────


class TestMetadataCommand:
    def test_metadata_from_file(self) -> None:
        stdout, _stderr, exit_code = _run_cli(["metadata", ARTICLE_PATH])
        assert exit_code == 0
        assert "Test Article OG Title" in stdout
        assert "Jane Doe" in stdout

    def test_metadata_json_envelope(self) -> None:
        stdout, _stderr, exit_code = _run_cli(["metadata", ARTICLE_PATH, "--json"])
        assert exit_code == 0
        data = json.loads(stdout)
        assert data["command"] == "metadata"
        assert data.get("title") == "Test Article OG Title"
        assert data.get("author") == "Jane Doe"
        assert "opengraph" in data
        assert "structured_data" in data

    def test_metadata_minimal_html(self, tmp_path: Path) -> None:
        """Minimal HTML — exercise the (none) defaults branches."""
        html_file = tmp_path / "min.html"
        html_file.write_text("<html><head></head><body></body></html>", encoding="utf-8")
        stdout, _stderr, exit_code = _run_cli(["metadata", str(html_file)])
        assert exit_code == 0
        # All fields should default to (none) display strings
        assert "(none)" in stdout


# ── fetch command ───────────────────────────────────────────────────


class TestFetchCommand:
    def test_fetch_default_human_output(self) -> None:
        resp = WebResponse(
            url="https://example.com",
            status_code=200,
            content_type="text/html",
            html="<html></html>",
            elapsed_ms=12.345,
        )

        # Build a fake HttpClient that supports `async with`
        client = MagicMock()
        client.fetch = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        with patch("kaos_web.clients.http.HttpClient", return_value=client):
            stdout, _stderr, exit_code = _run_cli(["fetch", "https://example.com"])
        assert exit_code == 0
        assert "URL: https://example.com" in stdout
        assert "Status: 200" in stdout
        assert "Content-Type: text/html" in stdout

    def test_fetch_json_envelope(self) -> None:
        resp = WebResponse(
            url="https://example.com",
            status_code=200,
            content_type="text/html",
            html="<html><body>x</body></html>",
            elapsed_ms=8.7,
        )
        client = MagicMock()
        client.fetch = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        with patch("kaos_web.clients.http.HttpClient", return_value=client):
            stdout, _stderr, exit_code = _run_cli(["fetch", "https://example.com", "--json"])
        assert exit_code == 0
        data = json.loads(stdout)
        assert data["command"] == "fetch"
        assert data["url"] == "https://example.com"
        assert data["status_code"] == 200
        assert data["content_type"] == "text/html"

    def test_fetch_with_output_file(self, tmp_path: Path) -> None:
        out = tmp_path / "page.html"
        resp = WebResponse(
            url="https://example.com",
            status_code=200,
            html="<html><body>SAVED</body></html>",
        )
        client = MagicMock()
        client.fetch = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("kaos_web.clients.http.HttpClient", return_value=client):
            _stdout, stderr, exit_code = _run_cli(
                ["fetch", "https://example.com", "--output", str(out)]
            )
        assert exit_code == 0
        assert out.exists()
        assert "SAVED" in out.read_text(encoding="utf-8")
        assert str(out) in stderr


# ── search command ──────────────────────────────────────────────────


@pytest.mark.skipif(not _has_kaos_nlp_core, reason="kaos-nlp-core not installed")
class TestSearchCommand:
    def test_search_from_file(self) -> None:
        stdout, _stderr, exit_code = _run_cli(["search", ARTICLE_PATH, "blockquote important"])
        assert exit_code == 0
        assert "Score:" in stdout or "matches" in stdout

    def test_search_json_envelope(self) -> None:
        stdout, _stderr, exit_code = _run_cli(
            ["search", ARTICLE_PATH, "blockquote important", "--json"]
        )
        assert exit_code == 0
        data = json.loads(stdout)
        assert data["command"] == "search"
        assert "query" in data
        assert data["query"] == "blockquote important"
        assert "results" in data
        assert isinstance(data["results"], list)
        assert "total_matches" in data
        assert "has_more" in data

    def test_search_no_results(self) -> None:
        """Query with zero matches hits the no-results branch."""
        stdout, _stderr, exit_code = _run_cli(
            ["search", ARTICLE_PATH, "xyzzy_unique_term_no_match_anywhere"]
        )
        assert exit_code == 0
        assert "No results" in stdout


# ── serve command ───────────────────────────────────────────────────


class TestServeCommand:
    def test_serve_passes_args_to_serve_main(self) -> None:
        """`kaos-web serve --browser --crawl --debug` should forward those flags."""
        captured: dict[str, list[str]] = {}

        def _fake_serve_main(argv: list[str] | None = None) -> None:
            captured["argv"] = list(argv or [])

        with patch("kaos_web.serve.main", _fake_serve_main):
            _stdout, _stderr, exit_code = _run_cli(["serve", "--browser", "--crawl", "--debug"])
        assert exit_code == 0
        argv = captured["argv"]
        assert "--browser" in argv
        assert "--crawl" in argv
        assert "--debug" in argv

    def test_serve_http_passes_host_port(self) -> None:
        captured: dict[str, list[str]] = {}

        def _fake_serve_main(argv: list[str] | None = None) -> None:
            captured["argv"] = list(argv or [])

        with patch("kaos_web.serve.main", _fake_serve_main):
            _stdout, _stderr, exit_code = _run_cli(
                ["serve", "--http", "--host", "0.0.0.0", "--port", "9999"]
            )
        assert exit_code == 0
        argv = captured["argv"]
        # Forwarded as four discrete tokens
        assert "--http" in argv
        assert "--host" in argv
        assert "0.0.0.0" in argv
        assert "--port" in argv
        assert "9999" in argv

    def test_serve_missing_mcp_extra_exits(self) -> None:
        """If kaos_web.serve cannot be imported, CLI exits with helpful error."""
        # Force the import inside _cmd_serve to raise ImportError.
        with patch.dict(sys.modules, {"kaos_web.serve": None}):
            _stdout, stderr, exit_code = _run_cli(["serve"])
        assert exit_code == 1
        assert "mcp" in stderr.lower()
