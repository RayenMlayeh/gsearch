"""Durable per-identity state (SQLite): hourly budget, daily search counts and CAPTCHA rest periods.

Survives restarts, so restarting the MCP server cannot reset the home IP's daily budget or a rest period.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identity_day (
    identity TEXT NOT NULL,
    day TEXT NOT NULL,
    searches INTEGER NOT NULL DEFAULT 0,
    resolves INTEGER NOT NULL DEFAULT 0,
    captchas INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (identity, day)
);
CREATE TABLE IF NOT EXISTS identity_rest (
    identity TEXT PRIMARY KEY,
    until_epoch REAL NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_bucket (
    identity TEXT PRIMARY KEY,
    tokens REAL NOT NULL,
    at_epoch REAL NOT NULL
);
"""


class StateStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._clock = clock

    def close(self) -> None:
        self._db.close()

    def _day(self) -> str:
        return datetime.fromtimestamp(self._clock()).date().isoformat()  # local calendar day

    def _bump(self, identity: str, column: str) -> None:
        self._db.execute(
            f"INSERT INTO identity_day (identity, day, {column}) VALUES (?, ?, 1) "
            f"ON CONFLICT(identity, day) DO UPDATE SET {column} = {column} + 1",
            (identity, self._day()),
        )

    def record_search(self, identity: str) -> None:
        self._bump(identity, "searches")

    def record_resolve(self, identity: str) -> None:
        self._bump(identity, "resolves")

    def record_captcha(self, identity: str, rest_until: float, reason: str) -> None:
        self._bump(identity, "captchas")
        self._db.execute(
            "INSERT INTO identity_rest (identity, until_epoch, reason) VALUES (?, ?, ?) "
            "ON CONFLICT(identity) DO UPDATE SET"
            " until_epoch = excluded.until_epoch, reason = excluded.reason",
            (identity, rest_until, reason),
        )

    def searches_today(self, identity: str) -> int:
        row = self._db.execute(
            "SELECT searches FROM identity_day WHERE identity = ? AND day = ?", (identity, self._day())
        ).fetchone()
        return int(row[0]) if row else 0

    def today(self, identity: str) -> dict[str, int]:
        row = self._db.execute(
            "SELECT searches, resolves, captchas FROM identity_day WHERE identity = ? AND day = ?",
            (identity, self._day()),
        ).fetchone()
        searches, resolves, captchas = row if row else (0, 0, 0)
        return {"searches": int(searches), "resolves": int(resolves), "captchas": int(captchas)}

    def _tokens(self, identity: str, rate_per_s: float, capacity: float) -> float:
        row = self._db.execute(
            "SELECT tokens, at_epoch FROM identity_bucket WHERE identity = ?", (identity,)
        ).fetchone()
        if row is None:
            return capacity
        return min(capacity, float(row[0]) + max(0.0, self._clock() - float(row[1])) * rate_per_s)

    def token_wait_s(self, identity: str, rate_per_s: float, capacity: float) -> float:
        tokens = self._tokens(identity, rate_per_s, capacity)
        return 0.0 if tokens >= 1 else (1 - tokens) / rate_per_s

    def take_token(self, identity: str, rate_per_s: float, capacity: float) -> float:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            tokens = self._tokens(identity, rate_per_s, capacity)
            if tokens < 1:
                return (1 - tokens) / rate_per_s
            self._db.execute(
                "INSERT OR REPLACE INTO identity_bucket (identity, tokens, at_epoch) VALUES (?, ?, ?)",
                (identity, tokens - 1, self._clock()),
            )
            return 0.0
        finally:
            self._db.execute("COMMIT")

    def resting_until(self, identity: str) -> tuple[float, str] | None:
        """(until_epoch, reason) if the identity is resting now, else None."""
        row = self._db.execute(
            "SELECT until_epoch, reason FROM identity_rest WHERE identity = ?", (identity,)
        ).fetchone()
        if row and float(row[0]) > self._clock():
            return float(row[0]), str(row[1])
        return None
