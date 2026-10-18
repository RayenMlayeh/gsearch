"""Fetch backends. Every backend returns the raw page; classification and parsing happen in the client."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

from gsearch_parse.models import ErrorCode, SearchRequest
from gsearch_parse.selectors import Engine

GOOGLE_SEARCH = "https://www.google.com/search"


def build_search_url(req: SearchRequest) -> str:
    params: list[tuple[str, str]] = [("q", req.effective_query())]
    if req.lang:
        params.append(("hl", req.lang))
    if req.country:
        params.append(("gl", req.country))
    if req.page > 1:
        params.append(("start", str((req.page - 1) * req.num)))
    if req.time_range:
        params.append(("tbs", f"qdr:{req.time_range}"))
    return f"{GOOGLE_SEARCH}?{urlencode(params)}"


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    status: int | None
    html: str
    engine: Engine
    backend: str
    headers: dict[str, str] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    block_retry_after_s: int | None = None  # set by backends that rest an identity after a block
    evidence_ref: str | None = None  # saved HTML/screenshot of a failed or blocked page


class FetchError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool,
        retry_after_s: int | None = None,
        evidence_ref: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.evidence_ref = evidence_ref


class Fetcher(Protocol):
    name: str

    async def fetch(self, req: SearchRequest) -> FetchedPage: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class LinkResolver(Protocol):
    """Optional backend capability: real destinations of Google's opaque /goto links."""

    async def resolve_links(self, links: list[str]) -> dict[str, str | None]: ...


@runtime_checkable
class StatusReporter(Protocol):
    def status(self) -> dict[str, str | int | float | bool | None]: ...


@dataclass(frozen=True, slots=True)
class FixtureEntry:
    id: str
    query: str
    page: int
    file: str
    url: str
    status: int
    engine: Engine
    source: str = ""
    note: str = ""


class FixtureFetcher:
    """Serves saved pages from `<dir>/index.json`. Used by tests, demos and the MCP integration test."""

    name = "fixture"

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        raw = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        self._entries = {(e["query"].casefold(), int(e["page"])): FixtureEntry(**e) for e in raw["fixtures"]}

    async def fetch(self, req: SearchRequest) -> FetchedPage:
        entry = self._entries.get((req.effective_query().casefold(), req.page))
        if entry is None:
            raise FetchError(
                "backend_unavailable",
                f"no fixture for query={req.effective_query()!r} page={req.page}",
                retryable=False,
            )
        path = self.directory / entry.file
        data = path.read_bytes()
        html = (gzip.decompress(data) if path.suffix == ".gz" else data).decode("utf-8")
        return FetchedPage(
            url=entry.url, status=entry.status, html=html, engine=entry.engine, backend=self.name
        )

    async def aclose(self) -> None:
        return None
