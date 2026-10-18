# F-0002 The SERP is gated by an inline JS challenge that sets `SG_SS`

- **Date:** 2026-10-16
- **Source:** `camoufox-headed.har` entry 0 (response body) and entry 1 (request cookies), rea Evidence `ev_56622ac3…69ba8`; also the Chrome arms of [F-0001](F-0001-cold-start-chrome-vs-camoufox.md).

## Observations

**OBSERVED-rea.** A cold `GET /search?q=…` returns **200, ~93 KB, no results**: no `id="rso"` and 0 `<h3>`. Inside it:

- A `<noscript>` block with `meta http-equiv="refresh" content="0;url=/httpservice/retry/enablejs?sei=<id>"`. Clients without JS are sent to an "enable JS" path.
- An obfuscated inline VM that builds the BotGuard script URL `https://www.google.com/js/bg/…` (offset ~58.7 K). It also contains the literal `String.fromCharCode(105,110,116,101,103,67,104,101,99,107,66,121,112,97,115,115)`, which decodes to `integCheckBypass`.
- At offset ~91.7 K, code that writes the cookie:
  `c="SG_SS="+a` where `a = "B."+challenge_version+"."+cbs+"."+<token>`,
  with expiry `now + (Number(ce)||300)` seconds, and `path=/`. Errors are beaconed to `/gen_204?cad=sg_b_e`.
- The page then navigates to the same URL plus `&sei=<id>`. The session-storage key observed in the Chrome arm is the `sei` value itself.

**OBSERVED-rea.** Set-Cookie on the gate response: `__Secure-STRP`, `SEARCH_SAMESITE`, `AEC`, `NID`. The `&sei=` request then sends those plus **`SG_SS`**, which no response had set before it, so it was written by JS. The SERP response sets `SG_SS` and `NID` again.

**OBSERVED-run.** The block decision is made on the `&sei=` request (302 → `/sorry` for Playwright-Chrome, 200 SERP for Camoufox).

## Consequences for design

- A plain HTTP client, even with perfect TLS/HTTP2 impersonation, receives the gate page and **no results** unless something executes the challenge. "HTTP-only" is not a viable cold path (OBSERVED-rea for the gate; whether the `enablejs` path yields anything is UNKNOWN, experiment E-H0).
- `SG_SS` is short-lived: the default expiry is 300 s unless the server sets `ce`. Any "browser mints a session, HTTP does the volume" design depends on three UNKNOWNs:
  1. Is `SG_SS` accepted for **several** queries, or bound to one `sei` or query?
  2. Is it bound to IP / TLS fingerprint / UA?
  3. Does the server expect the follow-up telemetry (`/gen_204`, `/client_204`, `/verify/…`, `jnn-pa.googleapis.com …Waa/GenerateIT`) that a real page emits?

  Experiment **E-H1** answered the first in practice: see F-0014.
- Block classification needs a **JS_GATE** state that is distinct from OK and from blocks.

## Limits

- Static reading of the gate is not deobfuscation. What signals the VM collects is UNKNOWN. The VM bytecode hides it from string search (0 hits for `webdriver`, `canvas`, `getContext`, `hardwareConcurrency`, `plugins` in the page). Runtime observation in Brave (`observe_web_execution`) and static analysis of `/js/bg/*.js` were not done.
- The variable names (`challenge_version`, `cbs`, `ce`, `ss_cgi`, `td`) are from one capture and will churn.
