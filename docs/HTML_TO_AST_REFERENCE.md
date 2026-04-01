# HTML to ContentDocument AST: Edge Cases and Reference Guide

Reference patterns for `kaos_web/extract/html_to_ast.py`. These patterns are derived from
analyzing turndown (JS), JohannesKaufmann/html-to-markdown (Go), Defuddle (JS),
readability-lxml (Python), htmd (Rust), alea-markdown-python, and markdownify (Python).

None of these libraries are dependencies — they produce markdown strings, not typed AST.
We produce `kaos-content` `ContentDocument` with `Block`/`Inline` nodes and provenance.
But the edge cases are the same.

---

## HTML Element to AST Node Mapping

### Block Elements

| HTML | AST Node | Notes |
|------|----------|-------|
| `<h1>`-`<h6>` | `Heading(depth=N)` | Depth from tag number |
| `<p>` | `Paragraph` | Collapse internal whitespace |
| `<ul>` | `BulletList` | |
| `<ol>` | `OrderedList(start=N)` | Preserve `start` attribute |
| `<li>` | `ListItem` | May contain nested lists |
| `<blockquote>` | `BlockQuote` | Recursive (may contain any blocks) |
| `<pre><code>` | `CodeBlock(language=...)` | Extract language from `class="language-*"` |
| `<pre>` (no code) | `CodeBlock` | Preserve whitespace verbatim |
| `<table>` | `Table` | Structured cells, not string formatting |
| `<hr>` | `ThematicBreak` | |
| `<figure>` | `Figure` | With optional `figcaption` |
| `<dl>` | `DefinitionList` | `<dt>` → term, `<dd>` → definition |
| `<div>` | Transparent or `Div` | See semantic handling below |
| `<section>` | Transparent | Process children |
| `<article>` | Transparent | Process children (after readability) |

### Inline Elements

| HTML | AST Node | Notes |
|------|----------|-------|
| `<strong>`, `<b>` | `Strong` | |
| `<em>`, `<i>` | `Emphasis` | |
| `<code>` | `Code` | Not inside `<pre>` |
| `<a href="...">` | `Link(target=url)` | Resolve relative URLs |
| `<img>` | `Image(target=src)` | Check data-src for lazy-load |
| `<br>` | `LineBreak` | |
| `<s>`, `<del>`, `<strike>` | `Strikethrough` | |
| `<sub>` | `RawInline(format="html")` | No markdown equivalent |
| `<sup>` | `RawInline(format="html")` | No markdown equivalent |
| `<span>` | Transparent | Process children |

### Elements to Strip (after readability pass)

| HTML | Action |
|------|--------|
| `<script>`, `<style>`, `<noscript>` | Remove entirely |
| `<nav>`, `<footer>`, `<header>` | Remove (handled by readability) |
| `<aside>` | Remove (handled by readability) |
| `<form>`, `<input>`, `<select>`, `<textarea>` | Remove |
| `<iframe>`, `<embed>`, `<object>` | Remove |
| `<svg>` | Remove (or extract alt text if available) |
| `<button>` | Remove |

---

## Edge Cases

### 1. Block Inside Inline (Invalid HTML, Common in the Wild)

```html
<a href="/page"><h2>Heading</h2></a>
<a href="/page"><div>Block content</div></a>
```

**Solution**: Invert the nesting. The block element becomes the outer node, the link
becomes an inline within it.

```
Heading(depth=2, content=[Link(target="/page", content=[Text("Heading")])])
```

**Reference**: JohannesKaufmann Go library explicitly handles this case.

### 2. Redundant Nesting

```html
<b><b><b>text</b></b></b>
<em><em>also text</em></em>
```

**Solution**: Collapse to single AST node. When entering a `Strong` and the current
context is already inside a `Strong`, don't create a new one.

### 3. Whitespace Inside Emphasis

```html
<em> text </em>
<strong> bold </strong>
```

**Solution**: Move leading/trailing whitespace outside the inline node.

```
[Text(" "), Emphasis([Text("text")]), Text(" ")]
```

Not:
```
[Emphasis([Text(" text ")])]
```

### 4. Empty Elements

```html
<p></p>
<strong></strong>
<a href="..."></a>
```

**Solution**: Drop empty elements. Don't produce empty AST nodes. An element is empty
if it contains no text content after whitespace collapsing.

Exception: `<br>`, `<hr>`, `<img>` are inherently empty and should be kept.

### 5. Table Structure

```html
<table>
  <thead><tr><th colspan="2">Header</th></tr></thead>
  <tbody><tr><td>A</td><td>B</td></tr></tbody>
</table>
```

**Solution**: Store colspan/rowspan in the `Table` AST node's cell metadata. The AST
can represent what markdown table syntax cannot. The `serialize_markdown()` serializer
decides how to render (e.g., duplicate cell content across spanned positions, or fall
back to HTML passthrough).

Missing `<thead>`: Infer header from first row if all cells are `<th>`.

### 6. Code Block Language Detection

