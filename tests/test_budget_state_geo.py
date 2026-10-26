"""Offline tests for pacing, durable identity state and exit-country language (no browser, no network)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from gsearch_core.pacing import PacingPolicy, lognormal_between
from gsearch_core.state import StateStore


class FakeClock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_hourly_tokens_burst_refill_and_are_shared(tmp_path: Path) -> None:
    clock = FakeClock()
    rate = 20 / 3600
    first = StateStore(tmp_path / "s.sqlite", clock)
    second = StateStore(tmp_path / "s.sqlite", clock)  # e.g. a second MCP server on the same machine
    assert first.take_token("home", rate, 2) == 0.0
    assert second.take_token("home", rate, 2) == 0.0
    assert first.take_token("home", rate, 2) == pytest.approx(180.0)  # 20/hour -> one token every 180 s
    clock.t += 90
    assert second.token_wait_s("home", rate, 2) == pytest.approx(90.0)
    clock.t += 90
    assert second.take_token("home", rate, 2) == 0.0
    first.close()
    second.close()


def test_lognormal_delay_stays_in_window() -> None:
    rng = random.Random(7)
    values = [lognormal_between(rng, 2.0, 9.0) for _ in range(2000)]
    assert min(values) >= 2.0 and max(values) <= 9.0
    assert 3.0 < sorted(values)[1000] < 8.0  # median near the window's middle


def test_conservative_defaults() -> None:
    p = PacingPolicy()
    assert (p.searches_per_hour, p.searches_per_day) == (20.0, 150)


def test_state_counts_per_day_and_survives_reopen(tmp_path: Path) -> None:
    clock = FakeClock()
    db = tmp_path / "state.sqlite"
    store = StateStore(db, clock)
    store.record_search("home")
    store.record_search("home")
    store.record_resolve("home")
    assert store.today("home") == {"searches": 2, "resolves": 1, "captchas": 0}
    store.close()

    reopened = StateStore(db, clock)  # e.g. the MCP server restarted
    assert reopened.searches_today("home") == 2
    clock.t += 86_400  # next day
    assert reopened.searches_today("home") == 0
    reopened.close()


def test_rest_period_expires(tmp_path: Path) -> None:
    clock = FakeClock()
    store = StateStore(tmp_path / "s.sqlite", clock)
    store.record_captcha("home", rest_until=clock.t + 600, reason="captcha")
    resting = store.resting_until("home")
    assert resting is not None and resting[1] == "captcha"
    assert store.today("home")["captchas"] == 1
    clock.t += 601
    assert store.resting_until("home") is None
    store.close()


def test_main_language_from_cldr() -> None:
    geo = pytest.importorskip("gsearch_core.geo")
    assert geo.main_language("TN") == "ar-TN"
    assert geo.main_language("FR") == "fr-FR"
    assert geo.main_language("US") == "en-US"
    assert geo.main_language("AE") == "ar-AE"
    assert geo.main_language("DE") == "de-DE"
    assert geo.Place(ip="1.2.3.4", country="TN", timezone="Africa/Tunis", locale="ar-TN").language == "ar"
