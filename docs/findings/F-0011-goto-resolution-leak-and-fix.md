# F-0011 Our own `/goto` resolution leaked `Sec-Fetch-Site: none` + Referer; fixed with real middle-clicks

- **Date:** 2026-10-17.
- **Found by rea** on our own live session: the tool's own traffic is checked like anyone else's.

## The leak (OBSERVED-rea)

- **Capture:** `captures/2026-10-17/live1.har`, the first live search through `CamoufoxFetcher` (home IP, headless). Document subset: `live1.documents.har` (`209bbd36…`). `inspect_web_network_capture` parent `ev_fed132ba…`; complete record #3 is `ev_ffec789f…`.
- **Request:** `GET https://www.google.com/goto?url=…` → **302** `location: https://docs.python.org/3/library/asyncio.html`.
- **Request headers:** `Referer: https://www.google.com/search?q=…`, `Sec-Fetch-Dest: document`, `Sec-Fetch-Mode: navigate`, **`Sec-Fetch-Site: none`**, `Sec-Fetch-User: ?1`.
- **Why it's a leak:** `Sec-Fetch-Site: none` means a user-initiated navigation with no initiating site (address bar, bookmark), and such navigations carry no Referer. A Referer from google.com together with `Sec-Fetch-Site: none` is a combination a real browser does not produce. The cause was `page.goto(link, referer=serp_url)`: Playwright's goto is browser-initiated, and the referer was forged on top.
- **Second problem in the same HAR:** destination pages loaded fully with subresources (medium 76 requests, w3schools 34, …). The session moved **5.07 MB**.

## Probes that shaped the fix (OBSERVED-run, local, offline: a one-off probe script (not kept in the repo))

| Click | Headers seen by the server | Playwright events |
|---|---|---|
| Ctrl+click | `same-origin`, `navigate`, `?1`, Referer = page | the tab and its events are reported to Playwright **seconds late** (after a 2 s window) |
| Middle-click | same | `request`, the 302 `response` with `location`, and `page` arrive immediately |
| Plain click | same | immediate, but the results page navigates away |

Two more harness observations:
- In a just-opened tab, `request.frame.page` raises for the first requests. A route gate must treat "owner unknown" as *not* the results page.
- With the fixture's stylesheet stripped, blocks overlap and "intercept pointer events". That is a harness artifact; live Google has its CSS.

## The fix (`CamoufoxFetcher.resolve_links`)

1. **Middle-click the result's title anchor**, `a[href="/goto…"]:has(h3)`. The same `/goto` URL also appears on hidden AI Overview source anchors, so a plain href match picked an unclickable one. If the click is intercepted, scroll the link to the centre and retry once.
2. **Fallback:** a typed-URL navigation **without** any Referer. That is coherent (`Sec-Fetch-Site: none`, no Referer) though atypical, and it is logged as `resolve_click_fallback_typed_url`.
3. While resolving, a context route allows only the results page's own requests and the exact `/goto` URLs; everything else is aborted. The destination's first document request still happens (the redirect hop cannot be routed, F-0010); its subresources never load.
4. The tab is closed as soon as the 302 `location` is known.

**Contract test** (`tests/test_camoufox_backend.py`, real headless Camoufox against a local fake Google): all 8 URLs are resolved; every `/goto` carries `sec-fetch-site: same-origin`, `sec-fetch-mode: navigate`, `sec-fetch-user: ?1` and a `/search` Referer; destination subresources fetched: **0**.


## Live verification (home IP, headless Camoufox, 2026-10-17)

| Run | What happened | Evidence |
|---|---|---|
| `live2` (`car insurance quote`) | Ctrl/middle-click requests went to **`/url?sa=t&source=web&rct=j&opi=89978449&url=<blob>…`**, not to the `/goto` href. Google's click handler rewrites the link. The gate (exact `/goto` match) aborted them; 9/9 unresolved. | debug log `resolve_gate_abort owner=unknown url=https://www.google.com/url?sa=t…` |
| `live3` (`best python web framework`) | With redirects matched by their `url=` blob: every `/url` request carried **`Sec-Fetch-Site: same-origin`, `Sec-Fetch-User: ?1`, `Referer: https://www.google.com/`**, so the leak is gone. But `/url` answered **200**, not 302: a ~0.8 KB page with the destination in a `noscript` meta refresh and a `redirectUrl='…'` script variable. 9/9 unresolved. | `live3.har` |
| `live4` (`how to learn rust programming`) | Destination parsed from the 200 interstitial (`gsearch_parse.redirect`). **9/9 URLs resolved, 0 warnings.** **No result-destination host was contacted**: the interstitial's own redirect is a new navigation, which the gate aborts. Session total 4.68 MB, all Google's own page assets and ad/telemetry hosts. | `live4.documents.har` (`aba0c59e…`), rea `inspect_web_network_capture` parent `ev_5573b467…` |

Final behaviour: the DOM serves `/goto?url=<blob>`. A plain left click in Brave got a 302 (F-0009). A middle-click in Camoufox is rewritten to `/url?…` and gets a 200 interstitial. The resolver handles both, keyed by the shared `url=` blob.

Still UNKNOWN: whether Google meters `/url` hits per identity. Each search now makes about 9 extra `/url` requests (experiment E-P).
