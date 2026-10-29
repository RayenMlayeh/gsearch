"""Live backend: one Camoufox session per identity.

- Google's JS gate runs once per session; later searches in the session are a single request.
- uBlock Origin is excluded: Camoufox loads it by default and it removes the ads from the page.
- Locale and geolocation come from the exit IP, so the device matches where it connects from.
- Budget, daily cap and CAPTCHA rest are checked before any browser work and persist across restarts.
- A lasting identity (home IP, fixed proxy) keeps one device and its Google cookies across launches, like a
  person's own Firefox; only the place (timezone, language, geolocation) follows the exit IP.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import random
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit

from browserforge.fingerprints import Screen
from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox
from camoufox.utils import launch_options
from gsearch_parse.classify import classify
from gsearch_parse.models import PageState, SearchRequest

from .fetch import FetchedPage, FetchError, build_search_url
from .geo import Place, locate_exit
from .pacing import PacingPolicy, lognormal_between
from .state import StateStore

log = logging.getLogger(__name__)

GOOGLE_ORIGIN = "https://www.google.com"
SEARCH_BOX = 'textarea[name="q"], input[name="q"]'
RESULTS_READY_JS = (
    "() => !!document.querySelector('#search') || location.pathname.startsWith('/sorry')"
    " || !!document.querySelector('form#captcha-form')"
)
CLICK_TIMEOUT_MS = 5_000
REJECT_ALL = "#W0wltc"  # "Reject all" in Google's consent dialog, same id in every language
# Ordinary pages a new device opens before its first search: a fresh browser on a fresh exit is refused on
# its first search far more often (nodemaven/google-browser-scraper's "light" ladder; google.com comes
# first among Google's pages so its consent dialog is answered there).
WARMUP_PAGES = (
    "https://www.theverge.com/",
    "https://www.wikihow.com/Main-Page",
    "https://www.google.com/imghp?hl=en",
    "https://translate.google.com/?hl=en",
    "https://scholar.google.com/?hl=en",
)
# What Firefox on Windows sends when a query is typed into the address bar with Google as search engine:
# a typed navigation (Sec-Fetch-Site: none, no Referer), with no homepage visit before it.
ADDRESS_BAR_SEARCH = "{base}/search?client=firefox-b-d&q={q}"
_WINDOWS = ((1536, 864), (1920, 1080), (1440, 900), (1366, 768))
_BLOCKS = (PageState.CAPTCHA, PageState.HARD_BLOCK)
_PLACE_KEYS = ("geolocation:", "locale:", "timezone", "humanize")
# Google's EU consent dialog links to consent.google.com; its continue URL carries Google's own view of the
# visitor's country, which can differ from what IP databases say about the exit.
_CONSENT = re.compile(
    r"consent\.google\.com/(?:save|d)(?:\?|%3F)continue[^\"'\s]*?gl(?:=|\\x3d|%3D)([A-Z]{2})"
)


def consent_country(html: str) -> str | None:
    """Country Google assigned to the visitor when it serves its consent dialog, else None."""
    match = _CONSENT.search(html)
    return match.group(1) if match else None


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
        proxy = urlsplit(cfg.proxy_url)
        options["proxy"] = {"server": f"{proxy.scheme}://{proxy.hostname}:{proxy.port}"}
        if proxy.username:  # the relay's own login; Playwright takes it apart from the address
            options["proxy"] |= {"username": proxy.username, "password": proxy.password or ""}
    return options


def device_config(options: dict[str, Any]) -> dict[str, Any]:
    """The device Camoufox draws for these launch options (hardware, screen, WebGL, fonts, voices, noise
    seeds), without the place, which follows the exit IP on every launch. A real-device preset can name a
    GPU Camoufox has no WebGL data for (observed: Intel Arc A750, GeForce 8800 GTX); another one is drawn."""
    for attempt in range(10):
        try:
            env = launch_options(**{**options, "headless": options["headless"] is True})["env"]
            break
        except ValueError as exc:
            if "WebGL" not in str(exc) or attempt == 9:
                raise
    chunks = sorted((int(k.rsplit("_", 1)[1]), v) for k, v in env.items() if k.startswith("CAMOU_CONFIG_"))
    config: dict[str, Any] = json.loads("".join(v for _, v in chunks))
    return {k: v for k, v in config.items() if not k.startswith(_PLACE_KEYS)}


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
    policy: PacingPolicy = field(default_factory=PacingPolicy)
    state_path: Path = field(default_factory=lambda: state_dir() / "state.sqlite")
    evidence_dir: Path = field(default_factory=lambda: state_dir() / "evidence")
    # How a session reaches Google: "homepage" (open google.com, type into its box) or "address_bar" (type
    # the query into Firefox's address bar, the usual way Firefox users search; no homepage load).
    entry: str = "homepage"
    fingerprint_preset: bool = False  # real-device fingerprints bundled with Camoufox instead of synthetic
    persist_profile: bool = (
        True  # same device and Google cookies across launches; off for one-shot identities
    )
    warmup: tuple[str, ...] = ()  # pages a lasting device without Google cookies opens before searching


class CamoufoxFetcher:
    name = "camoufox"

    def __init__(
        self,
        config: CamoufoxConfig,
        *,
        wall_clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self._wall = wall_clock
        self._rng = rng or random.Random()  # nosec B311 - pacing jitter, not security
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

    @property
    def _rest_key(self) -> str:
        """Behind a proxy, budget and rest belong to the exit IP: a login whose IP changes is not held back by
        the old one, and two ports on one IP share its budget. The home IP is the identity itself."""
        place = self.config.place
        return f"exit@{place.ip}" if self.config.proxy_url and place else self.config.identity

    def _hourly_wait_s(self) -> float:
        p = self.config.policy
        return self._state.token_wait_s(self._rest_key, p.searches_per_hour / 3600.0, p.burst)

    async def admit(self) -> None:
        """Refuse before any browser work when the identity has no budget. Raises FetchError."""
        ident, policy = self.config.identity, self.config.policy
        resting = self._state.resting_until(self._rest_key)
        if resting is not None:
            until, reason = resting
            raise FetchError(
                "captcha",
                f"identity {ident} is resting after {reason}",
                retryable=True,
                retry_after_s=math.ceil(until - self._wall()),
            )
        if self._state.searches_today(self._rest_key) >= policy.searches_per_day:
            raise FetchError(
                "rate_limited",
                f"daily budget of {policy.searches_per_day} searches used",
                retryable=True,
                retry_after_s=math.ceil(self._tomorrow_epoch() - self._wall()),
            )
        wait = self._state.take_token(self._rest_key, policy.searches_per_hour / 3600.0, policy.burst)
        if wait > 0:
            raise FetchError(
                "rate_limited",
                f"hourly budget of {policy.searches_per_hour:g} searches used",
                retryable=True,
                retry_after_s=math.ceil(wait),
            )

    # ----------------------------------------------------------------- profile
    @property
    def _profile_path(self) -> Path:
        return self.config.state_path.parent / "profiles" / f"{self.config.identity}.json"

    def _profile(self) -> dict[str, Any]:
        try:
            profile: dict[str, Any] = json.loads(self._profile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return profile

    def _save_profile(self, profile: dict[str, Any]) -> None:
        self._profile_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._profile_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(profile), encoding="utf-8")
        tmp.replace(self._profile_path)

    async def _same_device(self, options: dict[str, Any]) -> dict[str, Any]:
        """Launch options for the identity's saved device; the first launch draws it and saves it."""
        profile = self._profile()
        if "device" not in profile:
            profile["device"] = await asyncio.to_thread(device_config, options)
            self._save_profile(profile)
        options = {k: v for k, v in options.items() if k not in ("fingerprint_preset", "screen")}
        return options | {
            "config": profile["device"],
            "window": window_for(self.config.identity),
            "i_know_what_im_doing": True,
        }

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
        cookies = [c for c in self._profile().get("cookies", []) if c.get("expires", -1) > self._wall()]
        if cfg.persist_profile and cookies:
            await self._context.add_cookies(cookies)
        self._page = await self._context.new_page()
        self._page.on("response", self._on_response)
        self._page.on("request", self._on_request)
        self._session_hl = hl_hint or (place.language if place else "en")
        self._searches_in_session = 0
        google = urlsplit(cfg.base_url).hostname or ""
        known = any("google." in c.get("domain", "") or c.get("domain") == google for c in cookies)
        if cfg.persist_profile and cfg.warmup and not known:  # other sites' cookies: a warm-up cut short
            await self._warm_up()
        if cfg.entry == "address_bar":
            # A person opens the browser and types a query: nothing loads before the search itself.
            await asyncio.sleep(lognormal_between(self._rng, 1.5, 4.0))
            return
        await self._page.goto(
            f"{cfg.base_url}/?hl={self._session_hl}",
            wait_until="domcontentloaded",
            timeout=cfg.nav_timeout_ms,
        )
        await self._pass_consent()
        await self._settle()
        await asyncio.sleep(lognormal_between(self._rng, 1.5, 4.0))

    async def _warm_up(self) -> None:
        """Open each warm-up page and stay on it, like a person reading. A page that fails to load is
        skipped; Google's consent dialog is answered on the way."""
        p = self.config.policy
        for url in self.config.warmup:
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=self.config.nav_timeout_ms)
            except Exception as exc:  # a slow or broken third-party page must not cost the session
                log.info("warmup_page_failed url=%s error=%s", url, type(exc).__name__)
                continue
            await self._pass_consent()
            await asyncio.sleep(self._rng.uniform(p.warmup_dwell_min_s, p.warmup_dwell_max_s))
        await self._page.goto("about:blank")  # a new tab: the search is typed into the address bar

    async def _launch(self, cfg: CamoufoxConfig, place: Place | None) -> Any:
        """Start the browser. A real-device preset can name a GPU that Camoufox has no WebGL data for
        (observed: Intel Arc A750); that preset is skipped and another one drawn."""
        for attempt in range(3):
            try:
                options = device_options(cfg, place)
                if cfg.persist_profile:
                    options = await self._same_device(options)
                self._camoufox = AsyncCamoufox(**options)  # type: ignore[no-untyped-call]
                return await self._camoufox.__aenter__()
            except ValueError as exc:
                if not cfg.fingerprint_preset or "WebGL" not in str(exc) or attempt == 2:
                    raise
                log.info("preset_redrawn reason=%s", str(exc)[:120])
        raise AssertionError("unreachable")

    async def _pass_consent(self) -> None:
        """Google's EU/UK consent dialog: click "Reject all", as a visitor there does; the saved profile keeps
        the choice. Raise consent_wall when Google places the exit in another country than its IP lookup
        (wrong market: the pool draws another exit) or when the dialog cannot be answered."""
        # The dialog itself, not a link: every results page links to consent.google.com from its account menu.
        if "consent.google." not in self._page.url and not await self._page.locator(REJECT_ALL).count():
            return
        gl = consent_country(await self._page.content())
        place = self._place
        if gl and place and ("GB" if gl == "UK" else gl) != place.country:
            reason = f"Google places the exit in {gl}, not {place.country}"
        else:
            try:
                reject = self._page.locator(REJECT_ALL)
                await reject.click(timeout=CLICK_TIMEOUT_MS)
                await reject.wait_for(state="hidden", timeout=self.config.nav_timeout_ms)
                await self._settle()
                return
            except Exception:  # Google changed its dialog: refuse the exit, as before consent support
                reason = "its dialog could not be answered"
        evidence = await self._save_evidence("consent")
        await self._close_session()
        raise FetchError(
            "consent_wall",
            f"Google served its consent dialog ({reason})",
            retryable=True,
            retry_after_s=0,
            evidence_ref=evidence,
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
            if self.config.persist_profile:
                # shortcut: cookies the HTTP tier gets after adoption are not saved; Google's are long-lived.
                with contextlib.suppress(Exception):  # a lost cookie save must not mask the real error
                    self._save_profile({**self._profile(), "cookies": await self._context.cookies()})
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
            await self._pass_consent()
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

    async def fetch(self, req: SearchRequest, *, admitted: bool = False) -> FetchedPage:
        """`admitted`: the caller already took this search's budget (the hybrid redoing an HTTP search)."""
        async with self._lock:
            if not admitted:
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
            self._state.record_search(self._rest_key)
            self._searches_in_session += 1
            self._last_used = time.monotonic()

            verdict = classify(html, url=url, status=status)
            retry_after: int | None = None
            evidence_ref: str | None = None
            if verdict.state in _BLOCKS:
                evidence_ref = await self._save_evidence(verdict.state.value)
                await self._close_session()
                retry_after = self.record_block(verdict.state.value)
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
        """Rest the identity and forget its device and cookies, so it comes back as a fresh device; returns
        retry_after_s."""
        until = max(self._tomorrow_epoch(), self._wall() + 3600)
        self._state.record_captcha(self._rest_key, until, reason)
        self._profile_path.unlink(missing_ok=True)
        return math.ceil(until - self._wall())

    def record_search(self) -> None:
        self._state.record_search(self._rest_key)

    def record_resolve(self) -> None:
        self._state.record_resolve(self._rest_key)

    def note_used(self) -> None:
        self._last_used = time.monotonic()

    def move_to(self, place: Place) -> None:
        """The proxy changed its exit IP under this identity: rests and evidence follow the new IP."""
        self.config = replace(self.config, place=place)
        self._place = place if self._place else None

    def ready_in_s(self) -> float:
        """Seconds until admit() lets a search through: rest, daily cap or hourly budget (0 = now)."""
        resting = self._state.resting_until(self._rest_key)
        if resting is not None:
            return max(0.0, resting[0] - self._wall())
        if self._state.searches_today(self._rest_key) >= self.config.policy.searches_per_day:
            return self._tomorrow_epoch() - self._wall()
        return self._hourly_wait_s()

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

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, str | int | float | bool | None]:
        ident, p = self.config.identity, self.config.policy
        today = self._state.today(self._rest_key)
        resting = self._state.resting_until(self._rest_key)
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
            "hourly_wait_s": round(self._hourly_wait_s(), 1),
        }
