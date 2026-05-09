"""MCP tool definitions for browser interaction.

Phase 5 tools that enable agents to interact with web pages: click elements,
fill forms, take screenshots, evaluate JavaScript, and get accessibility snapshots.

All interaction tools require a prior fetch with ``context_id`` to establish
a persistent browser page. The context_id is then passed to interaction tools
for multi-step workflows (navigate → click → fill → screenshot → extract).
"""

from __future__ import annotations

import base64
from typing import Any

from kaos_core import KaosContext, KaosRuntime, KaosTool, ToolMetadata, ToolResult
from kaos_core.logging import get_logger
from kaos_core.types.annotations import ToolAnnotations
from kaos_core.types.enums import ToolCapability, ToolCategory
from kaos_core.types.parameters import ParameterSchema

logger = get_logger(__name__)

_MODULE = "kaos-web"
_VERSION = "0.1.0"

# Annotations for read-only browser tools (screenshot, snapshot).
_BROWSER_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

# Annotations for state-changing browser tools (click, fill, evaluate).
_BROWSER_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

# ── Shared browser client instance ──
# Managed as module-level singleton so all tools share contexts/pages.
_browser_client: Any = None
_browser_config_override: Any = None  # Set via configure_browser() before first use


def _build_browser_config() -> Any:
    """Build BrowserClientConfig from typed settings and auto-detection."""
    if _browser_config_override is not None:
        return _browser_config_override

    from kaos_web.settings import KaosWebSettings

    return KaosWebSettings().to_browser_config()


def configure_browser(config: Any) -> None:
    """Set a custom BrowserClientConfig for the shared browser client.

    Must be called before any browser tool is executed. If the client
    is already running, it will be shut down and recreated on next use.

    Args:
        config: A ``BrowserClientConfig`` instance.
    """
    global _browser_config_override, _browser_client
    _browser_config_override = config
    # Force recreation on next use
    if _browser_client is not None:
        import asyncio

        asyncio.get_event_loop().create_task(_shutdown_browser_client())


async def _get_browser_client() -> Any:
    """Get or create the shared BrowserClient instance."""
    global _browser_client
    if _browser_client is None:
        from kaos_web.clients.browser import BrowserClient

        config = _build_browser_config()
        _browser_client = BrowserClient(config)
    return _browser_client


async def _shutdown_browser_client() -> None:
    """Shut down the shared browser client (for cleanup)."""
    global _browser_client
    if _browser_client is not None:
        await _browser_client.close()
        _browser_client = None


def _context_id_param() -> ParameterSchema:
    """Shared context_id parameter used by all interaction tools."""
    return ParameterSchema(
        name="context_id",
        type="string",
        description=(
            "Named browser context ID. Must match a context_id used in a prior "
            "'kaos-web-fetch-page' or 'kaos-web-get-text' call with use_browser=true."
        ),
    )


class BrowserNavigateTool(KaosTool):
    """Navigate to a URL in a browser context, keeping the page alive for interaction."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-navigate",
            display_name="Browser Navigate",
            description=(
                "Navigate to a URL in a persistent browser context. Creates a named "
                "context with an active page for subsequent interaction tools (click, fill, "
                "screenshot, evaluate, snapshot). This is the entry point for interactive "
                "browser workflows. If request logging was previously enabled for this "
                "context, logging hooks are automatically re-attached to the new page "
                "and logs accumulate across navigations."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                ParameterSchema(name="url", type="string", description="URL to navigate to."),
                ParameterSchema(
                    name="context_id",
                    type="string",
                    description=(
                        "Named context ID for this session. Reuse across tools for "
                        "multi-step workflows. If the context already exists, replaces the page."
                    ),
                    required=False,
                    default="default",
                ),
                ParameterSchema(
                    name="wait_until",
                    type="string",
                    description="When to consider navigation done.",
                    required=False,
                    default="load",
                    constraints={"enum": ["load", "domcontentloaded", "networkidle", "commit"]},
                ),
                ParameterSchema(
                    name="wait_for_selector",
                    type="string",
                    description="CSS selector to wait for after navigation.",
                    required=False,
                ),
                ParameterSchema(
                    name="dismiss_overlays",
                    type="boolean",
                    description=(
                        "Auto-dismiss known cookie consent banners (OneTrust, CookieBot, "
                        "TrustArc, etc.) after navigation. Useful when overlays block "
                        "page interaction."
                    ),
                    required=False,
                    default=False,
                ),
                ParameterSchema(
                    name="wait_for_settled",
                    type="boolean",
                    description=(
                        "Wait for JS content to render before returning. "
                        "Zero penalty on already-rendered pages."
                    ),
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        url = inputs["url"]
        context_id = inputs.get("context_id", "default")
        wait_until = inputs.get("wait_until", "load")
        wait_for_selector = inputs.get("wait_for_selector")
        dismiss_overlays = inputs.get("dismiss_overlays", False)
        wait_for_settled = inputs.get("wait_for_settled", False)

        try:
            from kaos_web.models import WebRequest

            client = await _get_browser_client()
            extra: dict[str, Any] = {"context_id": context_id, "wait_until": wait_until}
            if wait_for_selector:
                extra["wait_for_selector"] = wait_for_selector
            if dismiss_overlays:
                extra["dismiss_overlays"] = True
            if wait_for_settled:
                extra["wait_for_settled"] = True

            resp = await client.fetch(WebRequest(url=url, extra=extra))

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "url": resp.url,
                    "title": resp.title,
                    "status_code": resp.status_code,
                    "message": (
                        f"Navigated to {resp.url}. Page is now active in context "
                        f"'{context_id}'. Use this context_id with browser interaction tools."
                    ),
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Navigation failed for {url}: {exc}. "
                "Verify the URL is correct and the site is accessible. "
                "For JS-heavy pages, try wait_until='networkidle'."
            )


class ClickElementTool(KaosTool):
    """Click an element on a browser page by CSS selector."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-click",
            display_name="Click Element",
            description=(
                "Click an element on the active browser page by CSS selector. "
                "Requires a prior 'kaos-web-browser-navigate' call to establish a page. "
                "Use 'kaos-web-browser-snapshot' first to find clickable elements."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.TRANSFORM,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="selector",
                    type="string",
                    description="CSS selector for the element to click (e.g., 'button#submit').",
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        selector = inputs["selector"]

        try:
            client = await _get_browser_client()
            await client.click(context_id, selector)
            current_url = await client.get_url(context_id)

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "selector": selector,
                    "url": current_url,
                    "message": f"Clicked '{selector}'. Page URL is now {current_url}.",
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Click failed for selector '{selector}' in context '{context_id}': {exc}. "
                "Verify the selector matches a visible, clickable element. "
                "Use 'kaos-web-browser-snapshot' to see available elements."
            )


