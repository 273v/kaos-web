"""kaos-web: Web content extraction for KAOS.

Fetches HTML from URLs and produces kaos-content ContentDocument AST
with provenance. Supports HTTP (httpx) and browser rendering (playwright).

Public parse/extract surface (per ``docs/guides/python-api-naming.md``):

- :func:`parse_html` — raw HTML string → :class:`~kaos_content.ContentDocument`
  (no readability scoping). Re-exported from
  :func:`kaos_content.parsers.html.parse_html` for discoverability so agents
  can reach for it via ``from kaos_web import parse_html``.
- :func:`html_to_document` — HTML → readability-scoped
  :class:`~kaos_content.ContentDocument` (drops nav/footer chrome). The
  "main reading content" pipeline most callers want.
- :func:`extract_content`, :func:`extract_metadata` — pull structured data
  out of HTML (matches the ``extract_<thing>`` rule: data extraction from
  an already-loaded source, not a pure parse).
"""

# parse_html is re-exported from kaos-content as the canonical "raw HTML →
# AST" entry point. The import is unconditional because kaos-web hard-deps
# on kaos-content[html] (lxml) — see pyproject.toml.
from kaos_content.parsers.html import parse_html
from kaos_web._version import __version__
from kaos_web.browser_tools import register_browser_tools
from kaos_web.crawl_tools import register_crawl_tools
from kaos_web.domain_tools import register_domain_tools
from kaos_web.extract import extract_content, extract_metadata, html_to_document
from kaos_web.tools import register_web_all_tools, register_web_tools

__all__ = [
    "__version__",
    "extract_content",
    "extract_metadata",
    "html_to_document",
    "parse_html",
    "register_browser_tools",
    "register_crawl_tools",
    "register_domain_tools",
    "register_web_all_tools",
    "register_web_tools",
]
