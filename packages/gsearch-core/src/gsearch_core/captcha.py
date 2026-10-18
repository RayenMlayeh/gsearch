"""CAPTCHA solver interface. The project ships no solver and wires none in (see docs/DESIGN.md).

Without a solver, a CAPTCHA rests the identity and the caller gets a `captcha` error with retry_after_s.
Anything you implement against this interface, and its legality under the Terms of Service of Google and of
your proxy provider, is your responsibility.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from gsearch_parse.challenge import ChallengeInfo


@dataclass(frozen=True, slots=True)
class CaptchaChallenge:
    """Everything a solver needs, without exposing the browser or identity internals."""

    info: ChallengeInfo
    page_url: str
    user_agent: str | None = None
    proxy_url: str | None = None  # the identity's exit, so a solver can match IP if it needs to
    evidence_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SolverResult:
    status: Literal["solved", "failed", "unsupported"]
    token: str | None = None
    detail: str = ""
    cost_hint: float | None = field(default=None)


@runtime_checkable
class CaptchaSolver(Protocol):
    name: str

    async def solve(self, challenge: CaptchaChallenge) -> SolverResult: ...


class NoSolver:
    """Default: never solves. The identity rests and the caller gets an error."""

    name = "none"

    async def solve(self, challenge: CaptchaChallenge) -> SolverResult:
        return SolverResult(status="unsupported", detail="no solver registered")


def load_solver(spec: str | None) -> CaptchaSolver:
    """Load a solver from 'package.module:attribute' (a class or zero-arg factory).

    None or 'none' gives NoSolver.
    """
    if not spec or spec == "none":
        return NoSolver()
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError(f"solver spec must be 'module:attribute', got {spec!r}")
    factory = getattr(importlib.import_module(module_name), attr)
    solver = factory()
    if not isinstance(solver, CaptchaSolver):
        raise TypeError(f"{spec} does not implement CaptchaSolver (needs .name and async .solve())")
    return solver
