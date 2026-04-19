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

# Shared parameter schema for content extraction scope.
_CONTENT_SCOPE_PARAM = ParameterSchema(
    name="content_scope",
    type="number",
    description=(
        "Content extraction breadth from 0.0 (strict, article-only) to 1.0 "
        "(permissive, include more). Default 0.5. Ignored when raw=true."
    ),
    required=False,
    default=0.5,
)

# Shared parameter schemas for browser-mode tools.
_BROWSER_PARAMS: list[ParameterSchema] = [
    ParameterSchema(
        name="dismiss_overlays",
        type="boolean",
        description=(
            "Auto-dismiss known cookie consent banners (OneTrust, CookieBot, "
            "TrustArc, etc.) before extracting content. Only applies when "
            "use_browser=true. Set false to skip."
        ),
        required=False,
        default=True,
    ),
    ParameterSchema(
        name="wait_for_selector",
        type="string",
        description=(
            "CSS selector to wait for before extracting content. Only applies "
            "when use_browser=true. Useful for JS-rendered pages where specific "
            "content must appear (e.g. '#results', '.article-body')."
        ),
        required=False,
    ),
    ParameterSchema(
        name="wait_for_settled",
        type="boolean",
        description=(
            "Wait for JS-rendered content to appear before extraction. "
            "Zero penalty on already-rendered pages; waits up to 5s on "
            "JS-heavy pages. Skipped when wait_for_selector is set. "
            "Only applies when use_browser=true."
        ),
        required=False,
        default=True,
    ),
]