class FillInputTool(KaosTool):
    """Fill an input field on a browser page."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-fill",
            display_name="Fill Input",
            description=(
                "Fill an input field with text. Clears existing content first. "
                "For inputs with autocomplete/JS listeners that need keystroke events, "
                "use 'kaos-web-browser-type' instead."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.TRANSFORM,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="selector",
                    type="string",
                    description="CSS selector for the input element.",
                ),
                ParameterSchema(
                    name="value",
                    type="string",
                    description="Text value to fill into the input.",
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        selector = inputs["selector"]
        value = inputs["value"]

        try:
            client = await _get_browser_client()
            await client.fill(context_id, selector, value)

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "selector": selector,
                    "value": value,
                    "message": f"Filled '{selector}' with '{value}'.",
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Fill failed for selector '{selector}' in context '{context_id}': {exc}. "
                "Verify the selector matches an editable input, textarea, "
                "or contenteditable element. "
                "Use 'kaos-web-browser-snapshot' to see available inputs."
            )


class TypeTextTool(KaosTool):
    """Type text character-by-character, simulating real keystrokes."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-type",
            display_name="Type Text",
            description=(
                "Type text character-by-character, firing keydown/keypress/keyup events. "
                "Use this instead of fill for inputs with autocomplete, search suggestions, "
                "or other JavaScript listeners that respond to individual keystrokes."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.TRANSFORM,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="selector",
                    type="string",
                    description="CSS selector for the input element.",
                ),
                ParameterSchema(
                    name="text",
                    type="string",
                    description="Text to type character-by-character.",
                ),
                ParameterSchema(
                    name="delay",
                    type="integer",
                    description="Delay between keystrokes in milliseconds.",
                    required=False,
                    default=0,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        selector = inputs["selector"]
        text = inputs["text"]
        delay = inputs.get("delay", 0)

        try:
            client = await _get_browser_client()
            await client.type_text(context_id, selector, text, delay=delay)

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "selector": selector,
                    "text": text,
                    "message": f"Typed '{text}' into '{selector}'.",
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Type failed for selector '{selector}' in context '{context_id}': {exc}. "
                "Verify the selector matches a focusable input element."
            )


