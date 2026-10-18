"""Measure your own proxy: N searches through exactly the path the MCP server uses, then a summary.

Settings come from the environment and `.env`. GSEARCH_PROXY and GSEARCH_DATA_LIMIT_MB are required, so a
benchmark can never overspend a data plan. Searches run as many at once as the tool itself allows.

usage: python tools/benchmark.py [-n 10]
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time

from gsearch_core.client import SearchClient
from gsearch_core.config import default_env, fetcher_from_env
from gsearch_core.pool import PARALLEL, ProxyPool
from gsearch_parse.models import SearchError, SearchRequest

SUBJECTS = [
    "olive oil", "the roman empire", "machine learning", "bicycle gears", "coral reefs", "jazz music",
    "sourdough", "electric cars", "the silk road", "chess openings", "rainforests", "quantum computing",
    "origami", "volcanoes", "honey bees", "solar energy", "glaciers", "lighthouses", "telescopes",
]  # fmt: skip
TOPICS = ["history", "how it works", "for beginners", "pros and cons", "facts", "tips", "explained"]


async def run(n: int) -> int:
    env = default_env()
    if not env.get("GSEARCH_PROXY") or not env.get("GSEARCH_DATA_LIMIT_MB"):
        print("Set GSEARCH_PROXY and GSEARCH_DATA_LIMIT_MB (environment or .env) first.", file=sys.stderr)
        return 2
    pool, _ = fetcher_from_env(env)
    assert isinstance(pool, ProxyPool)
    parallel = PARALLEL if pool.rotating else len(pool.config.proxies)
    client = SearchClient(pool)

    queries = [f"{s} {t}" for s in SUBJECTS for t in TOPICS]
    random.shuffle(queries)
    queue: asyncio.Queue[str] = asyncio.Queue()
    for q in queries[:n]:
        queue.put_nowait(q)
    served: list[float] = []
    links = resolved = 0
    mb_before, started = pool.meter.total / 1e6, time.monotonic()

    async def worker() -> None:
        nonlocal links, resolved
        while not queue.empty():
            query = queue.get_nowait()
            t = time.monotonic()
            result = await client.search(SearchRequest(query=query))
            took = time.monotonic() - t
            if isinstance(result, SearchError):
                print(f"  not served  {took:5.1f} s  {query!r}: {result.code}: {result.message[:80]}")
                if result.code == "proxy" and not result.retryable:  # byte cap reached
                    while not queue.empty():
                        queue.get_nowait()
                continue
            ok = sum(r.url is not None for r in result.organic)
            served.append(took)
            links += len(result.organic)
            resolved += ok
            print(
                f"  served      {took:5.1f} s  {query!r}: {len(result.organic)} results, {ok} links resolved"
            )

    kind = "rotating" if pool.rotating else f"{len(pool.config.proxies)} static"
    print(f"{n} searches, up to {parallel} at once, through {kind} proxies ({pool.status()['proxy']})")
    try:
        await asyncio.gather(*(worker() for _ in range(parallel)))
    finally:
        await pool.aclose()

    wall = time.monotonic() - started
    mb = pool.meter.total / 1e6 - mb_before
    s = pool.stats
    print()
    print(f"served      {len(served)}/{n} searches")
    print(
        f"exits       {s['spawned']} exits opened; {s['blocked']} blocked, {s['exits_dead']} dead, "
        f"{s['exits_wrong_country']} wrong country"
    )
    if served:
        k = len(served)
        print(f"per search  {s['spawned'] / k:.1f} exits, {mb / k:.2f} MB (failed exits included)")
        print(
            f"time        median {statistics.median(served):.0f} s, best {min(served):.0f} s per served "
            f"search; {k / wall * 3600:.0f} served/hour"
        )
        print(f"links       {resolved}/{links} resolved")
    print(f"data        {mb:.1f} MB this run, {pool.meter.total / 1e6:.0f} MB toward the limit")
    return 0 if served else 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", "--searches", type=int, default=10)
    args = parser.parse_args()
    return asyncio.run(run(args.searches))


if __name__ == "__main__":
    raise SystemExit(main())
