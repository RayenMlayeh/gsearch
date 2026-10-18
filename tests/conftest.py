from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
GECKO = FIXTURES / "gecko"
GOLDEN = ROOT / "tests" / "golden"


@dataclass(frozen=True)
class Fixture:
    id: str
    query: str
    page: int
    url: str
    status: int
    path: Path

    def html(self) -> str:
        data = self.path.read_bytes()
        return (gzip.decompress(data) if self.path.suffix == ".gz" else data).decode("utf-8")


def gecko_fixtures() -> list[Fixture]:
    index = json.loads((GECKO / "index.json").read_text(encoding="utf-8"))
    return [
        Fixture(e["id"], e["query"], e["page"], e["url"], e["status"], (GECKO / e["file"]).resolve())
        for e in index["fixtures"]
    ]


def fixture_by_id(fixture_id: str) -> Fixture:
    return next(f for f in gecko_fixtures() if f.id == fixture_id)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
