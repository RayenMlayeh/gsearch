"""MCP integration test: spawn the real server over stdio and drive it with the official MCP client."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator

import pytest
from conftest import GECKO
from gsearch_parse.models import SearchError, SearchResponse
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.anyio


@pytest.fixture
async def mcp_client() -> AsyncIterator[Client]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gsearch_mcp.server"],
        env={**os.environ, "GSEARCH_FIXTURES_DIR": str(GECKO)},
    )
    async with Client(params, read_timeout_seconds=30) as client:
        yield client


async def test_tools_are_listed_with_schemas(mcp_client: Client) -> None:
    tools = {t.name: t for t in (await mcp_client.list_tools()).tools}
    assert set(tools) == {"google_search", "google_search_status"}
    search = tools["google_search"]
    assert set(search.input_schema["properties"]) == {
        "query",
        "num",
        "page",
        "lang",
        "country",
        "time_range",
        "site",
    }
    assert search.input_schema["required"] == ["query"]
    assert search.output_schema is not None and "organic" in search.output_schema["properties"]
    assert "untrusted" in (search.description or "")
    assert search.annotations is not None and search.annotations.read_only_hint


async def test_search_returns_structured_results(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("google_search", {"query": "how tall is the eiffel tower"})
    assert not result.is_error
    resp = SearchResponse.model_validate(result.structured_content)
    assert resp.organic[0].title == "Eiffel Tower"
    assert resp.ai_overview is not None
    assert len(resp.people_also_ask) == 4
    assert resp.meta.untrusted_content is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "untrusted" in text and "<html" not in text.lower()


async def test_block_is_tool_error_with_retry_after(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("google_search", {"query": "synthetic captcha"})
    assert result.is_error
    assert result.structured_content is not None
    err = SearchError.model_validate(result.structured_content["error"])
    assert err.code == "captcha"
    assert err.retry_after_s is not None and err.retry_after_s > 0


async def test_invalid_arguments_are_rejected(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("google_search", {"query": "x", "country": "USA"})
    assert result.is_error


async def test_status_reports_cache_and_offline_backend(mcp_client: Client) -> None:
    await mcp_client.call_tool("google_search", {"query": "wikipedia"})
    await mcp_client.call_tool("google_search", {"query": "wikipedia"})
    result = await mcp_client.call_tool("google_search_status", {})
    assert not result.is_error and result.structured_content is not None
    status = result.structured_content
    assert status["backend"] == "fixture" and status["live"] is False
    assert status["cache_hits"] == 1