class PressKeyTool(KaosTool):
    """Press a keyboard key on an element."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-press",
            display_name="Press Key",
            description=(
                "Press a keyboard key on a focused element. Common keys: Enter, Tab, "
                "Escape, ArrowDown, ArrowUp, Backspace, Delete. "
                "Supports modifiers: 'Control+a', 'Shift+Enter', 'Meta+c'."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.TRANSFORM,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="selector",
                    type="string",
                    description="CSS selector for the element to receive the key press.",
                ),
                ParameterSchema(
                    name="key",
                    type="string",
                    description="Key to press (e.g., 'Enter', 'Tab', 'Escape', 'Control+a').",
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        selector = inputs["selector"]
        key = inputs["key"]

        try:
            client = await _get_browser_client()
            await client.press_key(context_id, selector, key)

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "selector": selector,
                    "key": key,
                    "message": f"Pressed '{key}' on '{selector}'.",
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Key press failed for '{key}' on '{selector}' in context '{context_id}': {exc}. "
                "Verify the selector matches a focusable element."
            )


class SelectOptionTool(KaosTool):
    """Select an option from a dropdown (<select>) element."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-select",
            display_name="Select Option",
            description=(
                "Select an option from a <select> dropdown by value. "
                "Use 'kaos-web-browser-snapshot' to find available options."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.TRANSFORM,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="selector",
                    type="string",
                    description="CSS selector for the <select> element.",
                ),
                ParameterSchema(
                    name="value",
                    type="string",
                    description="Option value to select.",
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        selector = inputs["selector"]
        value = inputs["value"]

        try:
            client = await _get_browser_client()
            selected = await client.select_option(context_id, selector, value)

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "selector": selector,
                    "value": value,
                    "selected": selected,
                    "message": f"Selected '{value}' in '{selector}'.",
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Select failed for '{value}' on '{selector}' in context '{context_id}': {exc}. "
                "Verify the selector matches a <select> element with the given value."
            )


