"""Proxies: parsing any common format, and the local relay every identity's traffic goes through.

The browser and the HTTP client use 127.0.0.1 without credentials; the relay speaks to the real proxy
(HTTP, HTTPS or SOCKS5, with or without a login), meters every byte and enforces a hard data limit.
- One exit per identity for both tiers: browser and curl_cffi go through the same listener, so the same
  proxy session, so the same exit IP.
- Metering: residential proxies bill per GB in both directions, so the bytes counted here are the bytes
  on the bill (headers and TLS records included; the provider may round differently).
- The limit is persisted, so it holds across runs.
- Credentials stay out of the browser profile and out of logs (only `redacted()` is ever logged).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import random
import re
import string
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

log = logging.getLogger(__name__)

# A sticky-session id in a rotating proxy's login: `{sid}`, or the provider's own `sid-123`,
# `session-abc`, `sessid-abc` (dash or underscore). A new id means a new exit IP.
_SESSION = re.compile(r"(?<![a-z])(sessid|session|sid)([-_])([A-Za-z0-9]+)", re.IGNORECASE)
_COUNTRY = re.compile(r"(?:^|[-_])(?:country|cc)[-_]([a-zA-Z]{2})(?=[-_]|$)", re.IGNORECASE)
_SCHEMES = {"http": "http", "https": "https", "socks5": "socks5", "socks5h": "socks5"}
_HEAD_LIMIT = 64 * 1024
_CHUNK = 64 * 1024
_HOP_HEADERS = (b"proxy-authorization:", b"proxy-connection:")


@dataclass(frozen=True, slots=True)
class Upstream:
    scheme: str  # http | https | socks5
    host: str
    port: int
    username: str = ""
    password: str = ""

    @classmethod
    def parse(cls, text: str) -> Upstream:
        """One proxy in any common format: `host:port`, `host:port:user:pass`, `user:pass:host:port`,
        `user:pass@host:port`, each optionally prefixed by `http://`, `https://`, `socks5://` or `socks5h://`."""
        rest = text.strip()
        scheme = "http"
        if "://" in rest:
            name, rest = rest.split("://", 1)
            if name.lower() not in _SCHEMES:
                raise ValueError(f"unsupported proxy type {name!r} (http, https, socks5)")
            scheme = _SCHEMES[name.lower()]
        if "@" in rest:
            creds, _, address = rest.rpartition("@")
            user, _, password = creds.partition(":")
            host, _, port = address.rpartition(":")
            if host and port.isdigit():
                return cls(scheme, host, int(port), unquote(user), unquote(password))
        else:
            parts = rest.split(":")
            if len(parts) == 2 and parts[1].isdigit():
                return cls(scheme, parts[0], int(parts[1]))
            if len(parts) >= 4 and parts[1].isdigit():
                return cls(scheme, parts[0], int(parts[1]), parts[2], ":".join(parts[3:]))
            if len(parts) >= 4 and parts[-1].isdigit():
                return cls(scheme, parts[-2], int(parts[-1]), parts[0], ":".join(parts[1:-2]))
        raise ValueError(  # never echo the input: it may hold a password
            "unrecognised proxy format: use host:port, host:port:user:pass, user:pass@host:port "
            "or a URL such as socks5://user:pass@host:port"
        )

    @property
    def rotating(self) -> bool:
        """True when the login carries a session id, so a new id gives a new exit IP."""
        return any("{sid}" in s or _SESSION.search(s) for s in (self.username, self.password))

    @property
    def country(self) -> str | None:
        """ISO country the login asks for (`...-country-us-...` -> "US"), if any."""
        for part in (self.username, self.password):
            if match := _COUNTRY.search(part):
                return match.group(1).upper()
        return None

    @property
    def key(self) -> str:
        """Short, stable, non-secret name for this exact proxy login."""
        raw = f"{self.scheme}:{self.host}:{self.port}:{self.username}:{self.password}"
        return hashlib.blake2b(raw.encode(), digest_size=5).hexdigest()

    def with_session(self, rng: random.Random) -> Upstream:
        """The same account on a new random session id (same length and alphabet as the provider's)."""

        def fresh(match: re.Match[str]) -> str:
            old = match.group(3)
            alphabet = string.digits if old.isdigit() else string.ascii_lowercase + string.digits
            return match.group(1) + match.group(2) + "".join(rng.choice(alphabet) for _ in old)

        def renew(part: str) -> str:
            if "{sid}" in part:
                return part.replace("{sid}", "".join(rng.choice(string.digits) for _ in range(8)))
            return _SESSION.sub(fresh, part, count=1)

        return replace(self, username=renew(self.username), password=renew(self.password))

    def redacted(self) -> str:
        login = f"{self.username}:***@" if self.username or self.password else ""
        return f"{self.scheme}://{login}{self.host}:{self.port}"


def parse_proxies(text: str) -> tuple[Upstream, ...]:
    """Proxies separated by newlines, commas or spaces, or the path of a file listing them (one per line,
    `#` comments allowed)."""
    source = text.strip()
    with contextlib.suppress(OSError, ValueError):
        if "\n" not in source and Path(source).is_file():
            source = Path(source).read_text(encoding="utf-8")
    items = [
        item
        for line in source.splitlines()
        if not line.strip().startswith("#")
        for item in re.split(r"[,\s]+", line.strip())
        if item
    ]
    if not items:
        raise ValueError("GSEARCH_PROXY is set but lists no proxy")
    return tuple(Upstream.parse(item) for item in items)


class ByteMeter:
    """Bytes through every relay of a process, persisted so a hard limit holds across runs."""

    def __init__(self, path: Path | None = None, cap_bytes: int | None = None) -> None:
        self.path = path
        self.cap_bytes = cap_bytes
        self.total = 0
        self._saved_at = 0.0
        if path is not None and path.exists():
            self.total = int(json.loads(path.read_text(encoding="utf-8")).get("total", 0))

    @property
    def exceeded(self) -> bool:
        return self.cap_bytes is not None and self.total >= self.cap_bytes

    def add(self, n: int) -> None:
        self.total += n
        if time.monotonic() - self._saved_at > 1.0:
            self.save()

    def save(self) -> None:
        if self.path is None:
            return
        self._saved_at = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"total": self.total}), encoding="utf-8")
        tmp.replace(self.path)


