from __future__ import annotations

import pytest
from conftest import GECKO
from gsearch_core.cache import QueryCache
from gsearch_core.client import SearchClient
from gsearch_core.fetch import FetchError, FixtureFetcher, build_search_url
from gsearch_parse.models import PageState, SearchError, SearchRequest, SearchResponse
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def client(ttl: float = 600) -> SearchClient:
    return SearchClient(FixtureFetcher(GECKO), QueryCache(ttl_s=ttl))


async def test_search_returns_typed_response() -> None:
    resp = await client().search(SearchRequest(query="wikipedia"))
    assert isinstance(resp, SearchResponse)
    assert resp.meta.page_state is PageState.OK
    assert resp.meta.backend == "fixture"
    assert resp.meta.untrusted_content is True
    assert len(resp.organic) == 9
    play = resp.organic[3]
    assert play.domain == "play.google.com" and str(play.url).startswith("https://play.google.com/")


async def test_site_filter_maps_to_query() -> None:
    resp = await client().search(SearchRequest(query="asyncio", site="python.org", time_range="y"))
    assert isinstance(resp, SearchResponse)
    assert all(r.domain and r.domain.endswith("python.org") for r in resp.organic)


async def test_captcha_is_an_error_with_retry_after_not_empty_results() -> None:
    resp = await client().search(SearchRequest(query="synthetic captcha"))
    assert isinstance(resp, SearchError)
    assert resp.code == "captcha"
    assert resp.retryable and resp.retry_after_s and resp.retry_after_s > 0


async def test_js_gate_is_an_error() -> None:
    resp = await client().search(SearchRequest(query="synthetic js gate"))
    assert isinstance(resp, SearchError) and resp.code == "js_gate"


async def test_no_results_is_a_valid_empty_response() -> None:
    resp = await client().search(SearchRequest(query="zxqv qwpl mmrt kkhh 9931"))
    assert isinstance(resp, SearchResponse)
    assert resp.meta.page_state is PageState.EMPTY_VALID and resp.organic == []


async def test_missing_fixture_is_backend_error() -> None:
    resp = await client().search(SearchRequest(query="not captured"))
    assert isinstance(resp, SearchError) and resp.code == "backend_unavailable" and not resp.retryable


async def test_cache_hit_is_flagged() -> None:
    c = client()
    first = await c.search(SearchRequest(query="wikipedia"))
    second = await c.search(SearchRequest(query="wikipedia"))
    assert isinstance(first, SearchResponse) and isinstance(second, SearchResponse)
    assert not first.meta.cache_hit and second.meta.cache_hit
    assert c.cache_stats().hits == 1


def test_build_search_url() -> None:
    url = build_search_url(
        SearchRequest(query="asyncio", site="python.org", page=3, lang="fr", country="us", time_range="w")
    )
    assert url == (
        "https://www.google.com/search?q=asyncio+site%3Apython.org&hl=fr&gl=us&start=20&tbs=qdr%3Aw"
    )


def test_request_validation() -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        SearchRequest(query="")
    with pytest.raises(ValidationError, match="Input should be 10"):
        SearchRequest(query="x", num=20)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="should match pattern"):
        SearchRequest(query="x", country="USA")


class _ResolvingFixtures(FixtureFetcher):
    async def resolve_links(self, links: list[str]) -> dict[str, str | None]:
        return {link: f"https://news.example/{i}" for i, link in enumerate(links)}


async def test_answer_box_links_are_resolved_like_results() -> None:
    req = SearchRequest(query="nobel prize 2026", resolve_links=True)
    resp = await SearchClient(_ResolvingFixtures(GECKO)).search(req)
    assert isinstance(resp, SearchResponse)
    stories = resp.answer_boxes[0]
    assert stories.title == "Top stories"
    assert all(str(x.url).startswith("https://news.example/") for x in stories.links)
    assert all(r.url is not None for r in resp.organic)


async def test_links_are_left_to_fetch_page_unless_asked() -> None:
    resp = await SearchClient(_ResolvingFixtures(GECKO)).search(SearchRequest(query="nobel prize 2026"))
    assert isinstance(resp, SearchResponse)
    assert all(r.url is None for r in resp.organic)  # no request to Google per link
    assert not resp.meta.warnings


async def test_read_top_brings_the_top_pages_in_the_same_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from gsearch_core import client as client_module
    from gsearch_parse.models import PageContent

    async def fetch_page(url: str, **_: object) -> PageContent:
        if "1" in url.rsplit("/", 1)[-1]:
            raise FetchError("network", "fetch failed: Timeout", retryable=True)
        return PageContent(url=url, title="T", text="x" * 9000, truncated=False)

    monkeypatch.setattr(client_module, "fetch_page", fetch_page)
    req = SearchRequest(query="nobel prize 2026", resolve_links=True, read_top=3)
    resp = await SearchClient(_ResolvingFixtures(GECKO)).search(req)
    assert isinstance(resp, SearchResponse)
    pages = [r.content for r in resp.organic]
    assert pages[0] is not None and len(pages[0].text) == 8000 and pages[0].truncated
    assert pages[1] is None and pages[2] is not None and pages[3:] == [None] * (len(pages) - 3)
    assert "result(s) 2 could not be read" in resp.meta.warnings
