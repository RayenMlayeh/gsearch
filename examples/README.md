# Examples

## LangChain search tool

Install `pip install gsearch-agent langchain-core`, then run
`python examples/langchain_tool.py` from this checkout. The tool searches the
bundled `fixtures/gecko` pages for `what is asyncio in python` and prints JSON.
No model key, browser or Google connection is needed. Import `google_search`
and pass it to a LangChain agent's tools list to reuse it.

For live searches, replace `FixtureFetcher` with `fetcher_from_env()` from
`gsearch_core.config` (which returns a fetcher and a live-mode flag). Live use
follows the project's normal backend setup.
LangChain is an example-only dependency, not a dependency of gsearch-agent.
