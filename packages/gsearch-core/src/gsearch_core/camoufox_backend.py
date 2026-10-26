"""Live backend: one Camoufox session per identity.

- Google's JS gate runs once per session; later searches in the session are a single request.
- uBlock Origin is excluded: Camoufox loads it by default and it removes the ads from the page.
- Locale and geolocation come from the exit IP, so the device matches where it connects from.
- Result links are resolved by real clicks in the same session, read from the redirect.
- Budget, daily cap and CAPTCHA rest are checked before any browser work and persist across restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import math
import os
import random
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlsplit

from browserforge.fingerprints import Screen
from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox
from gsearch_parse.classify import classify
from gsearch_parse.models import PageState, SearchRequest
from gsearch_parse.redirect import redirect_target

from .fetch import FetchedPage, FetchError, build_search_url
from .geo import Place, locate_exit
from .pacing import PacingPolicy, TokenBucket, lognormal_between
from .state import StateStore

log = logging.getLogger(__name__)

GOOGLE_ORIGIN = "https://www.google.com"
SEARCH_BOX = 'textarea[name="q"], input[name="q"]'
RESULTS_READY_JS = (
    "() => !!document.querySelector('#search') || location.pathname.startsWith('/sorry')"
    " || !!document.querySelector('form#captcha-form')"
)
CLICK_TIMEOUT_MS = 5_000
# What Firefox on Windows sends when a query is typed into the address bar with Google as search engine:
# a typed navigation (Sec-Fetch-Site: none, no Referer), with no homepage visit before it.
ADDRESS_BAR_SEARCH = "{base}/search?client=firefox-b-d&q={q}"
_WINDOWS = ((1536, 864), (1920, 1080), (1440, 900), (1366, 768))
_BLOCKS = (PageState.CAPTCHA, PageState.HARD_BLOCK)
# Google's EU consent dialog links to consent.google.com; its continue URL carries Google's own view of the
# visitor's country, which can differ from what IP databases say about the exit.
_CONSENT = re.compile(
    r"consent\.google\.com/(?:save|d)(?:\?|%3F)continue[^\"'\s]*?gl(?:=|\\x3d|%3D)([A-Z]{2})"
)


def consent_country(html: str) -> str | None:
    """Country Google assigned to the visitor when it serves its consent dialog, else None."""
    match = _CONSENT.search(html)
    return match.group(1) if match else None


def redirect_blob(url: str) -> str | None:
    """The opaque `url=` value of a Google result redirect, for both forms it takes.

    The DOM serves `/goto?url=<blob>`; on a real click Google's JS rewrites it to
    `/url?sa=t&source=web&rct=j&opi=...&url=<blob>&...` before navigating.
    """
    parts = urlsplit(url)
    if parts.path not in ("/goto", "/url"):
        return None
    values = parse_qs(parts.query).get("url")
    return values[0] if values else None


def device_options(cfg: CamoufoxConfig, place: Place | None) -> dict[str, Any]:
    """Camoufox launch options for an identity, shared by the backend and the identity probe."""
    headless: bool | str = cfg.headless
    if not headless and sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        headless = "virtual"  # a server without a screen: the headed browser runs on Xvfb
    options: dict[str, Any] = {
        "headless": headless,
        "os": "windows",
        # WebRTC's UDP does not go through an HTTP proxy: left on, it could reveal the machine's own IP to
        # the page and tie every identity together.
        "block_webrtc": True,
        "exclude_addons": [DefaultAddons.UBO],
        "humanize": cfg.humanize,
    }
    if cfg.fingerprint_preset:
        # A real device bundled with Camoufox (screen, cores, GPU coherent by construction). The preset
        # decides the geometry: forcing our window onto it could describe an impossible device.
        options["fingerprint_preset"] = True
    else:
        options["screen"] = Screen(min_width=1440, max_width=1920, min_height=900, max_height=1080)
        options["window"] = window_for(cfg.identity)
    if place is not None:
        options["locale"] = place.locale or "en-US"
        if cfg.geoip:
            options["geoip"] = place.ip
    if cfg.proxy_url:
        options["proxy"] = {"server": cfg.proxy_url}
    return options


def window_for(identity: str) -> tuple[int, int]:
    """Browser window size, stable per identity name."""
    digest = hashlib.blake2b(identity.encode(), digest_size=2).digest()
    return _WINDOWS[int.from_bytes(digest, "big") % len(_WINDOWS)]


def state_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "gsearch"


@dataclass(frozen=True, slots=True)
class CamoufoxConfig:
    identity: str = "home-direct"
    headless: bool = True
    base_url: str = GOOGLE_ORIGIN
    proxy_url: str | None = None
    place: Place | None = None  # precomputed place (tests); otherwise looked up from the exit IP
    geoip: bool = True
    humanize: bool = True
    idle_close_s: float = 600.0
    nav_timeout_ms: int = 30_000
    results_timeout_ms: int = 45_000
    settle_timeout_ms: int = 10_000  # waiting for `load` beyond this is skipped, never an error
    resolve_timeout_s: float = 10.0  # per link; a stuck link must never stall the whole search
    policy: PacingPolicy = field(default_factory=PacingPolicy)
    state_path: Path = field(default_factory=lambda: state_dir() / "state.sqlite")
    evidence_dir: Path = field(default_factory=lambda: state_dir() / "evidence")
    # How a session reaches Google: "homepage" (open google.com, type into its box) or "address_bar" (type
    # the query into Firefox's address bar, the usual way Firefox users search; no homepage load).
    entry: str = "homepage"
    fingerprint_preset: bool = False  # real-device fingerprints bundled with Camoufox instead of synthetic


class CamoufoxFetcher:
    name = "camoufox"

    def __init__(
        self,
        config: CamoufoxConfig,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self._wall = wall_clock
        self._rng = rng or random.Random()  # nosec B311 - pacing jitter, not security
        p = config.policy
        self._bucket = TokenBucket(p.searches_per_hour / 3600.0, p.burst, monotonic)
        self._state = StateStore(config.state_path, wall_clock)
        self._lock = asyncio.Lock()
        self._camoufox: Any = None
        self._context: Any = None
        self._page: Any = None
        self._place: Place | None = None
        self._session_hl: str | None = None
        self._last_used = 0.0
        self._searches_in_session = 0
        self._last_doc_status: int | None = None
        self._last_doc_request: Any = None

    # ------------------------------------------------------------------ budget
    def _tomorrow_epoch(self) -> float:
        """Local midnight after now."""
        tomorrow = datetime.fromtimestamp(self._wall()) + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    async def admit(self) -> None:
        """Refuse before any browser work when the identity has no budget. Raises FetchError."""
        ident, policy = self.config.identity, self.config.policy
        resting = self._state.resting_until(ident)
        if resting is not None:
            until, reason = resting
            raise FetchError(
                "captcha",
                f"identity {ident} is resting after {reason}",
                retryable=True,
                retry_after_s=math.ceil(until - self._wall()),
            )
        if self._state.searches_today(ident) >= policy.searches_per_day:
            raise FetchError(
                "rate_limited",
                f"daily budget of {policy.searches_per_day} searches used",
                retryable=True,
                retry_after_s=math.ceil(self._tomorrow_epoch() - self._wall()),
            )
        wait = self._bucket.wait_s()
        if wait > 0:
            raise FetchError(
                "rate_limited",
                f"hourly budget of {policy.searches_per_hour:g} searches used",
                retryable=True,
                retry_after_s=math.ceil(wait),
            )
        self._bucket.take()  # the token is spent on admission, even if the search later fails

    # ----------------------------------------------------------------- session
    async def _ensure_session(self, hl_hint: str | None) -> None:
        idle = time.monotonic() - self._last_used
        if self._page is not None and idle < self.config.idle_close_s:
            return
        await self._close_session()
        cfg = self.config
        place = cfg.place or (await locate_exit(cfg.proxy_url) if cfg.geoip else None)
        self._place = place
        browser = await self._launch(cfg, place)
        self._context = await browser.new_context()
        self._page = await self._context.new_page()
        self._page.on("response", self._on_response)
        self._page.on("request", self._on_request)
        self._session_hl = hl_hint or (place.language if place else "en")
        self._searches_in_session = 0
        if cfg.entry == "address_bar":
            # A person opens the browser and types a query: nothing loads before the search itself.
            await asyncio.sleep(lognormal_between(self._rng, 1.5, 4.0))
            return
        await self._page.goto(
            f"{cfg.base_url}/?hl={self._session_hl}",
            wait_until="domcontentloaded",
            timeout=cfg.nav_timeout_ms,
        )
        await self._refuse_consent_region()
        await self._settle()
        await asyncio.sleep(lognormal_between(self._rng, 1.5, 4.0))

    async def _launch(self, cfg: CamoufoxConfig, place: Place | None) -> Any:
        """Start the browser. A real-device preset can name a GPU that Camoufox has no WebGL data for
        (observed: Intel Arc A750); that preset is skipped and another one drawn."""
        for attempt in range(3):
            self._camoufox = AsyncCamoufox(**device_options(cfg, place))  # type: ignore[no-untyped-call]
            try:
                return await self._camoufox.__aenter__()
            except ValueError as exc:
                if not cfg.fingerprint_preset or "WebGL" not in str(exc) or attempt == 2:
                    raise
                log.info("preset_redrawn reason=%s", str(exc)[:120])
        raise AssertionError("unreachable")

    async def _refuse_consent_region(self) -> None:
        """Raise consent_wall when Google serves its consent dialog: Google places this exit in a consent
        region, so the identity cannot serve its market. Checked before the session spends its bytes; the
        pool draws another exit."""
        gl = consent_country(await self._page.content())
        if gl is not None or "consent.google." in self._page.url:
            await self._close_session()
            raise FetchError(
                "consent_wall",
                f"Google served its consent dialog (Google places the exit in {gl or 'a consent region'})",
                retryable=True,
                retry_after_s=0,
            )

    async def _settle(self) -> None:
        """Give the page time to finish loading, without making that a failure. Through a residential exit
        a page can stay short of `load` for longer than any useful timeout (observed: a CAPTCHA page whose
        widget never finished, reported as a network error instead of the CAPTCHA it was)."""
        with contextlib.suppress(Exception):
            await self._page.wait_for_load_state("load", timeout=self.config.settle_timeout_ms)

    def _on_request(self, request: Any) -> None:
        with contextlib.suppress(Exception):  # a listener must never break navigation
            if request.resource_type == "document" and request.frame == self._page.main_frame:
                self._last_doc_request = request

    def _on_response(self, response: Any) -> None:
        with contextlib.suppress(Exception):  # a listener must never break navigation
            if response.request.resource_type == "document" and response.frame == self._page.main_frame:
                self._last_doc_status = response.status

    async def _close_session(self) -> None:
        if self._context is not None:
            with contextlib.suppress(Exception):  # teardown must not mask the real error
                await self._context.close()
        if self._camoufox is not None:
            with contextlib.suppress(Exception):  # teardown must not mask the real error
                await self._camoufox.__aexit__(None, None, None)
        self._camoufox = self._context = self._page = None

    async def aclose(self) -> None:
        async with self._lock:
            await self._close_session()
            self._state.close()

    # ------------------------------------------------------------------ search
    def _needs_url_navigation(self, req: SearchRequest) -> bool:
        return bool(
            req.page > 1 or req.time_range or req.country or (req.lang and req.lang != self._session_hl)
        )

    async def _run_search(self, req: SearchRequest) -> tuple[str, str, int | None]:
        page, cfg = self._page, self.config
        self._last_doc_status = None
        typed = not page.url.startswith("http")  # nothing loaded yet (address-bar entry)
        if typed and not self._needs_url_navigation(req):
            url = ADDRESS_BAR_SEARCH.format(base=cfg.base_url, q=quote_plus(req.effective_query()))
            await page.goto(url, wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms)
            await self._refuse_consent_region()
        elif self._needs_url_navigation(req):
            url = build_search_url(req.model_copy(update={"lang": req.lang or self._session_hl}))
            url = url.replace(GOOGLE_ORIGIN, cfg.base_url, 1)
            # A typed URL has no Referer; a URL opened from the results page has that page as referrer.
            nav: dict[str, Any] = {} if typed else {"referer": page.url}
            await page.goto(url, wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms, **nav)
        else:
            await page.mouse.wheel(0, -20000)  # the search box is not sticky
            await asyncio.sleep(lognormal_between(self._rng, 0.5, 1.2))
            await page.locator(SEARCH_BOX).first.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(req.effective_query(), delay=self._rng.randint(60, 150))
            await asyncio.sleep(lognormal_between(self._rng, 0.3, 1.0))
            await page.keyboard.press("Enter")
        await page.wait_for_function(RESULTS_READY_JS, timeout=cfg.results_timeout_ms)
        await self._settle()
        await asyncio.sleep(lognormal_between(self._rng, 1.2, 2.8))
        await page.mouse.wheel(0, self._rng.randint(600, 1800))
        return await page.content(), page.url, self._last_doc_status

    async def _save_evidence(self, label: str) -> str | None:
        try:
            stamp = datetime.fromtimestamp(self._wall()).strftime("%Y%m%dT%H%M%S")
            folder = self.config.evidence_dir / f"{stamp}-{label}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "page.html").write_text(await self._page.content(), encoding="utf-8")
            await self._page.screenshot(path=str(folder / "page.png"), full_page=True)
            (folder / "url.txt").write_text(self._page.url, encoding="utf-8")
            return str(folder)
        except Exception:  # evidence is best effort; never turn a result into a failure
            return None

    async def fetch(self, req: SearchRequest) -> FetchedPage:
        async with self._lock:
            await self.admit()
            try:
                await self._ensure_session(req.lang)
                if self._searches_in_session:
                    p = self.config.policy
                    await asyncio.sleep(lognormal_between(self._rng, p.think_min_s, p.think_max_s))
                html, url, status = await self._run_search(req)
            except FetchError:
                raise
            except Exception as exc:
                evidence = await self._save_evidence("error") if self._page is not None else None
                await self._close_session()
                raise FetchError(
                    "network",
                    f"browser search failed: {type(exc).__name__}: {exc}"[:300],
                    retryable=True,
                    retry_after_s=30,
                    evidence_ref=evidence,
                ) from exc
            self._state.record_search(self.config.identity)
            self._searches_in_session += 1
            self._last_used = time.monotonic()

            verdict = classify(html, url=url, status=status)
            retry_after: int | None = None
            evidence_ref: str | None = None
            if verdict.state in _BLOCKS:
                evidence_ref = await self._save_evidence(verdict.state.value)
                retry_after = self.record_block(verdict.state.value)
                await self._close_session()  # the next session is a fresh device
            elif verdict.state in (PageState.JS_GATE, PageState.PARSE_DRIFT, PageState.CONSENT_WALL):
                evidence_ref = await self._save_evidence(verdict.state.value)
            return FetchedPage(
                url=url,
                status=status,
                html=html,
                engine="gecko",
                backend=self.name,
                block_retry_after_s=retry_after,
                evidence_ref=evidence_ref,
            )

    # ------------------------------------------- shared with the hybrid fetcher
    def record_block(self, reason: str) -> int:
        """Rest the identity after a block; returns retry_after_s."""
        until = max(self._tomorrow_epoch(), self._wall() + 3600)
        self._state.record_captcha(self.config.identity, until, reason)
        return math.ceil(until - self._wall())

    def record_search(self) -> None:
        self._state.record_search(self.config.identity)

    def record_resolve(self) -> None:
        self._state.record_resolve(self.config.identity)

    def note_used(self) -> None:
        self._last_used = time.monotonic()

    def ready_in_s(self) -> float:
        """Seconds until admit() lets a search through: rest, daily cap or hourly budget (0 = now)."""
        ident = self.config.identity
        resting = self._state.resting_until(ident)
        if resting is not None:
            return max(0.0, resting[0] - self._wall())
        if self._state.searches_today(ident) >= self.config.policy.searches_per_day:
            return self._tomorrow_epoch() - self._wall()
        return self._bucket.wait_s()

    async def close_session(self) -> None:
        """Close the browser but keep the identity's budget and state."""
        async with self._lock:
            await self._close_session()

    async def export_session(self) -> dict[str, Any] | None:
        """Cookies and the browser's real navigation headers, for the hybrid HTTP tier.

        Headers are read from the last main-frame document request the browser actually sent, so the HTTP
        client copies the browser instead of guessing (UA, Accept, Accept-Language, Accept-Encoding).
        """
        async with self._lock:
            if self._context is None or self._last_doc_request is None:
                return None
            headers = await self._last_doc_request.all_headers()
            return {
                "cookies": await self._context.cookies(),
                "headers": {k.lower(): v for k, v in headers.items()},
                "hl": self._session_hl,
            }

    # ------------------------------------------------------------- resolution
    async def resolve_links(self, links: list[str]) -> dict[str, str | None]:
        """Destination of each Google /goto link, read from its 302 inside the search session.

        Each link is opened the way a person opens a result in a background tab: a middle-click on the
        result's own anchor in the results page. A scripted `page.goto(link, referer=...)` sends
        `Sec-Fetch-Site: none` together with a Referer, a combination no real browser produces. While
        resolving, a context route aborts every request in the new tabs except the redirect itself, so
        destinations never load.
        """
        out: dict[str, str | None] = {}
        async with self._lock:
            if self._context is None:
                return dict.fromkeys(links)
            targets = {
                link: link.replace(GOOGLE_ORIGIN, self.config.base_url, 1) for link in dict.fromkeys(links)
            }
            loop = asyncio.get_running_loop()
            # Keyed by the redirect's url= blob: the request a click really makes is a rewritten /url
            # form of the /goto href.
            locations: dict[str, asyncio.Future[str | None]] = {
                blob: loop.create_future() for t in targets.values() if (blob := redirect_blob(t))
            }
            serp = self._page

            async def gate(route: Any) -> None:
                # Allow only the results page's own requests and the result redirects being resolved.
                # Requests from a just-opened tab often have no resolvable frame yet, so "owner unknown"
                # means abort.
                request = route.request
                try:
                    owner = request.frame.page
                except Exception:
                    owner = None
                if owner is serp or redirect_blob(request.url) in locations:
                    await route.fallback()
                else:
                    log.debug(
                        "resolve_gate_abort owner=%s url=%s",
                        "unknown" if owner is None else "tab",
                        request.url[:160],
                    )
                    await route.abort()

            async def on_response(response: Any) -> None:
                blob = redirect_blob(response.url)
                if blob is None:
                    return
                log.debug("resolve_response status=%s url=%s", response.status, response.url[:160])
                future = locations.get(blob)
                if future is None or future.done():
                    return
                if 300 <= response.status < 400:  # /goto: plain server redirect
                    future.set_result((await response.all_headers()).get("location"))
                else:  # clicked /url form: 200 interstitial page carrying the destination
                    try:
                        future.set_result(redirect_target(await response.text()))
                    except Exception:  # body unavailable (tab already closing)
                        future.set_result(None)

            await self._context.route("**/*", gate)
            self._context.on("response", on_response)
            try:
                for i, (link, target) in enumerate(targets.items()):
                    if i:
                        p = self.config.policy
                        await asyncio.sleep(
                            lognormal_between(self._rng, p.resolve_gap_min_s, p.resolve_gap_max_s)
                        )
                    blob = redirect_blob(target)
                    out[link] = await self._click_resolve(target, locations[blob]) if blob else None
                    self._state.record_resolve(self.config.identity)
            finally:
                self._context.remove_listener("response", on_response)
                with contextlib.suppress(Exception):
                    await self._context.unroute("**/*", gate)
            self._last_used = time.monotonic()
        return out

    async def _click_resolve(self, target: str, location: asyncio.Future[str | None]) -> str | None:
        parts = urlsplit(target)
        href = f"{parts.path}?{parts.query}" if parts.query else parts.path
        # The same /goto URL also appears on hidden anchors (e.g. collapsed AI Overview sources); click the
        # result's title link, the one a person clicks: the anchor that contains the result's <h3>.
        anchor = self._page.locator(f'a[href="{href}"]:has(h3)').first
        if await anchor.count() == 0:
            anchor = self._page.locator(f'a[href="{href}"]:visible').first
            if await anchor.count() == 0:
                log.info("resolve_skipped_no_anchor href=%s", href[:80])
                return None  # the link is not on the page (e.g. it came from a cached response)
        tab: Any = None
        nav: Any = None
        try:
            tab = await self._open_in_background(anchor, href)
            if tab is None:
                # Coherent fallback: a typed-URL navigation (Sec-Fetch-Site: none, no Referer), never a
                # scripted navigation with a forged Referer.
                tab = await self._context.new_page()
                nav = asyncio.ensure_future(
                    tab.goto(target, wait_until="commit", timeout=self.config.nav_timeout_ms)
                )
            return await asyncio.wait_for(location, timeout=self.config.resolve_timeout_s)
        except Exception as exc:  # timeout: report unresolved, never fail the search
            log.warning("resolve_failed href=%s error=%s: %s", href[:80], type(exc).__name__, str(exc)[:200])
            return None
        finally:
            if tab is not None:
                with contextlib.suppress(Exception):
                    await tab.close()
            if nav is not None:
                with contextlib.suppress(Exception):  # the cancelled navigation is expected to fail
                    await nav

    async def _open_in_background(self, anchor: Any, href: str) -> Any:
        """Middle-click the anchor (background tab). Retries once centred; None if it cannot be clicked.

        Ctrl+click sends the same headers but headless Camoufox reports that tab to Playwright
        seconds late, so its 302 is missed.
        """
        for attempt in (1, 2):
            try:
                async with self._context.expect_page(timeout=self.config.resolve_timeout_s * 1000) as opened:
                    await anchor.click(button="middle", timeout=CLICK_TIMEOUT_MS)
                return await opened.value
            except Exception as exc:  # usually another element intercepts the pointer
                log.info(
                    "resolve_click_retry href=%s attempt=%d error=%s", href[:80], attempt, type(exc).__name__
                )
                with contextlib.suppress(Exception):
                    await anchor.evaluate("el => el.scrollIntoView({block: 'center'})")
        log.warning("resolve_click_fallback_typed_url href=%s", href[:80])
        return None

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, str | int | float | bool | None]:
        ident, p = self.config.identity, self.config.policy
        today = self._state.today(ident)
        resting = self._state.resting_until(ident)
        return {
            "identity": ident,
            "session_open": self._page is not None,
            "exit_ip": self._place.ip if self._place else None,
            "country": self._place.country if self._place else None,
            "locale": self._place.locale if self._place else None,
            "searches_today": today["searches"],
            "searches_per_day": p.searches_per_day,
            "searches_per_hour": p.searches_per_hour,
            "resolves_today": today["resolves"],
            "captchas_today": today["captchas"],
            "resting_until_epoch": resting[0] if resting else None,
            "hourly_wait_s": round(self._bucket.wait_s(), 1),
        }
