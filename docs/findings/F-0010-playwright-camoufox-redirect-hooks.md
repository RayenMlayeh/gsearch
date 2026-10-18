# F-0010 Which Playwright hooks see a 302 in Camoufox, and whether the redirect target can be aborted

- **Date:** 2026-10-17.
- **Versions:** Playwright 1.62.0 + Camoufox 152.0.4-beta.30, headless.
- **Script:** a one-off probe script (not kept in the repo). Fully offline: two local HTTP origins, `/goto` answers 302 to the other origin.
- **Label:** OBSERVED-run (local harness). rea was not needed; this is a library-behaviour question answered by direct execution.

| Hook | Fired for `/goto` (302) | Fired for redirect target |
|---|---|---|
| `page.route("**/*")` handler | yes (`is_navigation_request()=True`) | **no** |
| `page.on("request")` | yes | yes, with `redirected_from` = the `/goto` URL |
| `page.on("response")` | yes: status 302 and **`location` header readable** via `all_headers()` | yes (200) |

Server log: both `/goto` **and** the destination were hit, even though the route handler would have aborted every non-`/goto` request. Routing cannot stop a redirect hop.

## Consequences (D-008, "always resolve URLs")

- The destination URL can be read from the 302 response event, before the destination loads.
- `page.route` cannot prevent the browser from contacting the destination.
- First implementation: open the `/goto` link as a real navigation in the search session's context, wait for `commit`, read the 302 `location`, then leave the tab (`about:blank`). Navigation headers stay those of a real click. The destination receives one partial page request.
- Superseded by F-0011: real middle-clicks plus a route gate, so destination subresources never load.
- UNKNOWN: whether an in-page `fetch(goto, {redirect: "manual"})` exposes the 302 to Playwright's response event. It would avoid the destination but sends `sec-fetch-mode: cors` instead of `navigate`; that difference is ASSUMED detectable, so it is not used.
