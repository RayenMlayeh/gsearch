# F-0013 TLS ClientHello: Camoufox vs curl_cffi Firefox profiles (offline)

- **Date:** 2026-10-17.
- **Versions:** Camoufox 152.0.4-beta.30; `curl_cffi` 0.16.3 (MIT), targets `firefox135/144/147`.
- **Script:** a one-off probe script (not kept in the repo). A local TCP listener records the first TLS record each client sends to `https://localhost:<port>/`; the handshake then fails on purpose, and nothing leaves the machine.
- **Raw data:** `captures/2026-10-17/tls/hellos.json`.
- **Label:** OBSERVED-run (local harness).

## Result

| Field | Camoufox run 1 vs run 2 | `firefox147` stock | `firefox147` + cipher list below |
|---|---|---|---|
| cipher suites (order) | same | **differs**: adds `0xc009` (`ECDHE-ECDSA-AES128-SHA`), which Firefox 152 no longer offers | **same** |
| extension order `[0,23,65281,10,11,35,16,5,34,18,51,43,13,45,28,27,65037]` | same | same | same |
| supported groups `[4588,29,23,24,25,256,257]` (incl. ML-KEM hybrid 4588) | same | same | same |
| signature algorithms, ALPN, key shares, versions, GREASE | same | same | same |

Camoufox's extension order is stable across launches (unlike Chrome, Firefox does not shuffle extensions).

Cipher list that reproduces Camoufox exactly, set via `CurlOpt.SSL_CIPHER_LIST` (TLS 1.3 names included; setting `TLS13_CIPHERS` separately produced no ClientHello at all):

`TLS_AES_128_GCM_SHA256:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_256_GCM_SHA384:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA:AES256-SHA`

## Not compared (still UNKNOWN)

- **Extension *contents*:** padding length, ECH GREASE payload size, session-ticket behaviour, `record_size_limit`, `delegated_credentials`, `compress_certificate` values.
- **HTTP/2 framing:** SETTINGS, WINDOW_UPDATE, PRIORITY/pseudo-header order (the "Akamai" fingerprint), and HTTP/3. The browser used h3 to Google (live HARs); `curl_cffi` uses h2.
- How Google scores any of this server-side (rea's limit; only real runs answer it, e.g. experiment E-H1).
