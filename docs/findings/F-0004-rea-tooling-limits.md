# F-0004 What rea can and cannot do in this project

- **Date:** 2026-10-16. rea MCP server, connected to Claude Code.

| Observation | Label | Consequence |
|---|---|---|
| `capture_browser_scenario` launch mode is backed by "REA Playwright browser scenario capture provider" and drove Chrome 155 fine. | OBSERVED-rea | Live rea scenarios work for Chromium (Chrome, Brave). |
| Pointing launch mode at `camoufox.exe` (Firefox/Juggler) **hung for >120 s with no result** and left 9 orphaned `camoufox.exe` processes, which had to be killed. | OBSERVED-run | rea cannot drive Camoufox. Live tools (`inspect_web_page`, `analyze_web_bundle`, `observe_web_execution`) need a CDP endpoint, and Camoufox has none. |
| `inspect_web_network_capture` accepts Playwright HARs from Camoufox **after** percent-encoding `[ ] | { } ^ \` and similar characters in request URLs (Google's `reqpld=[…]`). The raw HAR fails with `invalid_format … must match format "uri"` at `/log/entries/51/request/url`. | OBSERVED-rea | Camoufox HARs need their URLs percent-encoded before rea reads them (rewrite URLs only, never drop entries). |
| rea redacts `cookie`/`set-cookie` values in scenario captures and hashes storage values. Cookie **names** come from storage capture or the HAR. | OBSERVED-rea | Fine for analysis. Our own HAR files still contain raw values (gitignored). |
| Scenario network records have no initiator stacks; passive capture is needed for that. | OBSERVED-rea (tool limitation text) | "Which script sent X" questions need Brave + passive `observe_web_session`. |
| rea result sizes often exceed the client's inline limit and are spilled to a file. | OBSERVED | Use `detail: "summary"` + `inspect_analysis_view` paging. |

## MCP conventions worth borrowing (from rea itself)

**OBSERVED-rea**, from rea's own tool listing, server instructions and an error it returned:

- Tool names are `snake_case` verb-first (`inspect_web_page`, `capture_browser_scenario`).
- Errors are structured: `{"error":{"code":"invalid_request","category":"invalid_input","message":…,"retryable":true,"remediation":{"action":…},"details":{…}}}`.
- Untrusted target content is flagged in `_meta.provenance.untrusted_content`, and the server instructions tell agents to treat it as data, never as instructions.
- The setup CLI is `rea-agents doctor --client <id> --json`, then `setup --client <id> --dry-run --json`, then `setup --client <id> --yes`. That is diagnose, preview, apply, per client.
- Large results come back as a summary plus a retained ID, with paging.
