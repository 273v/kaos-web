"""MCP tool definitions for web content extraction.

KaosTool implementations registered with KaosRuntime and exposed via kaos-mcp.
Each tool fetches web content, extracts it into ContentDocument AST, and returns
summary + resource links following the artifact tiering model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kaos_core import KaosContext, KaosRuntime, KaosTool, ToolMetadata, ToolResult

if TYPE_CHECKING:
    from kaos_content.model.document import ContentDocument
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


# Anti-bot "you got a 200 but it's the challenge page" fingerprints.
# Live-tested 2026-05-23 against federalregister.gov and ecfr.gov:
# both return 200 OK with a 10 KB HTML payload titled "Request Access"
# or "Just a moment..." when an httpx UA hits them. We treat any
# match here as a soft failure and retry on the browser path.
_BOT_CHALLENGE_FINGERPRINTS: tuple[str, ...] = (
    # Federal Register / eCFR shared anti-bot interstitial
    "request access",
    # Cloudflare interactive challenge
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    # datadome (used by WSJ, Reuters, others)
    'src="https://geo.captcha-delivery.com',
    'class="ctp-c"',
    # Akamai bot manager
    "ak_bmsc",
    "/_Incapsula_",
    # PerimeterX / HUMAN
    "px-captcha",
    "perimeterx",
)


def _looks_like_bot_challenge(html: str | None) -> bool:
    """Return True if ``html`` looks like an anti-bot interstitial.

    Used after a 200-status httpx fetch to detect challenge pages
    that lie about success. When True the caller should fall back
    to the Playwright path (which carries a full browser
    fingerprint and passes most of these tiers).
    """
    if not html:
        return False
    head = html[:8192].lower()
    return any(fp in head for fp in _BOT_CHALLENGE_FINGERPRINTS)


async def _fetch_html(
    url: str,
    use_browser: bool | None = None,
    context_id: str | None = None,
    *,
    dismiss_overlays: bool = True,
    wait_for_selector: str | None = None,
    wait_for_settled: bool = True,
) -> tuple[str, str]:
    """Fetch HTML from a URL. Returns (html, final_url).

    **Default behaviour: Playwright-first.** When Playwright is
    available (``kaos-web[browser]``) the realistic-browser path —
    rotated desktop UA, 1365x768 viewport, en-US locale,
    America/New_York timezone, full sec-ch-ua / sec-fetch / Accept
    header set, configured in :class:`BrowserClient` — is used by
    default. It passes Cloudflare, SEC.gov / EDGAR, FederalRegister,
    eCFR, Investopedia, and other anti-bot stacks; the
    kelvin-legal-intelligence collector proves this pattern in
    production. Live-tested 2026-05-23: httpx returns 200 OK with a
    "Request Access" HTML body from federalregister.gov / ecfr.gov,
    which silently looked like success to the agent and triggered
    fabrication. Playwright pulls the real page cleanly.

    The bare httpx path is only taken when the caller **explicitly**
    passes ``use_browser=False`` (e.g. an internal connector that
    knows the endpoint is a JSON API and Playwright would be pure
    overhead). When Playwright is not installed we degrade silently
    to httpx and log a warning — the deployment stays usable.

    Failure fallbacks (both directions):

    * ``httpx 403/406`` → automatic browser retry.
    * ``httpx 200 + bot-challenge body fingerprint`` (Cloudflare,
      datadome, Akamai, PerimeterX, the FR "Request Access" page) →
      automatic browser retry. This is the fix for the silent
      "200-but-actually-blocked" failure observed 2026-05-23.

    Args:
        url: URL to fetch.
        use_browser: ``True`` = force browser; ``False`` = force
            httpx; ``None`` (default, and the value passed by every
            MCP tool unless the caller overrides) = browser if
            Playwright is importable, else httpx.
        context_id: Named browser context for persistent sessions
            (cookies / storage across requests). Implies use_browser.
        dismiss_overlays: Auto-dismiss known cookie consent banners
            (OneTrust, CookieBot, etc.) before extraction. Browser
            path only.
        wait_for_selector: CSS selector to wait for before extracting
            content. Browser path only.
        wait_for_settled: Wait for JS-rendered content to appear.
            Zero penalty on already-rendered pages. Skipped when
            wait_for_selector is set. Browser path only.
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

    # Resolve effective routing. context_id implies use_browser=True
    # because named contexts only make sense with a real browser page.
    effective_use_browser: bool
    if context_id is not None:
        effective_use_browser = True
    elif use_browser is None:
        # Auto-detect: browser if Playwright is importable, else httpx.
        try:
            import playwright  # noqa: F401 — probe only

            effective_use_browser = True
        except ImportError:
            effective_use_browser = False
    else:
        effective_use_browser = use_browser

    if effective_use_browser:
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
            # Playwright not installed at runtime. Fall through to httpx
            # so the deployment stays usable on a minimal install — the
            # caller's anti-bot coverage degrades, but they get *some*
            # answer rather than an exception.
            pass

    try:
        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url=url))
        # httpx 200 but body is a bot-challenge interstitial — the
        # silent failure mode that fooled the agent into fabricating
        # FR climate-disclosure results on 2026-05-23. Treat as a
        # soft failure and retry on the browser path.
        if _looks_like_bot_challenge(resp.html):
            try:
                from kaos_web.clients.browser import BrowserClient

                async with BrowserClient() as browser:
                    browser_resp = await browser.fetch(WebRequest(url=url, extra=_browser_extra()))
                    return browser_resp.html, browser_resp.url
            except ImportError:
                pass  # Playwright not installed — return the challenge HTML
        return resp.html, resp.url
    except Exception as http_exc:
        # 403/406 on the httpx path = bot-detection. Try the browser
        # path as a fallback even when the caller passed
        # use_browser=False — anti-bot defense is more important than
        # honoring the legacy default. Silent skip if Playwright isn't
        # installed; the original 403 propagates.
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


