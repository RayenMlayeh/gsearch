# Findings

One file per question. Each file states **what was asked, how it was measured, what was seen,
and what it does not prove**, so anyone can re-run it when Google changes.

## Evidence labels

| Label | Meaning |
|---|---|
| **OBSERVED-rea** | From rea output on a client-side artifact (DOM, JS, HAR, headers). Cites the rea operation and Evidence ID. |
| **OBSERVED-run** | Outcome of a real request against Google (OK / blocked). Cites the capture file. Says what Google *decided* for this identity at this time, not why. |
| **OBSERVED-doc** | Stated in official vendor documentation (URL and fetch date). |
| **ASSUMED** | Not verified. Must not be presented as observed. |
| **UNKNOWN** | Open question, with the experiment that would answer it. |

rea shows what Google ships to the client and what the client sends. It cannot show how Google
scores those signals server-side. Only OBSERVED-run evidence (real runs from real IPs) says whether
an approach gets blocked, and a single run is an anecdote, not a rate.

## Raw captures

Raw captures live in `captures/<date>/` (gitignored: they contain cookies and third-party content).
Findings cite them by filename + SHA-256 prefix. Scripts named "one-off" were probes that were not kept:
each finding describes its method well enough to rebuild them. F-0008 was withdrawn (it described a private
codebase); the numbering is kept so citations stay stable.

## Index

| ID | Title | Status |
|---|---|---|
| [F-0001](F-0001-cold-start-chrome-vs-camoufox.md) | Cold-start search: Playwright-Chrome blocked, Camoufox served | n=1 per arm |
| [F-0002](F-0002-serp-js-gate-sg-ss.md) | SERP is gated by an inline JS challenge that sets `SG_SS` | observed |
| [F-0003](F-0003-serp-structure-gecko.md) | SERP anchors, endpoints and transfer size (Camoufox / Gecko) | 1 query, 1 region |
| [F-0004](F-0004-rea-tooling-limits.md) | What rea can and cannot do in this project | observed |
| [F-0005](F-0005-custom-search-api-sunset.md) | Custom Search JSON API closed to new customers, ends 2027-01-01 | documented |
| [F-0006](F-0006-fixture-provenance-and-serp-anatomy.md) | Fixture provenance, session gating, SERP anatomy | 1 session, 1 region |
| [F-0007](F-0007-camoufox-geoip.md) | How Camoufox `geoip=True` derives location from a proxy | source-read |
| [F-0009](F-0009-e1-human-brave-and-goto-redirect.md) | E-1: human in Brave is served on the home IP; `/goto` is a plain server redirect | n=1 |
| [F-0010](F-0010-playwright-camoufox-redirect-hooks.md) | Which Playwright hooks see a 302 in Camoufox; redirect target cannot be route-aborted | local harness |
| [F-0011](F-0011-goto-resolution-leak-and-fix.md) | Our `/goto` resolution leaked `Sec-Fetch-Site: none` + Referer; fixed with real middle-clicks | rea on our live HAR + local harness |
| [F-0012](F-0012-headless-vs-headed-fingerprint.md) | What headless vs headed Camoufox exposes to page JS (offline, 3 launches each) | local harness |
| [F-0013](F-0013-tls-camoufox-vs-curl-cffi.md) | TLS ClientHello: Camoufox vs curl_cffi Firefox profiles; exact match with a cipher override | local harness |
| [F-0014](F-0014-eh1-hybrid-session-reuse.md) | E-H1: browser-opened session keeps working over plain HTTP; ~1.2 s per search | 1 session, 5 searches |
| [F-0015](F-0015-b1-residential-proxy-benchmark.md) | B-1: residential proxy pool (US): most fresh exits rejected after the JS gate; one-shot mode 7/10 at 4.7 MB | ~110 exits, 1 evening |
