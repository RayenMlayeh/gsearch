"""JSON schema of the public output, generated from the pydantic models (the single source of truth).

`python -m gsearch_parse.schema > packages/gsearch-parse/src/gsearch_parse/serp.schema.json`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import SearchError, SearchResponse

SCHEMA_PATH = Path(__file__).with_name("serp.schema.json")


def build_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "gsearch output",
        "oneOf": [{"$ref": "#/$defs/SearchResponse"}, {"$ref": "#/$defs/SearchError"}],
        "$defs": {
            "SearchResponse": SearchResponse.model_json_schema(mode="serialization"),
            "SearchError": SearchError.model_json_schema(mode="serialization"),
        },
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(render(), end="")
