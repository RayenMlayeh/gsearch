# F-0015 B-1: the product path through a residential proxy pool (US), 2026-10-17

- **Date:** 2026-10-17, 19:42–21:00 local time (UTC+1).
- **Network:** one provider's metered residential pool (`network-res`), `country-us`, sticky sessions (`ttl-30`), HTTP upstream.
  Every byte metered by our relay (`gsearch_core.relay`), cumulative cap 700 MB.
- **Path:** `ProxyPool` → per-identity local relay → Camoufox (session opening) → hybrid HTTP (`curl_cffi`).
- **Script:** a multi-arm benchmark script (its one-shot arm is now `tools/benchmark.py`); rows, summaries and logs in `captures/2026-10-17/bench/<arm>/`;
  CAPTCHA and error pages (HTML + screenshot) in `%LOCALAPPDATA%\gsearch\evidence\`.
- **Label:** OBSERVED-run. One provider, one country, one evening, ~110 fresh exits. Counts, not rates; small n per arm.

## 1. Most fresh exits are already rejected by Google

| Arm (setup) | Fresh exits that opened a session | Notes |
|---|---|---|
| sticky runs 1–3 (headless, homepage entry) | 1 / 7 | 1 blocked before the JS gate; 5 after it; 1 Dutch exit |
| open, A: headless, images/fonts/media skipped | 1 / 4 | 2 after-gate CAPTCHAs, 1 Dutch exit |
| open, B: headless, everything loaded | 0 / 4 | 3 after the gate, 1 before |
| open, C: **headed**, everything loaded | **3 / 4** | 1 after the gate |
| sticky 4 + survival + portable (headed, homepage) | 3 / 10 | later in the evening; 2 Dutch/German exits dropped |
| oneshot 1 (headed, homepage) | 0 / 4 | |
| **oneshot 2 (headed, address-bar entry, real-device preset)** | **10 / 30** | 3 exits lost to a preset bug (fixed) |

- **Where the block happens:** of the CAPTCHAs whose page we kept, most came **after** Google's JS gate had run in
  the browser (the `/sorry` continue URL carries `&sei=`). Google let the request in, ran its check inside
  Camoufox, and then refused. A minority were refused at the first request (IP reputation alone).
- **Same Camoufox on the home IP** (F-0001, F-0006, F-0014): served, headless, every time. ASSUMED: Google scores
  residential-proxy exits as riskier and applies a stricter gate to them; the browser alone is not the cause.
- **Headed vs headless:** across the evening, headless opened 2 of ~15, headed 16 of ~48 (incl. oneshot 2). Headed
  was clearly better; in the controlled interleaved comparison (open arm) 3/4 vs 1/4 and 0/4. Small n.
- **Time trend:** headed success fell from 3/4 (20:03–20:11) to 0/4 (20:18–20:20) and recovered with address-bar
  entry (20:34–20:50). Not separable from chance at this n.

## 2. Country targeting is not guaranteed

- 6 exits requested as `country-us` were placed by Google in **NL or DE** (EU consent dialog, `gl=NL`/`gl=DE` in its
  continue URL). 4 of them our IP database (GeoLite2 via Camoufox) placed in the US.
- Fix (in code): every new exit is located first (~7 KB) and dropped if not in the target country; Google's own
  consent dialog is detected right after the first navigation (`consent_country()`), and the exit dropped.
- **A new session id is not always a new IP:** one new `sid` returned the same exit IP as the previous one.

## 3. Exits die

- A sticky exit stopped answering after ~9 min (curl: 0 bytes in 20 s). Another exit timed out on its first IP
  lookup. Both now become `network` errors; the pool retires the exit and retries once on a fresh one.

## 4. HTTP after a browser-opened session: works on some exits, not others

| Session | HTTP searches served | Then |
|---|---|---|
| sticky 2 (headless session) | 4 at 2.4–4.4 s, 135–251 KB each | exit died at minute 9 |
| survival 2 (headed, browser kept open) | 1 at 10.2 s, 352 KB | run stopped by us |
| sticky 4 (headed, browser parked) | 0 | CAPTCHA on the first HTTP search, 90 s after opening |
| portable (headed) | 0 | CAPTCHA on the first HTTP search (the "new" sid had the same IP) |

- Home IP (F-0014): 4 / 4 at ~1.2 s. Through this proxy the same requests take 2.4–4.4 s (residential latency).
- UNKNOWN why two headed sessions failed on their first HTTP search; n=2 cannot separate the exit from the method.

## 5. One-shot mode (the default with a proxy): one exit, one session, one search, closed

Setup (oneshot 2): headed Camoufox, real-device fingerprint preset, query typed into Firefox's address bar
(`/search?client=firefox-b-d&q=…`, no homepage), all resources loaded, links resolved over HTTP on the same exit
with the same cookies, then everything closed. Up to 4 exits per search.

| Metric | Value |
|---|---|
| Searches served | **7 / 10** (1 miss was the preset bug, since fixed) |
| Exits per served search | 4.3 (30 exits for 7 served) |
| Data per served search | **4.7 MB** average incl. failed exits; best 0.6 MB, worst 6.4 MB |
| Data per failed exit | ~0.7–1.0 MB (no homepage load) |
| Time per served search | **73 s** average incl. retries; best 14 s |
| Results | 7–10 organic per page, **every link resolved**; ads on 1 of 7 |

Against the same mode with homepage entry and synthetic fingerprints (oneshot 1): 0 / 1, 4 exits, 7.7 MB.

## 6. Parallel one-shot (3 browsers at once, 20:42–20:47)

| Metric | Value |
|---|---|
| Searches served | 6 / 12 (6 failed all 4 exits) |
| Exits | 37 drawn, 30 CAPTCHA, 1 dead; **7 clean (19%)**, below oneshot 2's 33% |
| Throughput | 6 served in 298 s = **1.2 served searches/min (~72/h)** |
| Data | 38 MB for the arm (meter 124 → 162 MB) = **6.3 MB per served search** |
| Time per served search | 16–87 s (median 40 s) |

- Per-row byte deltas overlap when searches run concurrently; only the arm total (meter before/after) is valid.
- Real-device presets naming a GPU Camoufox has no WebGL data for were redrawn 7 times (fix working).
- UNKNOWN whether three simultaneous sessions from one account lowered the clean-exit share, or the hour did.

## 7. Data used

162 MB metered by the relay in total today (all arms, incl. failed attempts). The provider dashboard may count
slightly differently (its own rounding, retries inside its network).

## 8. What this means for ~1,000 searches/day on this pool (ASSUMED from the numbers above)

| Mode | Data per 1,000 served | Time for 1,000 | Cost at $1.25/GB (Premium) |
|---|---|---|---|
| One-shot, 3 browsers in parallel | ~5–6.5 GB | ~14 h | ~$6–8 |
| One-shot, 6 browsers (if the clean share holds) | same | ~7 h | same |
| Hybrid HTTP on exits that hold (not reliable here, section 4) | ~0.25 GB + session openings | ~1 h | ~$1 |

The biggest lever is the clean-exit share (19–33% here): every rejected exit costs ~0.7–1 MB and ~20 s.

## Not shown (UNKNOWN)

- Whether another pool (a cheaper tier, or another provider) has a lower rejected-exit share. The flagged share
  looks like a property of the pool, not of Camoufox (same browser served on the home IP).
- Whether the after-gate rejections key on something fixable in the browser (vs IP risk scoring). F-0012 lists
  the remaining headless/headed differences; WebRTC is disabled (deliberately: it could leak the home IP over UDP).
- Daytime behaviour, other countries, more than ~70 exits.
