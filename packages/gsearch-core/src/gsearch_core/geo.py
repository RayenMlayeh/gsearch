"""Where an identity's exit IP is, and what a local user there would look like.

Uses Camoufox's own data: its public-IP lookup, its GeoLite2-City database and its CLDR territory table.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import cache

from camoufox.geolocation import get_geolocation
from camoufox.ip import public_ip
from camoufox.locales import get_unicode_info


@dataclass(frozen=True, slots=True)
class Place:
    ip: str
    country: str  # ISO 3166-1 alpha-2, upper-case
    timezone: str
    locale: str | None  # e.g. "ar-TN"; None when CLDR has no language data for the country

    @property
    def language(self) -> str:
        """Google `hl` value: the language part of the locale, 'en' if unknown."""
        return (self.locale or "en").split("-")[0]


@cache
def main_language(country: str) -> str | None:
    """The country's most widely spoken *official* language as a locale (TN -> ar-TN, FR -> fr-FR).

    Falls back to the most spoken language when CLDR marks none as official.
    """
    code = country.upper()
    territory = get_unicode_info().find(f"territory[@type='{code}']")
    if territory is None:
        return None
    spoken = territory.findall("languagePopulation")
    official = [p for p in spoken if (p.get("officialStatus") or "").endswith("official")]
    candidates = official or spoken
    if not candidates:
        return None
    best = max(candidates, key=lambda p: float(p.get("populationPercent") or 0.0))
    language = (best.get("type") or "").split("_")[0]
    return f"{language}-{code}" if language else None


def locate(ip: str) -> Place:
    geo = get_geolocation(ip)
    country = (geo.locale.region or "").upper()
    return Place(ip=ip, country=country, timezone=geo.timezone, locale=main_language(country))


async def locate_exit(proxy_url: str | None = None, *, attempts: int = 2) -> Place:
    """Exit IP as seen through `proxy_url` (or directly), then its place. Blocking I/O runs off the loop.

    Retried once: residential exits are often slow to answer their first request.
    """
    for attempt in range(attempts):
        try:
            ip = (
                await asyncio.to_thread(public_ip, proxy_url)
                if proxy_url
                else await asyncio.to_thread(public_ip)
            )
            break
        except Exception:
            if attempt + 1 == attempts:
                raise
    return await asyncio.to_thread(locate, ip)
