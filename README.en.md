<div align="center">

<h1><img src="docs/readme/showcase/banner.en.png" alt="research-mcp: search 5 channels at once, then read the sources. The real search output on the right, recorded on 2026-11-11, shows all 5 channels succeeding, 119 candidates and 3.4 seconds elapsed." width="838"></h1>

[![CI](https://github.com/bmdy6/research-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bmdy6/research-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)
![Dependencies](https://img.shields.io/badge/CLI%20runtime%20dependencies-0-34d399)
![MCP](https://img.shields.io/badge/MCP-stdio-8b5cf6)
[![License](https://img.shields.io/badge/license-MIT-64748b)](LICENSE)

[Quick start](#quick-start) · [Features](#-features) · [Real output](#capabilities-and-real-output) · [FAQ](#faq-and-limitations) · [简体中文](README.md)

</div>

The banner records the old all-channel default on 2026-11-11: `search "mcp server" --limit 5` returned results from all 5 channels, pooled 119 candidates and took 3.4 seconds. The current version requires explicit provider selection after listing channels.

## Quick start

```sh
uvx --from git+https://github.com/bmdy6/research-mcp research-mcp --help
uvx --from git+https://github.com/bmdy6/research-mcp research-mcp channels --format json
uvx --from git+https://github.com/bmdy6/research-mcp research-mcp search "mcp server" --providers hacker_news --limit 5
uvx --from git+https://github.com/bmdy6/research-mcp research-mcp fetch \
  https://github.com/cli/cli/pull/14976
```

MCP installs its SDK via `--with "mcp>=1.0,<3"`; see the [usage reference](docs/readme/reference.en.md).

<details><summary>Claude Code / Codex / Cursor · MCP configuration</summary>

**Claude Code · .mcp.json**

```json
{"mcpServers":{"research":{"command":"uvx","args":["--from","git+https://github.com/bmdy6/research-mcp","--with","mcp>=1.0,<3","research-mcp","mcp"]}}}
```

**Codex · .codex/config.toml**

```toml
[mcp_servers.research]
command = "uvx"
args = ["--from", "git+https://github.com/bmdy6/research-mcp", "--with", "mcp>=1.0,<3", "research-mcp", "mcp"]
```

**Cursor · .cursor/mcp.json**

```json
{"mcpServers":{"research":{"command":"uvx","args":["--from","git+https://github.com/bmdy6/research-mcp","--with","mcp>=1.0,<3","research-mcp","mcp"]}}}
```

</details>

## Why use it

An AI agent's built-in search may only return a page of snippets: it can miss PR reviews, cannot read Bilibili videos and may not tell you which sources failed. research-mcp combines search and source reading in one tool, and reports what it could not read.

| | Before | With research-mcp |
|---|---|---|
| Research a topic | Open separate tabs for GitHub, HN, Bilibili and Juejin | The agent selects providers; one command searches them concurrently |
| Read a PR | Get only the title and description | Get reviews, inline comments and file paths; `coverage` reports how many were read |
| A source fails | Silently lose part of the evidence while the agent still draws conclusions | Results include `partial`, failed channels and truncation reasons |
| Connect an agent | Install a separate tool for each platform | 2 MCP tools: list capabilities, then search/read |

## ✨ Features

<div align="center">
<img src="docs/readme/showcase/features.en.png" alt="Six features: search 5 channels with one command; read PR reviews; turn webpages into Markdown; report incomplete reads; block private addresses by default; run a zero-dependency CLI with uvx." width="838">
</div>

## Capabilities and real output

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/hero-dark.png">
  <img src="docs/readme/hero-light.png" alt="A successful GitHub PR read returns 3 reviews, 2 inline comments, real file paths and original comment excerpts." width="600">
</picture>

A real PR read: review counts, inline comment counts, file paths and original comment excerpts.

These are real excerpts recorded on 2026-11-09, retaining the original field names; see the [usage reference](docs/readme/reference.en.md) for sources and usage. Network results vary; arXiv is enabled only when explicitly selected.

| Capability | Input | Real output excerpt |
|---|---|---|
| Multi-channel search | `mcp server` | `"status": "partial"`; `"succeeded_channels": ["hacker_news", "bilibili", "juejin"]` |
| Webpage body | `https://example.com` | `"via": "jina"`; `Title: Example Domain` |
| GitHub PR | `https://github.com/cli/cli/pull/14976` | `coverage.reviews.returned: 3`; `coverage.review_comments.returned: 2` |
| Bilibili fallback | `https://www.bilibili.com/video/BV1xx411c7mD` | `"status": "partial"`; `"hint": "设置 GROQ_API_KEY 环境变量"` |
| Chart data | An original JSON-LD Dataset fixture run through the real extractor | `### Sample`; `\| A \| 1.5 \|` |

The chart row is a local fixture run, not evidence of a successful online page read; extraction uses embedded page data, without image recognition. The Bilibili example retrieved only a webpage title and fallback content, without transcribing audio.

## How it works

```mermaid
flowchart LR
  Q[Keywords or URL] --> S{research_search}
  L[research_list_channels] --> A0[Agent selects providers]
  A0 --> Q
  S -->|Keywords| C[Selected free channels only]
  S -->|URL| F[Read sources<br>Webpages · PRs · Bilibili]
  C --> R[v1 JSON<br>status · errors · coverage]
  F --> R
  R --> A[Agent decides to search more or paginate]
```

1. Call `research_list_channels` (CLI: `research-mcp channels`), then explicitly select `providers`. Omitted/empty providers return `CHANNELS_REQUIRED` without network calls.
2. Pass a candidate URL to `fetch`; the single MCP tool `research_search` accepts keywords or URLs.
3. Inspect `status`, `errors`, `truncated` and `coverage` before searching more or following pages.

## 🛡️ Reliability

- **267 test cases.** CI runs offline tests on Ubuntu / macOS × Python 3.10 / 3.13, plus a separate MCP stdio job.
- **Public HTTPS reads by default.** Private, loopback and link-local addresses are blocked; redirects are checked at every hop.
- **Keyword search uses free paths only.** Paid Exa and reranking keys are ignored; quota and dependency failures are explicit. URL transcription retains its separate optional configuration.

## Options

Requires Python >=3.10; Quick start installs directly from main without cloning, and needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and network access. The CLI has no required Python runtime dependencies; stdio MCP needs the optional `[mcp]` SDK.

| Option | Purpose |
|---|---|
| `--limit` | CLI search defaults to 10, accepts 1–40; MCP defaults to 12. |
| `--providers` | A comma-separated CLI value or an MCP string array. |
| `--format json` | Emit v1 JSON; text currently also emits readable JSON. |

### Environment variables

| Variable | Purpose | When unset |
|---|---|---|
| `GROQ_API_KEY` | Bilibili audio transcription. | Skip transcription, attempt webpage fallback and suggest setting the key. |
| `EXA_API_KEY` | Ignored by search; fixed keyless endpoint. | Quota errors stop immediately; five-minute process-local cooldown. |
| `AI_GATEWAY_API_KEY` | Ignored by CLI/MCP/shared service search; no model reranking. | `rerank_downgraded: disabled_by_transport_policy`. |
| `OPEN_WEBSEARCH_ROOT` | Optional pinned source installation directory. | Use the fixed revision in the user cache; missing installation is `startup_failed`. |
| `TAVILY_API_KEY` / `OPENALEX_API_KEY` | Reserved for a later API integration. | Catalog marks these providers `not_implemented`; not read this round. |
| `RESEARCH_MCP_ALLOW_FAKE_IP` | Exactly `1` permits `198.18.0.0/15`. | Block that range and explain proxy fake-IP mode. |

### External commands

| Command | Impact when missing or unavailable | Installation |
|---|---|---|
| `gh` | GitHub search and repository/Issue/PR/file reading fail; other channels continue. The signed-in identity affects visibility. | [GitHub CLI](https://cli.github.com), then `gh auth login`. |
| `bili` | No backup search after Bilibili API failure or audio download; public API search and webpage fallback may still work. | `uv tool install 'bilibili-cli[audio]'`. |
| `curl` | Affects HN, Juejin, Bilibili, Exa searches and Groq transcription; safe webpage reading does not require it. | Install curl with the system package manager. |

## Free channel catalog and selection

The catalog exposes cost class, key/login requirements, languages, content types, unknown quota (`null`), status, last check, license, upstream terms and actual backend. Listing performs no network calls or key reads. `unchecked` is not a live health guarantee; observations last only for the current service process.

| Provider IDs | Content | Requirements |
|---|---|---|
| `baidu`, `sogou` | Chinese web | Optional open-webSearch request subprocess, no key; anti-bot responses are `blocked` |
| `bing`, `duckduckgo` | Web, Chinese/English | Same backend; this exit showed weak Bing relevance on long English queries, so try DDG first |
| `exa` | Semantic web | Official keyless MCP via system curl; never reads a paid key |
| `github_search` | Repositories | Free gh CLI with existing login |
| `hacker_news` | English discussions | Keyless Algolia API |
| `juejin`, `bilibili` | Chinese articles / videos | Existing public search adapters |
| `arxiv` | Papers | Still limited to abstracts linking to GitHub in this increment |
| `tavily`, `openalex` | Web / papers | Recurring free key required; catalog-only, `implemented=false` until a later PR |

The calling agent chooses 1–2 complementary providers: domestic facts → Baidu/Sogou; English official docs → DDG + keyless Exa; implementation → GitHub + Juejin; papers → arXiv + Exa. Shopping can currently use Bilibili/Baidu reviews; JD/Taobao and community browser adapters are not implemented. No separate routing model is called. Shared service searches force model reranking off even if a legacy caller supplies `rerank=True`; the old explicitly opted-in internal Python reranker remains separate.

```json
{"query":"Docker compose depends_on healthcheck","providers":["duckduckgo"],"limit":5}
```

Optional web backend (Node.js >=20, npm, git):

```sh
git clone https://github.com/bmdy6/research-mcp.git
cd research-mcp
python3 scripts/install_open_websearch.py
python3 -m research_mcp search "retrieval augmented generation" --providers duckduckgo --limit 5
```

The installer pins open-webSearch 2.2.0 at `2562c9233a945c18fa9d08b7e450ca218a8853b6`, runs upstream's locked `npm ci --ignore-scripts` and build, and installs into a user cache. Runtime validates the commit; searches never download dependencies. A packaged Node bridge directly invokes upstream's search service, one named engine per process with `SEARCH_MODE=request`, without daemon discovery or browser startup. See [third-party licenses](THIRD_PARTY_NOTICES.md).

`channel_statuses` and `failed_channels[].status` distinguish `ok`, `empty`, `blocked`, `auth_required`, `quota_exhausted`, `parse_error`, `startup_failed`, `timeout`, `connection_failed`, `unsupported`. HTTP 200 with upstream `partialFailures` is a failure, not successful empty. New results retain source, backend and rank. Raw exceptions are not exposed.

See [live acceptance and the 20-question comparison](docs/acceptance-2026-11-23.md) for outages and incomplete browser checks, not an availability guarantee.

## Comparison with Agent Reach

Read on **2026-11-09**. This compares the statements and implementation at that time, not every version or runtime environment of either project.

| Aspect | research-mcp | Agent Reach |
|---|---|---|
| Entry point | Packaged CLI and two-tool stdio MCP. | README demonstrates `agent-reach <channel> <action>`, `read` and natural-language installation/management. |
| Platforms | Ten opt-in search channels; explicit providers; webpage, GitHub and Bilibili reads. | README lists Bilibili, V2EX, GitHub, YouTube and web as defaults, with optional and experimental channels. |
| Identity/config | Free search ignores paid keys; GitHub reuses gh identity; transcription configured separately. | README describes copying existing browser login for some platforms, dedicated browsers and session management. |
| Failure/coverage | v1 JSON partial status, error codes, truncation and GitHub coverage. | README describes ordered preferred/backup tool lists per platform. |

Sources: [CLI](research_mcp/cli.py), [search/read core](research_mcp/research.py), [result contract](research_mcp/result_contract.py), [Agent Reach pinned README](https://github.com/Panniantong/Agent-Reach/blob/c70ae6da5d39bc90e09ad2ae0575ccc89f31e284/README.md) and [CLI implementation](https://github.com/Panniantong/Agent-Reach/blob/c70ae6da5d39bc90e09ad2ae0575ccc89f31e284/agent_reach/runtime/cli.py).

## FAQ and limitations

**Which addresses can it read?** It is intended for read-only public HTTPS; HTTP upgrades to HTTPS on the same host. The safe webpage path blocks private, loopback and link-local addresses and validates every redirect hop. GitHub uses authenticated gh APIs, so this is not an anonymous public-access boundary; do not use it as the basis for a public gateway.

**When should I enable fake-IP?** Only after confirming a trusted proxy maps domains into `198.18.0.0/15`; setting `1` relaxes that special non-public range and an incorrect DNS/proxy configuration may reach unintended targets. Other private ranges remain blocked; keep it unset on ordinary networks.

**Why are search results or transcripts missing?** Free Exa requests can be rate-limited; failure does not prove absence. Bilibili transcription needs `bili`, `GROQ_API_KEY` and downloadable audio; downloads may additionally need login/audio dependencies. Without a key, webpage fallback is not guaranteed to succeed and does not mean the video has been fully read.

**Why does HTTPS fail on a Mac?** With python.org Python, first run `Install Certificates.command` in its installation directory, as the [official Python installation instructions](https://docs.python.org/3/using/mac.html) require; keep certificate validation enabled. All three client configurations passed stdio tool discovery and a tool call with their exact argv; this does not establish GUI acceptance.

## Contributing

Include your Python version, command and redacted error codes in an issue; CI runs offline tests on Ubuntu/macOS × Python 3.10/3.13 and a separate optional MCP stdio job.

## License

[MIT](LICENSE). Source and transcript rights remain with their owners; follow upstream terms.

<div align="center">

If this helps, leave a ⭐. Have a channel in mind? Open an issue.

</div>
