"""Extraction of SERP components using one SelectorSet. Pure functions over a parsed tree."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .models import Ad, AIOverview, Link, OrganicResult, Pagination, PeopleAlsoAsk
from .selectors import SelectorSet

GOOGLE_ORIGIN = "https://www.google.com"
# Google marks UI labels ("AI Mode replied:", ...) with this attribute; they are never content.
UI_CHROME_ATTR = "data-ignore-copy"
_BREADCRUMB_SEP = chr(0x203A)  # single right-pointing angle quotation mark
_GOOGLE_HOST = re.compile(r"(^|\.)google\.[a-z.]{2,6}$")
# Date prefix before a snippet: "Jul 30, 2025 <sep> text", sep = em dash, middle dot or en dash.
_DATE_SEPS = chr(0x2014) + chr(0x00B7) + chr(0x2013)
_DATE_PREFIX = re.compile(rf"^(?P<date>[^{_DATE_SEPS}]{{3,40}}?)\s*[{_DATE_SEPS}]\s+(?P<rest>.+)$", re.S)


def text_of(node: LexborNode | None) -> str:
    if node is None:
        return ""
    return " ".join(node.text(separator=" ").split())


def absolute_link(href: str | None) -> str | None:
    if not href or href.startswith(("#", "javascript:")):
        return None
    return urljoin(GOOGLE_ORIGIN + "/", href)


def _is_search_host(host: str) -> bool:
    """www.google.<tld> and google.<tld> serve redirects.

    Other Google hosts (play., support.) are real destinations.
    """
    return bool(_GOOGLE_HOST.search(host)) and host.split(".")[0] in ("www", "google")


def destination(link: str) -> str | None:
    """Destination URL if the served link reveals it; None for opaque /goto redirects."""
    parts = urlsplit(link)
    host = parts.hostname or ""
    if parts.scheme in ("http", "https") and host and not _is_search_host(host):
        return link
    if _is_search_host(host) and parts.path == "/url":
        for key in ("q", "url"):
            values = parse_qs(parts.query).get(key)
            if values and values[0].startswith(("http://", "https://")):
                return values[0]
    return None


def _ancestor_link(node: LexborNode) -> LexborNode | None:
    cur: LexborNode | None = node
    while cur is not None:
        if cur.tag == "a" and cur.attributes.get("href"):
            return cur
        cur = cur.parent
    return None


_HOST_LIKE = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/|$)", re.I)


def _breadcrumb(cite_text: str) -> tuple[str | None, str | None, str | None]:
    """(display_url, domain, source_meta). Forum/video/social results put metadata such as
    '2 years ago' or '835.5K+ followers' in <cite> instead of a URL breadcrumb."""
    if not cite_text:
        return None, None, None
    head = cite_text.split(_BREADCRUMB_SEP)[0].strip()
    if not _HOST_LIKE.match(head):
        return None, None, cite_text
    host = urlsplit(head if "://" in head else f"https://{head}").hostname
    return cite_text, host, None


def _is_destination_link(link: str) -> bool:
    parts = urlsplit(link)
    if not _is_search_host(parts.hostname or ""):
        return not (parts.hostname or "").startswith("support.google.")
    return parts.path in ("/goto", "/url")


def _split_date(snippet: str) -> tuple[str | None, str]:
    m = _DATE_PREFIX.match(snippet)
    if m and any(ch.isdigit() for ch in m.group("date")):
        return m.group("date").strip(), m.group("rest").strip()
    return None, snippet


def _first(node: LexborNode, selectors: tuple[str, ...]) -> LexborNode | None:
    for sel in selectors:
        found = node.css_first(sel)
        if found is not None:
            return found
    return None


def extract_organic(tree: LexborHTMLParser, s: SelectorSet) -> tuple[list[OrganicResult], list[str]]:
    warnings: list[str] = []
    container = tree.css_first(s.organic_container)
    if container is None:
        return [], warnings
    results: list[OrganicResult] = []
    seen: set[str] = set()
    for item in container.css(s.organic_item):
        h3 = next((h for h in item.css(s.title) if UI_CHROME_ATTR not in h.attributes), None)
        if h3 is None:
            continue  # videos, PAA and other non-web blocks carry no (content) h3
        anchor = _ancestor_link(h3)
        link = absolute_link(anchor.attributes.get("href") if anchor else None)
        title = text_of(h3)
        if anchor is None or link is None or not title:
            warnings.append(f"organic item without title link (data-rpos={item.attributes.get('data-rpos')})")
            continue
        if link in seen:
            continue  # nested [data-rpos] wrappers repeat the same result
        seen.add(link)
        display_url, domain, source_meta = _breadcrumb(text_of(item.css_first(s.cite)))
        snippet_raw = text_of(_first(item, s.snippet)) or None
        date, snippet = _split_date(snippet_raw) if snippet_raw else (None, None)
        sitelinks: list[Link] = []
        for a in item.css("a[href]"):
            if a.css_first(s.title) is not None or a.css_first(s.cite) is not None:
                continue
            sl_title = text_of(a)
            sl_link = absolute_link(a.attributes.get("href"))
            if sl_title and sl_link and sl_link != link and _is_destination_link(sl_link):
                sitelinks.append(Link(title=sl_title, link=sl_link, url=destination(sl_link)))
        results.append(
            OrganicResult(
                position=len(results) + 1,
                title=title,
                link=link,
                url=destination(link),
                display_url=display_url,
                domain=domain,
                source_meta=source_meta,
                snippet=snippet or None,
                date=date,
                sitelinks=sitelinks,
            )
        )
    return results, warnings


def extract_ads(tree: LexborHTMLParser, s: SelectorSet) -> list[Ad]:
    ads: list[Ad] = []
    for selector, block in s.ads_containers:
        container = tree.css_first(selector)
        if container is None:
            continue
        for h in container.css('[role="heading"], h3'):
            anchor = _ancestor_link(h)
            link = absolute_link(anchor.attributes.get("href") if anchor else None)
            title = text_of(h)
            if not link or not title:
                continue
            ads.append(Ad(position=len(ads) + 1, block=block, title=title, link=link, url=destination(link)))
    return ads


def extract_related(tree: LexborHTMLParser, s: SelectorSet) -> list[str]:
    out: list[str] = []
    for a in tree.css(s.related):
        t = text_of(a)
        if t and t not in out:
            out.append(t)
    return out


def extract_paa(tree: LexborHTMLParser, s: SelectorSet) -> list[PeopleAlsoAsk]:
    out: list[str] = []
    for node in tree.css(s.paa_question):
        if node.css_first("[aria-expanded]") is None:
            continue  # [data-q] also appears on non-PAA widgets (e.g. holding the query itself)
        q = " ".join((node.attributes.get("data-q") or "").split())
        if q and q not in out:
            out.append(q)
    return [PeopleAlsoAsk(question=q) for q in out]


def extract_ai_overview(tree: LexborHTMLParser, s: SelectorSet) -> AIOverview | None:
    node = tree.css_first(s.ai_overview)
    if node is None:
        return None
    sources: list[Link] = []
    seen: set[str] = set()
    for chrome in node.css(f'[{UI_CHROME_ATTR}], [role="heading"], a[href*="/search?"]'):
        chrome.decompose()
    text = text_of(node)
    if not text:
        return None
    for a in node.css("a[href]"):
        link = absolute_link(a.attributes.get("href"))
        if not link or link in seen or "/search?" in link:
            continue
        seen.add(link)
        title = text_of(a) or (a.attributes.get("aria-label") or "")
        sources.append(Link(title=title, link=link, url=destination(link)))
    return AIOverview(text=text, sources=sources)


def extract_pagination(tree: LexborHTMLParser, s: SelectorSet, current: int) -> Pagination | None:
    pages: set[int] = set()
    for a in tree.css(s.page_links):
        text = text_of(a)
        if text.isdigit():  # str.isdigit/int() also accept non-ASCII digits (e.g. Arabic-Indic)
            pages.add(int(text))
    has_next = tree.css_first(s.next_page) is not None
    if not pages and not has_next:
        return None
    pages.add(current)
    return Pagination(current=current, next_page=current + 1 if has_next else None, pages=sorted(pages))
