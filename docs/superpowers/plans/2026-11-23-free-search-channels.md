# Free search channels, Issue #12 — Implementation Plan

> **For agentic workers:** Execute inline with executing-plans. The user authorized implementation, free account registration, commit/push and one PR; no merge.

**Goal:** Discover channels, explicitly select free searches, integrate four open-webSearch engines and repair keyless Exa.

**Architecture:** Keep `channels.search(config)` and the shared v1 envelope. A local catalog reports requirements and process-local observations. Wrap the pinned upstream runtime in a bounded Node subprocess; use system curl with normal TLS verification for Exa.

**Tech Stack:** Python >=3.10 standard library, optional MCP SDK 1/2, Node.js, upstream open-webSearch 2.2.0 at 2562c9233a945c18fa9d08b7e450ca218a8853b6.

## Global Constraints

- Free only: never read EXA_API_KEY or invoke a routing/reranking model through the default service; quota exhaustion stops searches.
- Four engines only: baidu, sogou, bing, duckduckgo; SEARCH_MODE=request, one named engine per subprocess, no daemon/browser startup.
- Keys only in a backed-up local vault; never in repository, CLI arguments, logs or PR.
- Shopping/community are browser research only, one low-frequency search per platform, existing Ego Lite space, no cookie export/CAPTCHA bypass.
- Preserve original checkout and untracked uv.lock; changes live in an isolated worktree from origin/main.

## Files and execution

1. `research_mcp/channel_catalog.py`, `channels/errors.py`, `research.py`, `result_contract.py`, `service.py`, `cli.py`, `mcp_server.py`: metadata, fixed safe failure states, explicit provider selection, list tool. Test in `tests/test_channel_catalog.py`, adapting intentional old default assertions in existing tests.
2. `channels/open_websearch.py`, `channels/open_websearch_bridge.mjs`, `scripts/install_open_websearch.py`, `MANIFEST.in`, `THIRD_PARTY_NOTICES.md`: pinned, opt-in upstream subprocess, no runtime download; Apache license preserved. Offline subprocess/envelope tests in `tests/test_open_websearch.py`.
3. `channels/exa.py`, `tests/test_channels.py`: reproduce missing Python CA vs successful curl, then replace RPC transport with bounded curl body/header files, timeout and cleanup. Test JSON/SSE identities, sessions, HTTP status, malformed content, quota circuit and ignored paid key.
4. Both READMEs/reference guides and `docs/acceptance-2026-11-23.md`: new behavior, installation, free boundaries, 20-query before/after checks, browser platform evidence and honest limitations.

## Conditions and downstream evidence

| Condition | Required result |
|---|---|
| providers omitted/empty | No network, actionable CHANNELS_REQUIRED error |
| explicit supported channels | Only requested providers; stable ordering and deduplication |
| missing runtime/key/login | Specific safe state; other providers continue |
| valid upstream zero rows | empty, distinct from blocked/parse/startup failure |
| HTTP 401 / 403 / 429 | auth_required / blocked / quota_exhausted |
| JSON/SSE shape drift | parse_error, never successful empty |
| paid keys present in environment | Default keyword search ignores them |
| cancellation/timeout | Existing async offload retained; subprocess/tempfiles cleaned under bounded worker lifetime |

## Checkpoints

- [x] Read Issue, supplied evidence, actual service/core/MCP/CLI paths; baseline: 250 tests pass, 8 optional skips.
- [x] Add failing behavior tests, run RED, implement catalog and adapters, run GREEN.
- [ ] Tavily free key saved and read back; OpenAlex final login blocked by browser permission prompt, no key obtained.
- [x] Run all offline tests, real MCP stdio discovery and selected search; build and inspect wheel.
- [x] Run bilingual four-engine + Exa attempts and 20-question comparison; record blocked/quota states and explicit skipped-network evidence.
- [ ] Shopping/community per-platform checks blocked when Ego Lite automatically handed its existing space to the user for a browser-owned local-network prompt. No alternative space used.
- [x] Review complete diff and scan credentials; code/build/no-key wheel checks passed.
- [ ] Commit/push and open a draft PR with Related #12; browser barriers remain explicitly pending, no merge.
