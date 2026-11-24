# Evidence quality implementation plan

**Goal:** Return readable source content, retain classic evidence by default, and measure a fixed public-source task set.

**Architecture:** Keep the existing safe fetch transport and result envelope. Extend the dependency-free HTML fallback and pass one optional age constraint through existing entry points; preserve all provider ordering except explicit PubMed relevance.

**Constraints:** No required Python dependency; no paid model, automatic browser or process restart. Preserve unrelated changes in the original checkout and its existing `uv.lock`. Implementation is reviewed before GitHub delivery.

## Task 1: Reliable HTML fallback

Files: `research_mcp/html_content.py`, `research_mcp/research.py`, `tests/test_research.py`.

- [x] Add failing cases for comment/BOM prefixes, hidden ancestors/empty main, heading-only Loading and access challenges, and article header preservation.
- [x] Extract visible main/article content before the output budget; fail explicitly for high-confidence shells/challenges; preserve ordinary text and article headings.
- [x] Run focused reader tests, expecting all assertions to pass and no raw HTML in the long-navigation fixture.

## Task 2: Search evidence coverage

Files: `research_mcp/research.py`, `service.py`, `cli.py`, `mcp_server.py`, `channels/free_apis.py`, associated tests.

- [x] Add failing assertions that default searches retain a 2005 paper and omit provider age filters, explicit 90-day searches filter it, and PubMed sends `sort=relevance`.
- [x] Default `max_age_days` to `None`; validate and propagate explicit positive windows through CLI/MCP; remove implicit GitHub/HN windows when absent.
- [x] Run focused core, provider, CLI and MCP tests.

## Task 3: Fixed evaluation and acceptance

Files: `scripts/evaluate_evidence.py`, `tests/test_evidence_eval.py`, `docs/acceptance/evidence-quality-2026-11-24*.json`, `docs/acceptance-evidence-quality-2026-11-24.md`, `README.md`.

- [x] Define 12 fixed public search/read/explicit-failure cases and offline deterministic fixtures; write the evaluation runner with optional `--live`.
- [x] Capture the same case set against baseline and changed code; export only query, URL, titles, status, elapsed time and rule hits, never full source text or credentials.
- [x] Run full unittest suite, package build and new stdio smoke; state live failures and that rule hits do not establish medical correctness.
