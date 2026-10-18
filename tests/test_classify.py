from __future__ import annotations

from conftest import FIXTURES, fixture_by_id
from gsearch_parse import PageState, classify, parse_page

SYNTHETIC = FIXTURES / "synthetic"


def test_sorry_url_is_captcha_even_without_body() -> None:
    c = classify("", url="https://www.google.com/sorry/index?continue=x", status=429)
    assert c.state is PageState.CAPTCHA


def test_redirect_to_sorry_is_captcha_with_hallmonitor_signal() -> None:
    c = classify(
        "",
        url="https://www.google.com/search?q=x&sei=y",
        status=302,
        headers={
            "Location": "https://www.google.com/sorry/index?continue=x",
            "X-Hallmonitor-Challenge": "abc",
        },
    )
    assert c.state is PageState.CAPTCHA
    assert "header:x-hallmonitor-challenge" in c.signals


def test_synthetic_sorry_page() -> None:
    html = (SYNTHETIC / "sorry-captcha.html").read_text(encoding="utf-8")
    result = parse_page(html, url="https://www.google.com/search?q=x", status=200)
    assert result.state is PageState.CAPTCHA  # form#captcha-form, even off the /sorry/ URL


def test_synthetic_js_gate() -> None:
    html = (SYNTHETIC / "js-gate.html").read_text(encoding="utf-8")
    result = parse_page(html, url="https://www.google.com/search?q=x", status=200)
    assert result.state is PageState.JS_GATE
    assert set(result.signals) == {"html:enablejs-refresh", "html:SG_SS-writer"}


def test_consent_redirect() -> None:
    c = classify("", url="https://consent.google.com/m?continue=x", status=200)
    assert c.state is PageState.CONSENT_WALL


def test_status_codes_without_challenge_page() -> None:
    assert (
        classify("<html></html>", url="https://www.google.com/search?q=x", status=403).state
        is PageState.HARD_BLOCK
    )
    assert (
        classify("<html></html>", url="https://www.google.com/search?q=x", status=429).state
        is PageState.SOFT_BLOCK
    )


def test_unknown_page_is_drift_not_empty() -> None:
    result = parse_page("<html><body><p>hello</p></body></html>", url="https://www.google.com/search?q=x")
    assert result.state is PageState.PARSE_DRIFT


def test_results_page_is_ok() -> None:
    f = fixture_by_id("c1-navigational")
    assert parse_page(f.html(), url=f.url).state is PageState.OK
