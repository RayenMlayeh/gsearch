## What and why

<!-- One or two sentences. Link the issue: "Closes #…" -->

## Checks

- [ ] `pytest` (or `GSEARCH_SKIP_BROWSER=1 pytest` without the browser)
- [ ] `ruff check packages tests tools examples` and `ruff format --check packages tests tools examples`
- [ ] `mypy`
- [ ] No live Google or proxy traffic in tests; no raw captures, `.env` or session tokens committed
- [ ] Claims about Google's behaviour have a file in `docs/findings/`
