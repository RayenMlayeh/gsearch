# F-0005 Custom Search JSON API: closed to new customers, ends 2027-01-01

- **Source:** https://developers.google.com/custom-search/v1/overview, fetched 2026-10-16. This is **OBSERVED-doc**, not rea evidence.

Quoted:
- "The Custom Search JSON API is closed to new customers."
- "Existing Custom Search JSON API customers have until January 1, 2027 to transition to an alternative solution."
- Pricing: "100 search queries per day for free", "Additional requests cost $5 per 1000 queries, up to 10k queries per day", "until the service discontinuation on January 1, 2027."
- The page recommends Vertex AI Search for searching up to 50 domains, and a contact form for full-web needs.

Secondary sources (not authoritative) add that Programmable Search Engines created since 2026-01-20 are limited to "Sites to search" (≤ 50 domains):
[brave.com](https://brave.com/learn/google-api-shutdown/), [cloro.dev](https://cloro.dev/blog/google-custom-search-api-shutdown/).

## Consequence

An official-API backend would only be usable by someone who **already** has a key, and only until 2027-01-01. The project has no official-API backend (DESIGN.md D-002); the `Fetcher` interface stays open for one.
