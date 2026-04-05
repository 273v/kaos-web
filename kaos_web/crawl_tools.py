"""MCP tools for multi-page operations — URL discovery, batch fetch, site crawl.

Three tools following the Firecrawl Map/Crawl pattern:
- discover-urls: Fast URL inventory (sitemaps + page links)
- batch-fetch: Concurrent multi-URL fetch with extraction
- crawl-site: Full site crawl with sitemap-first discovery
"""

from __future__ import annotations

from typing import Any

from kaos_core import KaosContext, KaosRuntime, KaosTool, ToolMetadata, ToolResult
from kaos_core.types.annotations import ToolAnnotations
from kaos_core.types.enums import ToolCapability, ToolCategory
from kaos_core.types.parameters import ParameterSchema
from kaos_web.models import WebResponse

_MODULE = "kaos-web"
_VERSION = "0.1.0"

# All crawl tools make HTTP requests (openWorld) but don't modify anything (readOnly).
_CRAWL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


class DiscoverUrlsTool(KaosTool):
    """Discover URLs from a domain via sitemaps and page links."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-discover-urls",
            display_name="Discover URLs",
            description=(
                "Discover all URLs from a domain using sitemaps (robots.txt Sitemap: "
                "directives, /sitemap.xml) and page link extraction. Returns URL list "
                "with source metadata. Fast — no content extraction. Use this before "
                "'kaos-web-batch-fetch' or 'kaos-web-crawl-site' to preview what's available."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_CRAWL_ANNOTATIONS,
            input_schema=[
                ParameterSchema(
                    name="url",
                    type="string",
                    description="Domain or page URL to discover URLs from.",
                ),
                ParameterSchema(
                    name="sitemap",
                    type="string",
                    description=(
                        "Sitemap strategy: 'include' (default) uses both sitemaps and "
                        "page links, 'skip' ignores sitemaps, 'only' uses only sitemaps."
                    ),
                    required=False,
                    default="include",
                    constraints={"enum": ["include", "skip", "only"]},
                ),
                ParameterSchema(
                    name="include_patterns",
                    type="string",
                    description=(
                        "Comma-separated regex patterns for URL paths to include. "
                        "Example: '/blog/,/docs/' to only include blog and docs pages."
                    ),
                    required=False,
                ),
                ParameterSchema(
                    name="exclude_patterns",
                    type="string",
                    description=(
                        "Comma-separated regex patterns for URL paths to exclude. "
                        "Example: '/tag/,/author/' to skip tag and author pages."
                    ),
                    required=False,
                ),
                ParameterSchema(
                    name="max_urls",
                    type="integer",
                    description="Maximum URLs to return (default 1000).",
                    required=False,
                    default=1000,
                    constraints={"minimum": 1, "maximum": 10000},
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs.get("url", "")
        if not url:
            return ToolResult.create_error(
                "URL is required. Provide a domain (example.com) or full URL (https://example.com)."
            )

        sitemap = inputs.get("sitemap", "include")
        max_urls = inputs.get("max_urls", 1000)

        inc = _split_patterns(inputs.get("include_patterns"))
        exc = _split_patterns(inputs.get("exclude_patterns"))

        try:
            from kaos_web.clients.http import HttpClient
            from kaos_web.discovery import discover_urls

            async with HttpClient() as client:
                result = await discover_urls(
                    url,
                    client.fetch,
                    sitemap=sitemap,
                    include_patterns=inc,
                    exclude_patterns=exc,
                    max_urls=max_urls,
                )

            return ToolResult.create_success(
                output={
                    "urls": [
                        {
                            "url": u.url,
                            "source": u.source,
                            "lastmod": u.lastmod.isoformat() if u.lastmod else None,
                            "link_type": u.link_type,
                        }
                        for u in result.urls
                    ],
                    "total": result.total,
                    "sitemap_count": result.sitemap_count,
                    "page_link_count": result.page_link_count,
                    "errors": result.errors[:10],
                }
            )
        except Exception as exc_err:
            return ToolResult.create_error(
                f"URL discovery failed for '{url}': {exc_err}. "
                "Verify the URL is accessible. The site may not have a sitemap. "
                "Try sitemap='skip' to discover URLs from page links only."
            )


class BatchFetchTool(KaosTool):
    """Fetch multiple URLs concurrently with content extraction."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-batch-fetch",
            display_name="Batch Fetch URLs",
            description=(
                "Fetch multiple URLs concurrently with rate limiting and content "
                "extraction. Returns extracted text or markdown for each URL. "
                "Use after 'kaos-web-discover-urls' to fetch a filtered subset."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_CRAWL_ANNOTATIONS,
            input_schema=[
                ParameterSchema(
                    name="urls",
                    type="string",
                    description=(
                        "Comma-separated URLs to fetch. "
                        "Example: 'https://example.com/page1,https://example.com/page2'"
                    ),
                ),
                ParameterSchema(
                    name="concurrency",
                    type="integer",
                    description="Max concurrent requests (default 5).",
                    required=False,
                    default=5,
                    constraints={"minimum": 1, "maximum": 20},
                ),
                ParameterSchema(
                    name="output_format",
                    type="string",
                    description=(
                        "Output format: 'text', 'markdown', or 'metadata' (default 'markdown')."
                    ),
                    required=False,
                    default="markdown",
                    constraints={"enum": ["text", "markdown", "metadata"]},
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        urls_str = inputs.get("urls", "")
        if not urls_str:
            return ToolResult.create_error(
                "URLs are required. Provide comma-separated URLs. "
                "Use 'kaos-web-discover-urls' to find URLs first."
            )

        urls = [u.strip() for u in urls_str.split(",") if u.strip()]
        if not urls:
            return ToolResult.create_error(
                "No valid URLs found. Provide comma-separated URLs starting with "
                "http:// or https://."
            )

        concurrency = inputs.get("concurrency", 5)
        output_format = inputs.get("output_format", "markdown")

        try:
            from kaos_web.batch import batch_fetch

            result = await batch_fetch(urls, concurrency=concurrency)

            has_context = context is not None and context.runtime is not None
            pages = []
            for resp in result.responses:
                # Store as artifact when runtime context is available
                if has_context and resp.ok and resp.html and context is not None:
                    try:
                        page_data = await _store_response_artifact(resp, context)
                        pages.append(page_data)
                        continue
                    except Exception:
                        pass  # Fall through to inline extraction

                page_data = await _extract_response(resp, output_format)
                pages.append(page_data)

            return ToolResult.create_success(
                output={
                    "pages": pages,
                    "total": result.total,
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                    "artifact_backed": has_context,
                    "elapsed_ms": round(result.elapsed_ms, 1),
                    "errors": [{"url": e.url, "error": e.error} for e in result.errors],
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Batch fetch failed: {exc}. Check that all URLs are valid and accessible."
            )


class CrawlSiteTool(KaosTool):
    """Crawl a site with sitemap-first discovery and BFS link following."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-crawl-site",
            display_name="Crawl Site",
            description=(
                "Crawl a website starting from a URL. Discovers pages via sitemaps "
                "and link following (BFS), then extracts content. Returns page titles, "
                "text/markdown, and metadata. Use 'kaos-web-discover-urls' first to "
                "preview available URLs before committing to a full crawl."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_CRAWL_ANNOTATIONS,
            input_schema=[
                ParameterSchema(
                    name="url",
                    type="string",
                    description="Starting URL for the crawl.",
                ),
                ParameterSchema(
                    name="max_depth",
                    type="integer",
                    description="Max link-following depth (default 2). 0 = start page only.",
                    required=False,
                    default=2,
                    constraints={"minimum": 0, "maximum": 5},
                ),
                ParameterSchema(
                    name="max_pages",
                    type="integer",
                    description="Max pages to extract (default 50).",
                    required=False,
                    default=50,
                    constraints={"minimum": 1, "maximum": 500},
                ),
                ParameterSchema(
                    name="concurrency",
                    type="integer",
                    description="Max concurrent requests (default 5).",
                    required=False,
                    default=5,
                    constraints={"minimum": 1, "maximum": 20},
                ),
                ParameterSchema(
                    name="sitemap",
                    type="string",
                    description="Sitemap strategy: 'include', 'skip', or 'only'.",
                    required=False,
                    default="include",
                    constraints={"enum": ["include", "skip", "only"]},
                ),
                ParameterSchema(
                    name="include_patterns",
                    type="string",
                    description="Comma-separated regex patterns for URL paths to include.",
                    required=False,
                ),
                ParameterSchema(
                    name="exclude_patterns",
                    type="string",
                    description="Comma-separated regex patterns for URL paths to exclude.",
                    required=False,
                ),
                ParameterSchema(
                    name="output_format",
                    type="string",
                    description=(
                        "Output format: 'summary' (title + URL + word count), "
                        "'text' (plain text), 'markdown' (default 'summary')."
                    ),
                    required=False,
                    default="summary",
                    constraints={"enum": ["summary", "text", "markdown"]},
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs.get("url", "")
        if not url:
            return ToolResult.create_error(
                "URL is required. Provide a starting URL for the crawl. "
                "Use 'kaos-web-discover-urls' to preview available URLs first."
            )

        max_depth = inputs.get("max_depth", 2)
        max_pages = inputs.get("max_pages", 50)
        concurrency = inputs.get("concurrency", 5)
        sitemap = inputs.get("sitemap", "include")
        output_format = inputs.get("output_format", "summary")

        inc = _split_patterns(inputs.get("include_patterns"))
        exc = _split_patterns(inputs.get("exclude_patterns"))

        try:
            from kaos_web.crawl import crawl_site

            result = await crawl_site(
                url,
                max_depth=max_depth,
                max_pages=max_pages,
                concurrency=concurrency,
                sitemap=sitemap,
                include_patterns=inc,
                exclude_patterns=exc,
            )

            has_context = context is not None and context.runtime is not None
            pages = []
            for page in result.pages:
                # Store as markdown artifact when runtime context is available
                if has_context and page.content_markdown and context is not None:
                    try:
                        page_data = await _store_crawl_page_artifact(page, context)
                        pages.append(page_data)
                        continue
                    except Exception:
                        pass  # Fall through to inline extraction

                if output_format == "summary":
                    word_count = len(page.content_text.split()) if page.content_text else 0
                    pages.append(
                        {
                            "url": page.url,
                            "title": page.title,
                            "depth": page.depth,
                            "word_count": word_count,
                            "link_count": len(page.links),
                        }
                    )
                elif output_format == "text":
                    pages.append(
                        {
                            "url": page.url,
                            "title": page.title,
                            "depth": page.depth,
                            "content": page.content_text[:5000],
                            "truncated": len(page.content_text) > 5000,
                        }
                    )
                else:  # markdown
                    pages.append(
                        {
                            "url": page.url,
                            "title": page.title,
                            "depth": page.depth,
                            "content": page.content_markdown[:5000],
                            "truncated": len(page.content_markdown) > 5000,
                        }
                    )

            return ToolResult.create_success(
                output={
                    "pages": pages,
                    "total_discovered": result.total_discovered,
                    "total_crawled": result.total_crawled,
                    "total_extracted": result.total_extracted,
                    "artifact_backed": has_context,
                    "sitemap_entries": result.sitemap_entries,
                    "elapsed_ms": round(result.elapsed_ms, 1),
                    "errors": [{"url": e.url, "error": e.error} for e in result.errors[:10]],
                }
            )
        except Exception as exc_err:
            return ToolResult.create_error(
                f"Crawl failed for '{url}': {exc_err}. "
                "Verify the URL is accessible. Try reducing max_depth or max_pages."
            )


def register_crawl_tools(runtime: KaosRuntime) -> int:
    """Register all crawl tools with the runtime. Returns count."""
    tools: list[KaosTool] = [
        DiscoverUrlsTool(),
        BatchFetchTool(),
        CrawlSiteTool(),
    ]
    for tool in tools:
        runtime.tools.register_tool(tool)
    return len(tools)


def _split_patterns(value: str | None) -> list[str] | None:
    """Split comma-separated patterns into a list."""
    if not value:
        return None
    patterns = [p.strip() for p in value.split(",") if p.strip()]
    return patterns or None


async def _extract_response(resp: WebResponse, output_format: str) -> dict[str, Any]:
    """Extract content from a WebResponse based on output format."""
    from kaos_web.extract import extract_metadata, html_to_document

    page_data: dict[str, Any] = {"url": resp.url, "status_code": resp.status_code}

    if not resp.ok or not resp.html:
        page_data["error"] = f"HTTP {resp.status_code}"
        return page_data

    try:
        if output_format == "metadata":
            meta = extract_metadata(resp.html, url=resp.url)
            page_data["metadata"] = meta.model_dump(exclude_none=True)
        elif output_format == "text":
            from kaos_content.serializers.text import serialize_text

            doc = html_to_document(resp.html, url=resp.url)
            page_data["title"] = doc.metadata.title
            page_data["content"] = serialize_text(doc)[:5000]
            page_data["truncated"] = len(serialize_text(doc)) > 5000
        else:  # markdown
            from kaos_content.serializers.markdown import serialize_markdown

            doc = html_to_document(resp.html, url=resp.url)
            md = serialize_markdown(doc)
            page_data["title"] = doc.metadata.title
            page_data["content"] = md[:5000]
            page_data["truncated"] = len(md) > 5000
    except Exception as exc:
        page_data["error"] = f"Extraction failed: {exc}"

    return page_data


async def _store_response_artifact(resp: WebResponse, context: KaosContext) -> dict[str, Any]:
    """Parse a WebResponse into a ContentDocument and store as a session artifact.

    Returns a dict with artifact metadata suitable for inclusion in
    BatchFetchTool / CrawlSiteTool structured output.
    """
    from kaos_content.artifacts import store_document, unique_document_name
    from kaos_web.extract import html_to_document

    doc = html_to_document(resp.html, url=resp.url)
    if not doc.body:
        msg = "No content extracted"
        raise ValueError(msg)

    manifest = await store_document(
        doc,
        context.runtime,
        context,
        name=unique_document_name(doc.metadata.title or resp.url),
        description=f"Batch fetch: {resp.url}",
        metadata={"source_url": resp.url, "block_count": len(doc.body)},
    )
    return {
        "url": resp.url,
        "artifact_id": manifest.artifact_id,
        "title": doc.metadata.title,
        "body_uri": manifest.body_uri,
        "size": manifest.size,
    }


async def _store_crawl_page_artifact(page: Any, context: KaosContext) -> dict[str, Any]:
    """Store a crawled page's markdown content as a session artifact.

    CrawlPage objects carry pre-serialized markdown (not a ContentDocument),
    so we store the markdown text directly via VFS + create_from_path.
    """
    from kaos_content.artifacts import unique_document_name
    from kaos_core.types.enums import ArtifactRole

    name = unique_document_name(page.title or page.url)
    vfs_path = f"documents/{name}.md"
    ctx_path = context.get_vfs_path(vfs_path)
    await ctx_path.write_bytes(page.content_markdown.encode("utf-8"))

    manifest = await context.runtime.artifacts.create_from_path(
        vfs_path,
        context_id=context.session_id,
        session_id=context.session_id,
        name=name,
        description=f"Crawled: {page.url}",
        mime_type="text/markdown",
        role=ArtifactRole.BODY,
        provenance={
            "source_url": page.url,
            "tool": "kaos-web-crawl-site",
            "depth": page.depth,
        },
        metadata={
            "source_url": page.url,
            "title": page.title,
            "depth": page.depth,
        },
    )
    return {
        "url": page.url,
        "artifact_id": manifest.artifact_id,
        "title": page.title,
        "body_uri": manifest.body_uri,
        "size": manifest.size,
        "depth": page.depth,
    }
