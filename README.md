<p align="center">
  <img src="docs/assets/banner.svg" alt="gsearch: Google Search for AI agents. Real results as clean JSON, over MCP." width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-4285F4" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-34A853" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/MCP-stdio-EA4335" alt="MCP server over stdio">
  <img src="https://img.shields.io/badge/proxies-any%20format-FBBC05" alt="Any proxy format">
</p>

<p align="center"><img src="docs/assets/results.svg" alt="Results: 24 of 24 searches served with 0 CAPTCHAs, 1.2 s per search, 0.6 MB per search, every link resolved to its real URL" width="100%"></p>

<p align="center"><sub>
Measured on clean IPs:
served and speed: <a href="docs/findings/F-0014-eh1-hybrid-session-reuse.md">F-0014</a>,
<a href="docs/findings/F-0006-fixture-provenance-and-serp-anatomy.md">F-0006</a>,
<a href="docs/findings/F-0011-goto-resolution-leak-and-fix.md">F-0011</a>,
<a href="docs/findings/F-0012-headless-vs-headed-fingerprint.md">F-0012</a>,
<a href="docs/findings/F-0001-cold-start-chrome-vs-camoufox.md">F-0001</a> ·
14 s and 0.6 MB on a clean proxy IP: <a href="docs/findings/F-0015-b1-residential-proxy-benchmark.md">F-0015</a> ·
full numbers, including flagged pools, in <a href="#performance">Performance</a>.
</sub></p>

**gsearch gives your AI agent Google Search.** It runs real searches in a real Firefox (Camoufox) through
your proxies. Each results page comes back as clean JSON: organic results with their **real destination
URLs**, the AI Overview, People Also Ask, related searches, ads and pagination. Use it as an **MCP server**
(Claude Code, Claude Desktop, Cursor and any other MCP client), from the **command line**, or from
**Python**.

There is almost nothing to configure. Give it a proxy, or none at all, and it uses the setup that worked
best in testing. When Google pushes back with a CAPTCHA, a consent page or a layout change, your agent gets
a clear error with `retry_after_s`. It never gets silently wrong data.

