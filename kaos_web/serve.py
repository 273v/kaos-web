"""Run the KAOS MCP server with web extraction tools.

Usage:
    # stdio (for Claude Code / Claude Desktop)
    kaos-web-serve

    # streamable HTTP
    kaos-web-serve --http --port 8000

    # with browser interaction tools
    kaos-web-serve --browser

    # with crawl tools
    kaos-web-serve --crawl

    # all tools + debug logging
    kaos-web-serve --browser --crawl --debug
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    """Entry point for the MCP server."""
    parser = argparse.ArgumentParser(description="KAOS MCP Server with web extraction tools")
    parser.add_argument("--http", action="store_true", help="Use streamable HTTP transport")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--browser", action="store_true", help="Enable browser interaction tools (19 tools)"
    )
    parser.add_argument(
        "--crawl", action="store_true", help="Enable crawl/discovery tools (3 tools)"
    )
    parser.add_argument(
        "--domain", action="store_true", help="Enable domain intelligence tools (10 tools)"
    )
    args = parser.parse_args(argv)

    try:
        from kaos_mcp import KaosMCPServer, KaosMCPSettings

        from kaos_core import KaosRuntime
    except ImportError:
        print(
            "Error: MCP server requires the 'mcp' extra.\n"
            "Install with: pip install 'kaos-web[mcp]'",
            file=sys.stderr,
        )
        sys.exit(1)

    from kaos_web.tools import register_web_tools

    # Create runtime and register core web tools (always)
    runtime = KaosRuntime()
    n_tools = register_web_tools(runtime)
    print(f"Registered {n_tools} web extraction tools", file=sys.stderr)

    # Optionally register browser interaction tools
    if args.browser:
        from kaos_web.browser_tools import register_browser_tools

        n_browser = register_browser_tools(runtime)
        n_tools += n_browser
        print(f"Registered {n_browser} browser interaction tools", file=sys.stderr)

    # Optionally register crawl/discovery tools
    if args.crawl:
        from kaos_web.crawl_tools import register_crawl_tools

        n_crawl = register_crawl_tools(runtime)
        n_tools += n_crawl
        print(f"Registered {n_crawl} crawl/discovery tools", file=sys.stderr)

    # Optionally register domain intelligence tools
    if args.domain:
        from kaos_web.domain_tools import register_domain_tools

        n_domain = register_domain_tools(runtime)
        n_tools += n_domain
        print(f"Registered {n_domain} domain intelligence tools", file=sys.stderr)

    print(f"Total: {n_tools} tools registered", file=sys.stderr)

    # Configure server
    instructions = (
        "kaos-web provides web content extraction and browser automation. "
        "For simple page content, use kaos-web-get-markdown. "
        "For sites that block HTTP requests, it auto-retries with a browser. "
        "For API endpoint discovery: enable request logging first, then navigate, "
        "then list requests filtered to resource_type='fetch' to find JSON APIs. "
        "For interactive workflows: navigate with a context_id, then use snapshot "
        "to find elements, click/fill/type to interact, and content to extract results. "
        "For SPA data extraction: navigate to establish context, enable "
        "log-requests with capture_bodies=true, navigate to target page "
        "(logging hooks survive page replacement), then use browser-requests "
        "with resource_type='fetch' to find API calls, browser-get-request to "
        "get decoded JSON bodies, and browser-captured-responses with "
        "store_artifacts=true to persist captured responses as session artifacts."
    )
    settings = KaosMCPSettings(
        name="kaos-web-server",
        instructions=instructions,
        transport="streamable-http" if args.http else "stdio",
        host=args.host,
        port=args.port,
        debug=args.debug,
    )

    server = KaosMCPServer(runtime=runtime, settings=settings)

    if args.http:
        print(f"Starting HTTP server on {args.host}:{args.port}/mcp", file=sys.stderr)
        server.run_streamable_http()
    else:
        print("Starting stdio server", file=sys.stderr)
        server.run_stdio()


if __name__ == "__main__":
    main()
