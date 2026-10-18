"""Committed fixtures must not carry session tokens from the capture (tools/minimize_fixture.py)."""

from __future__ import annotations

import re

import pytest
from conftest import Fixture, gecko_fixtures

LEAKS = {
    "raw /goto blob": re.compile(r"/goto\?url=CAES"),
    "sxsrf token": re.compile(r"APpeQ[A-Za-z0-9_-]{10}"),
    "iflsig token": re.compile(r"ABILxe[A-Za-z0-9_-]{10}"),
    "event id param": re.compile(r"[?&;]ei=[A-Za-z0-9_-]{16}"),
    "script tag": re.compile(r"<script[\s>]", re.I),
}


@pytest.mark.parametrize("fx", [f for f in gecko_fixtures() if not f.id.startswith("s")], ids=lambda f: f.id)
def test_fixture_has_no_session_tokens(fx: Fixture) -> None:
    html = fx.html()
    found = {name for name, rx in LEAKS.items() if rx.search(html)}
    assert not found, f"{fx.id}: {sorted(found)}; re-run tools/minimize_fixture.py"
