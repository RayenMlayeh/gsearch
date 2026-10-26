"""Hybrid tier logic, offline: a stand-in browser plus a local fake Google over plain HTTP.

Checks what the HTTP requests carry (browser-copied headers in order, same-origin click semantics, cookies),
that /goto is resolved from its 302 without contacting the destination, and that a gate or a block sends work
back to the browser / rests the identity. The real-browser path is covered by test_camoufox_backend.py.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import FIXTURES, fixture_by_id
from gsearch_parse.models import SearchError, SearchRequest, SearchResponse

hybrid = pytest.importorskip("gsearch_core.hybrid")
from gsearch_core.client import SearchClient  # noqa: E402
from gsearch_core.fetch import FetchedPage  # noqa: E402

pytestmark = pytest.mark.anyio

BROWSER_HEADERS = {  # order and values as the browser's own document request
    "host": "x",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "ar-TN,ar;q=0.9",
    "accept-encoding": "gzip, deflate, br, zstd",
    "referer": "https://www.google.com/",
    "alt-used": "www.google.com",
    "connection": "keep-alive",
    "cookie": "SHOULD_BE_REBUILT",
    "upgrade-insecure-requests": "1",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
}


@dataclass
class Seen:
    requests: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    destination_hits: int = 0


@pytest.fixture
def fake() -> Iterator[tuple[str, Seen]]:
    seen = Seen()
    serp = fixture_by_id("c3-paa").html()
    gate = (FIXTURES / "synthetic" / "js-gate.html").read_text(encoding="utf-8")

    class Google(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.requests.append((self.path, list(self.headers.items())))
            parts = urlsplit(self.path)
            q = parse_qs(parts.query).get("q", [""])[0]
            if parts.path == "/goto":
                blob = parse_qs(parts.query)["url"][0]
                self.send_response(302)
                self.send_header("Location", f"https://destination.example/{blob}")
                self.end_headers()
                return
            if parts.path == "/search" and q == "gate me":
                body, status = gate, 200
            elif parts.path == "/search" and q == "block me":
                self.send_response(302)
                self.send_header("Location", "/sorry/index?continue=/search")
                self.end_headers()
                return
            else:
                body, status = serp, 200
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "NID=rotated; Path=/")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Google)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


@dataclass
class FakeConfig:
    base_url: str
    proxy_url: str | None = None


class FakeBrowser:
    """Stands in for CamoufoxFetcher: same public surface, no real browser."""

    def __init__(self, base_url: str) -> None:
        self.config = FakeConfig(base_url)
        self.fetches = 0
        self.searches = 0
        self.resolves = 0
        self.blocks: list[str] = []
        self.parked = 0

    async def fetch(self, req: SearchRequest) -> FetchedPage:
        self.fetches += 1
        self.searches += 1
        return FetchedPage(
            url=f"{self.config.base_url}/search?q=x",
            status=200,
            html=fixture_by_id("c3-paa").html(),
            engine="gecko",
            backend="camoufox",
        )

    async def export_session(self) -> dict[str, Any]:
        cookies = [
            {"name": "AEC", "value": "a1"},
            {"name": "NID", "value": "n1"},
            {"name": "DV", "value": "d1"},
        ]
        return {"cookies": cookies, "headers": dict(BROWSER_HEADERS), "hl": "ar"}

    async def admit(self) -> None:
        return None

    def record_search(self) -> None:
        self.searches += 1

    def record_resolve(self) -> None:
        self.resolves += 1

    def record_block(self, reason: str) -> int:
        self.blocks.append(reason)
        return 3600

    def note_used(self) -> None:
        return None

    async def close_session(self) -> None:
        self.parked += 1

    async def resolve_links(self, links: list[str]) -> dict[str, str | None]:
        return dict.fromkeys(links)

    def status(self) -> dict[str, Any]:
        return {"identity": "fake"}

    async def aclose(self) -> None:
        return None


def _names(headers: list[tuple[str, str]]) -> list[str]:
    return [k.lower() for k, _ in headers]


async def test_first_search_mints_in_browser_then_http_with_browser_headers(fake: tuple[str, Seen]) -> None:
    base, seen = fake
    browser = FakeBrowser(base)
    fetcher = hybrid.HybridFetcher(browser)
    client = SearchClient(fetcher)
    try:
        first = await client.search(SearchRequest(query="what is asyncio in python"))
        assert isinstance(first, SearchResponse) and first.meta.backend == "camoufox"
        assert browser.fetches == 1

        second = await client.search(SearchRequest(query="python asyncio tutorial"))
        assert isinstance(second, SearchResponse), second
        assert second.meta.backend == "hybrid-http"
        assert browser.fetches == 1  # no browser involved in the second search
        assert browser.parked == 1  # browser closed once its session was adopted

        search_reqs = [(p, h) for p, h in seen.requests if p.startswith("/search")]
        path, headers = search_reqs[-1]
        assert "hl=ar" in path  # the browser session's language carries over
        got = dict((k.lower(), v) for k, v in headers)
        trail = [(p[:30], dict((k.lower(), v) for k, v in h).get("cookie")) for p, h in seen.requests]
        assert "cookie" in got, trail
        assert got["user-agent"] == BROWSER_HEADERS["user-agent"]
        assert got["accept-language"] == "ar-TN,ar;q=0.9"
        assert got["sec-fetch-site"] == "same-origin" and got["sec-fetch-user"] == "?1"
        assert got["referer"] == "https://www.google.com/"
        assert got["cookie"].startswith("AEC=a1; NID=")
        assert "alt-used" not in got and "SHOULD_BE_REBUILT" not in got["cookie"]
        order = [n for n in _names(headers) if n in BROWSER_HEADERS]
        expected = [
            "host",
            "user-agent",
            "accept",
            "accept-language",
            "accept-encoding",
            "referer",
            "cookie",
            "upgrade-insecure-requests",
            "sec-fetch-dest",
            "sec-fetch-mode",
            "sec-fetch-site",
            "sec-fetch-user",
        ]
        assert [n for n in order if n != "connection"] == expected
    finally:
        await fetcher.aclose()


async def test_cookies_rotate_from_set_cookie(fake: tuple[str, Seen]) -> None:
    base, seen = fake
    fetcher = hybrid.HybridFetcher(FakeBrowser(base))
    client = SearchClient(fetcher)
    try:
        await client.search(SearchRequest(query="one"))
        await client.search(SearchRequest(query="two"))  # HTTP: server rotates NID
        await client.search(SearchRequest(query="three"))  # must send the rotated value
        trail = [(path[:30], dict((k.lower(), v) for k, v in h).get("cookie")) for path, h in seen.requests]
        _, headers = [r for r in seen.requests if r[0].startswith("/search")][-1]
        assert "NID=rotated" in dict((k.lower(), v) for k, v in headers).get("cookie", ""), trail
    finally:
        await fetcher.aclose()


async def test_result_urls_resolved_over_http_from_302(fake: tuple[str, Seen]) -> None:
    base, seen = fake
    browser = FakeBrowser(base)
    fetcher = hybrid.HybridFetcher(browser)
    client = SearchClient(fetcher)
    try:
        resp = await client.search(SearchRequest(query="what is asyncio in python"))
        assert isinstance(resp, SearchResponse)
        assert all(str(r.url).startswith("https://destination.example/FIXTURE_") for r in resp.organic)
        assert browser.resolves == len(resp.organic) + sum(len(b.links) for b in resp.answer_boxes)
        assert all(x.url is not None for b in resp.answer_boxes for x in b.links)
        assert seen.destination_hits == 0  # Location was read, never followed
    finally:
        await fetcher.aclose()


async def test_gate_over_http_remints_in_browser(fake: tuple[str, Seen]) -> None:
    base, _ = fake
    browser = FakeBrowser(base)
    fetcher = hybrid.HybridFetcher(browser)
    client = SearchClient(fetcher)
    try:
        await client.search(SearchRequest(query="first"))
        resp = await client.search(SearchRequest(query="gate me"))
        assert isinstance(resp, SearchResponse)  # served by the browser fallback
        assert browser.fetches == 2
        assert fetcher.stats["remints"] == 1
    finally:
        await fetcher.aclose()


async def test_block_over_http_rests_identity_and_drops_session(fake: tuple[str, Seen]) -> None:
    base, _ = fake
    browser = FakeBrowser(base)
    fetcher = hybrid.HybridFetcher(browser)
    client = SearchClient(fetcher)
    try:
        await client.search(SearchRequest(query="first"))
        resp = await client.search(SearchRequest(query="block me"))
        assert isinstance(resp, SearchError) and resp.code == "captcha"
        assert resp.retry_after_s == 3600
        assert browser.blocks == ["captcha (http)"]
        assert fetcher.status()["http_session"] is False
    finally:
        await fetcher.aclose()
