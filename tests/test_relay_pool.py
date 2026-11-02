"""Proxies, relay and pool, offline: a fake HTTP proxy and a fake SOCKS5 proxy (both check the login and
tunnel to a local echo server) and stand-in fetchers. Covers proxy formats, the login rewrite, byte metering,
the persisted data limit, rotating one-shot identities, static proxies taken in turn, and exit checks.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import random
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from gsearch_core.fetch import FetchedPage, FetchError
from gsearch_core.geo import Place
from gsearch_core.relay import ByteMeter, Relay, Upstream, parse_proxies
from gsearch_parse.models import SearchRequest

pytestmark = pytest.mark.anyio

PASSWORD = "s3cret"


@dataclass
class FakeUpstream:
    port: int = 0
    socks_port: int = 0
    users: list[str] = field(default_factory=list)
    heads: list[bytes] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    echo_port: int = 0


async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError, OSError):
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    dst.close()


@pytest.fixture
async def upstream() -> Any:
    seen = FakeUpstream()

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError, OSError):  # the relay may cut a tunnel at the limit
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        writer.close()

    async def http_proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        seen.heads.append(head)
        auth = [ln for ln in head.split(b"\r\n") if ln.lower().startswith(b"proxy-authorization:")]
        user, _, password = (
            base64.b64decode(auth[0].split(b" ")[-1]).decode().partition(":") if auth else ("", "", "")
        )
        seen.users.append(user)
        if password != PASSWORD:
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        if head.startswith(b"CONNECT"):
            er, ew = await asyncio.open_connection("127.0.0.1", seen.echo_port)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(_pump(reader, ew), _pump(er, writer))
        else:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
            writer.close()

    async def socks_proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _, n = await reader.readexactly(2)
        methods = await reader.readexactly(n)
        assert 2 in methods
        writer.write(b"\x05\x02")
        _, ulen = await reader.readexactly(2)
        user = (await reader.readexactly(ulen)).decode()
        password = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        seen.users.append(user)
        ok = password == PASSWORD
        writer.write(b"\x01\x00" if ok else b"\x01\x01")
        await writer.drain()
        if not ok:
            writer.close()
            return
        *_, size = await reader.readexactly(5)
        host = (await reader.readexactly(size)).decode()
        port = int.from_bytes(await reader.readexactly(2), "big")
        seen.targets.append(f"{host}:{port}")
        er, ew = await asyncio.open_connection("127.0.0.1", seen.echo_port)
        writer.write(b"\x05\x00\x00\x01" + bytes(4) + b"\x00\x00")
        await writer.drain()
        await asyncio.gather(_pump(reader, ew), _pump(er, writer))

    servers = [
        await asyncio.start_server(echo, "127.0.0.1", 0),
        await asyncio.start_server(http_proxy, "127.0.0.1", 0),
        await asyncio.start_server(socks_proxy, "127.0.0.1", 0),
    ]
    seen.echo_port, seen.port, seen.socks_port = (s.sockets[0].getsockname()[1] for s in servers)
    yield seen
    for server in servers:
        server.close()


def account(port: int, password: str = PASSWORD) -> Upstream:
    return Upstream.parse(f"127.0.0.1:{port}:user-abc-network-res-country-us-sid-{{sid}}-ttl-30:{password}")


def relay_login(relay_url: str) -> bytes:
    parts = urlsplit(relay_url)
    return b"Proxy-Authorization: Basic " + base64.b64encode(f"{parts.username}:{parts.password}".encode())


async def tunnel(relay_url: str, payload: bytes, *, login: bool = True) -> tuple[bytes, bytes]:
    parts = urlsplit(relay_url)
    reader, writer = await asyncio.open_connection(parts.hostname, parts.port)
    auth = relay_login(relay_url) + b"\r\n" if login else b""
    writer.write(b"CONNECT www.google.com:443 HTTP/1.1\r\nHost: www.google.com:443\r\n" + auth + b"\r\n")
    await writer.drain()
    reply = await reader.readuntil(b"\r\n\r\n")
    echoed = b""
    if b" 200 " in reply:
        writer.write(payload)
        await writer.drain()
        try:
            echoed = await reader.readexactly(len(payload))
        except asyncio.IncompleteReadError as cut:  # the data limit also ends open tunnels
            echoed = cut.partial
    writer.close()
    return reply, echoed


# ------------------------------------------------------------------ proxies
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.2.3.4:8080", ("http", "1.2.3.4", 8080, "", "")),
        (
            "proxy.example:1337:user-u1:pw:with:colons",
            ("http", "proxy.example", 1337, "user-u1", "pw:with:colons"),
        ),
        ("user-u1:pw@proxy.example:1337", ("http", "proxy.example", 1337, "user-u1", "pw")),
        ("user-u1:pw:proxy.example:1337", ("http", "proxy.example", 1337, "user-u1", "pw")),
        ("user-u1:5555:proxy.example:1337", ("http", "proxy.example", 1337, "user-u1", "5555")),
        ("proxy.example:1337:user-u1:5555", ("http", "proxy.example", 1337, "user-u1", "5555")),
        ("localhost:1337:user-u1:5555", ("http", "localhost", 1337, "user-u1", "5555")),
        ("http://user-u1:p%40ss@proxy.example:8080", ("http", "proxy.example", 8080, "user-u1", "p@ss")),
        ("https://u:pw@secure.example:443", ("https", "secure.example", 443, "u", "pw")),
        ("socks5://u:pw@10.0.0.2:1080", ("socks5", "10.0.0.2", 1080, "u", "pw")),
        ("socks5h://10.0.0.2:1080", ("socks5", "10.0.0.2", 1080, "", "")),
    ],
)
def test_any_common_proxy_format(text: str, expected: tuple[object, ...]) -> None:
    p = Upstream.parse(text)
    assert (p.scheme, p.host, p.port, p.username, p.password) == expected
    assert not p.password or p.password not in p.redacted()


def test_unknown_formats_are_refused_without_echoing_the_password() -> None:
    with pytest.raises(ValueError, match="socks5") as err:
        Upstream.parse("ftp://u:topsecret@h:1")
    assert "topsecret" not in str(err.value)
    with pytest.raises(ValueError, match="unrecognised") as err:
        Upstream.parse("h:x:u:topsecret")
    assert "topsecret" not in str(err.value)


def test_rotating_logins_get_a_new_session_id_in_the_providers_own_shape() -> None:
    rng = random.Random(1)
    template = Upstream.parse("h:1:user-u1-country-us-sid-{sid}-ttl-30:pw")
    assert template.rotating and template.country == "US"
    a, b = template.with_session(rng), template.with_session(rng)
    assert re.fullmatch(r"user-u1-country-us-sid-\d{8}-ttl-30", a.username) and a.username != b.username
    oxy = Upstream.parse("customer-x-cc-DE-sessid-ab12cd:pw@h:1")
    assert oxy.rotating and oxy.country == "DE"
    assert re.fullmatch(r"customer-x-cc-DE-sessid-[a-z0-9]{6}", oxy.with_session(rng).username)
    in_password = Upstream.parse("h:1:user:pw_country-fr_session-Zx81kQ9a_lifetime-30m")
    assert in_password.rotating and in_password.country == "FR"
    assert in_password.with_session(rng).password != in_password.password
    static = Upstream.parse("user:pw@1.2.3.4:3128")
    assert not static.rotating and static.country is None
    assert static.key == Upstream.parse("1.2.3.4:3128:user:pw").key


def test_proxy_lists_and_files(tmp_path: Path) -> None:
    assert len(parse_proxies("1.1.1.1:80, 2.2.2.2:80\n3.3.3.3:80 4.4.4.4:80")) == 4
    listing = tmp_path / "proxies.txt"
    listing.write_text("# my VPS proxies\nsocks5://u:p@5.5.5.5:1080\nu:p@6.6.6.6:3128\n", encoding="utf-8")
    assert [p.host for p in parse_proxies(str(listing))] == ["5.5.5.5", "6.6.6.6"]
    with pytest.raises(ValueError, match="no proxy"):
        parse_proxies(" \n# nothing\n")


# ------------------------------------------------------------------- relay
async def test_relay_adds_login_meters_and_persists(upstream: FakeUpstream, tmp_path: Path) -> None:
    meter = ByteMeter(tmp_path / "bytes.sqlite", cap_bytes=None)
    relay = Relay(account(upstream.port).with_session(random.Random(2)), meter, tag="t")
    url = await relay.start()
    try:
        reply, echoed = await tunnel(url, b"x" * 5000)
        assert b" 200 " in reply and echoed == b"x" * 5000
        assert re.fullmatch(r"user-abc-network-res-country-us-sid-\d{8}-ttl-30", upstream.users[0])
        await asyncio.sleep(0.05)
        assert relay.bytes_up >= 5000 and relay.bytes_down >= 5000
        assert meter.total == relay.bytes_total
    finally:
        await relay.close()
    assert ByteMeter(tmp_path / "bytes.sqlite").total == meter.total  # survives a restart


def test_meters_in_two_processes_add_up(tmp_path: Path) -> None:
    first, second = ByteMeter(tmp_path / "bytes.sqlite"), ByteMeter(tmp_path / "bytes.sqlite")
    first.add(600)
    second.add(600)  # both started from 0: writing back a total read earlier would lose one of them
    first.save()
    second.save()
    assert ByteMeter(tmp_path / "bytes.sqlite").total == 1200
    assert second.total == 1200


async def test_relay_through_a_socks5_proxy(upstream: FakeUpstream) -> None:
    proxy = Upstream.parse(f"socks5://vps-user:{PASSWORD}@127.0.0.1:{upstream.socks_port}")
    relay = Relay(proxy, ByteMeter(), tag="t")
    url = await relay.start()
    try:
        reply, echoed = await tunnel(url, b"hello")
        assert reply.startswith(b"HTTP/1.1 200") and echoed == b"hello"
        assert upstream.users == ["vps-user"] and upstream.targets == ["www.google.com:443"]
        refused = Relay(
            Upstream.parse(f"socks5://vps-user:wrong@127.0.0.1:{upstream.socks_port}"), ByteMeter(), "t"
        )
        reply, _ = await tunnel(await refused.start(), b"a")
        assert reply.startswith(b"HTTP/1.1 502")
        await refused.close()
    finally:
        await relay.close()


async def test_relay_data_limit_refuses_new_connections(upstream: FakeUpstream, tmp_path: Path) -> None:
    meter = ByteMeter(tmp_path / "bytes.sqlite", cap_bytes=1000)
    relay = Relay(account(upstream.port).with_session(random.Random(3)), meter, tag="t")
    url = await relay.start()
    try:
        _, echoed = await tunnel(url, b"y" * 800)  # crosses the limit mid-tunnel
        assert echoed == b""  # the tunnel stopped at the limit, the reply was not relayed
        reply, _ = await tunnel(url, b"z")
        assert reply.startswith(b"HTTP/1.1 507")
        assert len(upstream.users) == 1  # the refused connection never reached the upstream
    finally:
        await relay.close()


async def test_relay_forwards_upstream_auth_failure(upstream: FakeUpstream) -> None:
    relay = Relay(account(upstream.port, password="wrong"), ByteMeter(), tag="t")
    url = await relay.start()
    try:
        reply, _ = await tunnel(url, b"a")
        assert reply.startswith(b"HTTP/1.1 407")
    finally:
        await relay.close()


async def test_relay_refuses_a_client_without_its_login(upstream: FakeUpstream) -> None:
    relay = Relay(account(upstream.port), ByteMeter(), tag="t")
    url = await relay.start()
    try:
        reply, echoed = await tunnel(url, b"x", login=False)
        assert reply.startswith(b"HTTP/1.1 407") and echoed == b""
        assert upstream.heads == []  # nothing reached the paid proxy
    finally:
        await relay.close()


async def test_relay_plain_http_gets_login_and_close(upstream: FakeUpstream) -> None:
    relay = Relay(account(upstream.port), ByteMeter(), tag="t")
    url = await relay.start()
    try:
        parts = urlsplit(url)
        reader, writer = await asyncio.open_connection(parts.hostname, parts.port)
        writer.write(
            b"GET http://example.test/ HTTP/1.1\r\nHost: example.test\r\nProxy-Connection: keep-alive\r\n"
            b"Connection: keep-alive\r\n" + relay_login(url) + b"\r\n\r\n"
        )
        await writer.drain()
        assert (await reader.read()).endswith(b"ok")
        head = upstream.heads[-1].lower()
        assert head.count(b"proxy-authorization:") == 1  # the upstream's login only, not the relay's
        assert relay_login(url).split(b" ")[-1].lower() not in head
        assert b"connection: close" in head and b"keep-alive" not in head
    finally:
        await relay.close()


# -------------------------------------------------------------------- pool
class FakeBrowser:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.rest_s = 0.0

    def ready_in_s(self) -> float:
        return self.rest_s

    def move_to(self, place: Place) -> None:
        self.config = replace(self.config, place=place)


class FakeHybrid:
    instances: list[FakeHybrid] = []  # noqa: RUF012 - test registry
    prewarm_s = 0.0

    def __init__(self, config: Any) -> None:
        self.browser = FakeBrowser(config)
        self.fetched: list[str] = []
        self.resolved: list[str] = []
        self.closed = False
        self.session = False
        self.prewarms: list[str] = []
        FakeHybrid.instances.append(self)

    @property
    def ready(self) -> bool:
        return self.session and self.browser.rest_s == 0

    async def open_session(self, query: str) -> None:
        self.prewarms.append(query)
        await asyncio.sleep(self.prewarm_s)
        if self.browser.config.place.country == "NL":
            raise FetchError("consent_wall", "Google served its consent dialog", retryable=True)
        self.session = True

    async def fetch(self, req: SearchRequest) -> FetchedPage:
        self.fetched.append(req.query)
        self.session = True
        if req.query.startswith("dead") and len(FakeHybrid.instances) == 1:
            raise FetchError("network", "exit did not answer", retryable=True)
        if req.query.startswith("eu") and len(FakeHybrid.instances) == 1:
            raise FetchError("consent_wall", "Google served its consent dialog (NL)", retryable=True)
        first_exit = self is FakeHybrid.instances[0]
        blocked = req.query.startswith("block") or (req.query.startswith("first") and first_exit)
        if blocked:
            self.browser.rest_s = 3600.0  # like the real fetcher: the identity rests after a block
        return FetchedPage(
            url="https://www.google.com/search",
            status=429 if blocked else 200,
            html="",
            engine="gecko",
            backend="hybrid-http",
            block_retry_after_s=3600 if blocked else None,
        )

    async def resolve_links(self, links: list[str]) -> dict[str, str | None]:
        self.resolved.extend(links)
        return {link: f"https://dest.example/{self.browser.config.identity}" for link in links}

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def pool_factory(upstream: FakeUpstream, tmp_path: Path) -> Any:
    from gsearch_core.pool import PoolConfig, ProxyPool

    FakeHybrid.instances = []
    FakeHybrid.prewarm_s = 0.0

    def make(
        proxies: tuple[Upstream, ...] | None = None, countries: list[str] | None = None, **kw: Any
    ) -> ProxyPool:
        queue = list(countries or [])

        async def locate(relay_url: str) -> Place:
            country = queue.pop(0) if queue else "US"
            if country == "dead":
                raise TimeoutError("lookup timed out")
            return Place(ip="203.0.113.7", country=country, timezone="America/Chicago", locale="en-US")

        cfg = PoolConfig(
            proxies=proxies or (account(upstream.port),), meter_path=tmp_path / "bytes.sqlite", **kw
        )
        return ProxyPool(cfg, make_fetcher=FakeHybrid, locate=locate)  # type: ignore[arg-type]

    return make


async def settle(pool: Any) -> None:
    while pool._tasks:
        await asyncio.gather(*pool._tasks)


async def test_rotating_one_shot_new_exit_per_search_closed_after_its_links(pool_factory: Any) -> None:
    pool = pool_factory()
    try:
        assert pool.rotating
        for q in ("a", "b"):
            await pool.fetch(SearchRequest(query=q))
            got = await pool.resolve_links([f"https://www.google.com/goto?url={q}"])
            serving = FakeHybrid.instances[-1]
            assert serving.resolved == [f"https://www.google.com/goto?url={q}"]  # links read on the same exit
            assert next(iter(got.values())) == f"https://dest.example/{serving.browser.config.identity}"
            assert pool._members == {}  # closed right after its own links, not at the next search
        assert [f.fetched for f in FakeHybrid.instances] == [["a"], ["b"]]
        assert all(f.closed for f in FakeHybrid.instances)
        assert len({f.browser.config.identity for f in FakeHybrid.instances}) == 2
    finally:
        await pool.aclose()


async def test_rotating_exit_without_links_is_closed_before_the_next_search(pool_factory: Any) -> None:
    pool = pool_factory()
    try:
        await pool.fetch(SearchRequest(query="no results"))  # no resolve_links call follows
        (member,) = pool._members.values()
        member.leased_until = 0  # its lease ran out
        await pool.fetch(SearchRequest(query="next"))
        assert FakeHybrid.instances[0].closed and FakeHybrid.instances[0].fetched == ["no results"]
    finally:
        await pool.aclose()


async def test_a_blocked_search_is_retried_on_fresh_exits(pool_factory: Any) -> None:
    pool = pool_factory()
    try:
        page = await pool.fetch(SearchRequest(query="block every exit"))
        assert page.block_retry_after_s == 3600 and len(FakeHybrid.instances) == 4  # max_attempts
        assert pool.stats["blocked"] == 4 and pool.stats["retries"] == 3
        assert all(f.closed for f in FakeHybrid.instances)
    finally:
        await pool.aclose()


async def test_static_proxies_are_used_in_turn_with_warm_sessions(pool_factory: Any) -> None:
    proxies = parse_proxies("u:p@10.0.0.1:3128, u:p@10.0.0.2:3128")
    pool = pool_factory(proxies=proxies)
    try:
        assert not pool.rotating
        for q in ("a", "b", "c", "d"):
            await pool.fetch(SearchRequest(query=q))
            await pool.resolve_links([f"https://www.google.com/goto?url={q}"])
            await settle(pool)
        assert [f.fetched for f in FakeHybrid.instances] == [["a", "c"], ["b", "d"]]  # two warm sessions
        assert all(len(f.prewarms) == 1 for f in FakeHybrid.instances)  # each readied in the background
        assert not any(f.closed for f in FakeHybrid.instances)
    finally:
        await pool.aclose()


async def test_a_static_proxy_that_gets_a_captcha_rests_and_the_next_one_serves(pool_factory: Any) -> None:
    proxies = parse_proxies("u:p@10.0.0.1:3128 u:p@10.0.0.2:3128")
    pool = pool_factory(proxies=proxies)
    try:
        page = await pool.fetch(SearchRequest(query="first search"))  # 10.0.0.1 blocked, 10.0.0.2 serves
        assert page.block_retry_after_s is None and pool.stats["blocked"] == 1
        await pool.fetch(SearchRequest(query="again"))
        assert FakeHybrid.instances[0].fetched == ["first search"]  # still resting
        assert FakeHybrid.instances[1].fetched == ["first search", "again"]
        FakeHybrid.instances[1].browser.rest_s = 120.0
        with pytest.raises(FetchError) as err:
            await pool.fetch(SearchRequest(query="none left"))
        assert err.value.code == "rate_limited" and err.value.retry_after_s == 120
    finally:
        await pool.aclose()


async def test_a_search_does_not_wait_long_for_a_session_being_readied(
    pool_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gsearch_core import pool as pool_module

    monkeypatch.setattr(pool_module, "WARM_WAIT_S", 0.05)
    FakeHybrid.prewarm_s = 0.2  # a new device warming up
    pool = pool_factory(proxies=parse_proxies("u:p@10.0.0.1:3128 u:p@10.0.0.2:3128"))
    try:
        pool.prewarm()  # what the MCP server does at start
        with pytest.raises(FetchError) as err:
            await pool.fetch(SearchRequest(query="too early"))
        assert err.value.code == "rate_limited" and err.value.retry_after_s == 60
        await settle(pool)
        await pool.fetch(SearchRequest(query="ready now"))
        assert FakeHybrid.instances[0].fetched == ["ready now"]
    finally:
        await pool.aclose()


async def test_a_block_hands_over_to_the_spare_exit(pool_factory: Any) -> None:
    pool = pool_factory(proxies=parse_proxies("u:p@10.0.0.1:3128 u:p@10.0.0.2:3128"))
    try:
        pool.prewarm()
        await settle(pool)
        pool.prewarm()
        await settle(pool)
        assert pool._ready() == {p.key for p in pool.config.proxies}  # two exits kept ready
        page = await pool.fetch(SearchRequest(query="first search"))  # the first exit gets a CAPTCHA
        assert page.block_retry_after_s is None and len(FakeHybrid.instances) == 2
        assert [f.fetched for f in FakeHybrid.instances] == [["first search"]] * 2
        assert all(len(f.prewarms) == 1 for f in FakeHybrid.instances)  # the spare needed no new session
    finally:
        await pool.aclose()


async def test_a_search_does_not_queue_behind_a_spare_being_warmed_up(pool_factory: Any) -> None:
    pool = pool_factory(proxies=parse_proxies("u:p@10.0.0.1:3128 u:p@10.0.0.2:3128"))
    try:
        pool.prewarm()
        await settle(pool)
        FakeHybrid.prewarm_s = 0.5  # the spare's warm-up, started after the next search
        await pool.fetch(SearchRequest(query="a"))
        await asyncio.sleep(0.05)
        serving, warming = FakeHybrid.instances
        pool._members[next(iter(pool._ready()))].busy = 1  # another search is running on the ready exit
        await pool.fetch(SearchRequest(query="b"))
        assert serving.fetched == ["a", "b"] and warming.fetched == []
    finally:
        await pool.aclose()


async def test_an_exit_that_cannot_be_readied_is_left_alone_for_a_while(pool_factory: Any) -> None:
    pool = pool_factory(proxies=parse_proxies("u:p@10.0.0.1:3128 u:p@10.0.0.2:3128"), countries=["NL", "US"])
    try:
        for q in ("a", "b", "c"):
            await pool.fetch(SearchRequest(query=q))
            await settle(pool)
        failing, serving = FakeHybrid.instances
        assert len(failing.prewarms) == 1 and serving.fetched == ["a", "b", "c"]
    finally:
        await pool.aclose()


async def test_a_static_exit_that_changes_ip_is_looked_up_again(pool_factory: Any) -> None:
    pool = pool_factory(proxies=parse_proxies("u:p@10.0.0.1:3128"), countries=["US", "US"])
    try:
        await pool.fetch(SearchRequest(query="a"))
        (member,) = pool._members.values()
        member.located_at = float("-inf")  # last looked up long ago (0.0 is recent on a just-booted CI VM)
        pool._locate = _locate_at("198.51.100.4")
        await pool.fetch(SearchRequest(query="b"))
        await settle(pool)
        assert member.fetcher.browser.config.place.ip == "198.51.100.4"
    finally:
        await pool.aclose()


def _locate_at(ip: str, country: str = "US") -> Any:
    async def locate(relay_url: str) -> Place:
        return Place(ip=ip, country=country, timezone="America/Chicago", locale="en-US")

    return locate


async def test_a_static_exit_that_moves_to_another_country_is_dropped(pool_factory: Any) -> None:
    proxy = parse_proxies("u:p@10.0.0.1:3128")
    pool = pool_factory(proxies=proxy, countries=["GB"])
    try:
        await pool.fetch(SearchRequest(query="a"))
        (member,) = pool._members.values()
        member.located_at = float("-inf")
        pool._locate = _locate_at("189.218.3.209", "MX")  # seen today: a London port moved to Mexico
        await pool.fetch(SearchRequest(query="b"))
        await settle(pool)
        assert pool._members == {} and FakeHybrid.instances[0].closed
        pool._dead_until[proxy[0].key] = 0.0
        with pytest.raises(FetchError):  # still in Mexico: refused, not a new device there
            await pool.fetch(SearchRequest(query="c"))
        assert len(FakeHybrid.instances) == 1
    finally:
        await pool.aclose()


async def test_a_dead_static_proxy_is_not_tried_again_on_every_search(pool_factory: Any) -> None:
    proxies = parse_proxies("u:p@10.0.0.1:3128 u:p@10.0.0.2:3128")
    pool = pool_factory(proxies=proxies, countries=["dead"])
    try:
        for q in ("a", "b", "c"):
            await pool.fetch(SearchRequest(query=q))
        (serving,) = FakeHybrid.instances
        assert serving.fetched == ["a", "b", "c"]
    finally:
        await pool.aclose()


async def test_a_block_on_the_only_static_proxy_is_reported_as_the_block(pool_factory: Any) -> None:
    pool = pool_factory(proxies=parse_proxies("u:p@10.0.0.1:3128"))
    try:
        page = await pool.fetch(SearchRequest(query="block me"))
        assert page.block_retry_after_s == 3600 and page.status == 429
    finally:
        await pool.aclose()


async def test_a_resting_static_proxy_is_looked_at_again_for_a_new_ip(pool_factory: Any) -> None:
    proxy = parse_proxies("u:p@10.0.0.1:3128")
    pool = pool_factory(proxies=proxy)
    try:
        await pool.fetch(SearchRequest(query="block me"))
        with pytest.raises(FetchError) as err:
            await pool.fetch(SearchRequest(query="too soon"))
        assert err.value.retry_after_s == 600 and pool._members == {}
        pool._dead_until[proxy[0].key] = 0.0
        await pool.fetch(SearchRequest(query="new ip"))
        assert FakeHybrid.instances[-1].fetched == ["new ip"]
    finally:
        await pool.aclose()


async def test_pool_refuses_when_data_limit_used(pool_factory: Any, tmp_path: Path) -> None:
    (tmp_path / "bytes.json").write_text(json.dumps({"total": 2_000_000}), encoding="utf-8")  # an older meter
    pool = pool_factory(byte_cap_bytes=1_000_000)
    try:
        with pytest.raises(FetchError) as err:
            await pool.fetch(SearchRequest(query="x"))
        assert err.value.code == "proxy" and not err.value.retryable
        assert FakeHybrid.instances == []
    finally:
        await pool.aclose()


async def test_pages_go_through_the_proxy_and_count_against_the_limit(
    pool_factory: Any, upstream: FakeUpstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gsearch_core import page

    async def public(host: str) -> str:
        return "93.184.215.14"

    from gsearch_core import pool as pool_module

    monkeypatch.setattr(page, "public_address", public)
    monkeypatch.setattr(pool_module, "PAGE_RELAY_GRACE_S", 0.0)
    pool = pool_factory(byte_cap_bytes=1_000_000)
    try:
        relay_url = await pool.page_proxy()
        assert await pool.page_proxy() == relay_url
        content = await page.fetch_page("http://site.example/", proxy=relay_url)
        assert content.text == "ok"
        assert upstream.heads[-1].startswith(b"GET http://site.example/")
        assert re.fullmatch(r"user-abc-network-res-country-us-sid-\d{8}-ttl-30", upstream.users[-1])
        assert pool.meter.total > 0
        await page.fetch_page("http://www.google.com/goto?url=x", proxy=relay_url, google_only=True)
        assert upstream.heads[-1].startswith(b"GET http://www.google.com/goto")  # Google's hop: proxied
        await pool.drop_page_proxy(relay_url)
        assert await pool.page_proxy() != relay_url  # a failed page exit is replaced
        await settle(pool)
        old = urlsplit(relay_url)
        with pytest.raises(ConnectionRefusedError):  # and the old relay is closed, not kept until shutdown
            await asyncio.open_connection(old.hostname, old.port)
        pool.meter.add(2_000_000)
        with pytest.raises(FetchError) as err:
            await pool.page_proxy()
        assert err.value.code == "proxy"
    finally:
        await pool.aclose()


async def test_pool_replaces_a_dead_exit(pool_factory: Any) -> None:
    pool = pool_factory()
    try:
        page = await pool.fetch(SearchRequest(query="dead exit"))
        assert page.block_retry_after_s is None and len(FakeHybrid.instances) == 2
        assert pool.stats["exit_failures"] == 1 and FakeHybrid.instances[0].closed
    finally:
        await pool.aclose()


async def test_pool_drops_dead_and_wrong_country_exits_before_any_browser_work(pool_factory: Any) -> None:
    pool = pool_factory(countries=["NL", "dead", "US"])
    try:
        await pool.fetch(SearchRequest(query="one"))
        assert len(FakeHybrid.instances) == 1  # only the US exit got a browser
        assert FakeHybrid.instances[0].browser.config.place.country == "US"
        assert pool.stats["exits_wrong_country"] == 1 and pool.stats["exits_dead"] == 1
    finally:
        await pool.aclose()


async def test_pool_gives_up_after_bounded_draws(pool_factory: Any) -> None:
    pool = pool_factory(countries=["NL"] * 10)
    try:
        with pytest.raises(FetchError) as err:
            await pool.fetch(SearchRequest(query="one"))
        assert err.value.code == "proxy" and "no usable exit" in str(err.value)
        assert FakeHybrid.instances == []
    finally:
        await pool.aclose()


async def test_pool_treats_a_consent_wall_as_a_wrong_country_exit(pool_factory: Any) -> None:
    pool = pool_factory()
    try:
        page = await pool.fetch(SearchRequest(query="eu exit"))
        assert page.block_retry_after_s is None and len(FakeHybrid.instances) == 2
        assert pool.stats["exits_wrong_country"] == 1 and FakeHybrid.instances[0].closed
    finally:
        await pool.aclose()


def test_consent_country_reads_googles_own_country_guess() -> None:
    from gsearch_core.camoufox_backend import consent_country

    # The three encodings seen on a live Dutch-exit homepage (2026-10-17).
    js = (
        r"'https://consent.google.com/save?continue\x3dhttps://www.google.com/?hl%3Den\x26gl\x3dNL\x26m\x3d0'"
    )
    assert consent_country(js) == "NL"
    link = (
        '<a href="https://consent.google.com/d?continue=https://www.google.com/?hl%3Den&amp;gl=DE&amp;m=0">'
    )
    assert consent_country(link) == "DE"
    assert consent_country("consent.google.com/d%3Fcontinue%3Dhttps://www.google.com/%26gl%3DFR%26m") == "FR"
    assert consent_country('<a href="https://policies.google.com/privacy?gl=US">Privacy</a>') is None


# ------------------------------------------------------------------ config
def test_settings_from_dotenv(tmp_path: Path) -> None:
    from gsearch_core.config import fetcher_from_env, load_env_file
    from gsearch_core.pool import ProxyPool

    (tmp_path / ".env").write_text(
        '# comment\nGSEARCH_PROXY="h:1:user-u-sid-{sid}-ttl-30:pw"\nGSEARCH_DATA_LIMIT_MB=700\n',
        encoding="utf-8",
    )
    env = load_env_file(
        tmp_path / ".env", {"GSEARCH_DATA_LIMIT_MB": "50", "GSEARCH_STATE_DIR": str(tmp_path)}
    )
    fetcher, live = fetcher_from_env(env)
    assert isinstance(fetcher, ProxyPool) and live and fetcher.rotating
    assert fetcher.config.byte_cap_bytes == 50_000_000  # an exported variable beats the file
    assert fetcher.status()["proxy"] == "http://user-u-sid-<sid>-ttl-30:***@h:1"
    browser = fetcher.config.browser
    assert browser.headless is False and browser.entry == "address_bar" and browser.fingerprint_preset


def test_env_file_can_be_named_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gsearch_core.config import default_env

    monkeypatch.delenv("GSEARCH_DATA_LIMIT_MB", raising=False)
    (tmp_path / "gsearch.env").write_text("GSEARCH_DATA_LIMIT_MB=4\n", encoding="utf-8")
    monkeypatch.setenv("GSEARCH_ENV_FILE", str(tmp_path / "gsearch.env"))
    assert default_env()["GSEARCH_DATA_LIMIT_MB"] == "4"
    monkeypatch.setenv("GSEARCH_ENV_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(ValueError, match="does not exist"):  # never fall back silently to the home IP
        default_env()