class ScreenshotTool(KaosTool):
    """Take a screenshot of a browser page."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-screenshot",
            display_name="Take Screenshot",
            description=(
                "Take a screenshot of the active page in a browser context. "
                "Can also take a one-shot screenshot of a URL without needing a context. "
                "Returns the image as base64-encoded PNG or JPEG."
            ),
            category=ToolCategory.MEDIA,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_READ_ANNOTATIONS,
            input_schema=[
                ParameterSchema(
                    name="context_id",
                    type="string",
                    description=(
                        "Named browser context with an active page. "
                        "Omit and provide 'url' for a one-shot screenshot."
                    ),
                    required=False,
                ),
                ParameterSchema(
                    name="url",
                    type="string",
                    description=(
                        "URL to screenshot (one-shot, no context needed). "
                        "Ignored if context_id is provided."
                    ),
                    required=False,
                ),
                ParameterSchema(
                    name="full_page",
                    type="boolean",
                    description="Capture full scrollable page (default true).",
                    required=False,
                    default=True,
                ),
                ParameterSchema(
                    name="format",
                    type="string",
                    description="Image format.",
                    required=False,
                    default="png",
                    constraints={"enum": ["png", "jpeg"]},
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs.get("context_id")
        url = inputs.get("url")
        full_page = inputs.get("full_page", True)
        fmt = inputs.get("format", "png")

        if not context_id and not url:
            return ToolResult.create_error(
                "Provide either 'context_id' (for active page) or 'url' (for one-shot). "
                "Use 'kaos-web-browser-navigate' first to create a context, "
                "or provide a URL directly."
            )

        try:
            from kaos_core.types.content import ImageContent

            client = await _get_browser_client()

            if context_id:
                img_bytes = await client.screenshot_context(
                    context_id, full_page=full_page, format=fmt
                )
                source = f"context:{context_id}"
            else:
                img_bytes = await client.screenshot(url, full_page=full_page, format=fmt)
                source = url

            mime = f"image/{fmt}"

            # Store as artifact when runtime context is available
            if context is not None and context.runtime is not None:
                from kaos_content.artifacts import unique_document_name
                from kaos_core.types.enums import ArtifactRole

                name = unique_document_name(source or "screenshot")
                vfs_path = f"images/{name}.{fmt}"
                ctx_path = context.get_vfs_path(vfs_path)
                await ctx_path.write_bytes(img_bytes)
                manifest = await context.runtime.artifacts.create_from_path(
                    vfs_path,
                    context_id=context.session_id,
                    session_id=context.session_id,
                    name=name,
                    description=f"Screenshot of {source}",
                    mime_type=mime,
                    role=ArtifactRole.BODY,
                    provenance={
                        "source": source,
                        "tool": "kaos-web-browser-screenshot",
                    },
                    metadata={
                        "format": fmt,
                        "full_page": full_page,
                        "size_bytes": len(img_bytes),
                    },
                )
                return manifest.to_tool_result(
                    summary=f"Screenshot of {source} ({len(img_bytes)} bytes, {fmt})",
                    structured_content={
                        "artifact_id": manifest.artifact_id,
                        "body_uri": manifest.body_uri,
                        "format": fmt,
                        "size_bytes": len(img_bytes),
                    },
                )

            # Inline fallback (no runtime context)
            b64 = base64.b64encode(img_bytes).decode("ascii")

            return ToolResult(
                content=[ImageContent(data=b64, mimeType=mime)],
                _meta={
                    "source": source,
                    "format": fmt,
                    "full_page": full_page,
                    "size_bytes": len(img_bytes),
                },
            )
        except Exception as exc:
            target = context_id or url
            return ToolResult.create_error(
                f"Screenshot failed for {target}: {exc}. "
                "Ensure the page is loaded and the browser is available. "
                "Install playwright with: pip install kaos-web[browser]"
            )


class EvaluateJSTool(KaosTool):
    """Execute JavaScript on a browser page and return the result."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-evaluate",
            display_name="Evaluate JavaScript",
            description=(
                "Execute a JavaScript expression on the active page and return the result. "
                "Can also evaluate on a fresh page by providing a URL. "
                "The expression is evaluated in the page context with full DOM access."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                ParameterSchema(
                    name="context_id",
                    type="string",
                    description=(
                        "Named context with an active page. "
                        "Omit and provide 'url' for one-shot evaluation."
                    ),
                    required=False,
                ),
                ParameterSchema(
                    name="url",
                    type="string",
                    description="URL to navigate to before evaluating (one-shot mode).",
                    required=False,
                ),
                ParameterSchema(
                    name="expression",
                    type="string",
                    description=(
                        "JavaScript expression to evaluate. "
                        "Examples: 'document.title', 'document.querySelectorAll(\"a\").length', "
                        "'JSON.stringify(performance.timing)'"
                    ),
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs.get("context_id")
        url = inputs.get("url")
        expression = inputs["expression"]

        if not context_id and not url:
            return ToolResult.create_error(
                "Provide either 'context_id' (for active page) or 'url' (for one-shot). "
                "Use 'kaos-web-browser-navigate' to create a context first."
            )

        try:
            client = await _get_browser_client()

            if context_id:
                result = await client.evaluate_in_context(context_id, expression)
            else:
                result = await client.evaluate(url, expression)

            # Serialize result for MCP
            if isinstance(result, (dict, list)):
                return ToolResult.create_success(output=result)
            return ToolResult.create_success(str(result))

        except Exception as exc:
            target = context_id or url
            return ToolResult.create_error(
                f"JavaScript evaluation failed in {target}: {exc}. "
                "Verify the expression is valid JavaScript. "
                "Common issue: expression returns a non-serializable value (DOM node, Promise)."
            )


class GetSnapshotTool(KaosTool):
    """Get the accessibility tree of a browser page."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-snapshot",
            display_name="Get Page Snapshot",
            description=(
                "Get the accessibility tree of the active page. Returns a structured "
                "representation of all interactive and text elements — buttons, links, "
                "inputs, headings, etc. Use this to understand page structure and find "
                "elements for click/fill/type operations."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_READ_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]

        try:
            client = await _get_browser_client()
            snapshot = await client.get_snapshot(context_id)
            current_url = await client.get_url(context_id)

            if not snapshot:
                return ToolResult.create_success(
                    output={
                        "context_id": context_id,
                        "url": current_url,
                        "snapshot": "",
                        "message": (
                            "Accessibility snapshot returned empty. "
                            "The page may not have loaded fully or has no accessible content."
                        ),
                    }
                )

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "url": current_url,
                    "snapshot": snapshot,
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Snapshot failed for context '{context_id}': {exc}. "
                "Ensure a page is loaded in this context. "
                "Use 'kaos-web-browser-navigate' first."
            )


class GetPageContentTool(KaosTool):
    """Get the current HTML/text content from an active browser page."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-content",
            display_name="Get Browser Page Content",
            description=(
                "Get the current HTML content from an active browser page and extract "
                "it into structured text or markdown. Useful after interaction (click, fill) "
                "to see updated page content."
            ),
            category=ToolCategory.DOCUMENT,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_READ_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="output_format",
                    type="string",
                    description="Output format for extracted content.",
                    required=False,
                    default="markdown",
                    constraints={"enum": ["markdown", "text", "html"]},
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        output_format = inputs.get("output_format", "markdown")

        try:
            client = await _get_browser_client()
            html = await client.get_content(context_id)
            current_url = await client.get_url(context_id)

            if output_format == "html":
                return ToolResult.create_success(html)

            from kaos_web.extract import html_to_document

            doc = html_to_document(html, url=current_url)

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
                    name=unique_document_name(doc.metadata.title or current_url),
                    description=f"Browser content from {current_url}",
                    metadata={
                        "source_url": current_url,
                        "context_id": context_id,
                        "block_count": len(doc.body),
                    },
                )
                summary = document_to_summary(doc, max_length=500)
                return manifest.to_tool_result(
                    summary=summary,
                    structured_content={
                        "artifact_id": manifest.artifact_id,
                        "title": doc.metadata.title,
                        "url": current_url,
                        "body_uri": manifest.body_uri,
                    },
                )

            # Inline fallback (no runtime context)
            if output_format == "text":
                from kaos_content.serializers.text import serialize_text

                text = serialize_text(doc)
                return ToolResult.create_success(text)
            else:
                from kaos_content.serializers.markdown import serialize_markdown

                md = serialize_markdown(doc)
                return ToolResult.create_success(md)

        except Exception as exc:
            return ToolResult.create_error(
                f"Content extraction failed for context '{context_id}': {exc}. "
                "Ensure a page is loaded in this context."
            )


# ── Cookie / Storage Tools ──


class GetCookiesTool(KaosTool):
    """Get cookies from a browser context."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-cookies",
            display_name="Get Cookies",
            description=(
                "Get cookies from a browser context. Returns all cookies or "
                "filters by URL. Use after navigating to see what cookies a site sets."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_READ_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="urls",
                    type="string",
                    description=(
                        "Comma-separated URLs to filter cookies by. "
                        "Omit for all cookies in the context."
                    ),
                    required=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        urls_str = inputs.get("urls")
        urls = [u.strip() for u in urls_str.split(",")] if urls_str else None

        try:
            client = await _get_browser_client()
            cookies = await client.get_cookies(context_id, urls=urls)

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "cookie_count": len(cookies),
                    "cookies": [
                        {
                            "name": c.get("name"),
                            "value": c.get("value"),
                            "domain": c.get("domain"),
                            "path": c.get("path"),
                            "secure": c.get("secure"),
                            "httpOnly": c.get("httpOnly"),
                            "sameSite": c.get("sameSite"),
                        }
                        for c in cookies
                    ],
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to get cookies for context '{context_id}': {exc}. "
                "Ensure a page is loaded with 'kaos-web-browser-navigate'."
            )


class SetCookieTool(KaosTool):
    """Set a cookie in a browser context."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-set-cookie",
            display_name="Set Cookie",
            description=(
                "Set a cookie in a browser context. Requires name, value, and "
                "either domain or url. Use for authentication or testing."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.TRANSFORM,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="name",
                    type="string",
                    description="Cookie name.",
                ),
                ParameterSchema(
                    name="value",
                    type="string",
                    description="Cookie value.",
                ),
                ParameterSchema(
                    name="domain",
                    type="string",
                    description="Cookie domain (e.g., '.example.com').",
                    required=False,
                ),
                ParameterSchema(
                    name="url",
                    type="string",
                    description="URL to associate cookie with (alternative to domain).",
                    required=False,
                ),
                ParameterSchema(
                    name="path",
                    type="string",
                    description="Cookie path.",
                    required=False,
                    default="/",
                ),
                ParameterSchema(
                    name="secure",
                    type="boolean",
                    description="HTTPS-only cookie.",
                    required=False,
                    default=False,
                ),
                ParameterSchema(
                    name="httpOnly",
                    type="boolean",
                    description="HTTP-only cookie (not accessible to JS).",
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        name = inputs["name"]
        value = inputs["value"]
        domain = inputs.get("domain")
        url = inputs.get("url")

        if not domain and not url:
            return ToolResult.create_error(
                "Provide either 'domain' or 'url' for the cookie. "
                "Example: domain='.example.com' or url='https://example.com'."
            )

        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "path": inputs.get("path", "/"),
            "secure": inputs.get("secure", False),
            "httpOnly": inputs.get("httpOnly", False),
        }
        if domain:
            cookie["domain"] = domain
        if url:
            cookie["url"] = url

        try:
            client = await _get_browser_client()
            await client.set_cookies(context_id, [cookie])

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "cookie_name": name,
                    "message": f"Cookie '{name}' set successfully.",
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to set cookie in context '{context_id}': {exc}. "
                "Ensure a browser context exists with 'kaos-web-browser-navigate'."
            )


