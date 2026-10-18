# F-0003 SERP anchors, endpoints and transfer size (Camoufox / Gecko)

- **Date:** 2026-10-16. One query (`python asyncio tutorial`, `hl=en`), one region (TN), Camoufox headed.
- **Source:** `camoufox-headed-serp.html` (`0760f8b1…`, `page.content()` after load + 4 s), `camoufox-headed.har` (`952b9eac…`). rea API-surface view Evidence `ev_2b4bacaf…a207d` (parent `ev_56622ac3…69ba8`).

## DOM anchors (counts in the final DOM)

| Anchor | Count | Note |
|---|---|---|
| `id="search"`, `id="rso"` | 1, 1 | organic container |
| `<h3` | 8 | organic titles |
| `role="heading"` | 14 | |
| `data-rpos` | 9 | per-result position attribute (candidate stable anchor) |
| `data-snc` | 18 | |
| `data-hveid` / `data-ved` | 200 / 418 | tracking attributes; everywhere, not result-specific |
| `jsname=` | 671 | |
| `id="tads"` | 1 | top ads container present; `data-text-ad` = 0 |
| `id="botstuff"`, `id="pnnext"`, `aria-label="Page 2"` | 1, 1, 1 | bottom block, next page, pagination |
| `class="g "` (legacy result class) | **0** | the classic `div.g` anchor is **absent** |
| PAA (`related-question-pair`, "People also ask") | 0 | this query showed none, so the PAA markup is still UNKNOWN |

**OBSERVED-rea.** IDs (`rso`, `tads`, `botstuff`, `pnnext`), ARIA, and `data-*` attributes are the candidate anchors. Obfuscated class names and the historical `.g` class must not be relied on. Stability across time, region and engine is UNKNOWN until we have more captures.

**OBSERVED-rea.** Results are **server-rendered** in the `&sei=` document (524,019 B decoded; 144,226 B on the wire). Of the XHRs seen, `/async/bgasy` (JSPB, returns a BotGuard script URL + program blob) and `/complete/s` (suggest) carry no organic results.

## Endpoints observed (rea `api-surface` view)

| Endpoint | Role (ASSUMED unless noted) |
|---|---|
| `GET /search?q,hl[,sei]` | gate (no `sei`), then SERP (OBSERVED) |
| `GET /async/bgasy?ei,opi,hl,yv,cs,async=_fmt:jspb` | BotGuard bootstrap (OBSERVED: response lists `/js/bg/…js` + blob) |
| `GET /js/bg/<hash>.js`, `GET /js/th/<hash>.js` | BotGuard / anti-abuse scripts |
| `POST jnn-pa.googleapis.com/$rpc/google.internal.waa.v1.Waa/GenerateIT` | integrity-token RPC |
| `GET /verify/<token>` (204, ×2) | verification beacon |
| `POST /gen_204`, `GET|POST /client_204`, `POST play.google.com/log` | telemetry |
| `GET /httpservice/retry/BatchAsyncTaskTriggerService/CheckTriggerStatus?reqpld=[…]` | async tasks; its literal `[ ]` breaks strict HAR validation (F-0004) |
| `GET /complete/s` | autocomplete |
| `GET /xjs/_/js/…`, `/xjs/_/ss/…` | page JS/CSS bundles (locale `en_GB`) |

## Transfer size, uncached, one SERP

**OBSERVED-rea**: 78 requests, **2.42 MB** total. By host: `www.google.com` 1.25 MB, **`www.youtube.com` 1.02 MB** (embedded video results), `www.gstatic.com` 98 KB, fonts 42 KB, thumbnails 20 KB. The SERP document alone is 144 KB on the wire.

Measured proxy bandwidth per search is in F-0015. Whether blocking YouTube, fonts or images changes the block outcome is UNKNOWN (ASSUMED risky: it removes requests a real browser makes).

## Request header order (Camoufox, SERP document)

`Host, User-Agent, Accept, Accept-Language, Accept-Encoding, Alt-Used, Connection, Referer, Cookie, Upgrade-Insecure-Requests, Sec-Fetch-Dest, Sec-Fetch-Mode, Sec-Fetch-Site`. Not yet diffed against a stock Firefox.
