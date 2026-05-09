"""Tests for ``kaos_web.serve`` — MCP server CLI entry point.

The ``main()`` function is exercised end-to-end with the real argument
parser but with the MCP server itself mocked out. We assert the parser
accepts the expected flags and the right tool registrar functions are
called for each combination.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kaos_web import serve as serve_module


def _make_mocks() -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Common patch targets — runtime + 4 registrars."""
    runtime = MagicMock()
    runtime.return_value = MagicMock()
    register_web = MagicMock(return_value=7)
    register_browser = MagicMock(return_value=19)
    register_crawl = MagicMock(return_value=3)
    register_domain = MagicMock(return_value=10)
    return runtime, register_web, register_browser, register_crawl, register_domain


def _patch_server() -> object:
    """Patch the kaos_mcp imports to do nothing real."""
    server = MagicMock()
    settings = MagicMock()
    server_class = MagicMock(
        return_value=MagicMock(run_stdio=MagicMock(), run_streamable_http=MagicMock())
    )
    return server, settings, server_class


class TestMainCli:
    def test_default_invocation(self, capsys: pytest.CaptureFixture[str]) -> None:
        runtime, web, browser, crawl, domain = _make_mocks()
        with (
            patch("kaos_core.KaosRuntime", runtime),
            patch("kaos_web.tools.register_web_tools", web),
            patch("kaos_mcp.KaosMCPServer") as server_cls,
            patch("kaos_mcp.KaosMCPSettings") as settings_cls,
        ):
            serve_module.main([])
        web.assert_called_once()
        browser.assert_not_called()
        crawl.assert_not_called()
        domain.assert_not_called()
        server_cls.return_value.run_stdio.assert_called_once()
        server_cls.return_value.run_streamable_http.assert_not_called()
        # Settings always include the instructions string
        assert settings_cls.call_args.kwargs["transport"] == "stdio"
        captured = capsys.readouterr()
        assert "Registered 7 web extraction tools" in captured.err
        assert "Total: 7 tools registered" in captured.err

    def test_browser_flag(self) -> None:
        runtime, web, browser, crawl, domain = _make_mocks()
        with (
            patch("kaos_core.KaosRuntime", runtime),
            patch("kaos_web.tools.register_web_tools", web),
            patch("kaos_web.browser_tools.register_browser_tools", browser),
            patch("kaos_mcp.KaosMCPServer"),
            patch("kaos_mcp.KaosMCPSettings"),
        ):
            serve_module.main(["--browser"])
        browser.assert_called_once()
        crawl.assert_not_called()
        domain.assert_not_called()

    def test_crawl_flag(self) -> None:
        runtime, web, browser, crawl, _domain = _make_mocks()
        with (
            patch("kaos_core.KaosRuntime", runtime),
            patch("kaos_web.tools.register_web_tools", web),
            patch("kaos_web.crawl_tools.register_crawl_tools", crawl),
            patch("kaos_mcp.KaosMCPServer"),
            patch("kaos_mcp.KaosMCPSettings"),
        ):
            serve_module.main(["--crawl"])
        crawl.assert_called_once()
        browser.assert_not_called()

    def test_domain_flag(self) -> None:
        runtime, web, browser, _crawl, domain = _make_mocks()
        with (
            patch("kaos_core.KaosRuntime", runtime),
            patch("kaos_web.tools.register_web_tools", web),
            patch("kaos_web.domain_tools.register_domain_tools", domain),
            patch("kaos_mcp.KaosMCPServer"),
            patch("kaos_mcp.KaosMCPSettings"),
        ):
            serve_module.main(["--domain"])
        domain.assert_called_once()
        browser.assert_not_called()

    def test_all_flags(self) -> None:
        runtime, web, browser, crawl, domain = _make_mocks()
        with (
            patch("kaos_core.KaosRuntime", runtime),
            patch("kaos_web.tools.register_web_tools", web),
            patch("kaos_web.browser_tools.register_browser_tools", browser),
            patch("kaos_web.crawl_tools.register_crawl_tools", crawl),
            patch("kaos_web.domain_tools.register_domain_tools", domain),
            patch("kaos_mcp.KaosMCPServer"),
            patch("kaos_mcp.KaosMCPSettings"),
        ):
            serve_module.main(["--browser", "--crawl", "--domain"])
        web.assert_called_once()
        browser.assert_called_once()
        crawl.assert_called_once()
        domain.assert_called_once()

    def test_http_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        runtime, web, *_ = _make_mocks()
        server_inst = MagicMock()
        with (
            patch("kaos_core.KaosRuntime", runtime),
            patch("kaos_web.tools.register_web_tools", web),
            patch("kaos_mcp.KaosMCPServer", return_value=server_inst),
            patch("kaos_mcp.KaosMCPSettings") as settings_cls,
        ):
            serve_module.main(["--http", "--port", "9999", "--host", "0.0.0.0"])
        server_inst.run_streamable_http.assert_called_once()
        server_inst.run_stdio.assert_not_called()
        kwargs = settings_cls.call_args.kwargs
        assert kwargs["transport"] == "streamable-http"
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 9999
        captured = capsys.readouterr()
        assert "Starting HTTP server on 0.0.0.0:9999" in captured.err

    def test_debug_flag_propagates(self) -> None:
        runtime, web, *_ = _make_mocks()
        with (
            patch("kaos_core.KaosRuntime", runtime),
            patch("kaos_web.tools.register_web_tools", web),
            patch("kaos_mcp.KaosMCPServer"),
            patch("kaos_mcp.KaosMCPSettings") as settings_cls,
        ):
            serve_module.main(["--debug"])
        assert settings_cls.call_args.kwargs["debug"] is True

    def test_help_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            serve_module.main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "browser" in captured.out.lower()
        assert "crawl" in captured.out.lower()

    def test_invalid_flag_errors(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            serve_module.main(["--unknown-flag"])
        assert exc_info.value.code == 2

    def test_import_error_exits_with_install_hint(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the kaos_mcp/kaos_core import inside main() to fail by
        # poisoning the module table so re-import raises ImportError.
        import sys

        # Save and remove kaos_mcp from sys.modules; install a finder that fails.
        monkeypatch.setitem(sys.modules, "kaos_mcp", None)  # type: ignore[arg-type]

        with pytest.raises(SystemExit) as exc_info:
            serve_module.main([])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "kaos-web[mcp]" in captured.err
