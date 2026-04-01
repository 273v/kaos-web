"""MCP tool definitions for web content extraction.

KaosTool implementations registered with KaosRuntime and exposed via kaos-mcp.
Each tool fetches web content, extracts it into ContentDocument AST, and returns
summary + resource links following the artifact tiering model.
"""

from __future__ import annotations

from typing import Any

from kaos_core import KaosContext, KaosRuntime, KaosTool, ToolMetadata, ToolResult
from kaos_core.types.annotations import ToolAnnotations
from kaos_core.types.enums import ToolCapability, ToolCategory
from kaos_core.types.parameters import ParameterSchema

_MODULE = "kaos-web"
_VERSION = "0.1.0"

# All web tools make HTTP requests (openWorld) but don't modify anything (readOnly).
_WEB_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


async def _fetch_html(url: str, use_browser: bool = False) -> tuple[str, str]:
    """Fetch HTML from a URL. Returns (html, final_url)."""
    from kaos_web.clients.http import HttpClient
    from kaos_web.models import WebRequest

    if use_browser:
        try:
            from kaos_web.clients.browser import BrowserClient

            async with BrowserClient() as client:
                resp = await client.fetch(WebRequest(url=url))
                return resp.html, resp.url
        except ImportError:
            pass  # Fall back to HTTP

    async with HttpClient() as client:
        resp = await client.fetch(WebRequest(url=url))
        return resp.html, resp.url


