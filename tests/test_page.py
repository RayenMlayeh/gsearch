"""Page fetching: readable text from HTML (offline), and the fetch itself against a local server."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from gsearch_core import page
from gsearch_core.fetch import FetchError
from gsearch_parse.page import readable

pytestmark = pytest.mark.anyio

ARTICLE = (
    "<html><head><title>Asyncio</title><script>track()</script></head><body>"
    "<nav><a href='/menu'>Menu</a></nav>"
    "<main><h1>What is asyncio?</h1><p>A library for <a href='/coro#top'>coroutines</a> and\n  tasks.</p>"
    "<ul><li>One</li><li>Two</li></ul><a href='https://other.example/x'>Elsewhere</a></main>"
    "<footer>Copyright</footer></body></html>"
)


def test_readable_keeps_main_text_in_blocks_and_absolute_links() -> None:
    content = readable(ARTICLE, url="https://docs.example/asyncio")
    assert content.title == "Asyncio"
    assert content.text == "What is asyncio?\nA library for coroutines and tasks.\nOne\nTwo\nElsewhere"
    assert [(link.text, str(link.url)) for link in content.links] == [
        ("coroutines", "https://docs.example/coro"),
        ("Elsewhere", "https://other.example/x"),
    ]
    assert not content.truncated


def test_readable_uses_aria_roles() -> None:
    html = (
        "<body><div role='navigation'>index | modules</div><div role='main'><p>Body</p></div>"
        "<div class='footer'>x</div></body>"
    )
    assert readable(html, url="https://docs.example/").text == "Body"


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://localhost:8080/", "http://10.0.0.1/", "http://[::1]/", "file:///etc/hosts"],
)
async def test_refuses_anything_but_public_http(url: str) -> None:
    with pytest.raises(FetchError) as err:
        await page.fetch_page(url)
    assert err.value.code == "invalid_request"


class _Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/old":
            self.send_response(301)
            self.send_header("Location", "/article")
            self.end_headers()
            return
        body, kind, status = {
            "/article": (ARTICLE.encode(), "text/html; charset=utf-8", 200),
            "/notes.txt": (b"a < b", "text/plain", 200),
            "/file.zip": (b"PK", "application/zip", 200),
            "/big": (b"<p>" + b"x" * 6_000_000 + b"</p>", "text/html", 200),
        }.get(self.path, (b"", "text/html", 404))
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    async def local(host: str) -> str:
        return "127.0.0.1"

    monkeypatch.setattr(page, "public_address", local)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://site.example:{server.server_port}"
    server.shutdown()


async def test_fetches_follows_redirects_and_reads_text(site: str) -> None:
    content = await page.fetch_page(f"{site}/old")
    assert str(content.url) == f"{site}/article"
    assert content.title == "Asyncio" and "coroutines and tasks." in content.text
    assert (await page.fetch_page(f"{site}/notes.txt")).text == "a < b"


async def test_refuses_other_content_and_reports_http_errors(site: str) -> None:
    with pytest.raises(FetchError) as err:
        await page.fetch_page(f"{site}/file.zip")
    assert err.value.code == "invalid_request"
    with pytest.raises(FetchError) as err:
        await page.fetch_page(f"{site}/missing")
    assert err.value.code == "network" and "404" in str(err.value)


async def test_big_pages_are_cut(site: str) -> None:
    content = await page.fetch_page(f"{site}/big")
    assert content.truncated and len(content.text) == 30_000
