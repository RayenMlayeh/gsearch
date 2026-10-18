"""Breakage detection: when Google changes markup, we must report PARSE_DRIFT, never silent empty data."""

from __future__ import annotations

import re

from conftest import fixture_by_id
from gsearch_parse import PageState, parse_page


def _mutated(fixture_id: str, pattern: str, repl: str) -> str:
    html = fixture_by_id(fixture_id).html()
    out, n = re.subn(pattern, repl, html)
    assert n > 0, "mutation did not apply; the test no longer exercises drift"
    return out


def test_result_attribute_renamed_is_drift() -> None:
    html = _mutated("c0-tutorial", r"data-rpos=", "data-rank=")
    result = parse_page(html, url="https://www.google.com/search?q=x")
    assert result.state is PageState.PARSE_DRIFT
    assert "zero organic" in result.reason


def test_results_container_renamed_is_drift() -> None:
    html = _mutated("c0-tutorial", r'id="rso"', 'id="results"')
    result = parse_page(html, url="https://www.google.com/search?q=x")
    assert result.state is PageState.PARSE_DRIFT


def test_titles_moved_out_of_h3_is_drift() -> None:
    html = _mutated("c1-navigational", r"<(/?)h3", r"<\1div")
    result = parse_page(html, url="https://www.google.com/search?q=x")
    assert result.state is PageState.PARSE_DRIFT
