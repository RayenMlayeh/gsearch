"""Capture fresh results pages for the fixture cases in one human-paced Camoufox session (live Google).

Types each query into the search box, waits, scrolls and saves HTML + a screenshot. Stops at the first
/sorry page so an identity is never pushed past a block. Raw captures contain cookies and session tokens:
write them under captures/ (gitignored) and turn them into fixtures with tools/minimize_fixture.py.

usage: python tools/collect_fixtures.py captures/<date> [--min-gap 45] [--max-gap 90]
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from camoufox.addons import DefaultAddons
from camoufox.sync_api import Camoufox

CASES: list[dict[str, str]] = [
    {"id": "c1-navigational", "query": "wikipedia"},
    {"id": "c2-featured-snippet", "query": "how tall is the eiffel tower"},
    {"id": "c3-paa", "query": "what is asyncio in python", "page2": "1"},
    {"id": "c4-ads", "query": "car insurance quote"},
    {"id": "c5-site-filter", "query": "asyncio site:python.org", "tbs": "qdr:y"},
    {"id": "c6-empty", "query": "zxqv qwpl mmrt kkhh 9931"},
    {"id": "c7-french", "query": "meilleur restaurant tunis"},
    {"id": "c8-news", "query": "nobel prize 2026"},
]

SEARCH_BOX = 'textarea[name="q"], input[name="q"]'


def blocked(url: str) -> bool:
    return "/sorry/" in url


def settle(lo: float = 2.5, hi: float = 4.5) -> None:
    time.sleep(random.uniform(lo, hi))


def navigate_and_wait(page: Any, action: Any) -> None:
    """Run an action that navigates, and wait until the URL actually changed and the page loaded."""
    previous = page.url
    action()
    page.wait_for_url(lambda u: u != previous and ("/search?" in u or "/sorry/" in u))
    page.wait_for_load_state("load")
    settle()


def run_case(page: Any, out: Path, case: dict[str, str]) -> dict[str, object]:
    started = time.time()
    page.mouse.wheel(0, -20000)  # the search box is not sticky; bring it back into view
    settle(0.6, 1.2)
    page.locator(SEARCH_BOX).first.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.type(case["query"], delay=random.randint(70, 160))
    settle(0.4, 1.2)
    navigate_and_wait(page, lambda: page.keyboard.press("Enter"))
    if case.get("tbs") and not blocked(page.url):
        navigate_and_wait(page, lambda: page.goto(page.url + "&tbs=" + case["tbs"], wait_until="commit"))

    entry: dict[str, object] = {"case": case["id"], "query": case["query"], "url": page.url}
    if blocked(page.url):
        entry["state"] = "sorry"
        (out / f"{case['id']}.sorry.html").write_text(page.content(), encoding="utf-8")
        return entry
    page.mouse.wheel(0, 2500)
    settle(1.5, 3)
    (out / f"{case['id']}.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(out / f"{case['id']}.png"), full_page=True)
    entry["state"] = "ok"
    entry["seconds"] = round(time.time() - started, 1)
    if case.get("page2"):
        settle(4, 9)
        navigate_and_wait(page, lambda: page.locator("#pnnext").first.click())
        entry["page2_url"] = page.url
        if blocked(page.url):
            entry["state"] = "sorry"
            return entry
        (out / f"{case['id']}.p2.html").write_text(page.content(), encoding="utf-8")
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--min-gap", type=float, default=45)
    ap.add_argument("--max-gap", type=float, default=90)
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    log: list[dict[str, object]] = []

    # uBlock Origin (loaded by Camoufox by default) would remove the ads from the captures.
    with Camoufox(
        headless=False, locale="en-US", os="windows", humanize=True, exclude_addons=[DefaultAddons.UBO]
    ) as browser:
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            page.goto("https://www.google.com/?hl=en", wait_until="load")
            settle(3, 6)
            for i, case in enumerate(CASES):
                if i:
                    time.sleep(random.uniform(args.min_gap, args.max_gap))
                entry = run_case(page, out, case)
                log.append(entry)
                print(json.dumps(entry), flush=True)
                if entry["state"] != "ok":
                    break
        finally:
            ctx.close()
            (out / "session.json").write_text(json.dumps(log, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
