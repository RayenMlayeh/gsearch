"""Proxy pool: an identity (exit IP + browser session + HTTP session) per proxy session, each behind its own
local relay.

Two kinds of proxy, each used the way that worked best in testing:
- rotating (a session id in the login, e.g. `...-sid-{sid}`): every search gets a new session id, so a new
  exit IP, and a new browser. Its results and links are read on that exit, then everything is closed.
  A session is never moved to another IP.
- static (a fixed IP per proxy: a VPS, datacenter or ISP proxy list): one warm session per proxy, used in
  turn, each with its own budget like a home IP. A proxy that gets a CAPTCHA rests until tomorrow.
Every new exit is located first and dropped if it does not answer, or if it is outside the country the
login asks for. A blocked search is retried on another exit, up to `max_attempts`.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from gsearch_parse.models import SearchRequest

from .camoufox_backend import CamoufoxConfig, CamoufoxFetcher
from .fetch import FetchedPage, FetchError
from .geo import Place, locate_exit
from .hybrid import HybridFetcher
from .relay import ByteMeter, Relay, Upstream

log = logging.getLogger(__name__)

PARALLEL = 3  # rotating proxies: browsers at once
LEASE_S = 60.0  # a member that just served a page keeps it until its links are resolved, at most this long
SPAWN_DRAWS = 4  # fresh exits tried before giving up (dead or wrong-country exits)
DEAD_S = 600.0  # a static proxy that failed its location check is skipped this long


@dataclass(frozen=True, slots=True)
class PoolConfig:
    proxies: tuple[Upstream, ...]
    browser: CamoufoxConfig = field(default_factory=CamoufoxConfig)  # template; identity and proxy per member
    byte_cap_bytes: int | None = None
    meter_path: Path | None = None
    max_attempts: int = 4  # exits tried per search (blocks, dead and wrong-country exits)


@dataclass(eq=False)
class Member:
    key: str
    relay: Relay
    fetcher: HybridFetcher
    searches: int = 0
    busy: int = 0
    leased_until: float = 0.0

    def idle(self) -> bool:
        return self.busy == 0 and time.monotonic() >= self.leased_until


_serving: contextvars.ContextVar[Member | None] = contextvars.ContextVar("gsearch_pool_member", default=None)


class ProxyPool:
    name = "pool"

    def __init__(
        self,
        config: PoolConfig,
        *,
        rng: random.Random | None = None,
        make_fetcher: Callable[[CamoufoxConfig], HybridFetcher] | None = None,
        locate: Callable[[str], Awaitable[Place]] = locate_exit,
    ) -> None:
        if not config.proxies:
            raise ValueError("no proxies")
        self.config = config
        self.rotating = all(p.rotating for p in config.proxies)
        self.meter = ByteMeter(config.meter_path, config.byte_cap_bytes)
        self._rng = rng or random.Random()  # nosec B311 - session ids, not secrets
        self._make = make_fetcher or (lambda cfg: HybridFetcher(CamoufoxFetcher(cfg)))
        self._locate = locate
        self._members: dict[str, Member] = {}
        self._dead_until: dict[str, float] = {}
        self._page_relay: Relay | None = None
        self._turn = 0
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(PARALLEL if self.rotating else len(config.proxies))
        self.stats = {
            "spawned": 0,
            "blocked": 0,
            "retries": 0,
            "exit_failures": 0,
            "exits_dead": 0,
            "exits_wrong_country": 0,
        }

    # --------------------------------------------------------------- members
    async def _spawn(self, upstream: Upstream) -> Member | None:
        """Relay plus location check for one exit; None if it is dead or in the wrong country."""
        relay = Relay(upstream, self.meter, tag=upstream.key)
        url = await relay.start()
        try:
            place = await self._locate(url)
        except Exception as exc:  # the exit did not answer the IP lookup
            self.stats["exits_dead"] += 1
            log.info("pool_exit_dead proxy=%s error=%s", upstream.key, type(exc).__name__)
            await relay.close()
            return None
        if upstream.country and place.country != upstream.country:
            self.stats["exits_wrong_country"] += 1
            log.info("pool_exit_rejected proxy=%s ip=%s country=%s", upstream.key, place.ip, place.country)
            await relay.close()
            return None
        cfg = replace(self.config.browser, identity=f"proxy-{upstream.key}", proxy_url=url, place=place)
        member = Member(upstream.key, relay, self._make(cfg))
        self._members[member.key] = member
        self.stats["spawned"] += 1
        log.info("pool_spawn proxy=%s ip=%s country=%s", upstream.key, place.ip, place.country)
        return member

    async def _retire(self, member: Member, reason: str) -> None:
        self._members.pop(member.key, None)
        log.info(
            "pool_retire proxy=%s reason=%s searches=%s bytes=%s",
            member.key,
            reason,
            member.searches,
            member.relay.bytes_total,
        )
        await member.fetcher.aclose()
        await member.relay.close()

    async def _fresh_exit(self) -> Member:
        """Rotating proxies: a new session id, so a new exit IP, for this search only."""
        for _ in range(SPAWN_DRAWS):
            member = await self._spawn(self._rng.choice(self.config.proxies).with_session(self._rng))
            if member is not None:
                return member
        raise FetchError("proxy", f"no usable exit in {SPAWN_DRAWS} draws", retryable=True, retry_after_s=60)

    async def _next_static(self, exclude: set[str]) -> Member:
        """Static proxies: the next one in turn that is not resting and has budget left."""
        async with self._lock:
            proxies = self.config.proxies
            start = self._turn
            self._turn += 1
            waits: list[float] = []
            fallback: Member | None = None
            for i in range(len(proxies)):
                upstream = proxies[(start + i) % len(proxies)]
                if upstream.key in exclude or self._dead_until.get(upstream.key, 0.0) > time.monotonic():
                    continue
                member = self._members.get(upstream.key) or await self._spawn(upstream)
                if member is None:
                    self._dead_until[upstream.key] = time.monotonic() + DEAD_S
                    continue
                wait = member.fetcher.browser.ready_in_s()
                if wait > 0:
                    waits.append(wait)
                elif not member.busy:
                    return member
                else:
                    fallback = fallback or member
            if fallback is not None:
                return fallback
        raise FetchError(
            "rate_limited",
            f"all {len(proxies)} proxies are resting or at their budget",
            retryable=True,
            retry_after_s=math.ceil(min(waits)) if waits else 60,
        )

    async def _sweep(self) -> None:
        """Close one-shot exits whose links were never resolved (e.g. a page without results)."""
        if self.rotating:
            for member in list(self._members.values()):
                if member.searches and member.idle():
                    await self._retire(member, "done")

    # ---------------------------------------------------------------- search
    def _check_limit(self) -> None:
        if self.meter.exceeded:
            raise FetchError(
                "proxy",
                f"proxy data limit reached ({self.meter.total / 1e6:.0f} MB); raise GSEARCH_DATA_LIMIT_MB",
                retryable=False,
            )

    async def fetch(self, req: SearchRequest) -> FetchedPage:
        self._check_limit()
        await self._sweep()
        async with self._slots:
            tried: set[str] = set()
            page: FetchedPage | None = None
            for attempt in range(self.config.max_attempts):
                last = attempt + 1 == self.config.max_attempts
                member = await (self._fresh_exit() if self.rotating else self._next_static(tried))
                tried.add(member.key)
                member.busy += 1
                try:
                    page = await member.fetcher.fetch(req)
                except FetchError as exc:
                    if exc.code == "consent_wall":
                        # Google places this exit outside the target market: a wrong-country exit the IP
                        # lookup missed.
                        self.stats["exits_wrong_country"] += 1
                    elif exc.code == "network":
                        self.stats["exit_failures"] += 1  # residential exits go offline without notice
                    else:
                        raise
                    log.warning("pool_exit_failed proxy=%s: %s", member.key, exc)
                    await self._retire(member, exc.code)
                    if last:
                        raise
                    self.stats["retries"] += 1
                    continue
                finally:
                    member.busy -= 1
                member.searches += 1
                if page.block_retry_after_s is None:
                    member.leased_until = time.monotonic() + LEASE_S
                    _serving.set(member)
                    return page
                self.stats["blocked"] += 1
                if self.rotating:  # a static proxy stays, resting (its fetcher recorded the block)
                    await self._retire(member, f"blocked {page.backend}")
                if not last:
                    self.stats["retries"] += 1
            assert page is not None
            return page

    async def resolve_links(self, links: list[str]) -> dict[str, str | None]:
        member = _serving.get()
        if member is None or self._members.get(member.key) is not member:
            return dict.fromkeys(links)
        member.busy += 1
        try:
            return await member.fetcher.resolve_links(links)
        finally:
            member.busy -= 1
            member.leased_until = 0.0
            _serving.set(None)
            if self.rotating and member.busy == 0:
                await self._retire(member, "done")  # one-shot: close it now, not at the next search

    async def page_proxy(self) -> str:
        self._check_limit()
        async with self._lock:
            if self._page_relay is None:
                upstream = self._rng.choice(self.config.proxies).with_session(self._rng)
                self._page_relay = Relay(upstream, self.meter, tag="pages")
                await self._page_relay.start()
            return self._page_relay.url

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, str | int | float | bool | None]:
        return {
            "backend": "pool",
            "proxies": len(self.config.proxies),
            "proxy_kind": "rotating" if self.rotating else "static",
            "proxy": self.config.proxies[0].redacted().replace("{sid}", "<sid>"),
            "open_sessions": len(self._members),
            "proxy_mb_used": round(self.meter.total / 1e6, 2),
            "proxy_mb_limit": round(self.config.byte_cap_bytes / 1e6) if self.config.byte_cap_bytes else None,
            **self.stats,
        }

    async def aclose(self) -> None:
        for member in list(self._members.values()):
            await self._retire(member, "shutdown")
        if self._page_relay is not None:
            await self._page_relay.close()
        self.meter.save()
