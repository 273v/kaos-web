"""kaos-web: Web content extraction for KAOS.

Fetches HTML from URLs and produces kaos-content ContentDocument AST
with provenance. Supports HTTP (httpx) and browser rendering (playwright).
"""

from kaos_web._version import __version__
from kaos_web.browser_tools import register_browser_tools
from kaos_web.crawl_tools import register_crawl_tools
from kaos_web.extract import extract_content, extract_metadata, html_to_document
from kaos_web.tools import register_web_tools

__all__ = [
    "__version__",
    "extract_content",
    "extract_metadata",
    "html_to_document",
    "register_browser_tools",
    "register_crawl_tools",
    "register_web_tools",
]
