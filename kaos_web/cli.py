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

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "extract": _cmd_extract,
        "metadata": _cmd_metadata,
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
