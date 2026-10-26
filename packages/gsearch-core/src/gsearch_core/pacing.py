"""Rate budgets and human-shaped delays. The RNG is injectable for tests."""

from __future__ import annotations

import math
import random
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


def lognormal_between(rng: random.Random, low: float, high: float, sigma: float = 0.45) -> float:
    """A human-shaped delay: lognormal around the window's midpoint, clamped to [low, high]."""
    mu = math.log((low + high) / 2)
    return min(high, max(low, rng.lognormvariate(mu, sigma)))
