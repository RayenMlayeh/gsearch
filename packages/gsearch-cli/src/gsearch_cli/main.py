"""`gsearch` CLI. Thin adapter: argument parsing and JSON output only; no scraping logic.

  gsearch parse page.html [--url URL] [--page N]   parse a saved page (offline)
  gsearch search "query" [...]                     search Google (settings: GSEARCH_* in the environment
                                                   or .env, see gsearch_core.config)
  gsearch search "query" --fixtures DIR [...]      search saved pages (offline)
  gsearch fetch URL                                readable text and links of a web page (through the proxy
                                                   when GSEARCH_PROXY is set)
  gsearch identity check [--headed]                launch the home-IP device against a local page and
                                                   check it is coherent with its exit IP (no Google traffic)
  gsearch schema                                   print the output JSON schema
  gsearch setup                                    download the browser once and print the MCP client config
Exit codes: 0 ok, 2 usage, 3 blocked/error result, 4 parse drift.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from gsearch_core.client import SearchClient
from gsearch_core.config import default_env, fetcher_from_env
from gsearch_core.fetch import Fetcher, FixtureFetcher
from gsearch_parse import PageState, parse_page
from gsearch_parse.models import PageContent, SearchError, SearchRequest, SearchResponse
from gsearch_parse.schema import render


def _read(path: Path) -> str:
    data = path.read_bytes()
    return (gzip.decompress(data) if path.suffix == ".gz" else data).decode("utf-8")


def _emit(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def cmd_parse(args: argparse.Namespace) -> int:
    result = parse_page(_read(args.file), url=args.url, status=args.status, page=args.page)
    _emit(
        {
            "state": result.state.value,
            "reason": result.reason,
            "selector_set": result.selector_set,
            "warnings": list(result.warnings),
            "data": result.data.model_dump(mode="json") if result.data else None,
        }
    )
    if result.state is PageState.PARSE_DRIFT:
        return 4
    return 0 if result.state in (PageState.OK, PageState.EMPTY_VALID) else 3


async def _search_once(fetcher: Fetcher, req: SearchRequest) -> SearchResponse | SearchError:
    try:
        return await SearchClient(fetcher).search(req)
    finally:
        await fetcher.aclose()


def cmd_search(args: argparse.Namespace) -> int:
    req = SearchRequest(
        query=args.query,
        page=args.page,
        lang=args.lang,
        country=args.country,
        time_range=args.time_range,
        site=args.site,
    )
    fetcher: Fetcher = FixtureFetcher(args.fixtures) if args.fixtures else fetcher_from_env()[0]
    result = asyncio.run(_search_once(fetcher, req))
    if isinstance(result, SearchError):
        _emit({"error": result.model_dump(mode="json")})
        return 4 if result.code == "parse_drift" else 3
    _emit(result.model_dump(mode="json"))
    return 0


async def _fetch_once(fetcher: Fetcher, url: str) -> PageContent | SearchError:
    try:
        return await SearchClient(fetcher).fetch_page(url)
    finally:
        await fetcher.aclose()


def cmd_fetch(args: argparse.Namespace) -> int:
    result = asyncio.run(_fetch_once(fetcher_from_env()[0], args.url))
    if isinstance(result, SearchError):
        _emit({"error": result.model_dump(mode="json")})
        return 3
    _emit(result.model_dump(mode="json"))
    return 0


def cmd_identity_check(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from gsearch_core.camoufox_backend import CamoufoxConfig
    from gsearch_core.probe import coherence, probe

    if default_env().get("GSEARCH_PROXY", "").strip():
        raise ValueError("identity check covers the home-IP identity only; unset GSEARCH_PROXY to run it")
    place, seen = asyncio.run(probe(replace(CamoufoxConfig(), headless=not args.headed)))
    checks = coherence(place, seen)
    _emit(
        {
            "place": None
            if place is None
            else {"country": place.country, "timezone": place.timezone, "locale": place.locale},
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
            "seen": seen,
        }
    )
    return 0 if all(c.ok for c in checks) else 3


def _server_command() -> str:
    found = shutil.which("gsearch-mcp")
    if found:
        return str(Path(found).resolve())
    return str(Path(sys.executable).with_name("gsearch-mcp.exe" if os.name == "nt" else "gsearch-mcp"))


def _say(msg: str) -> None:
    sys.stderr.write(f"gsearch: {msg}\n")


def cmd_setup(args: argparse.Namespace) -> int:
    from camoufox.pkgman import camoufox_path

    try:
        camoufox_path(download_if_missing=False)
        _say("the Camoufox browser is already installed.")
    except Exception:
        _say("downloading the Camoufox browser (~1 GB, once)...")
        if subprocess.call([sys.executable, "-m", "camoufox", "fetch"]) != 0:
            _say("download failed; run `python -m camoufox fetch` to see why.")
            return 3

    server = _server_command()
    env_file = os.environ.get("GSEARCH_ENV_FILE") or (".env" if Path(".env").is_file() else None)
    env = {"GSEARCH_ENV_FILE": str(Path(env_file).resolve())} if env_file else {}
    entry: dict[str, object] = {"command": server, **({"env": env} if env else {})}
    claude_env = "".join(f" -e {k}={v}" for k, v in env.items())
    lines = [
        "",
        "Ready. Connect your agent:",
        "",
        "Claude Code:",
        f'  claude mcp add gsearch --scope user{claude_env} -- "{server}"',
        "",
        "Claude Desktop, Cursor and other MCP clients (add to their mcpServers config):",
        json.dumps({"mcpServers": {"gsearch": entry}}, indent=2),
    ]
    if not env:
        lines += [
            "",
            "No proxy settings found, so searches use your own IP (at most 20 an hour). To use a proxy, run",
            "`gsearch setup` again from the folder that holds your .env, or with GSEARCH_ENV_FILE set.",
        ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gsearch", description="Google search results as structured JSON.")
    p.add_argument("--version", action="version", version=f"gsearch {version('gsearch-agent')}")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("parse", help="parse a saved results page (offline)")
    pp.add_argument("file", type=Path)
    pp.add_argument("--url", default="https://www.google.com/search")
    pp.add_argument("--status", type=int, default=200)
    pp.add_argument("--page", type=int, default=1)
    pp.set_defaults(func=cmd_parse)

    ps = sub.add_parser("search", help="search Google")
    ps.add_argument("query")
    ps.add_argument("--fixtures", type=Path, help="answer from saved pages in this directory (offline)")
    ps.add_argument("--page", type=int, default=1)
    ps.add_argument("--lang", help="interface language; default: the exit country's language")
    ps.add_argument("--country")
    ps.add_argument("--time-range", choices=["h", "d", "w", "m", "y"])
    ps.add_argument("--site")
    ps.set_defaults(func=cmd_search)

    pf = sub.add_parser("fetch", help="readable text and links of a web page")
    pf.add_argument("url")
    pf.set_defaults(func=cmd_fetch)

    ident = sub.add_parser("identity", help="identity tools")
    isub = ident.add_subparsers(dest="identity_command", required=True)
    chk = isub.add_parser("check", help="fingerprint coherence of the home-IP identity (no Google traffic)")
    chk.add_argument("--headed", action="store_true", help="probe the headed browser instead of headless")
    chk.set_defaults(func=cmd_identity_check)

    sc = sub.add_parser("schema", help="print the output JSON schema")
    sc.set_defaults(func=lambda _a: sys.stdout.write(render()) and 0)

    st = sub.add_parser("setup", help="download the browser once and print the MCP client config")
    st.set_defaults(func=cmd_setup)
    return p


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except ValueError as exc:  # configuration or argument errors
        sys.stderr.write(f"gsearch: {exc}\n")
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
