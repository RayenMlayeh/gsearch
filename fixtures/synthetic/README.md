# Synthetic fixtures

Hand-written pages that reproduce **only the markers** of real pages we observed, so the classifier can be tested without committing Google's anti-abuse code or reCAPTCHA markup.

| File | Reproduces | Observed in |
|---|---|---|
| `js-gate.html` | 200 page without results: `<noscript>` refresh to `/httpservice/retry/enablejs?sei=…`, plus a script that writes `SG_SS=` | F-0002 (93 KB gate page, Camoufox + Chrome) |
| `url-interstitial.html` | 200 page served for a clicked result's rewritten `/url?...` link: destination in a `noscript` meta refresh and in a `redirectUrl='...'` script variable | F-0011 (live run 3) |
| `sorry-captcha.html` | `/sorry/index` page served with HTTP 429: `form#captcha-form`, `div.g-recaptcha` | F-0001 (Chrome arms) |

If Google changes these pages, update the markers here **and** the finding. Never paste real gate or challenge code here.
