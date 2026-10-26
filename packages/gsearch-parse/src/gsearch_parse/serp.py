"""parse_page: classify, pick a selector set, extract, then self-test with invariants.

A page that looks like a results page but fails the invariants is PARSE_DRIFT, never "0 results".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser

from . import extract
from .classify import classify
from .models import PageState, SerpData
from .selectors import SELECTOR_SETS, SelectorSet

PARSER_VERSION = "0.1.0"
MAX_ORGANIC = 15


@dataclass(frozen=True, slots=True)
class ParseResult:
    state: PageState
    reason: str
    data: SerpData | None = None
    selector_set: str | None = None
    warnings: tuple[str, ...] = field(default=())
    signals: tuple[str, ...] = field(default=())


def select_set(tree: LexborHTMLParser) -> SelectorSet | None:
    for s in SELECTOR_SETS:
        if all(tree.css_first(marker) is not None for marker in s.signature):
            return s
    return None


def _empty_valid(tree: LexborHTMLParser) -> bool:
    """Structural, language-independent signature observed on fixture c6-no-results:
    #search present but empty, no #rso, no next-page link, and a non-empty #botstuff message."""
    search = tree.css_first("#search")
    return (
        search is not None
        and not extract.text_of(search)
        and tree.css_first("#rso") is None
        and tree.css_first("#pnnext") is None
        and bool(extract.text_of(tree.css_first("#botstuff")))
    )


def check_invariants(data: SerpData) -> list[str]:
    """Return violations. Any violation turns the page into PARSE_DRIFT."""
    problems: list[str] = []
    if len(data.organic) > MAX_ORGANIC:
        problems.append(f"{len(data.organic)} organic results > {MAX_ORGANIC}")
    for i, r in enumerate(data.organic, start=1):
        if r.position != i:
            problems.append(f"non-contiguous position {r.position} at index {i}")
        if not r.title.strip():
            problems.append(f"empty title at position {i}")
    if data.pagination and data.pagination.current not in data.pagination.pages:
        problems.append("current page missing from pagination")
    return problems


def parse_page(
    html: str,
    *,
    url: str,
    status: int | None = 200,
    headers: Mapping[str, str] | None = None,
    page: int = 1,
) -> ParseResult:
    tree = LexborHTMLParser(html)
    for junk in tree.css("script, style, noscript, svg"):
        junk.decompose()
    c = classify(html, url=url, status=status, headers=headers, tree=tree)
    if c.state is not PageState.OK:
        return ParseResult(state=c.state, reason=c.reason, signals=c.signals)

    s = select_set(tree)
    if s is None and _empty_valid(tree):
        return ParseResult(
            PageState.EMPTY_VALID,
            "no results for query",
            SerpData(),
            selector_set=None,
            signals=(*c.signals, "dom:#search-empty"),
        )
    if s is None:
        return ParseResult(
            PageState.PARSE_DRIFT, "results root present but no selector set matches", signals=c.signals
        )

    organic, warnings = extract.extract_organic(tree, s)
    data = SerpData(
        organic=organic,
        ads=extract.extract_ads(tree, s),
        ai_overview=extract.extract_ai_overview(tree, s),
        people_also_ask=extract.extract_paa(tree, s),
        related_searches=extract.extract_related(tree, s),
        pagination=extract.extract_pagination(tree, s, page),
        answer_boxes=extract.extract_answer_boxes(tree, s),
    )

    problems = check_invariants(data)
    if problems:
        return ParseResult(
            PageState.PARSE_DRIFT, "; ".join(problems), selector_set=s.name, warnings=tuple(warnings)
        )
    if not data.organic:
        # A real "no results" page is caught by _empty_valid above; anything else is markup drift,
        # even if side panels (AI Overview, related searches) still parse.
        return ParseResult(
            PageState.PARSE_DRIFT, "results page with zero organic results", selector_set=s.name
        )
    return ParseResult(PageState.OK, "parsed", data, s.name, tuple(warnings), c.signals)