class SaveAuthStateTool(KaosTool):
    """Save browser auth state (cookies + localStorage) to a file."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-save-auth",
            display_name="Save Auth State",
            description=(
                "Save the browser context's storage state (cookies and localStorage) "
                "to a JSON file. Use after logging in to persist authentication. "
                "To reuse, configure BrowserClientConfig(storage_state='path.json')."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="path",
                    type="string",
                    description="File path to save state to (JSON file).",
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        path = inputs["path"]

        try:
            client = await _get_browser_client()
            saved_path = await client.save_storage_state(context_id, path)

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "path": saved_path,
                    "message": (
                        f"Auth state saved to '{saved_path}'. "
                        "Load in future sessions with storage_state config."
                    ),
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to save auth state for context '{context_id}': {exc}. "
                "Ensure a browser context exists and the path is writable."
            )


# ── Network Monitoring Tools ──


class EnableRequestLoggingTool(KaosTool):
    """Enable network request logging on a browser context."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-log-requests",
            display_name="Enable Request Logging",
            description=(
                "Start recording network requests made by the browser page. "
                "Call this after 'kaos-web-browser-navigate' to set up a context. "
                "Logging survives page replacement — subsequent navigate calls on the "
                "same context_id automatically re-attach logging hooks. "
                "Set capture_bodies=true to also capture response bodies for "
                "fetch/xhr requests (e.g., JSON API calls made by SPA pages). "
                "Workflow: navigate → log-requests (capture_bodies=true) → navigate "
                "to target page → browser-requests (resource_type='fetch') → "
                "browser-get-request (get JSON body) → browser-captured-responses "
                "(store_artifacts=true to persist)."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.EXTRACT,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="capture_bodies",
                    type="boolean",
                    description=(
                        "Capture response bodies for matching requests. "
                        "Bodies are stored in memory and retrievable via "
                        "'kaos-web-browser-get-request' or listed with "
                        "'kaos-web-browser-captured-responses'. "
                        "Only captures fetch/xhr requests with text-like "
                        "content types (JSON, HTML, XML, text, CSV) under "
                        "the size limit."
                    ),
                    required=False,
                    default=False,
                ),
                ParameterSchema(
                    name="resource_types",
                    type="string",
                    description=(
                        "Comma-separated resource types to capture bodies for. "
                        "Only used when capture_bodies=true. "
                        "Options: document, stylesheet, image, script, font, "
                        "xhr, fetch, etc."
                    ),
                    required=False,
                    default="fetch,xhr",
                ),
                ParameterSchema(
                    name="max_body_size",
                    type="integer",
                    description=(
                        "Maximum response body size in bytes to capture. "
                        "Responses larger than this are skipped."
                    ),
                    required=False,
                    default=1048576,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        capture_bodies = inputs.get("capture_bodies", False)
        resource_types_str = inputs.get("resource_types", "fetch,xhr")
        resource_types = frozenset(rt.strip() for rt in resource_types_str.split(","))
        max_body_size = inputs.get("max_body_size", 1048576)

        try:
            client = await _get_browser_client()
            await client.enable_request_logging(
                context_id,
                capture_bodies=capture_bodies,
                resource_types=resource_types,
                max_body_size=max_body_size,
            )

            msg = f"Request logging enabled for context '{context_id}'."
            if capture_bodies:
                msg += f" Body capture active for: {', '.join(sorted(resource_types))}."
            msg += (
                " Navigate or interact to capture requests, then use "
                "'kaos-web-browser-requests' to retrieve them."
            )
            if capture_bodies:
                msg += (
                    " Use 'kaos-web-browser-captured-responses' to list "
                    "responses with captured bodies."
                )

            return ToolResult.create_success(output={"context_id": context_id, "message": msg})
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to enable logging for context '{context_id}': {exc}. "
                "Ensure a page is loaded with 'kaos-web-browser-navigate'."
            )


