"""Network-free parsing of Google result pages. No browser, no HTTP: HTML in, typed data out."""

from .classify import Classification, classify
from .models import PageState, SearchRequest, SearchResponse, SerpData
from .serp import PARSER_VERSION, ParseResult, parse_page

__all__ = [
    "PARSER_VERSION",
    "Classification",
    "PageState",
    "ParseResult",
    "SearchRequest",
    "SearchResponse",
    "SerpData",
    "classify",
    "parse_page",
]