# Internal artifact/content handles that the page tools emit (e.g.
# FetchPageTool returns ``body_uri = kaos://artifacts/<id>/body`` and
# ``sections_uri = kaos://content/<id>/sections``). When an agent chains
# a page tool onto one of these handles ("fetch this page, then search
# within it"), the handle must NOT be routed through the web fetcher: the
# security gate correctly rejects the non-(http|https) ``kaos://`` scheme,
# which left the agent unable to read content it had just fetched. Instead
# we resolve the already-stored ContentDocument from the artifact store.
_ARTIFACT_HANDLE_PREFIXES = ("kaos://artifacts/", "kaos://content/")


def _artifact_id_from_handle(url: str) -> str | None:
    """Return the artifact id from a ``kaos://artifacts/<id>/...`` or
    ``kaos://content/<id>/...`` handle, or ``None`` when ``url`` is an
    ordinary (http/https/…) URL that should be fetched normally."""
    for prefix in _ARTIFACT_HANDLE_PREFIXES:
        if url.startswith(prefix):
            artifact_id = url[len(prefix) :].split("/", 1)[0].strip()
            return artifact_id or None
    return None


async def _maybe_load_handle_document(
    url: str, context: KaosContext | None
) -> ContentDocument | None:
    """Resolve a ``kaos://`` artifact/content handle to its stored
    ContentDocument, composing with :class:`FetchPageTool`'s ``body_uri``
    output. Returns ``None`` for ordinary URLs so the caller fetches them
    as before.

    Raises a clear error when a handle is passed but no runtime/artifact
    store is available, so the failure names the cause instead of a 404.
    """
    artifact_id = _artifact_id_from_handle(url)
    if artifact_id is None:
        return None
    if context is None or context.runtime is None:
        raise ValueError(
            f"Reading {url} requires a KaosRuntime with artifact storage — this "
            "handle was produced by an earlier page fetch in the same session."
        )
    from kaos_content.artifacts import load_document

    # ``max_bytes=None``: the page already passed the per-fetch body cap
    # when it was stored, so the artifact is bounded by that.
    return await load_document(artifact_id, context.runtime, max_bytes=None)


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
                    description=(
                        "Fetcher selection. DEFAULT (unset): Playwright with "
                        "full browser fingerprint — passes Cloudflare, "
                        "SEC.gov, FederalRegister, eCFR, Investopedia, and "
                        "most anti-bot tiers. Set false to force the bare "
                        "httpx path (faster, lower memory, but blocked by "
                        "most major news/regulator sites — only use for "
                        "JSON APIs and known-clean hosts). Set true to "
                        "force browser even when a httpx fast-path exists."
                    ),
                    required=False,
                    default=None,
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
        use_browser = inputs.get("use_browser")
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
                    description=(
                        "Fetcher selection. DEFAULT (unset): Playwright — "
                        "passes Cloudflare, SEC.gov, FR, eCFR, and other "
                        "anti-bot tiers. Set false to force httpx (faster, "
                        "but silently blocked on most major sites)."
                    ),
                    required=False,
                    default=None,
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
            handle_doc = await _maybe_load_handle_document(url, context)
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to load {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )
        if handle_doc is not None:
            # ``url`` is a kaos:// handle from an earlier fetch in this
            # session — return the stored document's text directly (it is
            # already an artifact; no re-fetch, no re-store).
            from kaos_content.serializers.text import serialize_text

            return ToolResult.create_success(serialize_text(handle_doc))

        try:
            html, final_url = await _fetch_html(
                url, inputs.get("use_browser"), **_browser_inputs(inputs)
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
                    description=(
                        "Fetcher selection. DEFAULT (unset): Playwright — "
                        "passes Cloudflare, SEC.gov, FR, eCFR, and other "
                        "anti-bot tiers. Set false to force httpx (faster, "
                        "but silently blocked on most major sites)."
                    ),
                    required=False,
                    default=None,
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
            handle_doc = await _maybe_load_handle_document(url, context)
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to load {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )
        if handle_doc is not None:
            # ``url`` is a kaos:// handle from an earlier fetch in this
            # session — return the stored document's markdown directly (it
            # is already an artifact; no re-fetch, no re-store).
            from kaos_content.serializers.markdown import serialize_markdown

            return ToolResult.create_success(serialize_markdown(handle_doc))

        try:
            html, final_url = await _fetch_html(
                url, inputs.get("use_browser"), **_browser_inputs(inputs)
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
                    description=(
                        "Fetcher selection. DEFAULT (unset): Playwright — "
                        "passes Cloudflare, SEC.gov, FR, eCFR, and other "
                        "anti-bot tiers. Set false to force httpx (faster, "
                        "but silently blocked on most major sites)."
                    ),
                    required=False,
                    default=None,
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
            handle_doc = await _maybe_load_handle_document(url, context)
            if handle_doc is not None:
                # ``url`` is a kaos:// artifact/content handle from an
                # earlier fetch in this session — search the stored
                # document directly instead of re-fetching it through the
                # web gate (which blocks the non-http ``kaos://`` scheme).
                doc, source = handle_doc, url
            else:
                html, final_url = await _fetch_html(
                    url, inputs.get("use_browser"), **_browser_inputs(inputs)
                )
                from kaos_web.extract import html_to_document

                doc = html_to_document(html, url=final_url, content_scope=content_scope)
                source = final_url
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. "
                "Verify the URL is correct and the site is accessible."
            )

        try:
            from kaos_content.search import search_document

            search_results = search_document(doc, query, top_k=top_k, level=level)

            result_data = {
                "url": source,
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
                f"Found {search_results.total_matches} matches for '{query}' on {source}{more}"
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
                    description=(
                        "Fetcher selection. DEFAULT (unset): Playwright — "
                        "needed for JS-rendered tables on most modern news "
                        "and regulator sites. Set false to force httpx."
                    ),
                    required=False,
                    default=None,
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
        use_browser = inputs.get("use_browser")
        fmt = inputs.get("format", "tsv")

        try:
            from kaos_content.bridges.content_to_tabular import extract_tables_as_tabular

            handle_doc = await _maybe_load_handle_document(url, context)
            if handle_doc is not None:
                # ``url`` is a kaos:// handle from an earlier fetch in this
                # session — pull tables from the stored document instead of
                # re-fetching it through the web gate.
                doc, source = handle_doc, url
            else:
                html, final_url = await _fetch_html(
                    url, use_browser=use_browser, **_browser_inputs(inputs)
                )
                from kaos_web.extract.html_to_ast import html_to_document

                doc = html_to_document(html, url=final_url, extract_content=False)
                source = final_url
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch {url}: {exc}. Try use_browser=true for JS-rendered pages."
            )

        try:
            tabular_doc = extract_tables_as_tabular(doc)

            if not tabular_doc.tables:
                return ToolResult.create_text(
                    f"No tables found on {source}. "
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
                    f"Found {len(tabular_doc.tables)} table(s) on {source}. "
                    + ", ".join(f"{t.name}: {t.row_count} rows" for t in tabular_doc.tables)
                ),
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Table extraction failed for {url}: {exc}. "
                "Try kaos-web-get-markdown for the full page content."
            )