class ListRequestsTool(KaosTool):
    """List recorded network requests from a browser context."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-requests",
            display_name="List Network Requests",
            description=(
                "List network requests recorded by the browser. "
                "Call 'kaos-web-browser-log-requests' first to start recording. "
                "Returns URL, method, resource type, status, and has_body indicator "
                "for each request. Filter by resource_type (e.g., 'fetch' or 'xhr') "
                "to find API calls. Use 'kaos-web-browser-get-request' to get full "
                "details and response body for a specific request ID."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_READ_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="resource_type",
                    type="string",
                    description=(
                        "Filter by resource type (document, stylesheet, image, "
                        "script, font, xhr, fetch, etc.)."
                    ),
                    required=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        resource_type = inputs.get("resource_type")

        try:
            client = await _get_browser_client()
            log = await client.get_request_log(context_id)

            if resource_type:
                log = [e for e in log if e.get("resource_type") == resource_type]

            # Summarize for readability
            summary = [
                {
                    "id": e["id"],
                    "method": e["method"],
                    "url": e["url"][:200],
                    "resource_type": e.get("resource_type"),
                    "status": e.get("status"),
                    "has_body": e.get("has_body", False),
                    "body_size": e.get("body_size"),
                }
                for e in log
            ]

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "total_requests": len(log),
                    "requests": summary,
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to list requests for context '{context_id}': {exc}. "
                "Ensure request logging was enabled with "
                "'kaos-web-browser-log-requests'."
            )


class GetRequestDetailTool(KaosTool):
    """Get full details of a specific network request."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-get-request",
            display_name="Get Request Detail",
            description=(
                "Get full details of a specific network request by ID, "
                "including headers, post data, response headers, and "
                "optionally the response body (if captured with "
                "capture_bodies=true). JSON/text bodies are returned as "
                "decoded strings; binary as base64. "
                "Use 'kaos-web-browser-requests' first to find the request ID. "
                "For bulk retrieval, use 'kaos-web-browser-captured-responses' instead."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_READ_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="request_id",
                    type="integer",
                    description="Request ID from 'kaos-web-browser-requests'.",
                ),
                ParameterSchema(
                    name="include_body",
                    type="boolean",
                    description=(
                        "Include the captured response body in the result. "
                        "JSON/text content types are returned as decoded strings. "
                        "Binary content is base64-encoded."
                    ),
                    required=False,
                    default=True,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        request_id = inputs["request_id"]
        include_body = inputs.get("include_body", True)

        try:
            client = await _get_browser_client()
            detail = await client.get_request_detail(context_id, request_id)

            if detail is None:
                return ToolResult.create_error(
                    f"Request ID {request_id} not found in context "
                    f"'{context_id}'. Use 'kaos-web-browser-requests' "
                    "to list available request IDs."
                )

            # Optionally include captured response body
            if include_body and detail.get("has_body"):
                body_info = await client.get_response_body(context_id, request_id)
                if body_info is not None:
                    ct = body_info.get("content_type", "").lower()
                    body_bytes: bytes = body_info["body"]
                    # Decode text-like content types as UTF-8 strings
                    if any(
                        ct.startswith(t)
                        for t in (
                            "application/json",
                            "text/",
                            "application/xml",
                            "application/ld+json",
                        )
                    ):
                        try:
                            detail["body"] = body_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            detail["body"] = base64.b64encode(body_bytes).decode("ascii")
                            detail["body_encoding"] = "base64"
                    else:
                        detail["body"] = base64.b64encode(body_bytes).decode("ascii")
                        detail["body_encoding"] = "base64"

            return ToolResult.create_success(output=detail)

        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to get request {request_id} in context '{context_id}': {exc}. "
                "The context may have been closed, or request logging may not have "
                "been enabled before the request was issued. "
                "Call 'kaos-web-browser-list-contexts' to confirm the context is "
                "still active, then 'kaos-web-browser-requests' to re-list valid "
                "request IDs. If logging was off, re-enable it with "
                "'kaos-web-browser-log-requests' and re-issue the request before "
                "querying again."
            )


