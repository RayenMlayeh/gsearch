"""Turn a captured SERP into a committable fixture.

- removes scripts, styles, SVG, images, iframes, comments and inline data: URIs;
- replaces session/tracking tokens (ei, sxsrf, iflsig, sca_esv, ved, ...) with placeholders;
- replaces each opaque /goto?url=<blob> with a stable hash so links stay unique but carry no session data;
- writes <out_dir>/<id>.html.gz and upserts the entry in <out_dir>/index.json.

usage: python tools/minimize_fixture.py <captured.html> <out_dir> --id c2-featured-snippet
           --query "how tall is the eiffel tower" [--page 1] [--engine gecko]
           [--source <capture sha256 prefix>]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser

DROP = "script, style, svg, img, iframe, noscript, link, meta, template, canvas, video, audio, picture"
TOKEN_PARAMS = (
    "ei",
    "sxsrf",
    "iflsig",
    "sca_esv",
    "ved",
    "uact",
    "oq",
    "gs_lp",
    "sclient",
    "biw",
    "bih",
    "dpr",
)
TOKEN_ATTRS = (
    "data-ved",
    "decode-data-ved",
    "data-lk",
    "jsdata",
    "data-hveid",
    "data-mlro",
    "data-mlros",
    "data-mrc",
    "data-async-context",
    "data-amp",
    "data-im",
    "data-s",
)
GOTO = re.compile(r"/goto\?url=([A-Za-z0-9_\-%]+)")
# Plain (?a=), HTML-escaped (&amp;a=) and URL-encoded inside continue= URLs (%26a%3D, %2526a%253D).
_NAMES = "|".join(TOKEN_PARAMS)
PARAM = re.compile(rf"((?:[?&]|&amp;|%26|%2526)(?:{_NAMES})(?:=|%3D|%253D))[^&\"'#%]*")
DATA_URI = re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def _goto_sub(m: re.Match[str]) -> str:
    return "/goto?url=FIXTURE_" + hashlib.sha256(m.group(1).encode()).hexdigest()[:16]


EI_VALUE = re.compile(r"(?:[?&;]ei=|ei%3D|ei%253D|\"ei\":\"|kEI:')([A-Za-z0-9_-]{16,})")


def scrub_event_ids(html: str) -> str:
    """Google's per-request event ids (ei, aqid, sdh_* element ids) share a time-derived 5-char prefix.
    Collect the prefixes from the known ei values and replace every long token that starts with one."""
    prefixes = {v[:5] for v in EI_VALUE.findall(html)}
    if not prefixes:
        return html
    alternatives = "|".join(map(re.escape, prefixes))
    token = re.compile(rf"(?:(?<![A-Za-z0-9])|(?<=%3D)|(?<=%253D))(?:{alternatives})[A-Za-z0-9_-]{{12,}}")
    mapping: dict[str, str] = {}
    return token.sub(lambda m: mapping.setdefault(m.group(0), f"EIFIXTURE{len(mapping)}"), html)


def minimize(html: str) -> str:
    html = scrub_event_ids(html)
    tree = LexborHTMLParser(html)
    for node in tree.css(DROP):
        node.decompose()
    for node in tree.css("*"):
        for attr in TOKEN_ATTRS:
            if attr in node.attributes:
                node.attrs[attr] = "X"
    for node in tree.css("input[name][value]"):
        if node.attributes.get("name") in TOKEN_PARAMS:
            node.attrs["value"] = "X"
    out = tree.html or ""
    out = re.sub(r"<!--.*?-->", "", out, flags=re.S)
    out = GOTO.sub(_goto_sub, out)
    out = PARAM.sub(r"\1X", out)
    out = DATA_URI.sub("data:,", out)
    return re.sub(r"\n\s*\n+", "\n", out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--id", required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--status", type=int, default=200)
    ap.add_argument("--engine", default="gecko")
    ap.add_argument("--source", default="")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    html = minimize(args.src.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{args.id}.html.gz"
    (args.out_dir / name).write_bytes(gzip.compress(html.encode("utf-8"), mtime=0))

    index_path = args.out_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"fixtures": []}
    params = {"q": args.query, "hl": "en"} | ({"start": str((args.page - 1) * 10)} if args.page > 1 else {})
    entry = {
        "id": args.id,
        "query": args.query,
        "page": args.page,
        "file": name,
        "url": "https://www.google.com/search?" + urlencode(params),
        "status": args.status,
        "engine": args.engine,
        "source": args.source,
        "note": args.note,
    }
    index["fixtures"] = [e for e in index["fixtures"] if e["id"] != args.id] + [entry]
    index["fixtures"].sort(key=lambda e: e["id"])
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{name}: {len(html):,} chars minimized")


if __name__ == "__main__":
    main()
