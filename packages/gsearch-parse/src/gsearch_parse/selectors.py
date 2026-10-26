"""Versioned selector sets.

Each set is data, not code: semantic anchors only (ids, ARIA, data-* attributes, tag structure).
Obfuscated class names are deliberately absent (the historical `div.g` anchor is already gone).
A new Google layout means a NEW set (and new fixtures); old sets stay so old fixtures keep passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Engine = Literal["gecko", "blink"]


@dataclass(frozen=True, slots=True)
class SelectorSet:
    name: str
    engine: Engine
    # Markers that must all be present for this set to claim a page.
    signature: tuple[str, ...]
    organic_container: str
    organic_item: str
    title: str
    cite: str
    snippet: tuple[str, ...]
    ads_containers: tuple[tuple[str, Literal["top", "bottom"]], ...]
    related: str
    page_links: str
    next_page: str
    ai_overview: str
    paa_question: str
    box_heading: str
    box_item: str


GECKO_2026_10 = SelectorSet(
    name="gecko-2026_10",
    engine="gecko",
    signature=("#search", "#rso"),
    organic_container="#rso",
    organic_item="[data-rpos]",
    title="h3",
    cite="cite",
    snippet=('[data-sncf="1"]', '[style*="-webkit-line-clamp"]'),
    ads_containers=(("#tads", "top"), ("#tadsb", "bottom"), ("#bottomads", "bottom")),
    related='#bres a[href^="/search"]',
    # Page numbers are digits inside the pagination table: language-independent (aria-label is localized).
    page_links='[role="navigation"] table td a',
    next_page="#pnnext",
    ai_overview='#m-x-content [data-scope-id="turn"]',
    paa_question="#search [data-q]",
    box_heading='h2, [role="heading"][aria-level="2"]',
    box_item='[role="heading"][aria-level="3"]',
)

SELECTOR_SETS: tuple[SelectorSet, ...] = (GECKO_2026_10,)
