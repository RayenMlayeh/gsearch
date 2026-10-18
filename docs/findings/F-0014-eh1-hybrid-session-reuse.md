# F-0014 E-H1: a browser-opened session keeps working over plain HTTP; ~1.2 s per search

- **Date:** 2026-10-17, 03:42–03:45 local.
- **Network:** home IP (TN), no proxy.
- **Script:** a one-off probe script (not kept in the repo); log and data in `captures/2026-10-17/eh1/run.log` and `results.json`.
- **Label:** OBSERVED-run, one session, 5 searches. A description, not a rate.

## Setup

- Search 1 ran in Camoufox (headless; typed query; Google's JS gate ran there).
- The hybrid tier then adopted the browser's cookies (`AEC, DV, NID, SEARCH_SAMESITE, __Secure-STRP`) and the headers of its last document request.
- Searches 2–5 were `curl_cffi` GETs:
  - `firefox147` profile + cipher override, i.e. the ClientHello matches Camoufox (F-0013);
  - browser headers in Firefox order, `Sec-Fetch-Site: same-origin`, `Referer: https://www.google.com/`.
- Result URLs: one `/goto` GET each over HTTP, read from the 302 Location; destinations never requested.
- Gaps between searches: 20–40 s.

## Result

| # | Tier | Seconds (incl. URL resolution) | Organic / resolved | Response bytes (decoded) | Verdict |
|---|---|---|---|---|---|
| 1 | Camoufox (opens session) | 15.85 | 8 / 8 | (browser) | ok |
| 2 | HTTP | **1.15** | 9 / 9 | 911,225 | ok |
| 3 | HTTP | **1.11** | 8 / 8 | 593,890 | ok |
| 4 | HTTP | **1.10** | 8 / 8 | 612,263 | ok |
| 5 | HTTP | **1.39** | 8 / 8 | 594,400 | ok |
| 6 | — | 0 | — | — | `rate_limited`: **our own** budget (60/h, burst 3), not Google |

- **Speed:** ~1.2 s per search including resolving all URLs, against ~51–58 s per search in browser mode (F-0012) and ~8–16 s for a typed browser search without resolution.
- **Bandwidth:** 0.59–0.91 MB **decoded** per HTTP search (document plus 8–9 tiny `/goto` responses), against 3.4–5.8 MB on the wire per browser search (F-0012). The decoded figure overstates wire bytes. The earlier capture of the same document type compressed 524 KB to 144 KB (F-0003), so the ASSUMED wire size is ~0.15–0.25 MB per HTTP search, roughly **20× less**. Measuring on-wire bytes directly is a TODO.
- **Blocks:** none. The session was not gated again on searches 2–5.

## Not shown (UNKNOWN)

- How long a session stays valid over HTTP (minutes? hours? how many searches?), and what Google does when it expires. The code re-opens it in the browser on a JS gate.
- Block rate at volume, or under higher rates, for this mode (experiment E-P, now in hybrid mode).
- Whether Google correlates HTTP/2 framing (curl, h2) with the browser's earlier h3 connections (F-0013 "not compared").
