"""Web content extraction — readability, HTML-to-AST, metadata, links, images."""

# Re-export canonical search from kaos-content
from kaos_content.search import SearchResult, SearchResults, search_document
from kaos_web.extract.html_to_ast import html_to_document
from kaos_web.extract.images import ExtractedImage, extract_images
from kaos_web.extract.links import ExtractedLink, extract_links
from kaos_web.extract.metadata import extract_metadata
from kaos_web.extract.readability import extract_content

__all__ = [
    "ExtractedImage",
    "ExtractedLink",
    "SearchResult",
    "SearchResults",
    "extract_content",
    "extract_images",
    "extract_links",
    "extract_metadata",
    "html_to_document",
    "search_document",
]
