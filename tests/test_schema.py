from __future__ import annotations

from gsearch_parse.schema import SCHEMA_PATH, render


def test_checked_in_schema_matches_models() -> None:
    """Changing the models without regenerating the published schema is a breaking change in disguise."""
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(), (
        "Regenerate: python -m gsearch_parse.schema"
        " > packages/gsearch-parse/src/gsearch_parse/serp.schema.json"
    )
