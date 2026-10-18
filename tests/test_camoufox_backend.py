"""Live-backend contract tests against a LOCAL fake Google (never contacts the internet).

The browser test launches real headless Camoufox (~20 s). Skip with GSEARCH_SKIP_BROWSER=1.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import FIXTURES, fixture_by_id
from gsearch_parse.models import SearchError, SearchRequest, SearchResponse

camoufox_backend = pytest.importorskip("gsearch_core.camoufox_backend")
from gsearch_core.client import SearchClient  # noqa: E402
from gsearch_core.geo import Place  # noqa: E402
from gsearch_core.pacing import PacingPolicy  # noqa: E402
from gsearch_core.state import StateStore  # noqa: E402

pytestmark = pytest.mark.anyio

# Real Google submits the textarea on Enter with JS; minimized fixtures carry no scripts, so add it back.
SUBMIT_ON_ENTER = (
    "<script>document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.name === 'q')"
    " { e.preventDefault(); e.target.form.submit(); } });</script>"
)
ASSET_HITS: list[str] = []  # every request the destination site receives; resolution must cause none
GOTO_HEADERS: list[dict[str, str]] = []  # request headers of every /goto, to check click semantics
PAGE_HITS: list[dict[str, str]] = []  # path and headers of every homepage and /search request
HOME = (
    "<html><body><form action='/search' method='get'><textarea name='q'></textarea>"
    "<input type='hidden' name='hl' value='en'></form>" + SUBMIT_ON_ENTER + "</body></html>"
)


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def fake_google() -> Iterator[tuple[str, str]]:
    # Minimized fixtures have no stylesheet, so blocks overlap and intercept clicks (live Google has its CSS).
    # This rule stands in for the missing layout: only links take pointer events.
    clickable = "<style>*{pointer-events:none!important} a, textarea{pointer-events:auto!important}</style>"
    # Like live Google: on mousedown, a /goto href is rewritten to /url?sa=t&...&url=<blob>.
    rewrite = (
        "<script>document.addEventListener('mousedown', e => { const a = e.target.closest('a');"
        " if (a && a.getAttribute('href').startsWith('/goto?')) { const blob = new URL(a.href).searchParams"
        ".get('url'); a.setAttribute('href', '/url?sa=t&source=web&url=' + blob + '&ved=X'); } },"
        " true);</script>"
    )
    serp = (
        fixture_by_id("c3-paa").html().replace("</body>", clickable + rewrite + SUBMIT_ON_ENTER + "</body>")
    )
    sorry = (FIXTURES / "synthetic" / "sorry-captcha.html").read_text(encoding="utf-8")

    class Landing(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ASSET_HITS.append(self.path)  # any request at all to the destination site
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<html><body>landing<img src='/asset.png'><script src='/asset.js'></script></body></html>"
            )

        def log_message(self, *args: object) -> None:
            pass

    landing = _serve(Landing)
    landing_base = f"http://127.0.0.1:{landing.server_address[1]}"

    class Google(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str = "", location: str | None = None) -> None:
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            q = parse_qs(parts.query)
            if parts.path in ("/", "/search"):
                PAGE_HITS.append({"path": self.path, **{k.lower(): v for k, v in self.headers.items()}})
            if parts.path == "/":
                self._send(200, HOME)
            elif parts.path == "/search" and q.get("q", [""])[0].startswith("trigger captcha"):
                self._send(302, location="/sorry/index?continue=/search")
            elif parts.path == "/sorry/index":
                self._send(429, sorry)
            elif parts.path == "/search":
                self._send(200, serp)
            elif parts.path in ("/goto", "/url"):
                GOTO_HEADERS.append({"path": self.path, **{k.lower(): v for k, v in self.headers.items()}})
                dest = f"{landing_base}/landing/{q['url'][0]}"
                if parts.path == "/goto":  # DOM href: plain server redirect
                    self._send(302, location=dest)
                else:  # clicked, rewritten form: 200 interstitial like live Google
                    self._send(
                        200,
                        f'<html><head><noscript><meta content="0;url={dest}"'
                        ' http-equiv="refresh"></noscript></head></html>',
                    )
            else:
                self._send(404, "not found")

        def log_message(self, *args: object) -> None:
            pass

    google = _serve(Google)
    yield f"http://127.0.0.1:{google.server_address[1]}", landing_base
    google.shutdown()
    landing.shutdown()


def _config(tmp_path: Path, base_url: str, **policy: float | int | str) -> object:
    return camoufox_backend.CamoufoxConfig(
        identity="test-identity",
        base_url=base_url,
        place=Place(ip="127.0.0.1", country="US", timezone="America/New_York", locale="en-US"),
        geoip=False,
        humanize=False,
        policy=PacingPolicy(
            **{
                "searches_per_hour": 3600.0,
                "burst": 5.0,
                "think_min_s": 0.01,
                "think_max_s": 0.02,
                "resolve_gap_min_s": 0.01,
                "resolve_gap_max_s": 0.02,
                **policy,
            }  # type: ignore[arg-type]
        ),
        state_path=tmp_path / "state.sqlite",
        evidence_dir=tmp_path / "evidence",
    )


async def test_resting_identity_is_refused_before_any_browser_work(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "http://127.0.0.1:9")  # unroutable on purpose: must never be contacted
    store = StateStore(cfg.state_path)  # type: ignore[attr-defined]
    store.record_captcha("test-identity", rest_until=time.time() + 3600, reason="captcha")
    store.close()
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)
    result = await SearchClient(fetcher).search(SearchRequest(query="anything"))
    assert isinstance(result, SearchError)
    assert result.code == "captcha"
    assert result.retry_after_s is not None and 3500 < result.retry_after_s <= 3600
    assert fetcher.status()["session_open"] is False
    await fetcher.aclose()


async def test_daily_cap_is_refused_before_any_browser_work(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "http://127.0.0.1:9", searches_per_day=1)
    store = StateStore(cfg.state_path)  # type: ignore[attr-defined]
    store.record_search("test-identity")
    store.close()
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)
    result = await SearchClient(fetcher).search(SearchRequest(query="anything"))
    assert isinstance(result, SearchError)
    assert result.code == "rate_limited"
    assert result.retry_after_s is not None and result.retry_after_s > 0
    await fetcher.aclose()


async def test_hourly_budget_refuses_instead_of_blocking(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "http://127.0.0.1:9", searches_per_hour=1.0, burst=1.0)
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)
    fetcher._bucket.take()  # spend the only token
    result = await SearchClient(fetcher).search(SearchRequest(query="anything"))
    assert isinstance(result, SearchError)
    assert result.code == "rate_limited"
    assert result.retry_after_s is not None and 3500 < result.retry_after_s <= 3600
    await fetcher.aclose()


@pytest.mark.skipif(os.environ.get("GSEARCH_SKIP_BROWSER") == "1", reason="GSEARCH_SKIP_BROWSER=1")
async def test_live_backend_against_local_fake_google(tmp_path: Path, fake_google: tuple[str, str]) -> None:
    base_url, landing_base = fake_google
    fetcher = camoufox_backend.CamoufoxFetcher(_config(tmp_path, base_url))
    client = SearchClient(fetcher)
    try:
        # 1. typed search in a warm session, results parsed, every /goto resolved from its 302
        resp = await client.search(SearchRequest(query="what is asyncio in python"))
        assert isinstance(resp, SearchResponse), resp
        assert resp.meta.backend == "camoufox"
        assert len(resp.organic) == 8
        for r in resp.organic:
            assert r.url is not None
            assert str(r.url).startswith(f"{landing_base}/landing/FIXTURE_")
        assert fetcher.status()["resolves_today"] == 8
        assert ASSET_HITS == []  # the destination site was never contacted (interstitial navigation aborted)
        # Real-click semantics: same-origin, user-activated navigation with the results page as
        # referrer; never the scripted "Sec-Fetch-Site: none + Referer" combination.
        assert len(GOTO_HEADERS) == 8
        assert all(h["path"].startswith("/url?sa=t") for h in GOTO_HEADERS)  # the rewritten form was followed
        for h in GOTO_HEADERS:
            assert h.get("sec-fetch-site") == "same-origin", h
            assert h.get("sec-fetch-mode") == "navigate"
            assert h.get("sec-fetch-user") == "?1"
            assert h.get("referer", "").startswith(base_url + "/search")

        # 2. a CAPTCHA rests the identity and closes the session (next one is a fresh device)
        blocked = await client.search(SearchRequest(query="trigger captcha"))
        assert isinstance(blocked, SearchError)
        assert blocked.code == "captcha"
        assert blocked.retry_after_s is not None and blocked.retry_after_s > 0
        assert blocked.evidence_ref is not None and Path(blocked.evidence_ref, "page.png").exists()
        status = fetcher.status()
        assert status["session_open"] is False
        assert status["captchas_today"] == 1

        # 3. while resting, searches are refused without touching the browser
        again = await client.search(SearchRequest(query="something else"))
        assert isinstance(again, SearchError) and again.code == "captcha"
        assert fetcher.status()["searches_today"] == 2
    finally:
        await fetcher.aclose()


@pytest.mark.skipif(os.environ.get("GSEARCH_SKIP_BROWSER") == "1", reason="GSEARCH_SKIP_BROWSER=1")
async def test_address_bar_entry_searches_like_a_typed_firefox_query(
    tmp_path: Path, fake_google: tuple[str, str]
) -> None:
    """entry="address_bar": no homepage; the first request is Firefox's own address-bar search URL with
    typed-navigation headers (no Referer); results and links work as usual. Real-device preset on."""
    base_url, landing_base = fake_google
    cfg = dataclasses.replace(
        _config(tmp_path, base_url),  # type: ignore[type-var]
        identity="address-bar-test",
        entry="address_bar",
        fingerprint_preset=True,
    )
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)
    PAGE_HITS.clear()
    try:
        resp = await SearchClient(fetcher).search(SearchRequest(query="what is asyncio in python"))
        assert isinstance(resp, SearchResponse), resp
        assert all(str(r.url).startswith(f"{landing_base}/landing/") for r in resp.organic)
        first = PAGE_HITS[0]
        assert first["path"] == "/search?client=firefox-b-d&q=what+is+asyncio+in+python", PAGE_HITS
        assert first.get("sec-fetch-site") == "none" and "referer" not in first
        assert not any(h["path"] == "/" or h["path"].startswith("/?") for h in PAGE_HITS)
    finally:
        await fetcher.aclose()
