# F-0007 How Camoufox's `geoip=True` derives location from a proxy

- **Date:** 2026-10-16.
- **Source:** the installed package `camoufox` 0.5.6 (`site-packages/camoufox/ip.py`, `geolocation.py`, `utils.py`, `repos.yml`). This is **OBSERVED-source**: the code was read, not executed. rea is not needed for plain source reading; `gsearch identity check` checks the result at runtime.

## What the code does

1. `utils.py:910-926`: if `geoip is True` and a proxy is set, it calls `public_ip(proxy)`. Otherwise it calls `public_ip()` without a proxy. The returned IP is also written to `webrtc:ipv4` / `webrtc:ipv6`.
2. `ip.py:79-108`: `public_ip` uses Python **`requests`**, *not the browser*, to fetch the IP through the proxy. It tries `api.ipify.org`, `checkip.amazonaws.com`, `ipinfo.io/ip`, `icanhazip.com`, `ifconfig.co/ip`, `ipecho.net/plain` in order, each with a 5 s timeout.
3. `geolocation.py` + `repos.yml`: the IP is looked up in a local MaxMind-format **GeoLite2-City** `.mmdb`, downloaded from `cdn.jsdelivr.net/npm/@ip-location-db/geolite2-city-mmdb` (fallback: raw GitHub `sapics/ip-location-db`). That gives `timezone`, `locale:*` and geolocation, set unless already configured.
4. `utils.py:936-943`: a proxy without geoip triggers `LeakWarning('proxy_without_geoip')`, which "cannot be ignored".
5. `geoip_allowed()` requires the `geoip2` module. It is **not installed** in the Python used then, while the `.mmdb` files are already cached (`%LOCALAPPDATA%\camoufox\camoufox\Cache\geoip\mmdb`, ipv4 28 MB + ipv6 17 MB, dated 2026-09-25).

## Consequences

- **Rotating per-request proxies:** the IP that `requests` sees (step 2) and the IP Google sees can differ. The fingerprint's timezone/locale/WebRTC IP may then contradict Google's view of the request (ASSUMED risk; experiment E-R). Mitigations:
  - sticky sessions, or
  - a rotating gateway with country targeting in the username, so the *country* stays constant.

  Our relay (D-003) also lets us re-check the exit IP through the same upstream session.
- **SOCKS5 with auth:** Firefox/Juggler cannot authenticate SOCKS5 itself, so the browser talks to our local relay (D-003). `public_ip` must then go through the relay too, so both see the same upstream session.
- **Licence:** GeoLite2 data is CC BY-SA 4.0 (MaxMind). If we ever ship or redistribute the database, attribution is required. We don't redistribute it; Camoufox downloads it.