class ListCapturedResponsesTool(KaosTool):
    """List captured response bodies from browser network traffic."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-captured-responses",
            display_name="List Captured Responses",
            description=(
                "List network responses that have captured bodies. "
                "Enable body capture first with 'kaos-web-browser-log-requests' "
                "(capture_bodies=true), then navigate to trigger API calls. "
                "Filter by resource_type (e.g., 'fetch') or content_type (e.g., 'json') "
                "to find relevant API responses. "
                "Use request_id with 'kaos-web-browser-get-request' to retrieve "
                "the full decoded body for a specific response. "
                "Set store_artifacts=true to persist all captured JSON responses as "
                "session artifacts discoverable via kaos://session/{session_id}/artifacts "
                "and queryable with kaos-tabular tools."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_READ_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
                ParameterSchema(
                    name="resource_type",
                    type="string",
                    description="Filter by resource type (e.g., 'fetch', 'xhr', 'document').",
                    required=False,
                ),
                ParameterSchema(
                    name="content_type",
                    type="string",
                    description="Filter by content type substring (e.g., 'json', 'html').",
                    required=False,
                ),
                ParameterSchema(
                    name="store_artifacts",
                    type="boolean",
                    description=(
                        "Store captured JSON responses as session artifacts. "
                        "Each response becomes a separate artifact accessible via "
                        "kaos://session/{session_id}/artifacts. Requires a runtime "
                        "context (MCP server mode)."
                    ),
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]
        resource_type = inputs.get("resource_type")
        content_type = inputs.get("content_type")
        store_artifacts = inputs.get("store_artifacts", False)

        try:
            client = await _get_browser_client()
            responses = await client.get_captured_responses(
                context_id,
                resource_type=resource_type,
                content_type=content_type,
            )

            if not responses:
                return ToolResult.create_success(
                    output={
                        "context_id": context_id,
                        "total_captured": 0,
                        "responses": [],
                        "message": (
                            "No captured response bodies found. "
                            "Ensure 'kaos-web-browser-log-requests' was called with "
                            "capture_bodies=true before navigation."
                        ),
                    }
                )

            # Optionally store JSON responses as artifacts
            artifacts_created: list[dict[str, Any]] = []
            if store_artifacts and context is not None and context.runtime is not None:
                from urllib.parse import urlparse

                from kaos_content.artifacts import unique_document_name
                from kaos_core.types.enums import ArtifactRole

                for resp in responses:
                    ct = resp.get("content_type", "")
                    if "json" not in ct.lower():
                        continue
                    body_info = await client.get_response_body(context_id, resp["id"])
                    if body_info is None:
                        continue
                    body_bytes: bytes = body_info["body"]

                    # Build artifact name from URL
                    parsed = urlparse(resp["url"])
                    path_fragment = parsed.path.strip("/").replace("/", "-")[:40]
                    domain = parsed.hostname or "unknown"
                    name = unique_document_name(f"api-response-{domain}-{path_fragment}")

                    vfs_path = f"responses/{name}.json"
                    ctx_path = context.get_vfs_path(vfs_path)
                    await ctx_path.write_bytes(body_bytes)

                    manifest = await context.runtime.artifacts.create_from_path(
                        vfs_path,
                        context_id=context.session_id,
                        session_id=context.session_id,
                        name=name,
                        description=f"API response from {resp['url'][:100]}",
                        mime_type="application/json",
                        role=ArtifactRole.BODY,
                        provenance={
                            "source_url": resp["url"],
                            "tool": "kaos-web-browser-captured-responses",
                            "request_id": resp["id"],
                            "resource_type": resp.get("resource_type"),
                        },
                        metadata={
                            "status": resp.get("status"),
                            "size_bytes": body_info["size"],
                            "truncated": body_info["truncated"],
                        },
                    )
                    artifacts_created.append(
                        {
                            "artifact_id": manifest.artifact_id,
                            "body_uri": manifest.body_uri,
                            "name": name,
                            "source_url": resp["url"][:200],
                            "size_bytes": body_info["size"],
                        }
                    )

            output: dict[str, Any] = {
                "context_id": context_id,
                "total_captured": len(responses),
                "responses": responses,
            }
            if artifacts_created:
                output["artifacts_created"] = len(artifacts_created)
                output["artifacts"] = artifacts_created

            return ToolResult.create_success(output=output)

        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to list captured responses for context '{context_id}': {exc}. "
                "The context may have been closed, or response-body capture may not "
                "have been enabled (it requires 'capture_bodies=true' on "
                "'kaos-web-browser-log-requests'). "
                "Call 'kaos-web-browser-list-contexts' to confirm the context is "
                "active, then re-enable capture with "
                "'kaos-web-browser-log-requests' (capture_bodies=true) before "
                "issuing requests. For metadata-only network traces, use "
                "'kaos-web-browser-requests' instead — it does not require body "
                "capture to be on."
            )


# ── Context Management Tools ──


class ListContextsTool(KaosTool):
    """List active browser contexts."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-list-contexts",
            display_name="List Browser Contexts",
            description=(
                "List all active browser contexts with their current URLs. "
                "Use this to see what sessions are running and available for "
                "interaction tools."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.QUERY,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_READ_ANNOTATIONS,
            input_schema=[],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        try:
            client = await _get_browser_client()
            context_ids = client.active_contexts

            contexts = []
            for cid in context_ids:
                try:
                    url = await client.get_url(cid)
                except Exception:
                    url = "(unknown)"
                contexts.append({"context_id": cid, "url": url})

            return ToolResult.create_success(
                output={
                    "active_count": len(contexts),
                    "contexts": contexts,
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to list browser contexts: {exc}. "
                "The browser client may have disconnected, or a context "
                "was closed mid-iteration. "
                "Call 'kaos-web-browser-navigate' to (re)open a session, "
                "then retry this tool. "
                "If the issue persists, restart the MCP server."
            )


class CloseContextTool(KaosTool):
    """Close a browser context and free its resources."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="kaos-web-browser-close-context",
            display_name="Close Browser Context",
            description=(
                "Close a named browser context, releasing its page and resources. "
                "Use when done with an interactive session. "
                "Use 'kaos-web-browser-list-contexts' to see active contexts."
            ),
            category=ToolCategory.INTEGRATION,
            capability=ToolCapability.TRANSFORM,
            module_name=_MODULE,
            version=_VERSION,
            annotations=_BROWSER_WRITE_ANNOTATIONS,
            input_schema=[
                _context_id_param(),
            ],
        )

    async def execute(
        self, inputs: dict[str, Any], context: KaosContext | None = None
    ) -> ToolResult:
        context_id = inputs["context_id"]

        try:
            client = await _get_browser_client()
            if context_id not in client.active_contexts:
                return ToolResult.create_error(
                    f"No active context '{context_id}'. "
                    "Use 'kaos-web-browser-list-contexts' to see available contexts."
                )

            await client.close_context(context_id)

            return ToolResult.create_success(
                output={
                    "context_id": context_id,
                    "message": f"Context '{context_id}' closed.",
                    "remaining": len(client.active_contexts),
                }
            )
        except Exception as exc:
            return ToolResult.create_error(
                f"Failed to close browser context '{context_id}': {exc}. "
                "The context may already be closed or the browser disconnected. "
                "Call 'kaos-web-browser-list-contexts' to confirm which contexts "
                "remain active. If the context is gone, no action is needed; "
                "otherwise retry, or restart the MCP server to reset all browser "
                "state."
            )


def register_browser_tools(runtime: KaosRuntime) -> int:
    """Register all browser interaction tools with the runtime. Returns count."""
    tools: list[KaosTool] = [
        # Phase 5.1: Browser interaction
        BrowserNavigateTool(),
        ClickElementTool(),
        FillInputTool(),
        TypeTextTool(),
        PressKeyTool(),
        SelectOptionTool(),
        ScreenshotTool(),
        EvaluateJSTool(),
        GetSnapshotTool(),
        GetPageContentTool(),
        # Phase 5.2: Cookie / storage
        GetCookiesTool(),
        SetCookieTool(),
        SaveAuthStateTool(),
        # Phase 5.3: Network monitoring
        EnableRequestLoggingTool(),
        ListRequestsTool(),
        GetRequestDetailTool(),
        ListCapturedResponsesTool(),
        # Context management
        ListContextsTool(),
        CloseContextTool(),
    ]
    for tool in tools:
        runtime.tools.register_tool(tool)
    return len(tools)
