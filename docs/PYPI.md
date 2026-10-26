# gsearch

**Real Google Search for your AI agent. No API key. Self-hosted. Over MCP.**

<!-- mcp-name: io.github.RayenMlayeh/gsearch -->

Your agent calls `google_search` and gets Google's actual results page as typed JSON: every link resolved to
its real URL, plus the AI Overview, People also ask and related searches. A real Firefox
([Camoufox](https://github.com/daijro/camoufox)) runs Google's page as it is, on your own IP or through any
proxy you already have.

Screenshots, measured results and the full documentation: **https://github.com/RayenMlayeh/gsearch**

## Quick start

```bash
uv tool install gsearch-agent                         # or: pipx install gsearch-agent
gsearch setup                                         # downloads the browser once (~1 GB), prints your MCP config
claude mcp add gsearch --scope user -- gsearch-mcp    # or any MCP client
```

Without a proxy, gsearch uses your own IP, at most 20 searches an hour. For more, add a proxy in any format
(rotating or static; HTTP, HTTPS or SOCKS5) with a hard data limit:

```bash
claude mcp add gsearch --scope user -e GSEARCH_PROXY="gate.example.com:7000:user-sid-{sid}:password" -e GSEARCH_DATA_LIMIT_MB=1000 -- gsearch-mcp
```

## What your agent gets

| MCP tool | Returns |
|---|---|
| `google_search(query, page, lang, country, time_range, site)` | Organic results with real URLs, answer boxes (sports results, top stories, videos), AI Overview, People also ask, related searches, ads, pagination. A block is an error with `retry_after_s`, never an empty list. |
| `fetch_page(url)` | A page's title, readable text and links, to read the results. Through your proxy, counted against the data limit. |
| `google_search_status()` | Proxy data used against the limit, IPs used and blocked, the cache. Never contacts Google. |

Every string is flagged as untrusted third-party text (`meta.untrusted_content`), because search results can
carry prompt injection.

The same search is available as a CLI (`gsearch search "query"`) and from Python (`gsearch_core.client.SearchClient`).

## Legal

Automated access to Google Search may breach Google's Terms of Service. gsearch ships no CAPTCHA solver and
never forges Google's tokens. Read the
[legal and responsible-use section](https://github.com/RayenMlayeh/gsearch#-legal-and-responsible-use) before
running live searches.

MIT licensed. Camoufox (MPL-2.0) is installed from PyPI as a dependency and is never vendored.
