"""CLI for kaos-web."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    """Entry point for kaos-web CLI."""
    parser = argparse.ArgumentParser(prog="kaos-web", description="Web content extraction")
    sub = parser.add_subparsers(dest="command")

    # extract
    p_extract = sub.add_parser("extract", help="Extract content from a URL or HTML file")
    p_extract.add_argument("source", help="URL or path to HTML file")
    p_extract.add_argument("--format", choices=["markdown", "text", "json"], default="markdown")
    p_extract.add_argument("--no-readability", action="store_true", help="Skip readability")
    p_extract.add_argument("--output", "-o", type=Path, help="Output file")
    p_extract.add_argument("--json", action="store_true", help="Structured JSON output")

    # metadata
    p_meta = sub.add_parser("metadata", help="Extract metadata from a URL or HTML file")
    p_meta.add_argument("source", help="URL or path to HTML file")
    p_meta.add_argument("--json", action="store_true", help="Structured JSON output")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch a URL and show response info")
    p_fetch.add_argument("source", help="URL to fetch")
    p_fetch.add_argument("--browser", action="store_true", help="Use browser rendering")
    p_fetch.add_argument("--output", "-o", type=Path, help="Save HTML to file")
    p_fetch.add_argument("--json", action="store_true", help="Structured JSON output")

    # search
    p_search = sub.add_parser("search", help="Fetch and search within a web page")
    p_search.add_argument("source", help="URL or path to HTML file")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--top-k", "-k", type=int, default=10, help="Max results (default: 10)")
    p_search.add_argument(
        "--level",
        choices=["paragraph", "sentence"],
        default="paragraph",
        help="Search granularity (default: paragraph)",
    )
    p_search.add_argument("--json", action="store_true", help="Structured JSON output")

    # serve
    p_serve = sub.add_parser("serve", help="Start MCP server")
    p_serve.add_argument("--http", action="store_true", help="Use HTTP transport")
    p_serve.add_argument("--host", default="127.0.0.1", help="HTTP host")
    p_serve.add_argument("--port", type=int, default=8000, help="HTTP port")
    p_serve.add_argument("--debug", action="store_true", help="Debug logging")
    p_serve.add_argument("--browser", action="store_true", help="Enable browser interaction tools")
    p_serve.add_argument("--crawl", action="store_true", help="Enable crawl/discovery tools")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "extract": _cmd_extract,
        "metadata": _cmd_metadata,
        "fetch": _cmd_fetch,
        "search": _cmd_search,
        "serve": _cmd_serve,
    }
    handlers[args.command](args)


def _get_html(source: str) -> tuple[str, str]:
    """Get HTML from a URL or file path. Returns (html, url)."""
    if source.startswith(("http://", "https://")):
        import asyncio

        from kaos_web.clients.http import HttpClient
        from kaos_web.models import WebRequest

        async def _fetch() -> tuple[str, str]:
            client = HttpClient()
            try:
                resp = await client.fetch(WebRequest(url=source))
                return resp.html, resp.url
            finally:
                await client.close()

        return asyncio.run(_fetch())

    path = Path(source)
    if not path.exists():
        print(f"File not found: {source}", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8"), f"file://{path.resolve()}"


def _json_out(data: dict) -> None:
    """Print JSON to stdout."""
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()


def _cmd_extract(args: argparse.Namespace) -> None:
    """Extract content from HTML."""
    from kaos_content.serializers.markdown import serialize_markdown
    from kaos_content.serializers.text import serialize_text
    from kaos_web.extract import html_to_document

    html, url = _get_html(args.source)
    doc = html_to_document(html, url=url, extract_content=not args.no_readability)

    if args.json:
        _json_out(
            {
                "command": "extract",
                "source": args.source,
                "url": url,
                "title": doc.metadata.title,
                "block_count": len(doc.body),
                "format": args.format,
                "content": (
                    serialize_markdown(doc)
                    if args.format == "markdown"
                    else serialize_text(doc)
                    if args.format == "text"
                    else doc.model_dump(mode="json")
                ),
            }
        )
        return

    if args.format == "markdown":
        output = serialize_markdown(doc)
    elif args.format == "text":
        output = serialize_text(doc)
    else:
        output = json.dumps(doc.model_dump(mode="json"), indent=2, ensure_ascii=False)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output)


def _cmd_metadata(args: argparse.Namespace) -> None:
    """Extract metadata from HTML."""
    from kaos_web.extract import extract_metadata

    html, url = _get_html(args.source)
    meta = extract_metadata(html, url=url)

    if args.json:
        _json_out({"command": "metadata", "source": args.source, **meta.model_dump()})
        return

    print(f"Title: {meta.title or '(none)'}")
    print(f"Author: {meta.author or '(none)'}")
    print(f"Description: {meta.description or '(none)'}")
    print(f"URL: {meta.url or url}")
    print(f"Language: {meta.language or '(none)'}")
    print(f"Published: {meta.date_published or '(none)'}")
    if meta.site_name:
        print(f"Site: {meta.site_name}")
    if meta.structured_data:
        print(f"JSON-LD: {len(meta.structured_data)} items")
    if meta.opengraph:
        print(f"OpenGraph: {len(meta.opengraph)} properties")


def _cmd_fetch(args: argparse.Namespace) -> None:
    """Fetch a URL and show response info."""
    import asyncio

    from kaos_web.clients.http import HttpClient
    from kaos_web.models import WebRequest

    async def _do_fetch() -> None:
        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url=args.source))
            if args.json:
                _json_out(
                    {
                        "command": "fetch",
                        "url": resp.url,
                        "status_code": resp.status_code,
                        "content_type": resp.content_type,
                        "content_length": len(resp.html),
                        "elapsed_ms": resp.elapsed_ms,
                    }
                )
            elif args.output:
                args.output.write_text(resp.html, encoding="utf-8")
                print(f"Written {len(resp.html)} bytes to {args.output}", file=sys.stderr)
            else:
                print(f"URL: {resp.url}")
                print(f"Status: {resp.status_code}")
                print(f"Content-Type: {resp.content_type}")
                print(f"Content-Length: {len(resp.html)}")
                print(f"Elapsed: {resp.elapsed_ms:.0f} ms")

    asyncio.run(_do_fetch())


def _cmd_search(args: argparse.Namespace) -> None:
    """Fetch and search within a web page."""
    from kaos_web.extract import html_to_document

    html, url = _get_html(args.source)
    doc = html_to_document(html, url=url)

    from kaos_content.search import search_document

    search_results = search_document(doc, args.query, top_k=args.top_k, level=args.level)

    if args.json:
        _json_out(
            {
                "command": "search",
                "source": args.source,
                "url": url,
                "query": args.query,
                "level": args.level,
                "total_matches": search_results.total_matches,
                "has_more": search_results.has_more,
                "results": [
                    {
                        "text": r.text,
                        "score": r.score,
                        "page": r.page,
                        "section": r.section_title,
                        "ref": r.block_ref,
                    }
                    for r in search_results.results
                ],
            }
        )
        return

    results = search_results.results
    if not results:
        print(f'No results for "{args.query}"')
        return

    print(f'Results for "{args.query}" ({search_results.total_matches} matches):\n')
    for i, r in enumerate(results, 1):
        section_str = f" | {r.section_title}" if r.section_title else ""
        print(f"[{i}] Score: {r.score:.1f}{section_str}")
        for line in r.text.splitlines():
            print(f"    {line}")
        print()


def _cmd_serve(args: argparse.Namespace) -> None:
    """Start the MCP server."""
    try:
        from kaos_web.serve import main as serve_main
    except ImportError:
        print(
            "Error: MCP server requires the 'mcp' extra.\n"
            "Install with: pip install 'kaos-web[mcp]'",
            file=sys.stderr,
        )
        sys.exit(1)

    serve_argv: list[str] = []
    if args.http:
        serve_argv.extend(["--http", "--host", args.host, "--port", str(args.port)])
    if args.debug:
        serve_argv.append("--debug")
    if args.browser:
        serve_argv.append("--browser")
    if args.crawl:
        serve_argv.append("--crawl")
    serve_main(serve_argv)
