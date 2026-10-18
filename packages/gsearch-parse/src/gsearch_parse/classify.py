"""Page classification: (status, final URL, headers, HTML) -> PageState.

Signatures marked ASSUMED have no captured example yet and must be confirmed (or removed) when one exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from .models import PageState


@dataclass(frozen=True, slots=True)
class Classification:
    state: PageState
    reason: str
    signals: tuple[str, ...] = field(default=())


def classify(
    html: str,
    *,
    url: str,
    status: int | None = 200,
    headers: Mapping[str, str] | None = None,
    tree: LexborHTMLParser | None = None,
) -> Classification:
    """Classify a fetched page before any extraction is attempted.

    OK / EMPTY_VALID are only *candidates* here; the extractor confirms them (and downgrades to
    PARSE_DRIFT when its invariants fail).
    """
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    parts = urlsplit(url)
    location = hdrs.get("location", "")

    # A block is a 302 to /sorry/index (with x-hallmonitor-challenge), then 429 + a reCAPTCHA form.
    if "/sorry/" in parts.path or "/sorry/" in location:
        signals = ["url:/sorry/"]
        if "x-hallmonitor-challenge" in hdrs:
            signals.append("header:x-hallmonitor-challenge")
        return Classification(PageState.CAPTCHA, "Google /sorry challenge page", tuple(signals))

    t = tree if tree is not None else LexborHTMLParser(html)
    if t.css_first("form#captcha-form") is not None:
        return Classification(PageState.CAPTCHA, "captcha form present", ("dom:form#captcha-form",))

    # ASSUMED: consent-wall signatures, not yet checked against a captured consent page.
    if parts.hostname == "consent.google.com" or "consent.google.com" in location:
        return Classification(PageState.CONSENT_WALL, "consent redirect", ("url:consent.google.com",))
    if t.css_first('form[action*="consent.google"]') is not None:
        return Classification(PageState.CONSENT_WALL, "consent form present", ("dom:consent-form",))

    # ASSUMED: hard/soft block status codes without a /sorry page.
    if status == 403:
        return Classification(PageState.HARD_BLOCK, "HTTP 403", ("status:403",))
    if status in (429, 503):
        return Classification(
            PageState.SOFT_BLOCK, f"HTTP {status} without challenge page", (f"status:{status}",)
        )

    has_results_root = t.css_first("#search") is not None or t.css_first("#rso") is not None

    # The JS gate: no results root; a noscript refresh to enablejs and/or the SG_SS cookie writer.
    if not has_results_root:
        gate_signals = []
        if "/httpservice/retry/enablejs" in html:
            gate_signals.append("html:enablejs-refresh")
        if "SG_SS=" in html:
            gate_signals.append("html:SG_SS-writer")
        if gate_signals:
            return Classification(
                PageState.JS_GATE, "SearchGuard JS gate (no results served)", tuple(gate_signals)
            )

    if has_results_root:
        return Classification(PageState.OK, "results root present", ("dom:#search",))

    return Classification(PageState.PARSE_DRIFT, "no known page signature matched", ())