class FetchFeedTool(KaosTool):
    """Fetch an RSS 2.0 or Atom 1.0 feed and return structured items.

    Many publishers (SEC press releases, Federal Register, GitHub releases,
    agency news) expose their freshest content via RSS/Atom long before the
    sitemap.xml gets refreshed. Without this tool, the agent has to fetch
    the raw XML via ``kaos-web-fetch-page`` and parse it in its head —
    fragile and expensive. This tool reads the XML and returns parsed
    items with parsed ``pub_date``, ``title``, ``link``, ``author``, and
    ``description``.
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-fetch-feed",
            display_name="Fetch RSS/Atom Feed",
            description=(
                "Fetch an RSS 2.0 or Atom 1.0 feed URL and return its items "
                "as structured data (title, link, pub_date, description, "
                "author). Auto-detects format. ALWAYS PREFER THIS TOOL over "
                "kaos-web-fetch-page when the user asks 'what's the most "
                "recent X from publisher Y' and Y publishes a feed — RSS "
                "is faster, smaller, and dated. Returns items in publisher "
                "order (typically newest first). Common feed URLs: "
                "https://www.sec.gov/news/pressreleases.rss, "
                "https://github.com/{org}/{repo}/releases.atom."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_WEB_ANNOTATIONS,
            input_schema=[
                ParameterSchema(
                    name="url",
                    type="string",
                    description="Feed URL (RSS 2.0 or Atom 1.0).",
                ),
                ParameterSchema(
                    name="limit",
                    type="integer",
                    description=(
                        "Maximum items to return (most recent first). Default 20, "
                        "max 100. Drop to 5-10 for 'what's new' queries to save "
                        "tokens; raise for archival sweeps."
                    ),
                    required=False,
                    default=20,
                    constraints={"minimum": 1, "maximum": 100},
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        from kaos_web.clients.config import HttpClientConfig
        from kaos_web.clients.http import HttpClient
        from kaos_web.extract import parse_feed
        from kaos_web.models import WebRequest

        url = inputs["url"]
        limit = min(int(inputs.get("limit", 20) or 20), 100)

        try:
            cfg = HttpClientConfig(randomize_user_agent=True)
            async with HttpClient(cfg) as client:
                resp = await client.fetch(WebRequest(url=url))
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to fetch feed {url}: {exc}. "
                "Verify the URL is a public RSS or Atom feed. "
                "Alternative: kaos-web-fetch-page for non-feed URLs."
            )

        feed = parse_feed(resp.html or "")
        if feed.format == "unknown":
            return ToolResult.create_error(
                f"{url} did not parse as RSS 2.0 or Atom 1.0 (got "
                f"content_type={resp.content_type!r}, {len(resp.html or '')} bytes). "
                "Verify the URL is a feed endpoint (typically ending in .rss, "
                ".atom, /feed, or /rss). Alternative: kaos-web-fetch-page if "
                "the URL is an HTML page, then look for <link rel='alternate' "
                "type='application/rss+xml'>."
            )

        items = feed.items[:limit]
        if not items:
            return ToolResult.create_success(
                output=(
                    f"Feed '{feed.title or url}' ({feed.format}) returned 0 items. "
                    "The publisher may have cleared the feed or the URL points "
                    "to a feed index. Try a sub-feed (e.g. category-specific) "
                    "or kaos-web-fetch-page on the human-readable index."
                ),
                summary=f"empty feed (format={feed.format})",
                structuredContent={
                    "format": feed.format,
                    "title": feed.title,
                    "link": feed.link,
                    "items": [],
                },
            )

        lines = [
            f"# {feed.title or url}",
            f"format={feed.format} items_returned={len(items)} (of {len(feed.items)} total)",
            "",
        ]
        for idx, item in enumerate(items, start=1):
            date_str = item.pub_date.isoformat() if item.pub_date else "no-date"
            lines.append(f"{idx}. **{item.title}** ({date_str})")
            lines.append(f"   {item.link}")
            if item.description:
                snippet = item.description[:200].strip()
                lines.append(f"   {snippet}")
            lines.append("")

        return ToolResult.create_success(
            output="\n".join(lines),
            summary=(
                f"{len(items)} items from {feed.title or url} "
                f"(format={feed.format}, top: {items[0].title[:60]!r})"
            ),
            structuredContent={
                "format": feed.format,
                "title": feed.title,
                "link": feed.link,
                "description": feed.description,
                "items": [
                    {
                        "title": it.title,
                        "link": it.link,
                        "pub_date": it.pub_date.isoformat() if it.pub_date else None,
                        "description": it.description,
                        "author": it.author,
                        "categories": list(it.categories),
                    }
                    for it in items
                ],
            },
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
                    description=(
                        "Optional search backend. Omit this parameter to "
                        "auto-detect (SerpAPI → Exa → Brave → DuckDuckGo, "
                        "based on configured env keys). Do NOT pass the "
                        "literal string 'auto' — use one of the enum "
                        "values below to force a specific backend. The "
                        "string 'auto' is also accepted as a synonym for "
                        "omission (0.1.1, #545) but the canonical pattern "
                        "is to omit the parameter."
                    ),
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
    """Register the 10 HTTP fetch + feed + search tools with the runtime.

    Pins the SessionToolSet ``web`` group entry point for kaos-web.
    Covers the "fetch a URL and extract text / markdown / metadata /
    links / images / tables / RSS-Atom feed, or search the web"
    surface — every tool that performs a single bounded HTTP GET via
    ``httpx`` (no JS-rendering, no DNS / WHOIS / TLS introspection,
    no multi-URL crawling).

    The full 46-tool kaos-web surface is split across 4 register
    functions: see :func:`register_web_all_tools` for the union, or
    call :func:`register_browser_tools` / :func:`register_crawl_tools`
    / :func:`register_domain_tools` (the ``netinfra`` group) directly
    for granular opt-in.
    """
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
        FetchFeedTool(),
        WebSearchTool(),
    ]
    for tool in tools:
        runtime.tools.register_tool(tool)
    return len(tools)


def register_web_all_tools(runtime: KaosRuntime) -> int:
    """Register every kaos-web MCP tool — 45 total across 4 groups.

    Convenience union for callers that want the full kaos-web
    surface registered in one call. Composes:

    - :func:`register_web_tools` — 9 tools, SessionToolSet ``web``
      group (HTTP fetch + search)
    - :func:`register_browser_tools` — 19 tools, SessionToolSet
      ``browser`` group (Playwright; requires ``[browser]`` extra
      at runtime, not at registration)
    - :func:`register_domain_tools` — 14 tools, SessionToolSet
      ``netinfra`` group (DNS / WHOIS / TLS / TCP banner / UDP
      probe / HTTP header / org-extract; ``[dns]`` extra at
      runtime)
    - :func:`register_crawl_tools` — 3 tools, SessionToolSet
      ``web`` group (URL discovery, batch fetch, full-site crawl)

    Registration itself is lazy with respect to ``[browser]`` and
    ``[dns]`` — tool *construction* doesn't import Playwright or
    dnspython; those imports happen inside ``execute()``. Calling
    this from a process that lacks those extras is safe; the
    tool just errors at *invocation* with an actionable message.
    """
    from kaos_web.browser_tools import register_browser_tools
    from kaos_web.crawl_tools import register_crawl_tools
    from kaos_web.domain_tools import register_domain_tools

    count = register_web_tools(runtime)
    count += register_browser_tools(runtime)
    count += register_domain_tools(runtime)
    count += register_crawl_tools(runtime)
    return count
