"""Readable text and links of any web page. Pure, no network."""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin, urlsplit

from pydantic import ValidationError
from selectolax.lexbor import LexborHTMLParser

from .extract import text_of
from .models import PageContent, PageLink

MAX_CHARS = 30_000
MAX_LINKS = 100
_NOISE = (
    "script, style, noscript, svg, template, iframe, nav, header, footer, aside, form,"
    " [role=navigation], [role=banner], [role=contentinfo], [role=search], [role=complementary]"
)
_BLOCK = re.compile(
    r"<(?=/?(?:p|div|br|h[1-6]|li|dt|dd|tr|pre|blockquote|section|article|main|table|ul|ol)\b)", re.I
)
_BREAK = chr(0x2029)


def readable(html: str, *, url: str) -> PageContent:
    tree = LexborHTMLParser(_BLOCK.sub(_BREAK + "<", html))
    title = text_of(tree.css_first("title")) or None
    for junk in tree.css(_NOISE):
        junk.decompose()
    root = tree.css_first("main, [role=main]") or tree.css_first("article") or tree.body
    blocks = (" ".join(b.split()) for b in (root.text() if root else "").split(_BREAK))
    text = "\n".join(b for b in blocks if b)
    links: list[PageLink] = []
    seen = {url}
    for a in root.css("a[href]") if root else []:
        href = urldefrag(urljoin(url, a.attributes.get("href") or "")).url
        label = text_of(a)
        if not label or href in seen or urlsplit(href).scheme not in ("http", "https"):
            continue
        seen.add(href)
        try:
            links.append(PageLink(text=label, url=href))
        except ValidationError:
            continue
        if len(links) == MAX_LINKS:
            break
    return PageContent(
        url=url, title=title, text=text[:MAX_CHARS], truncated=len(text) > MAX_CHARS, links=links
    )
