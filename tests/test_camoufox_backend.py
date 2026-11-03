"""Live-backend contract tests against a LOCAL fake Google (never contacts the internet).

The browser test launches real headless Camoufox (~20 s). Skip with GSEARCH_SKIP_BROWSER=1.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import FIXTURES, fixture_by_id
from gsearch_parse.models import SearchError, SearchRequest, SearchResponse

camoufox_backend = pytest.importorskip("gsearch_core.camoufox_backend")
from gsearch_core.client import SearchClient  # noqa: E402
from gsearch_core.fetch import FetchError  # noqa: E402
from gsearch_core.geo import Place  # noqa: E402
from gsearch_core.pacing import PacingPolicy  # noqa: E402
from gsearch_core.state import StateStore  # noqa: E402

pytestmark = pytest.mark.anyio

# Real Google submits the textarea on Enter with JS; minimized fixtures carry no scripts, so add it back.
SUBMIT_ON_ENTER = (
    "<script>document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.name === 'q')"
    " { e.preventDefault(); e.target.form.submit(); } });</script>"
)
PAGE_HITS: list[dict[str, str]] = []  # path and headers of every homepage and /search request
HOME = (
    "<html><body><form action='/search' method='get'><textarea name='q'></textarea>"
    "<input type='hidden' name='hl' value='en'></form>" + SUBMIT_ON_ENTER + "</body></html>"
)
# Google's consent dialog, reduced to what the backend reads: Google's country guess and "Reject all".
CONSENT = (
    "<html><body><div role='dialog'><a href='https://consent.google.com/d?continue=https://www.google.com/"
    "&amp;gl=US&amp;m=0'>More options</a><button id='W0wltc' onclick=\"document.cookie='SOCS=rejected;"
    " path=/'; location.reload()\">Reject all</button></div></body></html>"
)


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def fake_google() -> Iterator[str]:
    # Minimized fixtures have no stylesheet, so blocks overlap and intercept clicks (live Google has its CSS).
    # This rule stands in for the missing layout: only links take pointer events.
    clickable = "<style>*{pointer-events:none!important} a, textarea{pointer-events:auto!important}</style>"
    # Like live Google: every results page links to the consent page from its account menu, with the
    # country Google placed the visitor in. That link is not the consent dialog.
    account_menu = (
        '<script>["https://consent.google.com/d?continue=https://www.google.com/\u0026gl=GB"]</script>'
    )
    serp = (
        fixture_by_id("c3-paa")
        .html()
        .replace("</body>", clickable + SUBMIT_ON_ENTER + account_menu + "</body>")
    )
    sorry = (FIXTURES / "synthetic" / "sorry-captcha.html").read_text(encoding="utf-8")

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
            elif (
                parts.path == "/search"
                and "consent" in q.get("q", [""])[0]
                and "SOCS=" not in str(self.headers.get("Cookie"))
            ):
                self._send(200, CONSENT)
            elif parts.path == "/sorry/index":
                self._send(429, sorry)
            elif parts.path == "/search":
                self._send(200, serp)
            else:
                self._send(404, "not found")

        def log_message(self, *args: object) -> None:
            pass

    google = _serve(Google)
    yield f"http://127.0.0.1:{google.server_address[1]}"
    google.shutdown()


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


async def test_block_just_before_midnight_still_rests_an_hour(tmp_path: Path) -> None:
    late = datetime.now().replace(hour=23, minute=58).timestamp()
    cfg = _config(tmp_path, "http://127.0.0.1:9")
    fetcher = camoufox_backend.CamoufoxFetcher(cfg, wall_clock=lambda: late)
    assert fetcher.record_block("captcha") == 3600
    await fetcher.aclose()


async def test_hourly_budget_refuses_instead_of_blocking(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "http://127.0.0.1:9", searches_per_hour=1.0, burst=1.0)
    other = StateStore(cfg.state_path)  # type: ignore[attr-defined]
    other.take_token("test-identity", 1 / 3600, 1.0)  # another process spends the only token
    other.close()
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)
    result = await SearchClient(fetcher).search(SearchRequest(query="anything"))
    assert isinstance(result, SearchError)
    assert result.code == "rate_limited"
    assert result.retry_after_s is not None and 3500 < result.retry_after_s <= 3600
    await fetcher.aclose()


@pytest.mark.skipif(os.environ.get("GSEARCH_SKIP_BROWSER") == "1", reason="GSEARCH_SKIP_BROWSER=1")
async def test_live_backend_against_local_fake_google(tmp_path: Path, fake_google: str) -> None:
    base_url = fake_google
    fetcher = camoufox_backend.CamoufoxFetcher(_config(tmp_path, base_url))
    client = SearchClient(fetcher)
    try:
        # 1. typed search in a warm session, results parsed
        resp = await client.search(SearchRequest(query="what is asyncio in python"))
        assert isinstance(resp, SearchResponse), resp
        assert resp.meta.backend == "camoufox"
        assert len(resp.organic) == 8
        assert fetcher._profile()["country"] == "US"  # saved at once, so other new devices can copy it

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
    tmp_path: Path, fake_google: str
) -> None:
    """entry="address_bar": no homepage; the first request is Firefox's own address-bar search URL with
    typed-navigation headers (no Referer); results parse as usual. Real-device preset on."""
    base_url = fake_google
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
        assert isinstance(resp, SearchResponse) and len(resp.organic) == 8, resp
        first = PAGE_HITS[0]
        assert first["path"] == "/search?client=firefox-b-d&q=what+is+asyncio+in+python", PAGE_HITS
        assert first.get("sec-fetch-site") == "none" and "referer" not in first
        assert not any(h["path"] == "/" or h["path"].startswith("/?") for h in PAGE_HITS)
    finally:
        await fetcher.aclose()


@pytest.mark.skipif(os.environ.get("GSEARCH_SKIP_BROWSER") == "1", reason="GSEARCH_SKIP_BROWSER=1")
async def test_consent_dialog_is_rejected_unless_google_places_the_exit_elsewhere(
    tmp_path: Path, fake_google: str
) -> None:
    base_url = fake_google
    cfg = dataclasses.replace(_config(tmp_path, base_url), entry="address_bar")  # type: ignore[type-var]
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)
    try:
        resp = await SearchClient(fetcher).search(SearchRequest(query="consent then results"))
        assert isinstance(resp, SearchResponse), resp
        assert len(resp.organic) == 8
    finally:
        await fetcher.aclose()

    elsewhere = Place(ip="127.0.0.1", country="FR", timezone="Europe/Paris", locale="fr-FR")
    fetcher = camoufox_backend.CamoufoxFetcher(
        dataclasses.replace(cfg, identity="fr-exit", place=elsewhere, persist_profile=False)
    )
    try:
        with pytest.raises(FetchError) as refused:
            await fetcher.fetch(SearchRequest(query="consent again"))
        assert refused.value.code == "consent_wall"
        assert "places the exit in US, not FR" in str(refused.value)
    finally:
        await fetcher.aclose()


def test_warm_up_pages_follow_the_exit_country_and_language() -> None:
    paris = Place(ip="203.0.113.8", country="FR", timezone="Europe/Paris", locale="fr-FR")
    urls = camoufox_backend.warmup_urls(camoufox_backend.WARMUP_PAGES, paris)
    assert urls[0] == "https://fr.wikipedia.org/wiki/Special:Random"
    assert urls[-1] == "https://news.google.com/home?hl=fr&gl=FR&ceid=FR:fr"
    assert not any("{" in u for u in urls)
    unlocated = camoufox_backend.warmup_urls(camoufox_backend.WARMUP_PAGES, None)
    assert unlocated[2] == "https://www.google.com/?hl=en"


@pytest.mark.skipif(os.environ.get("GSEARCH_SKIP_BROWSER") == "1", reason="GSEARCH_SKIP_BROWSER=1")
async def test_a_new_device_warms_up_before_its_first_search(tmp_path: Path, fake_google: str) -> None:
    base_url = fake_google
    cfg = dataclasses.replace(
        _config(tmp_path, base_url, warmup_dwell_min_s=0.01, warmup_dwell_max_s=0.02),  # type: ignore[type-var]
        identity="warmup-test",
        entry="address_bar",
        warmup=(f"{base_url}/?warm=1", f"{base_url}/no-such-page"),
    )
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)
    PAGE_HITS.clear()
    try:
        resp = await SearchClient(fetcher).search(SearchRequest(query="what is asyncio in python"))
        assert isinstance(resp, SearchResponse), resp
        assert [h["path"] for h in PAGE_HITS[:2]] == [
            "/?warm=1",
            "/search?client=firefox-b-d&q=what+is+asyncio+in+python",
        ]
        assert "referer" not in PAGE_HITS[1]  # still typed into the address bar, from a new tab
    finally:
        await fetcher.aclose()


@pytest.mark.skipif(os.environ.get("GSEARCH_SKIP_BROWSER") == "1", reason="GSEARCH_SKIP_BROWSER=1")
async def test_a_lasting_identity_keeps_its_device_across_launches(tmp_path: Path) -> None:
    place = Place(ip="203.0.113.7", country="US", timezone="America/Chicago", locale="en-US")
    cfg = dataclasses.replace(_config(tmp_path, "http://127.0.0.1:9"), fingerprint_preset=True, place=place)
    first = await camoufox_backend.CamoufoxFetcher(cfg)._same_device(
        camoufox_backend.device_options(cfg, place)
    )
    again = await camoufox_backend.CamoufoxFetcher(cfg)._same_device(
        camoufox_backend.device_options(cfg, place)
    )
    assert first["config"] == again["config"] and "webGl:renderer" in first["config"]
    assert "fingerprint_preset" not in first and first["locale"] == "en-US"
    assert not any(k.startswith(("timezone", "locale:", "geolocation:")) for k in first["config"])


@pytest.mark.skipif(os.environ.get("GSEARCH_SKIP_BROWSER") == "1", reason="GSEARCH_SKIP_BROWSER=1")
async def test_a_block_forgets_the_saved_device_and_cookies(tmp_path: Path) -> None:
    place = Place(ip="203.0.113.7", country="US", timezone="America/Chicago", locale="en-US")
    cfg = dataclasses.replace(_config(tmp_path, "http://127.0.0.1:9"), fingerprint_preset=True, place=place)
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)
    await fetcher._same_device(camoufox_backend.device_options(cfg, place))
    assert fetcher._profile_path.exists()
    fetcher.record_block("captcha")
    assert not fetcher._profile_path.exists()
    await fetcher.aclose()


async def test_a_new_device_copies_google_cookies_from_a_warm_one_in_its_country(tmp_path: Path) -> None:
    us = Place(ip="203.0.113.7", country="US", timezone="America/Chicago", locale="en-US")
    cfg = dataclasses.replace(
        _config(tmp_path, "http://127.0.0.1:9"), identity="proxy-new", share_cookies="proxy-*.json"
    )
    fetcher = camoufox_backend.CamoufoxFetcher(cfg)  # type: ignore[arg-type]
    fetcher._place = us
    later = time.time() + 86400
    profiles = tmp_path / "profiles"
    profiles.mkdir()

    def save(name: str, country: str, domain: str, expires: float) -> None:
        cookie = {"name": "NID", "value": name, "domain": domain, "expires": expires}
        (profiles / f"{name}.json").write_text(json.dumps({"cookies": [cookie], "country": country}))
        time.sleep(0.02)  # distinct save times

    save("proxy-warm", "US", ".google.com", later)
    save("proxy-gb", "GB", ".google.com", later)  # newer, another country
    save("proxy-expired", "US", ".google.com", time.time() - 1)
    save("proxy-other-sites", "US", ".bbc.com", later)
    save("home-direct", "US", ".google.com", later)  # the own IP's cookies never go to a proxy
    assert [c["value"] for c in fetcher._shared_cookies()] == ["proxy-warm"]

    save("proxy-new", "US", ".google.com", later)  # its own profile is no donor
    assert [c["value"] for c in fetcher._shared_cookies()] == ["proxy-warm"]
    off = camoufox_backend.CamoufoxFetcher(dataclasses.replace(cfg, share_cookies=None))  # type: ignore[type-var]
    off._place = us
    assert off._shared_cookies() == []
    await fetcher.aclose()
    await off.aclose()


async def test_behind_a_proxy_a_rest_belongs_to_the_exit_ip(tmp_path: Path) -> None:
    def fetcher(ip: str) -> Any:
        place = Place(ip=ip, country="US", timezone="America/Chicago", locale="en-US")
        cfg = dataclasses.replace(
            _config(tmp_path, "http://127.0.0.1:9"), proxy_url="http://127.0.0.1:1", place=place
        )
        return camoufox_backend.CamoufoxFetcher(cfg)

    blocked = fetcher("203.0.113.7")
    blocked.record_block("captcha")
    await blocked.aclose()
    same, new = fetcher("203.0.113.7"), fetcher("203.0.113.8")
    with pytest.raises(FetchError, match="resting"):
        await same.admit()
    await new.admit()
    await same.aclose()
    await new.aclose()


async def test_behind_a_proxy_two_ports_on_one_ip_share_its_budget(tmp_path: Path) -> None:
    place = Place(ip="203.0.113.7", country="US", timezone="America/Chicago", locale="en-US")

    def fetcher(identity: str) -> Any:
        cfg = _config(tmp_path, "http://127.0.0.1:9", searches_per_hour=1.0, burst=1.0)
        return camoufox_backend.CamoufoxFetcher(
            dataclasses.replace(cfg, identity=identity, proxy_url="http://127.0.0.1:1", place=place)  # type: ignore[type-var]
        )

    port_a, port_b = fetcher("proxy-a"), fetcher("proxy-b")
    await port_a.admit()
    with pytest.raises(FetchError, match="hourly budget"):
        await port_b.admit()
    await port_a.aclose()
    await port_b.aclose()


def test_a_device_without_webgl_data_is_drawn_again(monkeypatch: pytest.MonkeyPatch) -> None:
    draws = iter([ValueError('No WebGL data found for vendor "Google Inc. (Intel)"'), {"cpu": 8}])

    def launch_options(**_: object) -> dict[str, Any]:
        draw = next(draws)
        if isinstance(draw, Exception):
            raise draw
        return {"env": {"CAMOU_CONFIG_1": json.dumps(draw)}}

    monkeypatch.setattr(camoufox_backend, "launch_options", launch_options)
    assert camoufox_backend.device_config({"headless": True}) == {"cpu": 8}
