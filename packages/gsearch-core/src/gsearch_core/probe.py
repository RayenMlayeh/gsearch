"""Identity probe: what an identity's browser exposes to page JavaScript, checked against its exit IP.

Launches the exact device the backend launches (`device_options`) against a local probe page. No request
leaves the machine, except the exit-IP lookup when no place is supplied.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from camoufox.async_api import AsyncCamoufox

from .camoufox_backend import CamoufoxConfig, device_options
from .geo import Place, locate_exit

PROBE_JS = """
async () => {
  const out = {};
  const nav = navigator;
  out.userAgent = nav.userAgent;
  out.platform = nav.platform;
  out.language = nav.language;
  out.languages = Array.from(nav.languages || []);
  out.webdriver = nav.webdriver;
  out.hardwareConcurrency = nav.hardwareConcurrency;
  out.maxTouchPoints = nav.maxTouchPoints;
  out.plugins = (nav.plugins || []).length;
  out.mimeTypes = (nav.mimeTypes || []).length;
  out.pdfViewerEnabled = nav.pdfViewerEnabled;
  out.cookieEnabled = nav.cookieEnabled;
  out.doNotTrack = nav.doNotTrack;
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.intlLocale = Intl.DateTimeFormat().resolvedOptions().locale;
  out.timezoneOffset = new Date().getTimezoneOffset();
  out.screen = {w: screen.width, h: screen.height, availW: screen.availWidth, availH: screen.availHeight,
                colorDepth: screen.colorDepth};
  out.window = {innerW: innerWidth, innerH: innerHeight, outerW: outerWidth, outerH: outerHeight,
                screenX: screenX, screenY: screenY, dpr: devicePixelRatio};
  out.visibility = document.visibilityState;
  out.hasFocus = document.hasFocus();
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
    out.webglVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null;
    out.webglRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null;
  } catch (e) { out.webglRenderer = 'ERROR:' + e; }
  try {
    const c = document.createElement('canvas'); c.width = 220; c.height = 40;
    const x = c.getContext('2d'); x.font = '16px Arial'; x.fillStyle = '#f60'; x.fillRect(0, 0, 220, 40);
    x.fillStyle = '#069'; x.fillText('gsearch probe \\u2603', 4, 24);
    const data = c.toDataURL();
    let h = 0; for (let i = 0; i < data.length; i++) { h = (h * 31 + data.charCodeAt(i)) | 0; }
    out.canvasHash = h;
  } catch (e) { out.canvasHash = 'ERROR:' + e; }
  out.fontsArial = document.fonts ? document.fonts.check('16px Arial') : null;
  out.fontsSegoe = document.fonts ? document.fonts.check('16px "Segoe UI"') : null;
  out.voices = await new Promise(resolve => {
    if (!window.speechSynthesis) return resolve(-1);
    const done = () => resolve(speechSynthesis.getVoices().length);
    if (speechSynthesis.getVoices().length) return done();
    speechSynthesis.onvoiceschanged = done; setTimeout(done, 1500);
  });
  try { out.notification = Notification.permission; } catch (e) { out.notification = null; }
  out.mediaDevices = nav.mediaDevices ? (await nav.mediaDevices.enumerateDevices()).length : -1;
  out.rtcPeerConnection = typeof RTCPeerConnection;
  return out;
}
"""


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _serve_probe_page() -> ThreadingHTTPServer:
    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<!doctype html><html><head><title>probe</title></head><body>probe</body></html>"
            )

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def probe(cfg: CamoufoxConfig, place: Place | None = None) -> tuple[Place | None, dict[str, Any]]:
    """Launch the identity's device and read what page JavaScript sees."""
    if place is None and cfg.place is None and cfg.geoip:
        place = await locate_exit(cfg.proxy_url)
    place = place or cfg.place
    server = _serve_probe_page()
    try:
        async with AsyncCamoufox(**device_options(cfg, place)) as browser:  # type: ignore[no-untyped-call]
            page = await browser.new_page()
            await page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="load")
            await asyncio.sleep(0.5)
            seen: dict[str, Any] = await page.evaluate(PROBE_JS)
    finally:
        server.shutdown()
    return place, seen


def coherence(place: Place | None, seen: dict[str, Any]) -> list[Check]:
    """Checks a real Windows Firefox user at `place` would pass."""
    ua = str(seen.get("userAgent", ""))
    screen, window = seen.get("screen") or {}, seen.get("window") or {}
    checks = [
        Check(
            "webdriver_hidden",
            seen.get("webdriver") in (False, None),
            f"navigator.webdriver={seen.get('webdriver')!r}",
        ),
        Check("ua_windows_firefox", "Windows NT" in ua and "Firefox/" in ua, ua[:90]),
        Check("platform_win32", seen.get("platform") == "Win32", f"platform={seen.get('platform')!r}"),
        Check("plugins_present", (seen.get("plugins") or 0) > 0, f"plugins={seen.get('plugins')}"),
        Check(
            "webgl_present",
            bool(seen.get("webglRenderer")) and not str(seen.get("webglRenderer")).startswith("ERROR"),
            f"renderer={seen.get('webglRenderer')!r}",
        ),
        Check(
            "screen_in_envelope",
            1440 <= (screen.get("w") or 0) <= 1920 and 900 <= (screen.get("h") or 0) <= 1080,
            f"screen={screen.get('w')}x{screen.get('h')}",
        ),
        Check(
            "outer_ge_inner",
            (window.get("outerW") or 0) >= (window.get("innerW") or 0)
            and (window.get("outerH") or 0) >= (window.get("innerH") or 0),
            f"outer={window.get('outerW')}x{window.get('outerH')}"
            f" inner={window.get('innerW')}x{window.get('innerH')}",
        ),
        Check(
            "rtc_blocked",
            seen.get("rtcPeerConnection") == "undefined",
            f"RTCPeerConnection: {seen.get('rtcPeerConnection')}",
        ),
    ]
    if place is not None:
        checks.append(
            Check(
                "timezone_matches_ip",
                seen.get("timezone") == place.timezone,
                f"{seen.get('timezone')!r} vs exit {place.timezone!r}",
            )
        )
        want = (place.locale or "").split("-")[0]
        checks.append(
            Check(
                "language_matches_ip",
                str(seen.get("language", "")).split("-")[0] == want,
                f"{seen.get('language')!r} vs exit-country language {place.locale!r}",
            )
        )
    return checks
