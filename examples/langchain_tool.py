"""Offline LangChain tool example.

Install: pip install gsearch-agent langchain-core
Run from the checkout root: python examples/langchain_tool.py
For live use, get the fetcher from gsearch_core.config.fetcher_from_env().
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from gsearch_core.client import SearchClient  # noqa: TID251
from gsearch_core.fetch import FixtureFetcher  # noqa: TID251
from gsearch_parse.models import SearchRequest
from langchain_core.tools import tool


@tool
async def google_search(query: str) -> str:
    """Search saved Google results and return typed results as JSON."""
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "gecko"
    fetcher = FixtureFetcher(fixtures)
    client = SearchClient(fetcher)
    try:
        response = await client.search(SearchRequest(query=query))
        return json.dumps(response.model_dump(mode="json"), ensure_ascii=True)
    finally:
        await fetcher.aclose()


if __name__ == "__main__":
    print(asyncio.run(google_search.ainvoke({"query": "what is asyncio in python"})))
