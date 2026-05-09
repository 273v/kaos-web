# kaos-web

Web content extraction for KAOS — HTML to ContentDocument AST with provenance.

## Installation

```bash
pip install kaos-web
# For browser rendering:
pip install kaos-web[browser]
```

## Quick Start

```python
from kaos_web import html_to_document

doc = html_to_document(html_string, url="https://example.com")
# doc is a ContentDocument — use DocumentView, serialize_markdown(), search, etc.
```

## CLI

```bash
kaos-web extract https://example.com
kaos-web extract page.html --format text
kaos-web metadata https://example.com --json
kaos-web serve                               # MCP server (stdio)
kaos-web serve --http --port 8000            # MCP server (HTTP)
kaos-web serve --browser --crawl --domain    # all 45 tools
```

## MCP Integration

### Python API

```python
from kaos_core import KaosRuntime
from kaos_web import register_web_tools, register_browser_tools, register_crawl_tools
from kaos_mcp import KaosMCPServer

runtime = KaosRuntime()
register_web_tools(runtime)          # 7 extraction tools (always)
register_browser_tools(runtime)      # 18 browser interaction tools (optional)
register_crawl_tools(runtime)        # 3 crawl/discovery tools (optional)
server = KaosMCPServer(runtime=runtime)
server.run_stdio()
```

### Standalone server

```bash
# stdio (for Claude Code / Claude Desktop)
kaos-web-serve

# streamable HTTP
kaos-web-serve --http --port 8000

# with browser tools (navigate, click, fill, screenshot, etc.)
kaos-web-serve --browser

# with crawl tools (discover-urls, batch-fetch, crawl-site)
kaos-web-serve --crawl

# all tools + debug logging
kaos-web-serve --browser --crawl --debug
```

The `--browser` flag adds 18 browser interaction tools (requires `kaos-web[browser]`).
The `--crawl` flag adds 3 multi-page crawl/discovery tools.
The `--domain` flag adds 14 domain-intelligence tools (DNS, WHOIS, TLS, HTTP/UDP probing, TCP banner-grab, service fingerprint, org-entity extraction).
Without flags, only the 7 core extraction tools are registered.

### Security note — domain-intelligence TLS verification

The domain-intelligence tools (`kaos-web-http-headers`, `kaos-web-extract-org`)
run with TLS certificate verification **disabled by default**. These tools
target arbitrary third-party hosts whose cert configurations are the
*subject* of inspection — failing closed on an expired or self-signed cert
would defeat the purpose of the probe. To require standard CA validation
on these probes, set:

```bash
export KAOS_WEB_DOMAIN_VERIFY_TLS=true
```

For verified GETs of trusted endpoints, use `kaos-web-fetch-page` (or the
underlying `HttpClient`) — those paths keep TLS verification on.
