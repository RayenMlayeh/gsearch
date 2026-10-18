"""Offline parser tests over saved, minimized Google pages (fixtures/gecko).

Two layers: semantic assertions written by a human who looked at each page, and golden files that
catch any unintended change. Regenerate goldens with GSEARCH_UPDATE_GOLDEN=1 and review the diff.
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import GOLDEN, Fixture, fixture_by_id, gecko_fixtures
from gsearch_parse import PageState, ParseResult, parse_page

REAL = [f for f in gecko_fixtures() if not f.id.startswith("s")]


def parse(f: Fixture) -> ParseResult:
    return parse_page(f.html(), url=f.url, status=f.status, page=f.page)


@pytest.mark.parametrize("fx", REAL, ids=lambda f: f.id)
def test_golden(fx: Fixture) -> None:
    result = parse(fx)
    actual = {
        "state": result.state.value,
        "selector_set": result.selector_set,
        "data": result.data.model_dump(mode="json") if result.data else None,
    }
    path = GOLDEN / "gecko" / f"{fx.id}.json"
    if os.environ.get("GSEARCH_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected


@pytest.mark.parametrize("fx", [f for f in REAL if f.id != "c6-no-results"], ids=lambda f: f.id)
def test_every_results_page_has_sane_organic_results(fx: Fixture) -> None:
    result = parse(fx)
    assert result.state is PageState.OK, result.reason
    assert result.data is not None
    organic = result.data.organic
    assert 7 <= len(organic) <= 10
    assert [r.position for r in organic] == list(range(1, len(organic) + 1))
    for r in organic:
        assert r.title.strip()
        # Almost every link is an opaque /goto redirect (url unknown); Google-owned
        # destinations such as play.google.com are linked directly (url known).
        if str(r.link).startswith("https://www.google.com/goto?url="):
            assert r.url is None
        else:
            assert r.url is not None and str(r.url) == str(r.link)
        assert (r.domain is not None) != (r.source_meta is not None)  # exactly one of breadcrumb / meta
    assert result.data.related_searches
    assert result.data.pagination is not None
    assert result.data.pagination.next_page == fx.page + 1


def test_breadcrumb_and_date_split() -> None:
    data = parse(fixture_by_id("c0-tutorial")).data
    assert data is not None
    first = data.organic[0]
    assert first.title == "Python's asyncio: A Hands-On Walkthrough"
    assert first.domain == "realpython.com"
    assert first.display_url is not None and first.display_url.startswith("https://realpython.com")
    assert first.date == "Jul 30, 2025"
    assert first.snippet is not None and first.snippet.startswith("In this tutorial")
    assert [s.title for s in first.sitelinks] == [
        "Async I/O in Python With asyncio",
        "Other Async I/O Features in...",
    ]


def test_forum_result_exposes_metadata_instead_of_breadcrumb() -> None:
    data = parse(fixture_by_id("c0-tutorial")).data
    assert data is not None
    forum = data.organic[4]
    assert forum.domain is None
    assert forum.source_meta == "2 years ago"


def test_ai_overview_and_people_also_ask() -> None:
    data = parse(fixture_by_id("c2-ai-overview-paa")).data
    assert data is not None
    assert data.featured_snippet is None  # no classic featured snippet on this page
    assert data.ai_overview is not None
    assert data.ai_overview.text.startswith("The Eiffel Tower is 330 metres (1,083 feet) tall")
    assert "AI Overview" not in data.ai_overview.text[:40]  # UI chrome stripped
    assert len(data.ai_overview.sources) >= 5
    assert [q.question for q in data.people_also_ask] == [
        "Is the Eiffel Tower 300 meters tall?",
        "What are 5 facts about the Eiffel Tower?",
        "How many floors up is the Eiffel Tower?",
        "Is the Eiffel Tower 1000 feet?",
    ]


def test_paa_ignores_query_echo_widget() -> None:
    data = parse(fixture_by_id("c3-paa")).data
    assert data is not None
    questions = [q.question for q in data.people_also_ask]
    assert "what is asyncio in python" not in questions
    assert questions[0] == "What is the use of asyncio in Python?"


def test_second_page_pagination() -> None:
    data = parse(fixture_by_id("c3-paa-p2")).data
    assert data is not None and data.pagination is not None
    assert data.pagination.current == 2
    assert data.pagination.next_page == 3
    assert data.pagination.pages[:3] == [1, 2, 3]


def test_no_results_is_empty_valid_not_error() -> None:
    result = parse(fixture_by_id("c6-no-results"))
    assert result.state is PageState.EMPTY_VALID
    assert result.data is not None and result.data.organic == []


def test_non_english_query() -> None:
    data = parse(fixture_by_id("c7-french-query")).data
    assert data is not None
    assert data.organic[0].title.startswith("LES 10 MEILLEURS restaurants")
    assert "Meilleur restaurant Tunis Lac" in data.related_searches


def test_commercial_query_without_ads_returns_empty_ads() -> None:
    # No ads in this capture; uBlock Origin was active during collection, so the cause is unknown.
    # Ad extraction stays unverified until a capture without uBO shows ads.
    data = parse(fixture_by_id("c4-commercial-no-ads")).data
    assert data is not None and data.ads == []
