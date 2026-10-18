"""Short-lived query cache. An agent repeating a query gets the cached answer instead of a new fetch."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from gsearch_parse.models import SearchRequest, SearchResponse


@dataclass(frozen=True, slots=True)
class CacheStats:
    size: int
    hits: int
    misses: int
    ttl_s: float


class QueryCache:
    def __init__(
        self, ttl_s: float = 600, max_entries: int = 1000, clock: Callable[[], float] = time.monotonic
    ):
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, SearchResponse]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def key(req: SearchRequest) -> str:
        return req.model_dump_json()

    def get(self, req: SearchRequest) -> SearchResponse | None:
        k = self.key(req)
        item = self._items.get(k)
        if item is None or self._clock() - item[0] > self.ttl_s:
            self._items.pop(k, None)
            self._misses += 1
            return None
        self._hits += 1
        self._items.move_to_end(k)
        return item[1]

    def put(self, req: SearchRequest, resp: SearchResponse) -> None:
        self._items[self.key(req)] = (self._clock(), resp)
        self._items.move_to_end(self.key(req))
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def stats(self) -> CacheStats:
        return CacheStats(size=len(self._items), hits=self._hits, misses=self._misses, ttl_s=self.ttl_s)
