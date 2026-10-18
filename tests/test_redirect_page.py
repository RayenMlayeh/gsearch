from __future__ import annotations

from conftest import FIXTURES
from gsearch_parse.redirect import redirect_target


def test_meta_refresh_is_preferred_and_unescaped() -> None:
    page = (FIXTURES / "synthetic" / "url-interstitial.html").read_text(encoding="utf-8")
    assert redirect_target(page) == "https://example.org/path?a=1&b=2"


def test_script_variable_fallback_with_js_escapes() -> None:
    page = r"<script>var redirectUrl='https:\/\/example.org\/p?x\x3d1\u0026y\x3d2';</script>"
    assert redirect_target(page) == "https://example.org/p?x=1&y=2"


def test_not_a_redirect_page() -> None:
    assert redirect_target("<html><body>results</body></html>") is None
    assert redirect_target("<script>var redirectUrl='javascript:alert(1)';</script>") is None
