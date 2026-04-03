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
        "--browser", action="store_true", help="Enable browser interaction tools (18 tools)"
    )
    parser.add_argument(
        "--crawl", action="store_true", help="Enable crawl/discovery tools (3 tools)"
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

    print(f"Total: {n_tools} tools registered", file=sys.stderr)

    # Configure server
    settings = KaosMCPSettings(
        name="kaos-web-server",
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