```html
<pre><code class="language-python">...</code></pre>
<pre><code class="highlight-js">...</code></pre>
<pre><code class="lang-rust">...</code></pre>
<pre data-lang="go">...</pre>
```

**Solution**: Check class for patterns: `language-*`, `lang-*`, `highlight-*`.
Also check `data-lang` attribute. Strip `language-`/`lang-`/`highlight-` prefix.

### 7. Lazy-Loaded Images

```html
<img data-src="/real.jpg" src="/placeholder.gif">
<img data-lazy-src="/real.jpg" src="">
<img loading="lazy" src="/real.jpg">
```

**Solution**: Check attributes in priority order:
1. `data-src`
2. `data-lazy-src`
3. `data-original`
4. `src`

Skip if src is a data URI placeholder (`data:image/gif;base64,...` with tiny size).

### 8. Relative URL Resolution

```html
<a href="/page">Link</a>
<a href="../other">Other</a>
<img src="images/photo.jpg">
```

**Solution**: Resolve all relative URLs against the page's base URL (from `<base href>`
tag or the fetch URL). Store resolved absolute URLs in AST nodes. The base URL is
available in provenance.

Use `urllib.parse.urljoin(base_url, relative_url)`.

### 9. Dangerous URIs

```html
<a href="javascript:alert(1)">Click</a>
<a href="data:text/html,...">Data</a>
<img src="javascript:void(0)">
```

**Solution**: Strip links with `javascript:` scheme. Strip `data:` URIs for links
(but allow `data:image/*` for inline images if needed). Only allow `http:`, `https:`,
`mailto:`, `tel:` schemes in Link nodes.

### 10. Whitespace Handling

**Inside `<pre>`**: Preserve exactly as-is, including newlines and spaces.
Strip one leading newline (per HTML spec, browsers do this).

**Everywhere else**: Collapse runs of whitespace to single space. Trim leading/trailing
whitespace from paragraph content. Convert `&nbsp;` to regular space (unless preserving
layout matters).

**Between blocks**: Don't produce extra whitespace between block elements — the
serializer handles inter-block spacing.

### 11. HTML Entities

lxml handles entity decoding automatically (`&amp;` → `&`, `&#8220;` → `"`).
The AST stores Unicode text, not entities. No special handling needed.

### 12. Definition Lists

```html
<dl>
  <dt>Term</dt>
  <dd>Definition</dd>
  <dt>Another term</dt>
  <dd>First definition</dd>
  <dd>Second definition</dd>
</dl>
```

**Solution**: Map to kaos-content `DefinitionList` node. This is one of kaos-content's
advantages over plain markdown — the AST can represent definition lists natively, and
`serialize_markdown()` renders them in deflist syntax.

### 13. Math Content

```html
<span class="math" data-latex="E = mc^2">E = mc²</span>
<script type="math/tex">E = mc^2</script>
<math xmlns="http://www.w3.org/1998/Math/MathML">...</math>
```

**Solution**: Check for `data-latex` attribute first (MathJax/KaTeX pages often include
this). Store as `RawInline(format="latex", text="E = mc^2")` or
`RawBlock(format="latex", ...)` for display math.

If no `data-latex`, extract text content as fallback.

### 14. Linked Images

```html
<a href="/page"><img src="/photo.jpg" alt="Photo"></a>
```

**Solution**: `Link(target="/page", content=[Image(target="/photo.jpg", alt="Photo")])`.

If the link contains only an image (no other text), this is the standard pattern.
If the link contains an image plus text, both are children of the Link inline.

---

## Reference Libraries

These are NOT dependencies — they are references for how specific patterns are handled.

| Library | URL | License | Best reference for |
|---------|-----|---------|-------------------|
| **JohannesKaufmann/html-to-markdown** | [GitHub](https://github.com/JohannesKaufmann/html-to-markdown) | MIT | Smart escaping (`ESCAPING.md`), relative URL resolution, block-in-inline |
| **turndown** | [GitHub](https://github.com/mixmark-io/turndown) | MIT | Rule architecture, GFM plugin patterns |
| **Defuddle** | [GitHub](https://github.com/kepano/defuddle) | MIT | Content cleaning, math/code normalization, mobile CSS detection |
| **readability-lxml** | [GitHub](https://github.com/buriy/python-readability) | Apache 2.0 | Readability scoring algorithm (~300 lines) |
| **alea-markdown-python** | [GitHub](https://github.com/alea-institute/alea-markdown-python) | MIT | Normalizer patterns, size-aware parsing |
| **htmd** | [GitHub](https://github.com/letmutex/htmd) | Apache 2.0 | Rust HTML→markdown, tag handler patterns |
| **markdownify** | [GitHub](https://github.com/matthewwithanm/python-markdownify) | MIT | Table handling, heading styles, inline formatting |
| **html-to-markdown** (kreuzberg) | [GitHub](https://github.com/kreuzberg-dev/html-to-markdown) | MIT | Rust-powered, preprocessing options, visitor pattern |
| **Pandoc** | [pandoc.org](https://pandoc.org/) | GPL v2+ | Definition lists, math, footnotes, grid tables (reference only, not compatible license) |
