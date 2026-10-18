"""Rate budgets and human-shaped delays. Clock and RNG are injectable for tests."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PacingPolicy:
    """Conservative home-IP defaults."""

    searches_per_hour: float = 20.0
    burst: float = 2.0
    searches_per_day: int = 150
    think_min_s: float = 2.0
    think_max_s: float = 9.0
    resolve_gap_min_s: float = 0.8
    resolve_gap_max_s: float = 2.5


class TokenBucket:
    """Fractional token bucket: `rate_per_s` refill, `capacity` burst."""

    def __init__(
        self, rate_per_s: float, capacity: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if rate_per_s <= 0 or capacity < 1:
            raise ValueError("rate must be > 0 and capacity >= 1")
        self.rate = rate_per_s
        self.capacity = capacity
        self._clock = clock
        self._tokens = capacity
        self._at = clock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._at) * self.rate)
        self._at = now

    def wait_s(self) -> float:
        self._refill()
        return 0.0 if self._tokens >= 1 else (1 - self._tokens) / self.rate

    def take(self) -> None:
        self._refill()
        if self._tokens < 1:
            raise RuntimeError("take() without an available token; call wait_s() first")
        self._tokens -= 1


def lognormal_between(rng: random.Random, low: float, high: float, sigma: float = 0.45) -> float:
    """A human-shaped delay: lognormal around the window's midpoint, clamped to [low, high]."""
    mu = math.log((low + high) / 2)
    return min(high, max(low, rng.lognormvariate(mu, sigma)))