class Relay:
    """One local HTTP proxy listener in front of one upstream proxy session."""

    def __init__(self, upstream: Upstream, meter: ByteMeter, tag: str, *, timeout_s: float = 20.0) -> None:
        self.upstream = upstream
        self.meter = meter
        self.tag = tag
        self.timeout_s = timeout_s
        self.bytes_up = 0
        self.bytes_down = 0
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def url(self) -> str:
        assert self._server is not None, "relay not started"
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    @property
    def bytes_total(self) -> int:
        return self.bytes_up + self.bytes_down

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._on_client, "127.0.0.1", 0)
        return self.url

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):  # cancelled tunnels end with CancelledError
                await task
        self.meter.save()

    # ---------------------------------------------------------------- tunnel
    def _count(self, n: int, *, up: bool) -> None:
        if up:
            self.bytes_up += n
        else:
            self.bytes_down += n
        self.meter.add(n)

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            await self._serve(reader, writer)
        except (ConnectionError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass  # a client or upstream hung up or stalled (TimeoutError is an OSError): normal for browsers
        finally:
            self._tasks.discard(task)
            with contextlib.suppress(Exception):
                writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.timeout_s)
        if len(head) > _HEAD_LIMIT:
            return
        if self.meter.exceeded:
            log.warning("relay_refused_data_limit tag=%s total=%s", self.tag, self.meter.total)
            writer.write(b"HTTP/1.1 507 Data limit reached\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        up = self.upstream
        is_connect = head[:8].upper() == b"CONNECT "
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(up.host, up.port, ssl=True if up.scheme == "https" else None),
                self.timeout_s,
            )
        except OSError:
            log.warning("relay_upstream_unreachable tag=%s", self.tag)
            writer.write(b"HTTP/1.1 502 Upstream proxy unreachable\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        try:
            if up.scheme == "socks5":
                if not await self._socks5_open(head, is_connect, up_reader, up_writer, writer):
                    return
            elif not await self._http_open(head, is_connect, up_reader, up_writer, writer):
                return
            await asyncio.gather(
                self._pipe(reader, up_writer, up=True), self._pipe(up_reader, writer, up=False)
            )
        finally:
            with contextlib.suppress(Exception):
                up_writer.close()

    async def _http_open(
        self,
        head: bytes,
        is_connect: bool,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
        writer: asyncio.StreamWriter,
    ) -> bool:
        """Forward the request to an HTTP(S) proxy with its login added. False if the proxy refused."""
        auth = None
        if self.upstream.username or self.upstream.password:
            token = base64.b64encode(f"{self.upstream.username}:{self.upstream.password}".encode()).decode()
            auth = f"Proxy-Authorization: Basic {token}".encode()
        out = _rewrite(head, auth=auth, origin_form=False, is_connect=is_connect)
        up_writer.write(out)
        self._count(len(out), up=True)
        if not is_connect:
            return True
        reply = await asyncio.wait_for(up_reader.readuntil(b"\r\n\r\n"), self.timeout_s)
        self._count(len(reply), up=False)
        writer.write(reply)
        await writer.drain()
        status = reply.split(b" ", 2)[1] if reply.count(b" ") >= 1 else b"?"
        if status != b"200":
            log.warning("relay_upstream_refused tag=%s status=%s", self.tag, status.decode(errors="replace"))
            return False
        return True

    async def _socks5_open(
        self,
        head: bytes,
        is_connect: bool,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
        writer: asyncio.StreamWriter,
    ) -> bool:
        """Open the target through a SOCKS5 proxy (RFC 1928/1929; the proxy resolves the host name, so no
        DNS query leaves this machine). False if the proxy refused."""
        target = head.split(b" ", 2)[1].decode("latin-1")
        if is_connect:
            host, _, port = target.rpartition(":")
        else:
            parts = urlsplit(target)
            host, port = parts.hostname or "", str(parts.port or 80)
        try:
            await asyncio.wait_for(
                self._socks5_handshake(up_reader, up_writer, host, int(port)), self.timeout_s
            )
        except (ConnectionError, OSError, asyncio.IncompleteReadError, ValueError) as exc:
            log.warning("relay_upstream_refused tag=%s socks5: %s", self.tag, exc)
            writer.write(b"HTTP/1.1 502 Upstream proxy refused\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return False
        if is_connect:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
        else:
            out = _rewrite(head, auth=None, origin_form=True, is_connect=False)
            up_writer.write(out)
            self._count(len(out), up=True)
        return True

    async def _socks5_handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int
    ) -> None:
        up = self.upstream

        async def send(data: bytes) -> None:
            writer.write(data)
            await writer.drain()
            self._count(len(data), up=True)

        async def recv(n: int) -> bytes:
            data = await reader.readexactly(n)
            self._count(n, up=False)
            return data

        login = bool(up.username or up.password)
        await send(b"\x05\x02\x00\x02" if login else b"\x05\x01\x00")
        method = (await recv(2))[1]
        if method == 2:
            user, password = up.username.encode(), up.password.encode()
            await send(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
            if (await recv(2))[1] != 0:
                raise ConnectionError("login refused")
        elif method != 0:
            raise ConnectionError("no acceptable authentication method")
        name = host.encode("idna")
        await send(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + port.to_bytes(2, "big"))
        reply = await recv(4)
        if reply[1] != 0:
            raise ConnectionError(f"connect refused (code {reply[1]})")
        size = {1: 4, 4: 16}.get(reply[3]) or (await recv(1))[0]
        await recv(size + 2)  # bound address and port

    async def _pipe(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter, *, up: bool) -> None:
        try:
            while not self.meter.exceeded:
                chunk = await src.read(_CHUNK)
                if not chunk:
                    break
                self._count(len(chunk), up=up)
                dst.write(chunk)
                await dst.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                dst.close()


def _rewrite(head: bytes, *, auth: bytes | None, origin_form: bool, is_connect: bool) -> bytes:
    """The client's request head without proxy headers, with the upstream login if any. `origin_form`
    turns `GET http://host/path` into `GET /path` for a direct (SOCKS) connection."""
    lines = head[:-4].split(b"\r\n")
    first = lines[0]
    if origin_form:
        method, target, version = first.split(b" ", 2)
        parts = urlsplit(target.decode("latin-1"))
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        first = b" ".join([method, path.encode("latin-1"), version])
    kept = [first]
    for line in lines[1:]:
        low = line.lower()
        if low.startswith(_HOP_HEADERS) or (not is_connect and low.startswith(b"connection:")):
            continue
        kept.append(line)
    if auth:
        kept.append(auth)
    if not is_connect:
        kept.append(b"Connection: close")  # later requests on a kept-alive socket would skip the rewrite
    return b"\r\n".join(kept) + b"\r\n\r\n"
