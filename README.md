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
```
