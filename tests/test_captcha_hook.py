from __future__ import annotations

import pytest
from conftest import FIXTURES
from gsearch_core.captcha import (
    CaptchaChallenge,
    CaptchaSolver,
    NoSolver,
    SolverResult,
    load_solver,
)
from gsearch_parse.challenge import extract_challenge

pytestmark = pytest.mark.anyio

SORRY_URL = "https://www.google.com/sorry/index?continue=https://www.google.com/search%3Fq%3Dsynthetic"


def sorry_html() -> str:
    return (FIXTURES / "synthetic" / "sorry-captcha.html").read_text(encoding="utf-8")


def test_extract_challenge_from_sorry_page() -> None:
    info = extract_challenge(sorry_html(), url=SORRY_URL)
    assert info.kind == "recaptcha"
    assert info.site_key == "SYNTHETIC"
    assert info.form_action == "index"
    assert info.continue_url == "https://www.google.com/search?q=synthetic"
    assert info.hidden_fields["q"] == "SYNTHETIC"


def test_extract_challenge_unknown_page() -> None:
    info = extract_challenge("<html></html>", url=SORRY_URL)
    assert info.kind == "unknown"
    assert info.site_key is None
    assert info.continue_url == "https://www.google.com/search?q=synthetic"


async def test_default_is_no_solver() -> None:
    solver = load_solver(None)
    assert isinstance(solver, NoSolver)
    challenge = CaptchaChallenge(info=extract_challenge(sorry_html(), url=SORRY_URL), page_url=SORRY_URL)
    result = await solver.solve(challenge)
    assert result.status == "unsupported"
    assert result.token is None


class _EchoSolver:
    """Test double standing in for an operator-supplied solver."""

    name = "echo"

    async def solve(self, challenge: CaptchaChallenge) -> SolverResult:
        return SolverResult(status="solved", token=f"token-for-{challenge.info.site_key}")


def make_echo() -> _EchoSolver:
    return _EchoSolver()


async def test_load_operator_solver_from_spec() -> None:
    solver = load_solver("test_captcha_hook:make_echo")
    assert isinstance(solver, CaptchaSolver)
    challenge = CaptchaChallenge(info=extract_challenge(sorry_html(), url=SORRY_URL), page_url=SORRY_URL)
    result = await solver.solve(challenge)
    assert result.status == "solved"
    assert result.token == "token-for-SYNTHETIC"


def test_bad_specs_are_rejected() -> None:
    with pytest.raises(ValueError, match="module:attribute"):
        load_solver("no_colon_here")
    with pytest.raises(TypeError, match="does not implement CaptchaSolver"):
        load_solver("builtins:object")