class FetchPageTool(KaosTool):
    """Fetch a web page and extract it into a ContentDocument artifact."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-fetch-page",
            display_name="Fetch Web Page",
            description=(
                "Fetch a URL and extract content into a structured document with "
                "headings, paragraphs, and provenance. Returns a summary and resource "
                "link to the full document. Use level='sentence' for sentence-level "
                "search within the extracted content."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(name="url", type="string", description="URL to fetch."),
                ParameterSchema(
                    name="use_browser",
                    type="boolean",
                    description="Use browser rendering for JS pages (requires playwright).",
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        use_browser = inputs.get("use_browser", False)

        if context is None or context.runtime is None:
            return ToolResult.create_error(
                "No runtime context available. "
                "FetchPage requires a KaosRuntime with artifact storage. "
                "Use 'kaos-web-get-markdown' for context-free extraction."
            )

        try:
            html, final_url = await _fetch_html(url, use_browser)
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_content.artifacts import document_outline, document_to_summary, store_document
            from kaos_content.views import DocumentView
            from kaos_web.extract import html_to_document

            doc = html_to_document(html, url=final_url)
            if not doc.body:
                return ToolResult.create_error(
                    f"No content extracted from {url}. "
                    "The page may be empty, require JavaScript rendering "
                    "(try use_browser=true), or have no article content."
                )

            manifest = await store_document(
                doc,
                context.runtime,
                context,
                name=doc.metadata.title or final_url,
                description=f"Extracted from {final_url}",
                metadata={"source_url": final_url, "block_count": len(doc.body)},
            )

            summary = document_to_summary(doc, max_length=500)
            outline = document_outline(doc)
            view = DocumentView(doc)

            return manifest.to_tool_result(
                summary=summary,
                structured_content={
                    "artifact_id": manifest.artifact_id,
                    "title": doc.metadata.title,
                    "url": final_url,
                    "block_count": len(doc.body),
                    "has_sections": view.has_sections,
                    "section_count": len(view.flat_sections),
                    "outline": outline[:10],
                    "body_uri": manifest.body_uri,
                    "sections_uri": f"kaos://content/{manifest.artifact_id}/sections",
                },
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Content extraction failed for {url}: {exc}. "
                "The HTML may be malformed or incompatible."
            )


class GetPageTextTool(KaosTool):
    """Fetch a URL and return plain text content."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-get-text",
            display_name="Get Page Text",
            description="Fetch a URL and return the extracted plain text content.",
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(name="url", type="string", description="URL to fetch."),
                ParameterSchema(
                    name="use_browser",
                    type="boolean",
                    description="Use browser rendering for JS pages.",
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        try:
            html, final_url = await _fetch_html(url, inputs.get("use_browser", False))
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_content.serializers.text import serialize_text
            from kaos_web.extract import html_to_document

            doc = html_to_document(html, url=final_url)
            text = serialize_text(doc)
            return ToolResult.create_success(text)
        except Exception as exc:
            return ToolResult.create_error(
                f"Extraction failed for {url}: {exc}. "
                "The HTML may be malformed. Try 'kaos-web-fetch-page' for full AST extraction."
            )


class GetPageMarkdownTool(KaosTool):
    """Fetch a URL and return markdown content."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-get-markdown",
            display_name="Get Page Markdown",
            description=(
                "Fetch a URL and return the extracted content as markdown. "
                "No runtime context needed — works standalone."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(name="url", type="string", description="URL to fetch."),
                ParameterSchema(
                    name="use_browser",
                    type="boolean",
                    description="Use browser rendering for JS pages.",
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        try:
            html, final_url = await _fetch_html(url, inputs.get("use_browser", False))
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_content.serializers.markdown import serialize_markdown
            from kaos_web.extract import html_to_document

            doc = html_to_document(html, url=final_url)
            md = serialize_markdown(doc)
            return ToolResult.create_success(md)
        except Exception as exc:
            return ToolResult.create_error(
                f"Extraction failed for {url}: {exc}. "
                "The HTML may be malformed. Try 'kaos-web-fetch-page' for full AST extraction."
            )


class GetPageMetadataTool(KaosTool):
    """Extract metadata from a web page (JSON-LD, OpenGraph, meta tags)."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-get-metadata",
            display_name="Get Page Metadata",
            description=(
                "Extract structured metadata from a URL: title, author, description, "
                "dates, JSON-LD, OpenGraph. No content extraction — fast."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(name="url", type="string", description="URL to fetch."),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        try:
            html, final_url = await _fetch_html(url)
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_web.extract import extract_metadata

            meta = extract_metadata(html, url=final_url)
            return ToolResult.create_success(output=meta.model_dump(exclude_none=True))
        except Exception as exc:
            return ToolResult.create_error(
                f"Metadata extraction failed for {url}: {exc}. "
                "The HTML may be malformed or the page may not have standard metadata tags."
            )


class SearchPageTool(KaosTool):
    """Fetch a web page and search within the extracted content."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-search-page",
            display_name="Search Web Page",
            description=(
                "Fetch a URL, extract content, and search within it using BM25. "
                "Returns matching paragraphs or sentences with block_refs and scores. "
                "Requires kaos-nlp-core for BM25; falls back to term frequency."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(name="url", type="string", description="URL to fetch."),
                ParameterSchema(name="query", type="string", description="Search query text."),
                ParameterSchema(
                    name="top_k",
                    type="integer",
                    description="Maximum number of results (default 10).",
                    required=False,
                    default=10,
                ),
                ParameterSchema(
                    name="level",
                    type="string",
                    description="Search granularity: 'paragraph' or 'sentence'.",
                    required=False,
                    default="paragraph",
                    constraints={"enum": ["paragraph", "sentence"]},
                ),
                ParameterSchema(
                    name="use_browser",
                    type="boolean",
                    description="Use browser rendering for JS pages.",
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        query = inputs["query"]
        top_k = inputs.get("top_k", 10)
        level = inputs.get("level", "paragraph")

        if not query.strip():
            return ToolResult.create_error(
                "Query must not be empty. Provide one or more search terms."
            )

        try:
            html, final_url = await _fetch_html(url, inputs.get("use_browser", False))
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_content.search import search_document
            from kaos_web.extract import html_to_document

            doc = html_to_document(html, url=final_url)
            search_results = search_document(doc, query, top_k=top_k, level=level)

            return ToolResult.create_success(
                output={
                    "url": final_url,
                    "results": [
                        {
                            "text": r.text,
                            "score": r.score,
                            "block_ref": r.block_ref,
                            "page": r.page,
                            "section_ref": r.section_ref,
                            "section_text": r.section_title,
                        }
                        for r in search_results.results
                    ],
                    "total_matches": search_results.total_matches,
                    "has_more": search_results.has_more,
                    "query": query,
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Search failed for {url}: {exc}. Content extraction or search may have failed."
            )


def register_web_tools(runtime: KaosRuntime) -> int:
    """Register all web tools with the runtime. Returns count."""
    tools: list[KaosTool] = [
        FetchPageTool(),
        GetPageTextTool(),
        GetPageMarkdownTool(),
        GetPageMetadataTool(),
        SearchPageTool(),
    ]
    for tool in tools:
        runtime.tools.register_tool(tool)
    return len(tools)
