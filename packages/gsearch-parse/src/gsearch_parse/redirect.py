"""Destination of Google's result-redirect interstitial (`/url?...`), parsed from its HTML. Pure, no network.

A clicked result goes to `/url?sa=t&...&url=<blob>`, which answers 200 with a ~0.8 KB page carrying the
destination twice: in `<noscript><meta http-equiv="refresh" content="0;url=...">` and in a script variable
`redirectUrl='...'`.
"""

from __future__ import annotations

import html
import re

_META_REFRESH = re.compile(
    r"<meta[^>]+content=\"\s*\d+\s*;\s*url=([^\"]+)\"[^>]*http-equiv=\"refresh\""
    r"|<meta[^>]+http-equiv=\"refresh\"[^>]*content=\"\s*\d+\s*;\s*url=([^\"]+)\"",
    re.IGNORECASE,
)
_SCRIPT_VAR = re.compile(r"redirectUrl\s*=\s*'([^']+)'")
_JS_ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})|\\u([0-9a-fA-F]{4})|\\(/)")


def _unescape_js(value: str) -> str:
    def repl(m: re.Match[str]) -> str:
        if m.group(1):
            return chr(int(m.group(1), 16))
        if m.group(2):
            return chr(int(m.group(2), 16))
        return m.group(3)

    return _JS_ESCAPE.sub(repl, value)


def redirect_target(page: str) -> str | None:
    """The http(s) destination of a Google redirect interstitial, or None if this is not one."""
    m = _META_REFRESH.search(page)
    if m:
        target = html.unescape(m.group(1) or m.group(2)).strip()
    else:
        s = _SCRIPT_VAR.search(page)
        if not s:
            return None
        target = _unescape_js(s.group(1)).strip()
    return target if target.startswith(("http://", "https://")) else None
