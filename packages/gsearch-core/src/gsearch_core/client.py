"""SearchClient: the one entry point the CLI and MCP adapters use.

Blocks and parser drift are returned as SearchError, never as an empty result list.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from gsearch_parse import PARSER_VERSION, parse_page
from gsearch_parse.models import (
    ErrorCode,
    OrganicResult,
    PageState,
    ResponseMeta,
    SearchError,
    SearchRequest,
    SearchResponse,
)
from pydantic import ValidationError

from .cache import CacheStats, QueryCache
from .fetch import Fetcher, FetchError, LinkResolver


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
            return SearchError(
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                retry_after_s=exc.retry_after_s,
                remediation=_FETCH_REMEDIATION.get(exc.code, "Check the backend configuration."),
                evidence_ref=exc.evidence_ref,
                details={"backend": self.fetcher.name},
            )

        result = parse_page(page.html, url=page.url, status=page.status, headers=page.headers, page=req.page)
        if result.state in (PageState.OK, PageState.EMPTY_VALID) and result.data is not None:
            data = result.data
            organic, warnings = await self._resolve(data.organic)
            resp = SearchResponse(
                request=req,
                organic=organic,
                ads=data.ads,
                featured_snippet=data.featured_snippet,
                ai_overview=data.ai_overview,
                people_also_ask=data.people_also_ask,
                related_searches=data.related_searches,
                pagination=data.pagination,
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

    async def _resolve(self, organic: list[OrganicResult]) -> tuple[list[OrganicResult], list[str]]:
        """Fill `url` from the backend's /goto resolution, when it offers one."""
        pending = [str(r.link) for r in organic if r.url is None]
        if not pending or not isinstance(self.fetcher, LinkResolver):
            return organic, []
        resolved = await self.fetcher.resolve_links(pending)
        out: list[OrganicResult] = []
        for r in organic:
            target = resolved.get(str(r.link)) if r.url is None else None
            if target:
                # Validate: model_copy(update=...) would store an unvalidated str. A non-http(s)
                # target keeps url unset and is reported below.
                with contextlib.suppress(ValidationError):
                    r = OrganicResult.model_validate({**r.model_dump(), "url": target})
            out.append(r)
        missing = sum(1 for r in out if r.url is None)
        return out, ([f"{missing} result URL(s) could not be resolved"] if missing else [])
