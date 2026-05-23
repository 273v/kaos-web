"""Web content extraction — readability, HTML-to-AST, metadata, links, images, feeds."""

# Re-export canonical search from kaos-content
from kaos_content.search import SearchResult, SearchResults, search_document
from kaos_web.extract.feed import FeedItem, FeedResult, parse_feed
from kaos_web.extract.html_to_ast import html_to_document
from kaos_web.extract.images import ExtractedImage, extract_images
from kaos_web.extract.links import ExtractedLink, extract_links
from kaos_web.extract.metadata import extract_metadata
from kaos_web.extract.readability import extract_content as extract_content_heuristic
from kaos_web.extract.readability_l3 import extract_content_l3

# Default extract_content uses L3 model (consistent with html_to_document).
extract_content = extract_content_l3

__all__ = [
    "ExtractedImage",
    "ExtractedLink",
    "FeedItem",
    "FeedResult",
    "SearchResult",
    "SearchResults",
    "extract_content",
    "extract_content_heuristic",
    "extract_content_l3",
    "extract_images",
    "extract_links",
    "extract_metadata",
    "html_to_document",
    "parse_feed",
    "search_document",
]