> [!IMPORTANT]
> Automated access to Google Search may breach Google's Terms of Service, and there is active litigation in
> this area. Read [Legal and responsible use](#legal-and-responsible-use) before running live searches.

## What your agent gets

<p align="center"><img src="docs/assets/what-you-get.svg" alt="Each part of a Google results page and the JSON field it becomes" width="100%"></p>

| Field | What it holds |
|---|---|
| `organic[].url` | The real destination of each result. Google serves opaque `/goto?url=…` redirects; gsearch resolves every one in the same session, without loading the page. |
| `organic[].title`, `snippet`, `date`, `domain`, `display_url` | What the result shows, as text. `link` keeps the exact link Google served. |
| `organic[].sitelinks`, `source_meta` | A result's sub-links, and what Google shows instead of a URL on forum and video results (`2 years ago`). |
| `ai_overview` | The AI Overview text and its sources, when Google shows one. |
| `people_also_ask`, `related_searches` | The questions and related queries on the page. |
| `ads` | Text ads, when the market serves them. |
| `pagination` | Current page and next page. Ask for more results with `page`. |
| `meta.untrusted_content` | Always `true`: every string is third-party web text (see [prompt injection](#prompt-injection)). |

The full JSON Schema is in [`serp.schema.json`](packages/gsearch-parse/src/gsearch_parse/serp.schema.json).
Here is real output from a saved results page (offline demo mode, where `url` stays `null`; a live search
fills it):

<p align="center"><img src="docs/assets/search.svg" alt="gsearch search output" width="820"></p>

A block is never an empty result. It comes back as an error that says what happened, how long to wait and
what to do:

<p align="center"><img src="docs/assets/captcha.svg" alt="A CAPTCHA returned as a structured error" width="820"></p>

## Quick start

You need Python 3.12+ and about 1 GB of disk for the browser.

**1. Install**

```bash
git clone https://github.com/RayenMlayeh/gsearch.git
cd gsearch
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -e packages/gsearch-parse -e "packages/gsearch-core[live]" -e packages/gsearch-cli -e packages/gsearch-mcp
python -m camoufox fetch           # downloads the browser once
```

Check that it works, offline (the answer comes from a saved page):

```bash
gsearch search "what is asyncio in python" --fixtures fixtures/gecko
```

**2. Add your proxy** (optional, but needed for volume)

```bash
cp .env.example .env
```

```ini
GSEARCH_PROXY=user-country-us-sid-{sid}:password@gate.example.com:7000
GSEARCH_DATA_LIMIT_MB=1000
```

Those are the only two settings most people need. The proxy can be in [any format](#proxies). The data
limit stops all proxy traffic once that many MB have been used, counted across restarts.

**3. Connect your agent**

The server is one command, `gsearch-mcp`. MCP clients start it in their own folder, so tell it where your
`.env` is with `GSEARCH_ENV_FILE`. Use absolute paths. On Windows the command is
`C:\path\to\gsearch\.venv\Scripts\gsearch-mcp.exe`; on macOS and Linux it is
`/path/to/gsearch/.venv/bin/gsearch-mcp`.

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add gsearch --scope user \
  -e GSEARCH_ENV_FILE=/path/to/gsearch/.env \
  -- /path/to/gsearch/.venv/bin/gsearch-mcp
```

</details>

<details>
<summary><b>Claude Desktop</b> (<code>claude_desktop_config.json</code>)</summary>

```json
{
  "mcpServers": {
    "gsearch": {
      "command": "C:\\path\\to\\gsearch\\.venv\\Scripts\\gsearch-mcp.exe",
      "env": { "GSEARCH_ENV_FILE": "C:\\path\\to\\gsearch\\.env" }
    }
  }
}
```

</details>

<details>
<summary><b>Cursor</b> (<code>~/.cursor/mcp.json</code>, or <code>.cursor/mcp.json</code> in a project)</summary>

```json
{
  "mcpServers": {
    "gsearch": {
      "command": "/path/to/gsearch/.venv/bin/gsearch-mcp",
      "env": { "GSEARCH_ENV_FILE": "/path/to/gsearch/.env" }
    }
  }
}
```

</details>

<details>
<summary><b>Offline demo</b> (saved pages, no Google traffic)</summary>

```bash
claude mcp add gsearch-demo -e GSEARCH_FIXTURES_DIR=/path/to/gsearch/fixtures/gecko -- /path/to/gsearch/.venv/bin/gsearch-mcp
```

It answers only the saved queries in `fixtures/gecko/index.json`, such as `what is asyncio in python`.

</details>

Then ask your agent something like:

> *"Search Google for what asyncio is in Python and summarise the top three results with their links."*<br>
> *"What do people also ask about asyncio?"*<br>
> *"Find asyncio pages on python.org from the past year."*<br>
> *"How much proxy data has gsearch used?"*

## How it works

<p align="center"><img src="docs/assets/how-it-works.svg" alt="One search: agent asks, fresh IP, real Firefox, clean JSON, all closed" width="100%"></p>

- **A real browser.** Google only shows results after a JavaScript check. On the same IP, a
  Playwright-driven Chrome was sent to a CAPTCHA while Camoufox was served
  ([F-0001](docs/findings/F-0001-cold-start-chrome-vs-camoufox.md)). gsearch never forges or alters Google's
  tokens: the browser runs the page as it is.
- **Like a person.** A real-device fingerprint. Locale and location that match the IP. A visible window,
  because on proxy IPs headless sessions were challenged far more often
  ([F-0015](docs/findings/F-0015-b1-residential-proxy-benchmark.md)). The query is typed into the address
  bar. WebRTC is off, so your own IP can't leak around the proxy.
- **Real URLs, cheaply.** Result links are resolved from Google's own redirects, on the same IP with the same
  cookies and a TLS fingerprint matching the browser's. Destination pages are never loaded
  ([F-0011](docs/findings/F-0011-goto-resolution-leak-and-fix.md),
  [F-0013](docs/findings/F-0013-tls-camoufox-vs-curl-cffi.md)).
- **On a server.** On a Linux machine with no screen, the browser runs on a virtual display automatically.
  Install Xvfb (`sudo apt install xvfb`).

The design and its trade-offs are in [docs/DESIGN.md](docs/DESIGN.md). Every claim about Google's behaviour
is backed by a write-up in [docs/findings/](docs/findings/README.md).

## Proxies

<p align="center"><img src="docs/assets/proxies.svg" alt="Rotating residential, static or VPS proxies, or no proxy; any format" width="100%"></p>

Put one proxy, several, or the path of a file in `GSEARCH_PROXY`:

```ini
# rotating residential: {sid}, or the provider's own sid-…, session-…, sessid-… part, gives a new IP per search
GSEARCH_PROXY=gate.example.com:7000:user-country-us-sid-{sid}:password

# static proxies (VPS, datacenter, ISP): one or many, separated by commas, spaces or new lines
GSEARCH_PROXY=socks5://user:password@203.0.113.10:1080, http://203.0.113.11:3128

# or a file with one proxy per line (# comments allowed)
GSEARCH_PROXY=/path/to/proxies.txt
```

| Accepted | Examples |
|---|---|
| Layouts | `host:port` · `host:port:user:pass` · `user:pass@host:port` · `user:pass:host:port` |
| Types | `http://` (the default) · `https://` · `socks5://` · `socks5h://` |
| Country check | `country-xx` or `cc-xx` in the login: exits elsewhere are dropped before the browser starts. |

- **Rotating:** a new IP and a new browser for every search, up to 3 at once. A blocked IP is replaced, up
  to 4 IPs per search.
- **Static:** each IP keeps one warm session, and the IPs take turns. Each has its own budget (20 searches
  an hour, 150 a day), and an IP that meets a CAPTCHA rests until midnight. Ten static IPs allow up to about 200
  searches an hour.
- **Data:** every byte goes through a local relay that counts it, which is what residential providers bill.
  `GSEARCH_DATA_LIMIT_MB` is a hard stop that holds across restarts.

> [!NOTE]
> Check your proxy provider's acceptable-use policy. Many forbid using their network on sites whose terms
> prohibit automated access, and forbid solving CAPTCHAs.

## Performance

**Proxy quality decides speed and cost.** Many residential IPs are already flagged by Google. Each flagged
one costs a browser launch (about 0.7–1 MB and 20 s) before the next IP is tried.

### Expected with a high-quality rotating residential proxy

These are **expected** figures, built from measurements on clean IPs. They assume that **at least ~85% of
fresh IPs are served by Google**.

| | Expected | Measured basis |
|---|---|---|
| Time per search, links resolved | ~15–20 s | 14 s on a clean IP, all links resolved |
| Proxy data per search | ~0.6–1 MB | 0.6 MB on a clean IP |
| Throughput (3 browsers at once) | ~500–600 searches an hour | the time above, 3 at once |
| 1,000 searches | ~0.6–1 GB, under 2 hours | the two rows above |
| No proxy (warm session on your own IP) | ~1.2 s per search | 4 of 4 served ([F-0014](docs/findings/F-0014-eh1-hybrid-session-reuse.md)); limited to 20 an hour |

### Measured on a low-quality pool

One evening on one provider's US residential pool, where most IPs were already flagged
([F-0015](docs/findings/F-0015-b1-residential-proxy-benchmark.md)):

| | Measured |
|---|---|
| Fresh IPs served by Google | 1 in 3 to 1 in 5 |
| Proxy data per search (failed IPs included) | 4.7–6.3 MB |
| Time per search (retries included) | 40–75 s |
| Throughput (3 browsers at once) | ~72 searches an hour |
| Result links resolved | all of them |

Static proxies have not been benchmarked yet.

### Measure your own proxy

```bash
python tools/benchmark.py -n 10
```

The benchmark runs ten searches the way the MCP server does, within your data limit. It then reports
searches served, IPs used, MB and seconds per search, and links resolved.

## Settings

| Setting | What it does |
|---|---|
| `GSEARCH_PROXY` | Your proxies, in any format above, or a file listing them. Unset: your own IP. |
| `GSEARCH_DATA_LIMIT_MB` | Stop all proxy traffic at this many MB, counted across restarts. Unset: no limit. |
| `GSEARCH_ENV_FILE` | Absolute path of your `.env`. Needed in MCP client configs. If set, the file must exist. |
| `GSEARCH_FIXTURES_DIR` | Answer from saved pages instead of Google (demos and tests). |
| `GSEARCH_STATE_DIR` | Where budgets, the data meter and saved block pages live. Default: `%LOCALAPPDATA%\gsearch` or `~/.local/share/gsearch`. |

Everything else is fixed to what worked best in testing: one-shot rotation, a visible browser, address-bar
search, real-device fingerprints, 4 attempts per search, always resolving URLs, and a 10-minute cache for
repeated queries.

## Tools, CLI and Python

**MCP tools**

| Tool | Arguments | Returns |
|---|---|---|
| `google_search` | `query`, and optionally `page` (1–50), `lang` (`en`, `fr`, …), `country` (`us`, …), `time_range` (`h`, `d`, `w`, `m`, `y`), `site` (a domain) | The JSON above, plus a short text summary. An error result on a block. |
| `google_search_status` | none | Proxy data used against the limit, IPs used and blocked, cache. Never contacts Google. |

**CLI**

```text
gsearch search "query" [--page N] [--lang xx] [--country xx] [--time-range h|d|w|m|y] [--site domain]
gsearch search "query" --fixtures fixtures/gecko     answer from saved pages (offline)
gsearch parse page.html                              parse any saved Google results page
gsearch schema                                       print the JSON Schema
gsearch identity check                               check the browser's fingerprint matches its IP (no Google traffic)
```

Exit codes: `0` results, `2` usage or settings error, `3` blocked or other error, `4` Google changed its
layout.

**Python**

```python
import asyncio
from gsearch_core.client import SearchClient
from gsearch_core.config import fetcher_from_env
from gsearch_parse.models import SearchRequest

async def main() -> None:
    fetcher, _live = fetcher_from_env()      # reads the settings and .env
    result = await SearchClient(fetcher).search(SearchRequest(query="what is asyncio in python"))
    print(result.model_dump_json(indent=2))  # SearchResponse or SearchError
    await fetcher.aclose()

asyncio.run(main())
```

## Troubleshooting

| You see | What it means | What to do |
|---|---|---|
| `captcha` on most searches | Your proxy's IPs are flagged by Google. | Try another pool or country. Measure with `tools/benchmark.py`. |
| `captcha` with hours to wait, no proxy | Your own IP was challenged and rests until midnight. | Wait, or add a proxy. |
| `rate_limited` | Every IP is at its hourly or daily budget (own IP or static proxies). | Wait `retry_after_s`, or add proxies. |
| `proxy: no usable exit in 4 draws` | Four IPs in a row were dead or in the wrong country. | Check the country part of the login, and the provider. |
| `proxy: proxy data limit reached` | `GSEARCH_DATA_LIMIT_MB` is used up. | Raise it, or delete `proxy_bytes.json` in the state folder to count from zero. |
| `parse_drift` | Google changed its page layout. | Update gsearch, or see [CONTRIBUTING.md](CONTRIBUTING.md#when-google-changes-its-page). |
| `GSEARCH_ENV_FILE=… does not exist` | The settings path is wrong. | Use an absolute path. gsearch refuses to start rather than fall back to your own IP. |
| Browser windows open | Expected with a proxy: visible sessions are challenged far less. | On a server without a screen, install Xvfb and they run on a virtual display. |

When the browser hits a block or an error, the page is saved with a screenshot in the state folder's
`evidence/`, and the error's `evidence_ref` points to it.

## Legal and responsible use

- **Google's Terms of Service restrict automated access to Google Search.** Running this tool against Google
  may breach those terms, whatever this project's license says.
- **Litigation risk is real and current.** In *Google v. SerpApi* (N.D. Cal., filed 2025-12-19), Google argues
  that solving its SearchGuard JavaScript challenge by automated means is circumvention under DMCA §1201.
  gsearch runs that challenge in a real browser and does not forge or alter tokens, but the complaint also
  targets automated browsers. See [docs/DESIGN.md](docs/DESIGN.md) (D-004).
- **No CAPTCHA solver is shipped, wired in or recommended.** `gsearch_core.captcha` defines an interface only;
  anything you plug into it, and its legality, is your responsibility.
- **Get legal advice** for your jurisdiction and use case before running live searches. The parser
  (`gsearch-parse`) only reads HTML you already have.
- **Limits are on by default and cannot be bypassed from the CLI or MCP.** Don't use proxies to hammer
  Google.
- **No personal-data harvesting.** Don't profile people, scrape people-search results or collect personal
  data at scale. Keep caches short-lived and don't republish raw results.
- **Prefer licensed data where it exists.** Google's Custom Search JSON API is closed to new customers and ends
  on 2027-01-01 ([F-0005](docs/findings/F-0005-custom-search-api-sunset.md)).
- **On a block, back off.** CAPTCHAs and rate limits come back as errors with `retry_after_s`. gsearch never
  tries to defeat the challenge.

### Prompt injection

Every title, snippet, URL and question in a result is **untrusted third-party text** from the open web, and
may contain instructions aimed at your agent. The tool descriptions say so, every response carries
`meta.untrusted_content: true` and an MCP `untrusted_content` flag, and the text summary ends with a
warning. Treat results strictly as data.

## Contributing

Tests never contact Google: the parser is tested against saved pages, and the browser against a local fake
Google. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Camoufox (MPL-2.0) is installed from PyPI as an optional dependency and is never vendored.
