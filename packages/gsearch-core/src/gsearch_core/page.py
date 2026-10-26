"""Page fetching for agents: a public http(s) page as readable text and links.

Goes through the proxy when one is configured, so its bytes count against the data limit; else direct.
Only public addresses are fetched: a URL from the web must never reach this machine or its network.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

from curl_cffi.const import CurlOpt
from curl_cffi.requests import AsyncSession
from gsearch_parse.models import PageContent
from gsearch_parse.page import readable

from .fetch import FetchError
from .hybrid import IMPERSONATE

MAX_BYTES = 5_000_000
MAX_REDIRECTS = 5
TIMEOUT_S = 20
_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


async def public_address(host: str) -> str:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError("network", f"cannot resolve {host}", retryable=False) from exc
    addresses = [str(info[4][0]) for info in infos]
    if not addresses or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses):
        raise FetchError("invalid_request", f"{host} is not a public address", retryable=False)
    return addresses[0]


async def fetch_page(url: str, *, proxy: str | None = None) -> PageContent:
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError("invalid_request", "only http and https URLs can be fetched", retryable=False)
        address = await public_address(parts.hostname)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        pinned = f"[{address}]" if ":" in address else address
        options: dict[CurlOpt, Any] = (
            {} if proxy else {CurlOpt.RESOLVE: [f"{parts.hostname}:{port}:{pinned}"]}
        )
        async with AsyncSession(impersonate=IMPERSONATE, proxy=proxy, curl_options=options) as http:
            try:
                resp = await http.get(url, allow_redirects=False, stream=True, timeout=TIMEOUT_S)
            except Exception as exc:
                raise FetchError("network", f"fetch failed: {type(exc).__name__}", retryable=True) from exc
            try:
                location = resp.headers.get("location")
                if 300 <= resp.status_code < 400 and location:
                    url = urljoin(url, location)
                    continue
                if resp.status_code >= 400:
                    retry = resp.status_code == 429 or resp.status_code >= 500
                    raise FetchError("network", f"HTTP {resp.status_code}", retryable=retry)
                content_type = (resp.headers.get("content-type") or "text/html").lower()
                if not content_type.startswith(_TYPES):
                    raise FetchError(
                        "invalid_request", f"unsupported content type {content_type}", retryable=False
                    )
                body = bytearray()
                async for chunk in resp.aiter_content():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
            except FetchError:
                raise
            except Exception as exc:
                raise FetchError("network", f"fetch failed: {type(exc).__name__}", retryable=True) from exc
            finally:
                await resp.aclose()
        charset = content_type.partition("charset=")[2].split(";")[0].strip(" \"'") or "utf-8"
        try:
            text = bytes(body[:MAX_BYTES]).decode(charset, errors="replace")
        except LookupError:
            text = bytes(body[:MAX_BYTES]).decode("utf-8", errors="replace")
        if content_type.startswith("text/plain"):
            text = f"<pre>{html.escape(text)}</pre>"
        return readable(text, url=url)
    raise FetchError("network", f"more than {MAX_REDIRECTS} redirects", retryable=False)
