# F-0016 Home IP after the read_top release: ~0.6 s per warm search, ~2 s with the top 3 pages read

- **Date:** 2026-11-02, afternoon local.
- **Network:** home IP (TN), no proxy. Default settings: the home identity with its saved device and cookies.
- **Code:** commit `268f109` (links left to `fetch_page` unless `resolve_links`; `read_top`).
- **Script:** `captures/2026-11-02/home-measure/measure_home.py` (`d23dc0f7`), log `measure_home.log`
  (`abcf87e8`). It calls `SearchClient` exactly as the MCP server does and waits out the hourly budget.
- **Label:** OBSERVED-run, one session, 5 searches and 1 page read. A description, not a rate.

## Result

| # | Call | Tier | Seconds | Organic | Notes |
|---|---|---|---|---|---|
| 1 | search, first of the session | Camoufox (opens session) | 17.17 | 8 | 4 PAA |
| 2 | search | HTTP | **0.56** | 8 | 4 PAA |
| 3 | search | HTTP | **0.66** | 9 | |
| 4 | search | HTTP | **0.62** | 8 | 4 PAA |
| 5 | search, `read_top=3` | HTTP + 3 page reads | **2.03** | 9 | 3 of 3 pages read |
| 6 | `fetch_page` on result 1's Google link | direct | 0.98 | | page read |

- **Blocks:** none. Searches 2–5 stayed on the session the browser opened.
- **Speed:** a warm search is ~0.6 s, against ~1.2 s in F-0014. The difference is the link resolution F-0014
  included: results now keep Google's link and `fetch_page` follows it when the agent reads a page.
  With `resolve_links`, F-0014's ~1.2 s still applies.
- **read_top:** the top 3 pages' text came back with the results in ~2 s, one call instead of four.
- **Budget:** searches 3–5 each waited ~3 min for the home IP's own budget (20 an hour, burst 2), not for Google.

## Not shown (UNKNOWN)

- Variance across days, queries and networks; one session is an anecdote.
- `read_top` time depends on the sites read; a slow site is left out after 6 s.
- No AI Overview appeared for these queries, so its parse time is not covered.
