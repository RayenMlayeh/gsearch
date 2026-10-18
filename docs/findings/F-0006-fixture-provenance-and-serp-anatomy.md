# F-0006 Fixture provenance and SERP anatomy (Gecko, TN)

- **Date:** 2026-10-16, 19:32–19:42 UTC.
- **Session:** one Camoufox session (152.0.4-beta.30, headed, `humanize=True`), home IP (TN), no proxy. Queries were typed into the search box with 45–90 s gaps (`tools/collect_fixtures.py`).
- **Captures:** `captures/2026-10-16/cases/session.har` (82.9 MB), with a document-only copy `session.documents.har` (`02fc102f…`; rea's HAR budget is 32 MiB).
- **rea:** `inspect_web_network_capture` (summary) → parent Evidence `ev_bda79c84…a4f2`; `inspect_analysis_view` capture preview → `ev_02e12fc4…d7c8`.

## Provenance of every committed fixture (OBSERVED-rea: status, URL; OBSERVED: body markers)

| HAR # | Time | Status | Request | Body | Fixture |
|---|---|---|---|---|---|
| 0 | 19:32:08 | 200 | `/?hl=en` (homepage) | sets `__Secure-STRP, SEARCH_SAMESITE, AEC, NID` | – |
| 1 | 19:32:18 | 200 | `/search?q=wikipedia…` | **JS gate** (93 KB, no `#rso`) | – |
| 2 | 19:32:18 | 200 | same + `&sei=…`, sends `SG_SS` | SERP, `#rso` | `c1-navigational` |
| 3 | 19:33:43 | 200 | `q=how tall is the eiffel tower` | SERP | `c2-ai-overview-paa` |
| 4 | 19:35:17 | 200 | `q=what is asyncio in python` | SERP | `c3-paa` |
| 5 | 19:35:35 | 200 | same, `start=10` (clicked Next) | SERP | `c3-paa-p2` |
| 6 | 19:36:52 | 200 | `q=car insurance quote` | SERP, **no ads** | `c4-commercial-no-ads` |
| 7–8 | 19:38:03/08 | 200 | `q=asyncio site:python.org`, then `&tbs=qdr:y` | SERP | `c5-site-past-year` |
| 9 | 19:39:23 | 200 | `q=zxqv qwpl mmrt kkhh 9931` | no-results page | `c6-no-results` |
| 10 | 19:40:49 | 200 | `q=meilleur restaurant tunis` | SERP | `c7-french-query` |
| 11 | 19:42:09 | 200 | `q=nobel prize 2026` | SERP | `c8-news` |

`c0-tutorial` comes from `camoufox-headed.har` (F-0001 arm 3). The synthetic block pages are documented in `fixtures/synthetic/README.md`.

**OBSERVED-run:** 9 searches (plus 1 page-2 click) in one session, 10 minutes, **0 blocks**. This is one session, not a rate.

## Session behaviour

- **OBSERVED-rea/HAR.** Only the **first** search of the session went through the JS gate (#1 → #2). Searches #3–#11 were a **single** document request each, 0.35–2.1 s.
- **OBSERVED.** Requests #3–#11 did **not** send `SG_SS`. Instead they carried a `DV` cookie that no document response set (so it was set by JS or a subresource). The session stayed valid for 10 minutes, past `SG_SS`'s 300 s default (F-0002).
- **ASSUMED** (later confirmed in practice by E-H1, F-0014): the cookies that keep a session warm (`AEC`, `NID`, `__Secure-STRP`, `DV`) may be what a hybrid HTTP tier has to carry, rather than `SG_SS`.
- **OBSERVED.** The no-results page also contains the `/httpservice/retry/enablejs` string. JS_GATE is therefore only valid when no results root exists, and `classify()` already checks it in that order.

## SERP anatomy findings that shaped the parser

| Finding | Label | Parser decision |
|---|---|---|
| Organic links are **opaque** `href="/goto?url=<blob>"`, both in the server HTML (`camoufox-headed.har` #1: 22 such links, 0 direct hrefs) and the final DOM. The destination URL appears **nowhere** in the page. Exception: Google-owned destinations such as `play.google.com` are linked directly. | OBSERVED | `link` = as served; `url` = destination only when exposed; `display_url`/`domain` from `<cite>`. Resolving `/goto` costs one extra Google request per result (see F-0009, F-0011). |
| `<cite>` holds a breadcrumb (`https://realpython.com › async-io-python`), except on forum/video/social results, where it holds metadata (`2 years ago`, `835.5K+ followers`, `6 answers · 8 years ago`). | OBSERVED | `domain` only when the head is host-like; otherwise `source_meta`. |
| Result unit `#rso [data-rpos]` containing an `h3` inside an `a`; snippet in `[data-sncf="1"]`; date prefix `<date> — <snippet>`. | OBSERVED | Selector set `gecko-2026_10`. |
| Google marks UI labels with `data-ignore-copy` (e.g. an `h3` "AI Mode replied:" inside PAA blocks). | OBSERVED | Ignored as content everywhere. |
| No classic featured snippet for `how tall is the eiffel tower`. An **AI Overview** at `#m-x-content [data-scope-id="turn"]` (outside `#rso`) carries the answer, with sources as `/goto` links. | OBSERVED (1 page) | `ai_overview` field added; `featured_snippet` stays `null` until a fixture shows one. |
| PAA questions on `[data-q]` inside `#search`. A `[data-q]` outside `#search` echoes the query. | OBSERVED | Scoped to `#search [data-q]` with an `[aria-expanded]` child. |
| No-results page: `#search` present and empty, no `#rso`, no `#pnnext`, message in `#botstuff`. | OBSERVED | Language-independent EMPTY_VALID signature. |
| `car insurance quote` from a TN IP: `#tads`/`#tadsb`/`#bottomads` empty, no "Sponsored" text. | OBSERVED, but **confounded** (see correction below) | Ad extraction is **unverified**. |
| Related searches: `#bres a[href^="/search"]`. Pagination: `a[aria-label^="Page "]` + `#pnnext`. | OBSERVED | as selected. Note: `aria-label` text is English-locale (`hl=en`); other `hl` values UNKNOWN. |
| Known false positive: "Translate this page" appears as a sitelink on non-English results. | OBSERVED | Accepted rather than filtering on English text; revisit with locale fixtures. |

## Not verified here

- Blink markup (needs Brave captures).
- EEA consent wall.
- Ads markup.
- Classic featured snippets.
- Non-`hl=en` pagination labels.
- Anything about block rates.

## Correction, 2026-10-17: every capture here ran with uBlock Origin

`camoufox` 0.5.6 `addons.py` defines `DefaultAddons.UBO`, and the launch loads it unless it is passed in `exclude_addons` (source-read). The collection scripts used then did not exclude it.

So the empty ad containers in `c4-commercial-no-ads` have **two candidate causes**: the TN market, or uBO removing the ads. They cannot be separated from this data, and "Google served no ads to the TN IP" is downgraded to UNKNOWN.

Other components (organic, PAA, AI Overview, related, pagination) are not ad elements. They are ASSUMED unaffected, but uBO cosmetic filters can hide other blocks too, so the Blink capture (F-0009, Brave, also with partial blocking) is not a clean control either.

Every Camoufox launch in the backend and in `tools/collect_fixtures.py` now passes `exclude_addons=[DefaultAddons.UBO]`.

## Update, 2026-10-17: ads without uBO, still none for this market

`live2` (`car insurance quote`, home TN IP, Camoufox with `exclude_addons=[UBO]`): `#tads` present but empty, no "Sponsored" text, 0 ads parsed. With the ad blocker ruled out, **this TN IP was served no ads for this query** (OBSERVED-run, n=1). Ad extraction stays **unverified** until a market that serves ads (e.g. a US exit) is captured.
