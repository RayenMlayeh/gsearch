# F-0012 What headless vs headed Camoufox exposes to page JavaScript (offline probe)

- **Date:** 2026-10-17.
- **Setup:** Camoufox 152.0.4-beta.30, launched with the backend's own `device_options` (Windows, screen envelope, WebRTC blocked, uBO excluded, place TN / `Africa/Tunis` / `ar-TN`).
- **Method:** 3 launches per mode, each against a local `127.0.0.1` page. No Google traffic.
- **Tools:** a one-off probe script (not kept in the repo), using `gsearch_core.probe.PROBE_JS`.
- **Raw data:** `captures/2026-10-17/e2/probe_modes.json`.
- **Label:** OBSERVED-run (local harness). This shows what a page *can read*, not what Google *scores*.

## Coherence checks (`gsearch identity check`)

All 6 launches passed all 10 checks:

| Check | Result |
|---|---|
| `navigator.webdriver` | hidden |
| User agent | Windows Firefox 152 |
| `platform` | `Win32` |
| Plugins | present |
| WebGL | present |
| Screen | within 1440×900 – 1920×1080 |
| outer vs inner window | outer ≥ inner |
| `RTCPeerConnection` | undefined (blocked) |
| Timezone | `Africa/Tunis`, matches the IP |
| Language | `ar-TN`, matches the exit-country language |

## Field behaviour

| Field | Behaviour |
|---|---|
| canvas hash, WebGL vendor/renderer (AMD / Intel / NVIDIA), `hardwareConcurrency` (8/12/16), screen size (1680×1050, 1920×1080), window position | **New on every launch**, in both modes. This is Camoufox drawing a fresh device per launch; it is not a mode effect. |
| `devicePixelRatio` | **headless 1, headed 1.5.** Headed exposes the host display's real scaling. |
| `innerWidth`×`innerHeight` (outer 1536×864 in both) | **headless 1536×807, headed 1521×800.** Headed subtracts a 15 px scrollbar; headless does not. |
| `speechSynthesis.getVoices().length` | **0 in both modes.** A stock Windows Firefox normally lists the installed system voices. Candidate gap; whether Google reads it is UNKNOWN. |
| `document.visibilityState`, `hasFocus()` | `visible`, `true` in both |
| `mediaDevices.enumerateDevices()` | 2 in both |

## What this does and does not say

- Headless mode is not exposed by any of the classic tells checked here: `webdriver`, missing plugins, missing WebGL, or zero-size outer window.
- The mode differences (DPR, scrollbar) are real. Which one looks *more* like a typical user is ASSUMED either way: a laptop at 150% scaling is common, and so is a 100% desktop. That is a question for the live arms (E-2) and E-P, not for this probe.
- Speech voices = 0 is the one candidate inconsistency found. Follow-up: check whether Camoufox's `voices.json` / config can populate voices, and diff against a stock Firefox once one is installed.

## Live arms of E-2 (partial: stopped at 02:57, 5 of 12 searches)

Home IP, `captures/2026-10-17/e2/run.log` + `results.json` + `headless-full.har`. All 5 searches came back OK with no block. Every result URL resolved.

| Arm | Searches | Organic / resolved | Seconds | Bytes per search |
|---|---|---|---|---|
| headless, full page | 3 | 8/8, 9/9, 8/8 | 57.7, 54.9, 51.2 | 5.78 MB, 3.35 MB, 4.70 MB (session 13.8 MB) |
| headed, images/media/fonts blocked | 2 | 9/9, 7/7 | 67.9, 49.0 | not measured: the HAR is written on close and the run was killed |
| headed, full / headless, blocked | 0 | — | — | — |

Not concluded: bandwidth saving from blocking, and any mode effect on Google's verdict. Both need the remaining arms.
