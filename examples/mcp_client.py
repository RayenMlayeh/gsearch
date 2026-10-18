"""Call gsearch the way an AI agent does: start `gsearch-mcp` over stdio and call `google_search`.

    python examples/mcp_client.py "what is asyncio in python"                 # live, with your settings
    GSEARCH_FIXTURES_DIR=fixtures/gecko python examples/mcp_client.py "what is asyncio in python"   # offline

Live searches use your GSEARCH_* settings (or a .env in the working directory). Without a proxy they run on
your own IP, at most 20 an hour.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from mcp import Client, StdioServerParameters


async def main(query: str) -> int:
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "gsearch_mcp.server"], env={**os.environ}
    )
    async with Client(server, read_timeout_seconds=180) as client:
        print(f'google_search(query="{query}")')
        started = time.perf_counter()
        result = await client.call_tool("google_search", {"query": query})
        seconds = time.perf_counter() - started
        data = result.structured_content or {}
        if result.is_error:
            err = data.get("error", {})
            print(f"error: {err.get('code')}: {err.get('message')}, retry after {err.get('retry_after_s')} s")
            return 1
        print(f"{len(data['organic'])} results in {seconds:.1f} s\n")
        for item in data["organic"][:6]:
            print(f"{item['position']:>2}. {item['title']}\n    {item['url'] or item['link']}")
        if len(data["organic"]) > 6:
            print(f"    … and {len(data['organic']) - 6} more")
        if data.get("ai_overview"):
            print(f"\nAI Overview: {data['ai_overview']['text'][:100]}…")
        for question in data.get("people_also_ask", [])[:3]:
            print(f"People also ask: {question['question']}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]) or "what is asyncio in python")))
