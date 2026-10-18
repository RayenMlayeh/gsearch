# F-0009 E-1: a human in Brave on the home IP is served; `/goto` is a plain server redirect

- **Date:** 2026-10-17, 00:33–00:38 UTC.
- **Browser:** Brave on Chrome/155.0.8059.40, fresh profile `%LOCALAPPDATA%\gsearch-brave-baseline`, `--remote-debugging-port=9222`, logged out. Launched by the agent; **a human typed and clicked**.
- **Network:** home IP (TN), no proxy. The same IP as F-0001.
- **rea:**
  - `list_browser_targets` `ev_e6a72507…`, `ev_9e4de069…`;
  - `observe_web_session` `ev_9f71c5b5…` (first window: no search happened in it);
  - `observe_web_session` `ev_47d8ba1f…` (allowed origins `www.google.com`, `docs.python.org`; Back + click).

## Observations

1. **OBSERVED-run (E-1).** A human searched `what is asyncio in python` by hand and was served a results page, with no `/sorry` or CAPTCHA, then reached the first result. Together with F-0001, this indicates the earlier `/sorry` on Playwright-driven Chrome came from **automation signals, not IP reputation**. n=1, and the arms were not interleaved, so this is strong but not conclusive.
2. **OBSERVED-rea.** The human's results URL ends in `&sei=BcPS…` (seq 6/9). The **JS gate → `&sei=` reload** of F-0002 applies to a real Blink browser too; it is not an automation-only interstitial.
3. **OBSERVED-rea.** The click was `navigation_requested` (`anchorclick`) to `https://www.google.com/goto?url=CAESaQHrOzAV…` (seq 24), then a `redirect` straight to `https://docs.python.org/3/library/asyncio.html` (seq 25), then commit. That means:
   - **Blink markup also uses opaque `/goto` links** (F-0006 is not Gecko-specific);
   - `/goto` resolves with a **server redirect, no interstitial page**. Its target is known from the redirect response alone, without loading the destination.
4. **OBSERVED-rea.** One `load_failed` with `net::ERR_BLOCKED_BY_CLIENT` (seq 7). Brave was still blocking something on google.com, so this profile is not a clean ads baseline yet.

## Not captured

- Redirect status code, headers and cookies (`observe_web_session` keeps navigation metadata only).
- The Blink SERP DOM.

## Consequence for "always resolve URLs" (D-008)

- **Mechanism:** request each `/goto` link **inside the same browser session** (same cookies, TLS and IP as the search), capture the redirect target, and **abort the request to the destination**. Cost is one Google request per result, with no destination bandwidth.
  - Using Playwright's `APIRequestContext` would send the request from a non-browser network stack with a different TLS fingerprint. That is ASSUMED detectable, so it is not used.
  - Whether a `/goto` blob is bound to the session that served it is UNKNOWN. Resolve within the same session to be safe.
- **Budget:** about 10 extra Google requests per searched page. At 1,000 searches/day that is about 11,000 Google requests/day, and pacing must count `/goto` requests against each identity's bucket. Whether Google rate-limits `/goto` separately from `/search` is UNKNOWN (part of experiment E-P).

## Blink SERP structure (rea `inspect_web_page` `ev_8aa78085…`, raw saved as `captures/2026-10-16/rea-brave-serp-inspect.json`)

rea keeps DOM attribute **names** only, not values. Counts on the human's results page: 9 `h3`, 9 `[data-rpos]` (8 of the `h3` sit under a `data-rpos` element, 7 inside an `<a>`), 9 `[data-sncf]`, 14 `cite`, 5 `[data-q]`, 6 `[data-ignore-copy]`, 2 `[data-scope-id]`, 0 `[data-text-ad]`.

**OBSERVED:** the anchors of selector set `gecko-2026_10` (F-0006) are present in Blink too. Values (ids, hrefs) were not captured, so a Blink fixture with values is still needed before a `blink-*` set is declared verified.

**OBSERVED difference:** Blink results carry **105 `ping` attributes** (hyperlink-auditing beacons). The Gecko capture of the same query (`cases/c3-paa.html`) has **0**. Google appears to serve `ping` only to engines that send it (Firefox disables `<a ping>` by default); this explanation is ASSUMED. The parser does not depend on it.

**OBSERVED:** the accessibility tree lists the same leading results, in the same order, as Camoufox's C3 page ("asyncio — Asynchronous I/O", "Python's asyncio: A Hands-On Walkthrough", …).
