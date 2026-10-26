"""Settings, shared by the CLI and the MCP server. Everything else is fixed to what worked best in testing.

GSEARCH_PROXY          one or more proxies in any common format, or the path of a file listing them.
                       Unset: searches go out from this machine's own IP.
GSEARCH_DATA_LIMIT_MB  stop all proxy traffic at this many MB, counted across runs (default: no limit)
GSEARCH_HEADLESS       1: no browser window behind proxies either (default: a visible window behind proxies)
GSEARCH_ENV_FILE       absolute path of the settings file (default: .env in the working directory)
GSEARCH_FIXTURES_DIR   answer from saved pages instead of Google (demos and tests)
GSEARCH_STATE_DIR      budgets, data meter and evidence (default %LOCALAPPDATA%\\gsearch or
                       ~/.local/share/gsearch)

Variables are read from the process environment first, then from the settings file.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .fetch import Fetcher, FixtureFetcher


def load_env_file(path: Path, env: dict[str, str]) -> dict[str, str]:
    """KEY=VALUE lines from a local .env into `env`; existing keys win."""
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env.setdefault(key.strip(), value.strip().strip("\"'"))
    return env


def default_env() -> dict[str, str]:
    """The process environment over a `.env` file: the one GSEARCH_ENV_FILE names, else the working
    directory's. MCP clients start servers in their own working directory, hence GSEARCH_ENV_FILE."""
    named = os.environ.get("GSEARCH_ENV_FILE")
    path = Path(named) if named else Path.cwd() / ".env"
    if named and not path.is_file():
        # Failing loudly matters: without its proxy settings the tool would search from the home IP.
        raise ValueError(f"GSEARCH_ENV_FILE={named} does not exist")
    return {**load_env_file(path, {}), **os.environ}


def fetcher_from_env(env: Mapping[str, str] | None = None) -> tuple[Fetcher, bool]:
    """(fetcher, live). Imports the browser stack only for live search."""
    env = default_env() if env is None else env
    if fixtures := env.get("GSEARCH_FIXTURES_DIR"):
        return FixtureFetcher(Path(fixtures)), False

    from .camoufox_backend import CamoufoxConfig, CamoufoxFetcher, state_dir
    from .hybrid import HybridFetcher

    state = Path(env["GSEARCH_STATE_DIR"]) if env.get("GSEARCH_STATE_DIR") else state_dir()
    proxies = env.get("GSEARCH_PROXY", "").strip()
    if not proxies:
        # Own IP: one warm session, headless, with a conservative budget.
        return HybridFetcher(
            CamoufoxFetcher(
                CamoufoxConfig(state_path=state / "state.sqlite", evidence_dir=state / "evidence")
            )
        ), True

    from .pool import PoolConfig, ProxyPool
    from .relay import parse_proxies

    limit = env.get("GSEARCH_DATA_LIMIT_MB")
    # Behind proxies: a visible browser (headless sessions drew far more CAPTCHAs on residential exits),
    # queries typed into the address bar, real-device fingerprints.
    browser = CamoufoxConfig(
        headless=env.get("GSEARCH_HEADLESS") == "1",
        entry="address_bar",
        fingerprint_preset=True,
        state_path=state / "state.sqlite",
        evidence_dir=state / "evidence",
    )
    pool = ProxyPool(
        PoolConfig(
            proxies=parse_proxies(proxies),
            browser=browser,
            byte_cap_bytes=int(float(limit) * 1e6) if limit else None,
            meter_path=state / "proxy_bytes.json",
        )
    )
    return pool, True
