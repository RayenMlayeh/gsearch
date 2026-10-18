# Contributing

Thanks for helping. The one rule that shapes everything here: **tests never contact Google or a proxy.**
Live behaviour is checked against saved pages and a local fake Google, so the suite is free, fast and safe
to run anywhere.

## Development setup

```bash
git clone https://github.com/RayenMlayeh/gsearch.git && cd gsearch
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                # one package, gsearch-agent: four import packages under packages/
gsearch setup                          # downloads the Camoufox browser (~1 GB on disk, once)
```

## Checks

All four must pass before a pull request is merged:

```bash
pytest                                  # everything, including real Camoufox against a local fake Google
ruff check packages tests tools
ruff format --check packages tests tools
mypy
```

`GSEARCH_SKIP_BROWSER=1 pytest` skips the tests that launch Camoufox (CI does this, since it has no browser
binary). Everything else runs without a browser.

## How the tests stay offline

| Test file | What it covers | Stand-in for the network |
|---|---|---|
| `test_parse_fixtures.py`, `test_classify.py`, `test_drift.py` | parser, page states, invariants | saved pages in `fixtures/` + golden JSON in `tests/golden/` |
| `test_camoufox_backend.py` | real Camoufox: typed and address-bar searches, link resolution by real clicks, CAPTCHA handling | a local HTTP server that serves the fixtures as "Google" |
| `test_hybrid.py` | browser-to-HTTP session handoff, cookies, redirects, re-gating | local HTTP server + a stand-in browser |
| `test_relay_pool.py` | proxy relay (login, metering, byte cap), identity pool (rotation, retries, country checks) | a local fake upstream proxy and an echo server |
| `test_mcp_stdio.py` | the MCP server through the official client, over stdio | fixture backend |
| `test_schema.py` | the committed JSON schema matches the models | none |
| `test_fixture_hygiene.py` | no session tokens in committed fixtures | none |

## When Google changes its page

A layout change shows up as `parse_drift` errors (never as "0 results"). To fix it:

1. **Capture** fresh pages yourself, on your own connection and at a human pace:
   `python tools/collect_fixtures.py captures/<date>`. This contacts Google live; it stops at the first
   CAPTCHA. Captures contain cookies and session tokens: they stay under `captures/`, which is gitignored.
2. **Minimize** each page into a committable fixture. This strips scripts, styles, images and tracking tokens,
   and replaces every opaque link with a stable hash:
   `python tools/minimize_fixture.py captures/<date>/c3-paa.html fixtures/gecko --id c3-paa --query "what is asyncio in python"`
3. **Update** the selectors in `packages/gsearch-parse/src/gsearch_parse/selectors.py`. A new layout gets a
   new selector set, so the old fixtures keep passing.
4. **Regenerate** the golden files and read the diff line by line before committing:
   `GSEARCH_UPDATE_GOLDEN=1 pytest tests/test_parse_fixtures.py`
5. If the public models changed, regenerate the schema:
   `python -m gsearch_parse.schema > packages/gsearch-parse/src/gsearch_parse/serp.schema.json`

Never commit raw captures, HAR files, `.env` or anything from the state directory. `test_fixture_hygiene.py`
fails if a fixture still carries session tokens.

## Evidence

Claims about how Google, Camoufox or the libraries behave go in a new file in `docs/findings/`, labelled
OBSERVED / ASSUMED / UNKNOWN as described in [docs/findings/README.md](docs/findings/README.md). Say what was
measured, how, and what it does not prove. One run is an anecdote, not a rate.

## Scope

Read [docs/DESIGN.md](docs/DESIGN.md) first. Pull requests that generate or alter Google's anti-abuse tokens,
or that add or recommend a CAPTCHA-solving service, will not be accepted.