def _browser_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Extract browser-related kwargs from tool inputs for ``_fetch_html``."""
    kw: dict[str, Any] = {}
    if inputs.get("dismiss_overlays") is not None:
        kw["dismiss_overlays"] = inputs["dismiss_overlays"]
    if inputs.get("wait_for_selector"):
        kw["wait_for_selector"] = inputs["wait_for_selector"]
    if inputs.get("wait_for_settled") is not None:
        kw["wait_for_settled"] = inputs["wait_for_settled"]
    return kw


async def _fetch_html(
    url: str,
    use_browser: bool = False,
    context_id: str | None = None,
    *,
    dismiss_overlays: bool = True,
    wait_for_selector: str | None = None,
    wait_for_settled: bool = True,
) -> tuple[str, str]:
    """Fetch HTML from a URL. Returns (html, final_url).

    If ``context_id`` is provided with ``use_browser=True``, the browser page
    is kept alive for subsequent interaction via browser tools.

    Args:
        url: URL to fetch.
        use_browser: Use Playwright browser rendering.
        context_id: Named browser context for persistent sessions.
        dismiss_overlays: Auto-dismiss known cookie consent banners
            (OneTrust, CookieBot, etc.) before extraction. Only applies
            when using browser rendering. Defaults to True.
        wait_for_selector: CSS selector to wait for before extracting
            content. Only applies when using browser rendering.
        wait_for_settled: Wait for JS-rendered content to appear.
            Zero penalty on already-rendered pages. Skipped when
            wait_for_selector is set. Defaults to True.
    """
    from kaos_web.clients.http import HttpClient
    from kaos_web.models import WebRequest

    def _browser_extra(
        cid: str | None = None,
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if cid:
            extra["context_id"] = cid
        if dismiss_overlays:
            extra["dismiss_overlays"] = True
        if wait_for_selector:
            extra["wait_for_selector"] = wait_for_selector
        if wait_for_settled:
            extra["wait_for_settled"] = True
        return extra

    if use_browser:
        try:
            if context_id:
                # Use shared browser client so page persists for interaction
                from kaos_web.browser_tools import _get_browser_client

                client = await _get_browser_client()
                resp = await client.fetch(WebRequest(url=url, extra=_browser_extra(context_id)))
                return resp.html, resp.url
            else:
                from kaos_web.clients.browser import BrowserClient

                async with BrowserClient() as client:
                    resp = await client.fetch(WebRequest(url=url, extra=_browser_extra()))
                    return resp.html, resp.url
        except ImportError:
            pass  # Fall back to HTTP

    try:
        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url=url))
            return resp.html, resp.url
    except Exception as http_exc:
        # Auto-fallback to browser on 403/bot-blocking errors
        status = getattr(http_exc, "status_code", None)
        if status in (403, 406):
            try:
                from kaos_web.clients.browser import BrowserClient

                async with BrowserClient() as browser:
                    browser_resp = await browser.fetch(WebRequest(url=url, extra=_browser_extra()))
                    return browser_resp.html, browser_resp.url
            except ImportError:
                pass  # Playwright not installed
        raise


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
                ParameterSchema(
                    name="raw",
                    type="boolean",
                    description=(
                        "If true, skip readability content extraction and return the "
                        "full page body (including navigation, sidebars, footers). "
                        "Useful when you need the complete page structure."
                    ),
                    required=False,
                    default=False,
                ),
                _CONTENT_SCOPE_PARAM,
                *_BROWSER_PARAMS,
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        use_browser = inputs.get("use_browser", False)
        raw = inputs.get("raw", False)
        content_scope = inputs.get("content_scope", 0.5)

        if context is None or context.runtime is None:
            return ToolResult.create_error(
                "No runtime context available. "
                "FetchPage requires a KaosRuntime with artifact storage. "
                "Use 'kaos-web-get-markdown' for context-free extraction."
            )

        try:
            html, final_url = await _fetch_html(url, use_browser, **_browser_inputs(inputs))
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_content.artifacts import document_outline, document_to_summary, store_document
            from kaos_content.views import DocumentView
            from kaos_web.extract import html_to_document

            doc = html_to_document(
                html, url=final_url, extract_content=not raw, content_scope=content_scope
            )
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
                ParameterSchema(
                    name="raw",
                    type="boolean",
                    description="Skip readability and return full page text.",
                    required=False,
                    default=False,
                ),
                _CONTENT_SCOPE_PARAM,
                *_BROWSER_PARAMS,
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        raw = inputs.get("raw", False)
        content_scope = inputs.get("content_scope", 0.5)
        try:
            html, final_url = await _fetch_html(
                url, inputs.get("use_browser", False), **_browser_inputs(inputs)
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_web.extract import html_to_document

            doc = html_to_document(
                html, url=final_url, extract_content=not raw, content_scope=content_scope
            )

            # Store as artifact when runtime context is available
            if context is not None and context.runtime is not None:
                from kaos_content.artifacts import (
                    document_to_summary,
                    store_document,
                    unique_document_name,
                )

                manifest = await store_document(
                    doc,
                    context.runtime,
                    context,
                    name=unique_document_name(doc.metadata.title or final_url),
                    description=f"Text extraction from {final_url}",
                    metadata={"source_url": final_url, "block_count": len(doc.body)},
                )
                summary = document_to_summary(doc, max_length=500)
                return manifest.to_tool_result(
                    summary=summary,
                    structured_content={
                        "artifact_id": manifest.artifact_id,
                        "title": doc.metadata.title,
                        "url": final_url,
                        "body_uri": manifest.body_uri,
                    },
                )

            # Inline fallback (no runtime context)
            from kaos_content.serializers.text import serialize_text

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
                ParameterSchema(
                    name="raw",
                    type="boolean",
                    description="Skip readability and return full page markdown.",
                    required=False,
                    default=False,
                ),
                _CONTENT_SCOPE_PARAM,
                *_BROWSER_PARAMS,
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        raw = inputs.get("raw", False)
        content_scope = inputs.get("content_scope", 0.5)
        try:
            html, final_url = await _fetch_html(
                url, inputs.get("use_browser", False), **_browser_inputs(inputs)
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_web.extract import html_to_document

            doc = html_to_document(
                html, url=final_url, extract_content=not raw, content_scope=content_scope
            )

            # Store as artifact when runtime context is available
            if context is not None and context.runtime is not None:
                from kaos_content.artifacts import (
                    document_to_summary,
                    store_document,
                    unique_document_name,
                )

                manifest = await store_document(
                    doc,
                    context.runtime,
                    context,
                    name=unique_document_name(doc.metadata.title or final_url),
                    description=f"Markdown extraction from {final_url}",
                    metadata={"source_url": final_url, "block_count": len(doc.body)},
                )
                summary = document_to_summary(doc, max_length=500)
                return manifest.to_tool_result(
                    summary=summary,
                    structured_content={
                        "artifact_id": manifest.artifact_id,
                        "title": doc.metadata.title,
                        "url": final_url,
                        "body_uri": manifest.body_uri,
                        "markdown_uri": f"kaos://content/{manifest.artifact_id}/markdown",
                    },
                )

            # Inline fallback (no runtime context)
            from kaos_content.serializers.markdown import serialize_markdown

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
            meta_dict = meta.model_dump(exclude_none=True)
            title = meta_dict.get("title", final_url)
            summary = f"Metadata for {title}"
            return ToolResult.create_success(output=meta_dict, summary=summary)
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
                _CONTENT_SCOPE_PARAM,
                *_BROWSER_PARAMS,
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        query = inputs["query"]
        top_k = inputs.get("top_k", 10)
        level = inputs.get("level", "paragraph")
        content_scope = inputs.get("content_scope", 0.5)

        if not query.strip():
            return ToolResult.create_error(
                "Query must not be empty. Provide one or more search terms."
            )

        try:
            html, final_url = await _fetch_html(
                url, inputs.get("use_browser", False), **_browser_inputs(inputs)
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_content.search import search_document
            from kaos_web.extract import html_to_document

            doc = html_to_document(html, url=final_url, content_scope=content_scope)
            search_results = search_document(doc, query, top_k=top_k, level=level)

            result_data = {
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
            more = " (has more)" if search_results.has_more else ""
            summary = (
                f"Found {search_results.total_matches} matches for '{query}' on {final_url}{more}"
            )
            return ToolResult.create_success(output=result_data, summary=summary)
        except Exception as exc:
            return ToolResult.create_error(
                f"Search failed for {url}: {exc}. Content extraction or search may have failed."
            )


class GetPageLinksTool(KaosTool):
    """Extract and classify all links from a web page."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-get-links",
            display_name="Get Page Links",
            description=(
                "Extract all links from a URL with classification: navigation, "
                "content, pagination, social, download, anchor. Each link includes "
                "position (nav, header, footer, sidebar, body) and internal/external "
                "flag. Use to discover site structure, navigation menus, and outbound links."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(name="url", type="string", description="URL to fetch."),
                ParameterSchema(
                    name="link_type",
                    type="string",
                    description=(
                        "Filter by link type: 'navigation', 'content', 'pagination', "
                        "'social', 'download', 'anchor', 'all' (default 'all')."
                    ),
                    required=False,
                    default="all",
                    constraints={
                        "enum": [
                            "all",
                            "navigation",
                            "content",
                            "pagination",
                            "social",
                            "download",
                            "anchor",
                        ]
                    },
                ),
                ParameterSchema(
                    name="internal_only",
                    type="boolean",
                    description="If true, only return internal (same-domain) links.",
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs.get("url", "")
        if not url:
            return ToolResult.create_error(
                "URL is required. Provide the page URL to extract links from."
            )

        link_type_filter = inputs.get("link_type", "all")
        internal_only = inputs.get("internal_only", False)

        try:
            html, final_url = await _fetch_html(url)
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_web.extract.links import extract_links

            links = extract_links(html, url=final_url)

            if link_type_filter != "all":
                links = [lnk for lnk in links if lnk.link_type == link_type_filter]
            if internal_only:
                links = [lnk for lnk in links if lnk.is_internal]

            # Group by position for structured output
            by_position: dict[str, list[dict[str, Any]]] = {}
            for lnk in links:
                entry = {
                    "url": lnk.url,
                    "text": lnk.text,
                    "type": lnk.link_type,
                    "internal": lnk.is_internal,
                }
                if lnk.title:
                    entry["title"] = lnk.title
                by_position.setdefault(lnk.position, []).append(entry)

            result_data = {
                "url": final_url,
                "total": len(links),
                "by_position": by_position,
                "summary": {pos: len(items) for pos, items in by_position.items()},
            }
            summary = f"Found {len(links)} links on {final_url}"
            return ToolResult.create_success(output=result_data, summary=summary)
        except Exception as exc:
            return ToolResult.create_error(
                f"Link extraction failed for {url}: {exc}. The HTML may be malformed."
            )


class GetPageImagesTool(KaosTool):
    """Extract and classify all images from a web page."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-get-images",
            display_name="Get Page Images",
            description=(
                "Extract all images from a URL with classification: content, "
                "decorative, icon, social_card, tracking. Includes alt text, "
                "dimensions, srcset variants, and surrounding context. "
                "Use to audit images, find content images, or detect tracking pixels."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(name="url", type="string", description="URL to fetch."),
                ParameterSchema(
                    name="image_type",
                    type="string",
                    description=(
                        "Filter by type: 'content', 'decorative', 'icon', "
                        "'social_card', 'tracking', 'all' (default 'all')."
                    ),
                    required=False,
                    default="all",
                    constraints={
                        "enum": [
                            "all",
                            "content",
                            "decorative",
                            "icon",
                            "social_card",
                            "tracking",
                        ]
                    },
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs.get("url", "")
        if not url:
            return ToolResult.create_error(
                "URL is required. Provide the page URL to extract images from."
            )

        image_type_filter = inputs.get("image_type", "all")

        try:
            html, final_url = await _fetch_html(url)
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_web.extract.images import extract_images

            images = extract_images(html, url=final_url)

            if image_type_filter != "all":
                images = [img for img in images if img.image_type == image_type_filter]

            results = []
            for img in images:
                entry: dict[str, Any] = {
                    "src": img.src,
                    "type": img.image_type,
                }
                if img.alt:
                    entry["alt"] = img.alt
                if img.title:
                    entry["title"] = img.title
                if img.width:
                    entry["width"] = img.width
                if img.height:
                    entry["height"] = img.height
                if img.srcset:
                    entry["srcset"] = [
                        {"url": s.url, "descriptor": s.descriptor} for s in img.srcset
                    ]
                if img.context:
                    entry["context"] = img.context
                # Infer format from URL
                ext = img.src.rsplit(".", 1)[-1].split("?")[0].lower()
                if ext in ("jpg", "jpeg", "png", "gif", "webp", "svg", "avif"):
                    entry["format"] = ext
                results.append(entry)

            # Group by type for summary
            from collections import Counter

            type_counts = Counter(img.image_type for img in images)

            result_data = {
                "url": final_url,
                "total": len(results),
                "by_type": dict(type_counts),
                "images": results,
            }
            summary = f"Found {len(results)} images on {final_url}"
            return ToolResult.create_success(output=result_data, summary=summary)
        except Exception as exc:
            return ToolResult.create_error(
                f"Image extraction failed for {url}: {exc}. The HTML may be malformed."
            )


class GetPageTablesTool(KaosTool):
    """Extract HTML tables from a web page as structured tabular data."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-get-tables",
            display_name="Get Page Tables",
            description=(
                "Extract all HTML tables from a web page as structured tabular data. "
                "Returns each table with typed columns (text, integer, float, etc.) "
                "and data as TSV. Tables can be queried with SQL via kaos-tabular-register."
            ),
            category=ToolCategory.DATA,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(
                    name="url",
                    type="string",
                    description="URL of the web page to extract tables from.",
                ),
                ParameterSchema(
                    name="use_browser",
                    type="boolean",
                    description="Use headless browser for JS-rendered pages.",
                    required=False,
                    default=False,
                ),
                ParameterSchema(
                    name="format",
                    type="string",
                    description=(
                        "Output format: 'tsv' (default, token-efficient), 'markdown', or 'json'."
                    ),
                    required=False,
                    default="tsv",
                    constraints={"enum": ["tsv", "markdown", "json"]},
                ),
                *_BROWSER_PARAMS,
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        use_browser = inputs.get("use_browser", False)
        fmt = inputs.get("format", "tsv")

        try:
            html, final_url = await _fetch_html(
                url, use_browser=use_browser, **_browser_inputs(inputs)
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. Try use_browser=true for JS-rendered pages."
            )

        try:
            from kaos_content.bridges.content_to_tabular import extract_tables_as_tabular
            from kaos_web.extract.html_to_ast import html_to_document

            doc = html_to_document(html, url=final_url, extract_content=False)
            tabular_doc = extract_tables_as_tabular(doc)

            if not tabular_doc.tables:
                return ToolResult.create_text(
                    f"No tables found on {final_url}. "
                    "The page may use CSS grid/flex layouts instead of <table> elements. "
                    "Try kaos-web-get-markdown for the full page content."
                )

            from kaos_content.serializers.tabular import (
                serialize_json_records,
                serialize_markdown_table,
                serialize_tsv,
            )

            formatters = {
                "tsv": serialize_tsv,
                "markdown": serialize_markdown_table,
                "json": serialize_json_records,
            }
            formatter = formatters[fmt]

            parts = []
            for table in tabular_doc.tables:
                header = f"## {table.name} ({table.row_count} rows, {len(table.columns)} columns)"
                parts.append(header)
                parts.append(formatter(table))

            return ToolResult.create_success(
                output="\n".join(parts),
                summary=(
                    f"Found {len(tabular_doc.tables)} table(s) on {final_url}. "
                    + ", ".join(f"{t.name}: {t.row_count} rows" for t in tabular_doc.tables)
                ),
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Table extraction failed for {url}: {exc}. "
                "Try kaos-web-get-markdown for the full page content."
            )


class WebSearchTool(KaosTool):
    """Search the web and return results with titles, URLs, and snippets."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-search",
            display_name="Web Search",
            description=(
                "Search the web using SerpAPI, DuckDuckGo, Exa, or Brave. "
                "Returns titles, URLs, and snippets. Use the URLs with "
                "kaos-web-fetch-page or kaos-web-get-markdown to retrieve full content. "
                "Auto-detects backend from env vars (SERPAPI_API_KEY, EXA_API_KEY, BRAVE_API_KEY) "
                "or falls back to DuckDuckGo (free, no key needed)."
            ),
            category=ToolCategory.TEXT,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(
                    name="query",
                    type="string",
                    description="Search query.",
                ),
                ParameterSchema(
                    name="max_results",
                    type="integer",
                    description="Maximum number of results. Default: 10.",
                    required=False,
                    default=10,
                    constraints={"min": 1, "max": 20},
                ),
                ParameterSchema(
                    name="backend",
                    type="string",
                    description="Search backend. Default: auto-detect from env vars.",
                    required=False,
                    constraints={"enum": ["serpapi", "duckduckgo", "exa", "brave"]},
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        query = inputs["query"]
        max_results = inputs.get("max_results", 10)
        backend = inputs.get("backend")

        try:
            from kaos_web.search.backends import search_web

            results = await search_web(query, max_results=max_results, backend=backend)

            if not results:
                return ToolResult.create_text(f"No results found for: {query}")

            lines = [f"**{len(results)} results for:** {query}\n"]
            for r in results:
                lines.append(f"{r.position}. **{r.title}**")
                lines.append(f"   {r.url}")
                if r.snippet:
                    lines.append(f"   {r.snippet}")
                lines.append("")

            return ToolResult.create_success(
                output="\n".join(lines),
                summary=f"{len(results)} results for '{query}'",
            )
        except ValueError as exc:
            backend_hint = f" (backend={backend})" if backend else ""
            return ToolResult.create_error(
                f"Web search failed{backend_hint}: {exc}. "
                "Verify your search API key is set (KAOS_WEB_SERPAPI_API_KEY, "
                "KAOS_WEB_EXA_API_KEY, or KAOS_WEB_BRAVE_API_KEY). "
                "Try a different backend with the 'backend' parameter "
                "(serpapi, exa, brave, duckduckgo)."
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Web search failed: {exc}. Check your BRAVE_API_KEY or SEARXNG_URL configuration."
            )


def register_web_tools(runtime: KaosRuntime) -> int:
    """Register all web tools with the runtime. Returns count."""
    from kaos_web.settings import KaosWebSettings

    runtime.module_settings["web"] = KaosWebSettings()

    tools: list[KaosTool] = [
        FetchPageTool(),
        GetPageTextTool(),
        GetPageMarkdownTool(),
        GetPageMetadataTool(),
        SearchPageTool(),
        GetPageLinksTool(),
        GetPageImagesTool(),
        GetPageTablesTool(),
        WebSearchTool(),
    ]
    for tool in tools:
        runtime.tools.register_tool(tool)
    return len(tools)
