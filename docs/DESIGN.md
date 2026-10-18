# Design

How gsearch is put together, the decisions behind it, and what it deliberately does not do. Every claim about
Google, browsers or libraries is backed by a file in [findings/](findings/README.md).

## Architecture

One distribution (`gsearch-agent` on PyPI) holding four import packages, each depending only on the ones
above it:

| Package | Network | What it does |
|---|---|---|
| `gsearch_parse` | none | HTML in, typed data out: page classification, versioned selector sets, invariants, JSON schema. Never imports `gsearch_core` (enforced by ruff). |
| `gsearch_core` | yes | `SearchClient`, fetch backends, query cache, proxy pool and relay, pacing, durable state. |
| `gsearch_cli` | via core | `gsearch search / parse / schema / identity check / setup`. Argument parsing and JSON output only. |
| `gsearch_mcp` | via core | MCP server over stdio: `google_search`, `google_search_status`. No scraping logic. |

Fetch backends all return a raw page; the client classifies and parses it, so every backend gets the same
block detection and the same output:

| Backend | Used when | Path |
|---|---|---|
| `FixtureFetcher` | `GSEARCH_FIXTURES_DIR` is set | saved pages from `index.json` (tests, demos) |
| `ProxyPool` | `GSEARCH_PROXY` is set | relay per identity → `HybridFetcher` → `CamoufoxFetcher` |
| `HybridFetcher` | no proxy | the browser opens one session on the own IP, `curl_cffi` does later searches |

Settings are deliberately few (`GSEARCH_PROXY`, `GSEARCH_DATA_LIMIT_MB`, plus `GSEARCH_ENV_FILE`,
`GSEARCH_FIXTURES_DIR` and `GSEARCH_STATE_DIR`). Everything else is fixed to what worked best in testing.

A live search with a rotating proxy:

1. The pool draws a new session id, which on a rotating account means a new exit IP, and starts a local relay
   for it. The relay holds the proxy login (HTTP, HTTPS or SOCKS5) and meters every byte against the data
   limit.
2. The exit is located through the relay (a few KB). It is dropped if it does not answer, or if it is
   outside the country the login asks for.
3. Camoufox launches with a real-device fingerprint preset, the exit's locale and geolocation, and WebRTC
   blocked, in a visible window (a virtual display on a Linux server). It types the query into Firefox's
   address bar: no homepage visit, no Referer.
4. If Google shows its EU consent dialog, the exit is dropped. On a CAPTCHA, the exit is retired and the
   search retried on a fresh one, up to 4 exits.
5. On results, the session's cookies and headers move to `curl_cffi` (same relay, same exit, matching TLS
   ClientHello), which resolves every `/goto` link from its redirect without loading the destination.
6. Everything for that exit is closed. The next search starts again at step 1.

Static proxies (no session id in the login, e.g. a VPS or datacenter list) keep one warm session per IP
instead, used in turn, each with the own-IP budget, and rest until midnight after a CAPTCHA.

## Decisions

Numbered so the findings can cite them.

- **D-001 Camoufox is the browser.** On the same IP, Playwright-Chrome was sent to a CAPTCHA and Camoufox was
  served (F-0001). It is a dependency (MPL-2.0), installed by pip, never vendored.
- **D-002 No official-API backend.** Google's Custom Search JSON API is closed to new customers and ends on
  2027-01-01 (F-0005). The `Fetcher` interface stays open for one.
- **D-003 Proxies go through a local relay.** The browser and the HTTP client both use `127.0.0.1` without
  credentials. One relay per identity means both tiers share one exit IP, the bytes counted are the bytes
  billed, and a persisted limit holds across runs. The relay speaks HTTP, HTTPS and SOCKS5 upstream, so any
  proxy works, in any common format.
- **D-004 No token forging; no CAPTCHA solving shipped.** Results need a cookie that Google's obfuscated
  JavaScript VM computes (F-0002). Rebuilding that VM is the conduct at issue in *Google v. SerpApi*
  (N.D. Cal., filed 2025-12-19), so the project never generates or alters a token: a real browser runs the
  page as is. `gsearch_core.captcha` is an interface only, and no solver is shipped, wired in or recommended.
- **D-005 Errors are data.** A block is returned as a `SearchError` with `code` and `retry_after_s`, never
  as an empty list, and page layout changes surface as `parse_drift` instead of "0 results".
- **D-006 Gecko fixtures are the test base.** The parser is tested against minimized, scrubbed captures from
  Camoufox, with golden JSON per fixture. CI never contacts Google.
- **D-007 The market follows the exit IP.** Locale, timezone, geolocation and the default interface language
  come from the exit's country (F-0007).
- **D-008 Result URLs are always resolved.** Google serves organic links as opaque `/goto` redirects (F-0006).
  Each one is resolved in the same session that served the page, from its redirect, without loading the
  destination (F-0009 to F-0011).
- **D-009 Conservative limits per IP.** On the own IP and on each static proxy: 20 searches an hour and 150 a
  day, and a rest until midnight after a CAPTCHA. Budgets live in SQLite and are checked before any browser
  work, so a restart cannot reset them.
- **D-010 Warm sessions over HTTP where an IP is kept.** Google's gate runs only on a session's first search.
  Later searches over HTTP took ~1.2 s instead of ~55 s in the browser (F-0014).
- **D-011 Rotating proxies: one-shot, headed, address-bar entry, real-device presets.** On residential exits,
  headless sessions were served far less often than headed ones (F-0015). A session is never moved to
  another IP: a session that changes IP mid-way is itself a bot signal.
- **D-012 Few settings.** Choices that were measured to work better are fixed rather than configurable, so
  a user only provides proxies and, optionally, a data limit.

## Out of scope

- Generating, replaying or altering Google's anti-abuse tokens, and solving CAPTCHAs.
- A hosted, multi-user service. gsearch is a local tool: one process, one SQLite file.
- Personal-data collection, people search, or bulk archiving of result pages.

## Open questions

- **E-P** Block rate at sustained volume, per exit and per hour of day; whether Google meters `/goto`
  separately.
- **E-S** How static datacenter and VPS IPs fare with warm sessions (not benchmarked yet).
- **E-R** Whether gateways that rotate the IP per TCP connection (not per session id) can pass the gate.
- **E-2** Whether blocking images, fonts and media changes Google's verdict (it saves bandwidth).
- Blink (Chrome/Brave) fixtures and selector set; ads and classic featured snippets from a market that serves
  them.
