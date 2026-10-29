"""SearchClient: the one entry point the CLI and MCP adapters use.

Blocks and parser drift are returned as SearchError, never as an empty result list.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from gsearch_parse import PARSER_VERSION, parse_page
from gsearch_parse.models import (
    ErrorCode,
    Link,
    OrganicResult,
    PageContent,
    PageState,
    ResponseMeta,
    SearchError,
    SearchRequest,
    SearchResponse,
)
from pydantic import ValidationError

from .cache import CacheStats, QueryCache
from .fetch import Fetcher, FetchError, LinkResolver, PageProxy
from .page import fetch_page

READ_CHARS = 8000  # per page read with read_top: three pages stay a modest part of an agent's context
READ_TIMEOUT_S = 10.0  # a slow site is left out rather than holding up the answer


@dataclass(frozen=True, slots=True)
class _Policy:
    code: ErrorCode
    retryable: bool
    retry_after_s: int | None
    remediation: str


# Default retry hints; a backend that rests an identity reports its own retry_after_s instead.
_BLOCK_POLICY: dict[PageState, _Policy] = {
    PageState.CAPTCHA: _Policy(
        "captcha", True, 3600, "Identity challenged by Google; it is resting. Do not retry immediately."
    ),
    PageState.SOFT_BLOCK: _Policy("soft_block", True, 900, "Google is rate limiting; slow down."),
    PageState.HARD_BLOCK: _Policy(
        "hard_block", True, 86400, "Identity or exit IP is blocked; it will be retired."
    ),
    PageState.CONSENT_WALL: _Policy(
        "consent_wall", True, 0, "Consent page served; the browser backend must handle consent first."
    ),
    PageState.JS_GATE: _Policy(
        "js_gate", True, 0, "Google served its JavaScript gate; this backend cannot execute it."
    ),
    PageState.PARSE_DRIFT: _Policy(
        "parse_drift", False, None, "Google changed its page layout; the parser needs updating."
    ),
}


_FETCH_REMEDIATION: dict[str, str] = {
    "rate_limited": "Budget for this identity is used up; wait retry_after_s. Do not loop on the tool.",
    "captcha": "Identity is resting after a Google challenge; wait retry_after_s.",
    "network": "Browser or network failure; retry later.",
    "invalid_request": "Pass the URL of a public http(s) page that serves HTML or plain text.",
}


class SearchClient:
    def __init__(self, fetcher: Fetcher, cache: QueryCache | None = None) -> None:
        self.fetcher = fetcher
        self.cache = cache if cache is not None else QueryCache()

    def cache_stats(self) -> CacheStats:
        return self.cache.stats()

    async def search(self, req: SearchRequest) -> SearchResponse | SearchError:
        cached = self.cache.get(req)
        if cached is not None:
            return cached.model_copy(update={"meta": cached.meta.model_copy(update={"cache_hit": True})})

        try:
            page = await self.fetcher.fetch(req)
        except FetchError as exc:
            return self._fetch_error(exc)

        result = parse_page(page.html, url=page.url, status=page.status, headers=page.headers, page=req.page)
        if result.state in (PageState.OK, PageState.EMPTY_VALID) and result.data is not None:
            data = result.data
            resolved = (
                await self._resolve(
                    [str(r.link) for r in data.organic if r.url is None]
                    + [str(x.link) for b in data.answer_boxes for x in b.links if x.url is None]
                )
                if req.resolve_links
                else None
            )
            organic = [_with_url(r, resolved) for r in data.organic]
            missing = sum(1 for r in organic if r.url is None) if resolved is not None else 0
            warnings = [f"{missing} result URL(s) could not be resolved"] if missing else []
            if req.read_top:
                organic, unread = await self._read_top(organic, req.read_top)
                warnings += [f"result(s) {', '.join(map(str, unread))} could not be read"] if unread else []
            resp = SearchResponse(
                request=req,
                organic=organic,
                ads=data.ads,
                featured_snippet=data.featured_snippet,
                ai_overview=data.ai_overview,
                people_also_ask=data.people_also_ask,
                related_searches=data.related_searches,
                pagination=data.pagination,
                answer_boxes=[
                    b.model_copy(update={"links": [_with_url(x, resolved) for x in b.links]})
                    for b in data.answer_boxes
                ],
                meta=ResponseMeta(
                    engine=page.engine,
                    backend=page.backend,
                    selector_set=result.selector_set or "none",
                    parser_version=PARSER_VERSION,
                    page_state=result.state,
                    fetched_at=page.fetched_at,
                    warnings=[*result.warnings, *warnings],
                ),
            )
            self.cache.put(req, resp)
            return resp

        policy = _BLOCK_POLICY[result.state]
        return SearchError(
            code=policy.code,
            message=result.reason,
            retryable=policy.retryable,
            retry_after_s=page.block_retry_after_s
            if page.block_retry_after_s is not None
            else policy.retry_after_s,
            remediation=policy.remediation,
            evidence_ref=page.evidence_ref,
            details={"backend": page.backend, "signals": ",".join(result.signals)},
        )

    async def fetch_page(self, url: str) -> PageContent | SearchError:
        try:
            if isinstance(self.fetcher, PageProxy):
                proxy = await self.fetcher.page_proxy()
                return await fetch_page(url, proxy=proxy, google_only=self.fetcher.pages_direct)
            return await fetch_page(url)
        except FetchError as exc:
            return self._fetch_error(exc)

    async def _read_top(self, organic: list[OrganicResult], n: int) -> tuple[list[OrganicResult], list[int]]:
        """The first n results with their pages, fetched at once, and the positions that could not be read."""
        top = organic[:n]
        pages = await asyncio.gather(*(self._read(r) for r in top))
        unread = [r.position for r, page in zip(top, pages, strict=True) if page is None]
        read = [r.model_copy(update={"content": page}) for r, page in zip(top, pages, strict=True)]
        return read + organic[n:], unread

    async def _read(self, result: OrganicResult) -> PageContent | None:
        try:
            page = await asyncio.wait_for(self.fetch_page(str(result.url or result.link)), READ_TIMEOUT_S)
        except TimeoutError:
            return None
        if isinstance(page, SearchError):
            return None
        if len(page.text) > READ_CHARS:
            return page.model_copy(update={"text": page.text[:READ_CHARS], "truncated": True})
        return page

    def _fetch_error(self, exc: FetchError) -> SearchError:
        return SearchError(
            code=exc.code,
            message=str(exc),
            retryable=exc.retryable,
            retry_after_s=exc.retry_after_s,
            remediation=_FETCH_REMEDIATION.get(exc.code, "Check the backend configuration."),
            evidence_ref=exc.evidence_ref,
            details={"backend": self.fetcher.name},
        )

    async def _resolve(self, links: list[str]) -> dict[str, str | None] | None:
        """Destinations of /goto links from the backend; None when it offers no resolution."""
        if not links or not isinstance(self.fetcher, LinkResolver):
            return None
        return await self.fetcher.resolve_links(links)


def _with_url[T: (OrganicResult, Link)](item: T, resolved: dict[str, str | None] | None) -> T:
    target = resolved.get(str(item.link)) if resolved and item.url is None else None
    if target:
        # Validate: model_copy(update=...) would store an unvalidated str. A non-http(s)
        # target keeps url unset and is reported as unresolved.
        with contextlib.suppress(ValidationError):
            return type(item).model_validate({**item.model_dump(), "url": target})
    return item
