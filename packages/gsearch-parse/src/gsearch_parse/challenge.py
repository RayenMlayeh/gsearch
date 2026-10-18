"""Extract the parameters of a Google /sorry challenge page. Pure parsing, no network."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from selectolax.lexbor import LexborHTMLParser


@dataclass(frozen=True, slots=True)
class ChallengeInfo:
    kind: str  # "recaptcha" (observed: reCAPTCHA Enterprise) or "unknown"
    site_key: str | None
    data_s: str | None  # per-challenge blob on div.g-recaptcha
    form_action: str | None
    continue_url: str | None
    hidden_fields: dict[str, str]


def extract_challenge(html: str, *, url: str) -> ChallengeInfo:
    tree = LexborHTMLParser(html)
    widget = tree.css_first(".g-recaptcha[data-sitekey]")
    form = tree.css_first("form#captcha-form")
    hidden = {
        name: node.attributes.get("value") or ""
        for node in (form.css("input[type=hidden][name]") if form is not None else [])
        if (name := node.attributes.get("name"))
    }
    from_url = parse_qs(urlsplit(url).query).get("continue")
    continue_url = hidden.get("continue") or (from_url[0] if from_url else None)
    return ChallengeInfo(
        kind="recaptcha" if widget is not None else "unknown",
        site_key=widget.attributes.get("data-sitekey") if widget is not None else None,
        data_s=widget.attributes.get("data-s") if widget is not None else None,
        form_action=form.attributes.get("action") if form is not None else None,
        continue_url=continue_url,
        hidden_fields=hidden,
    )
