"""Offline tests for pacing, durable identity state and exit-country language (no browser, no network)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from gsearch_core.pacing import PacingPolicy, TokenBucket, lognormal_between
from gsearch_core.state import StateStore


class FakeClock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_token_bucket_burst_then_refill() -> None:
    clock = FakeClock(0.0)
    bucket = TokenBucket(rate_per_s=20 / 3600, capacity=2, clock=clock)
    bucket.take()
    bucket.take()
    assert bucket.wait_s() == pytest.approx(180.0)  # 20/hour -> one token every 180 s
    clock.t += 90
    assert bucket.wait_s() == pytest.approx(90.0)
    clock.t += 90
    assert bucket.wait_s() == 0.0
    with pytest.raises(ValueError, match="rate must be"):
        TokenBucket(rate_per_s=0, capacity=1, clock=clock)


def test_take_without_token_is_a_bug() -> None:
    bucket = TokenBucket(rate_per_s=1.0, capacity=1, clock=FakeClock(0.0))
    bucket.take()
    with pytest.raises(RuntimeError, match="without an available token"):
        bucket.take()


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
