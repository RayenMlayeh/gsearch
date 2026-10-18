"""Hybrid tier: a real browser opens the session, plain HTTP does the searches.

Google's JS gate runs on a session's first search only; later searches in the same session are single
document GETs carrying ordinary session cookies. The browser produces the session genuinely and this module
reuses it. Nothing here generates or alters a token.

- TLS: curl_cffi `firefox147` plus a cipher override, which matches Camoufox's ClientHello.
- Headers: copied from the browser's own last document request, in its order; only Referer, Sec-Fetch-*
  and Cookie are set per request.
- Result URLs: one `/goto?url=<blob>` GET each, read from the 302 Location. The destination is never
  requested.
- Any gate or block sends the work back to the browser tier. Budget and rest state are the browser
  identity's own, so the hybrid cannot outrun the identity's limits.
- Once the session is adopted the browser is closed: an open results page keeps making background
  requests, which a per-GB proxy bills. The next gate opens a fresh browser session.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from curl_cffi.const import CurlOpt
from curl_cffi.requests import AsyncSession
from gsearch_parse.classify import classify
from gsearch_parse.models import PageState, SearchRequest
from gsearch_parse.redirect import redirect_target

from .camoufox_backend import GOOGLE_ORIGIN, CamoufoxFetcher
from .fetch import FetchedPage, FetchError, build_search_url

log = logging.getLogger(__name__)

IMPERSONATE = "firefox147"
# Reproduces Camoufox 152's cipher order exactly; the stock curl_cffi profile adds 0xc009.
FIREFOX152_CIPHERS = ":".join(
    [
        "TLS_AES_128_GCM_SHA256",
        "TLS_CHACHA20_POLY1305_SHA256",
        "TLS_AES_256_GCM_SHA384",
        "ECDHE-ECDSA-AES128-GCM-SHA256",
        "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
        "ECDHE-RSA-CHACHA20-POLY1305",
        "ECDHE-ECDSA-AES256-GCM-SHA384",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "ECDHE-ECDSA-AES256-SHA",
        "ECDHE-RSA-AES128-SHA",
        "ECDHE-RSA-AES256-SHA",
        "AES128-GCM-SHA256",
        "AES256-GCM-SHA384",
        "AES128-SHA",
        "AES256-SHA",
    ]
)
_DROP = {"host", "connection", "alt-used", "cookie", "content-length", "referer", "te"}
_BLOCKS = (PageState.CAPTCHA, PageState.HARD_BLOCK)
RESOLVE_CONCURRENCY = 3
RESOLVE_TIMEOUT_S = 10.0


class HybridFetcher:
    name = "hybrid"

    def __init__(self, browser: CamoufoxFetcher, *, rng: random.Random | None = None) -> None:
        self.browser = browser
        self._rng = rng or random.Random()  # nosec B311 - jitter, not security
        self._lock = asyncio.Lock()
        self._http: AsyncSession[Any] | None = None
        self._template: dict[str, str] = {}
        self._cookies: dict[str, str] = {}
        self._hl: str | None = None
        self._adopted = False
        self.stats = {"http_searches": 0, "browser_searches": 0, "http_bytes": 0, "remints": 0}

    @property
    def config(self) -> Any:
        return self.browser.config

    # --------------------------------------------------------------- session
    async def _adopt(self) -> bool:
        exported = await self.browser.export_session()
        if not exported:
            return False
        self._template = {
            k: v for k, v in exported["headers"].items() if k not in _DROP and not k.startswith(":")
        }
        # The browser context is dedicated to this identity, so every cookie in it belongs to the session.
        self._cookies = {c["name"]: c["value"] for c in exported["cookies"]}
        self._hl = exported["hl"]
        self._adopted = True
        if self._http is None:
            self._http = AsyncSession(
                impersonate=IMPERSONATE,
                default_headers=False,
                curl_options={CurlOpt.SSL_CIPHER_LIST: FIREFOX152_CIPHERS},
                proxy=self.config.proxy_url,
            )
        log.info("hybrid_session_adopted cookies=%s", sorted(self._cookies))
        return True

    def _drop_session(self, reason: str) -> None:
        log.info("hybrid_session_dropped reason=%s", reason)
        self._cookies = {}
        self._template = {}
        self._adopted = False

    def _headers(self) -> dict[str, str]:
        """The browser's navigation headers, in its order, as a same-origin click from google.com."""
        out: dict[str, str] = {}
        for k, v in self._template.items():
            if k == "sec-fetch-site":
                out[k] = "same-origin"
            elif k == "sec-fetch-user":
                out[k] = "?1"
            else:
                out[k] = v
            if k == "accept-encoding":
                # Firefox order (live HARs): ... Accept-Encoding, Referer, [Alt-Used, Connection], Cookie,
                # Upgrade-Insecure-Requests, Sec-Fetch-*. Google's referrer policy sends only the origin.
                out["referer"] = f"{GOOGLE_ORIGIN}/"
                if self._cookies:
                    out["cookie"] = "; ".join(f"{name}={value}" for name, value in self._cookies.items())
        return out

    def _absorb_cookies(self, response: Any) -> None:
        """Keep cookies in our own ordered map and empty the library jar: once curl's jar holds cookies,
        its cookie engine replaces the Cookie header we build (observed with curl_cffi 0.16.3)."""
        for name, value in response.cookies.items():
            self._cookies[name] = value
        if self._http is not None:
            self._http.cookies.clear()

    # ---------------------------------------------------------------- search
    async def fetch(self, req: SearchRequest) -> FetchedPage:
        async with self._lock:
            if not self._adopted:
                return await self._browser_search(req)
            await self.browser.admit()
            url = build_search_url(req.model_copy(update={"lang": req.lang or self._hl}))
            url = url.replace(GOOGLE_ORIGIN, self.config.base_url, 1)
            assert self._http is not None
            try:
                resp = await self._http.get(url, headers=self._headers(), allow_redirects=False, timeout=20)
            except Exception as exc:  # curl_cffi raises its own error types; the exit did not answer
                raise FetchError(
                    "network",
                    f"http search failed: {type(exc).__name__}: {exc}"[:300],
                    retryable=True,
                    retry_after_s=30,
                ) from exc
            self.browser.record_search()
            self.browser.note_used()
            self._absorb_cookies(resp)
            size = len(resp.content)
            self.stats["http_searches"] += 1
            self.stats["http_bytes"] += size
            headers = {k.lower(): v for k, v in resp.headers.items()}
            html = resp.text
            verdict = classify(html, url=str(resp.url), status=resp.status_code, headers=headers)
            log.info("hybrid_http_search status=%s state=%s bytes=%s", resp.status_code, verdict.state, size)
            if verdict.state in _BLOCKS:
                retry_after = self.browser.record_block(f"{verdict.state.value} (http)")
                self._drop_session(verdict.state.value)
                return FetchedPage(
                    url=str(resp.url),
                    status=resp.status_code,
                    html=html,
                    engine="gecko",
                    backend="hybrid-http",
                    headers=headers,
                    block_retry_after_s=retry_after,
                )
            if verdict.state is PageState.JS_GATE:
                # The session no longer satisfies the gate: re-open it in the real browser.
                self._drop_session("js_gate")
                self.stats["remints"] += 1
                return await self._browser_search(req)
            return FetchedPage(
                url=str(resp.url),
                status=resp.status_code,
                html=html,
                engine="gecko",
                backend="hybrid-http",
                headers=headers,
            )

    async def _browser_search(self, req: SearchRequest) -> FetchedPage:
        page = await self.browser.fetch(req)
        self.stats["browser_searches"] += 1
        ok = classify(page.html, url=page.url, status=page.status).state is PageState.OK
        if ok and await self._adopt():
            await self.browser.close_session()
        return page

    # ------------------------------------------------------------ resolution
    async def resolve_links(self, links: list[str]) -> dict[str, str | None]:
        if not self._adopted or self._http is None:
            return await self.browser.resolve_links(links)
        sem = asyncio.Semaphore(RESOLVE_CONCURRENCY)

        async def one(link: str) -> tuple[str, str | None]:
            async with sem:
                await asyncio.sleep(self._rng.uniform(0.05, 0.25))
                target = link.replace(GOOGLE_ORIGIN, self.config.base_url, 1)
                try:
                    assert self._http is not None
                    resp = await self._http.get(
                        target, headers=self._headers(), allow_redirects=False, timeout=RESOLVE_TIMEOUT_S
                    )
                except Exception as exc:  # a failed resolution must never fail the search
                    log.warning("hybrid_resolve_failed error=%s", type(exc).__name__)
                    return link, None
                self.browser.record_resolve()
                self._absorb_cookies(resp)
                self.stats["http_bytes"] += len(resp.content)
                if 300 <= resp.status_code < 400:
                    return link, resp.headers.get("location")
                return link, redirect_target(resp.text)

        pairs = await asyncio.gather(*(one(link) for link in dict.fromkeys(links)))
        return dict(pairs)

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, str | int | float | bool | None]:
        return {**self.browser.status(), **self.stats, "http_session": self._adopted}

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.close()
        await self.browser.aclose()
