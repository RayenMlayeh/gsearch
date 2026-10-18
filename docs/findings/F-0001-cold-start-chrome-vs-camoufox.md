# F-0001 Cold-start search: Playwright-Chrome blocked, Camoufox served

- **Date:** 2026-10-16, 19:03–19:09 UTC
- **Network:** a home connection, no proxy. Google localized the page as `lang="en-TN"` (Tunisia, non-EEA).
- **Query:** `https://www.google.com/search?q=python+asyncio+tutorial&hl=en`, empty profile, no cookies.

## Arms

| # | Browser | Driver | Outcome | Capture |
|---|---|---|---|---|
| 1 | Chrome 155.0.8059.41, headless | rea `capture_browser_scenario` (Playwright, launch mode) | **/sorry + 429 + reCAPTCHA Enterprise** | `rea-chrome-headless-scenario.json` (`6e7eb9ab…`), Evidence `ev_f00d19b0…a70f8` |
| 2 | Chrome 155.0.8059.41, headed | same | **/sorry + 429 + reCAPTCHA Enterprise** | `rea-chrome-headed-scenario.json` (`8bda54fa…`), Evidence `ev_5819846b…df54` |
| 3 | Camoufox 152.0.4-beta.30 (python pkg 0.5.6, Playwright 1.62.0), headed, `os="windows"`, `locale="en-US"` | Camoufox script (one-off) | **200, real SERP** (8 organic `<h3>`, `#rso`, `#tads`) | `camoufox-headed.har` (`952b9eac…`), rea Evidence `ev_56622ac3…69ba8` |

## Observations

**OBSERVED-run.** Arms 1 and 2 followed the same request chain (rea scenario network events, seq 1–17):

1. `GET /search?q=…&hl=en` → **200** `text/html`. This is the JS gate described in [F-0002](F-0002-serp-js-gate-sg-ss.md). It sets cookies and sends 12 `Accept-CH` client-hint requests.
2. The page's JS re-requests `GET /search?q=…&hl=en&sei=<id>` → **302** with `location: /sorry/index?continue=…` and a response header **`x-hallmonitor-challenge`**.
3. `GET /sorry/index?…` → **429**, `server: scaffolding on HTTPServer2`. The body has `<form id="captcha-form" action="index" method="post">` and a `div.g-recaptcha` (reCAPTCHA **Enterprise**, `data-sitekey=6LdLLIMbAAAAAIl-KLj9p1ePhM-4LCCDbjtJLqRO`, plus a `data-s` blob).

Arm 3 followed step 1 the same way, but step 2 returned **200** with a 524 KB server-rendered SERP.

**OBSERVED-rea.** These are leaks in the Chrome arms:
- Arm 1 `user-agent` contains **`HeadlessChrome/155.0.0.0`**.
- Arm 2 had a clean UA (`Chrome/155.0.0.0`) and was still blocked. The headless UA alone does not explain the block.
- In arm 1, `sec-ch-prefers-color-scheme` was **`dark` → `light` → `dark`** across three consecutive document requests, even though the scenario requested `color_scheme: light`. That is an incoherence a real browser would not produce.
- Chrome sent `x-browser-validation`, `x-browser-channel`, `x-browser-year`, `x-browser-copyright` on the document request. A non-Chrome client claiming a Chrome UA would lack them (ASSUMED that Google checks them).

**OBSERVED-rea.** Cookies after the block (arm 1, rea storage capture): `AEC`, `NID`, `__Secure-STRP`, all values hashed by rea. No consent cookie and no consent redirect, consistent with a non-EEA IP.

## What this does NOT prove

- **n = 1 per arm**, run sequentially (Chrome, Chrome, Camoufox) within 6 minutes on one IP. Order effects are possible: an IP flag could have decayed, or the arms could have been scored differently by time. It is not a block rate.
- It does not tell us *which* signal flagged Chrome: `navigator.webdriver`, CDP artifacts, Playwright's Chromium launch flags, or token content are all candidates (UNKNOWN). The decision happened **after** the JS gate ran (step 2), which is consistent with the gate collecting environment signals. It is not proof.
- Whether this IP is clean for a human: UNKNOWN. Experiment **E-1** (a manual search in a human-driven Brave on the same IP) answers it: see F-0009.

## Re-run

```text
# Arms 1/2: rea capture_browser_scenario, launch mode, executable = chrome.exe,
#   start_url https://www.google.com/search q=python asyncio tutorial hl=en,
#   capture: at_end [url, dom, storage, screenshot], events [network, console, page-errors],
#   network.header_values = true, environment {locale en-US, timezone Africa/Tunis}
# Arm 3: Camoufox (headed, os="windows", locale="en-US"), same URL, HAR recorded, then
#   rea inspect_web_network_capture {capture_path: <sanitized.har>, format: har, detail: summary}  (see F-0004)
```
