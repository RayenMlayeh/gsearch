from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("langchain_core", reason="optional example dependency")
pytestmark = pytest.mark.anyio

spec = importlib.util.spec_from_file_location(
    "langchain_example", Path(__file__).resolve().parents[1] / "examples" / "langchain_tool.py"
)
assert spec is not None and spec.loader is not None
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


async def test_saved_query_returns_results() -> None:
    result = json.loads(await example.google_search.ainvoke({"query": "what is asyncio in python"}))
    assert result["organic"]
    assert result["meta"]["backend"] == "fixture"


async def test_missing_query_returns_search_error() -> None:
    result = json.loads(await example.google_search.ainvoke({"query": "not captured"}))
    assert result["code"] == "backend_unavailable"
    assert result["retryable"] is False
