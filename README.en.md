# research-mcp

Give AI agents search results and source text through a CLI and MCP.

[简体中文](README.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/hero-dark.png">
  <img src="docs/readme/hero-light.png" alt="A keyword search returns source URLs, partial status and a skipped rerank marker." width="640">
</picture>

Recorded no-key search excerpt; partial means some channels failed, so coverage is incomplete.

## Quick start

Run inside a `feat/v0.1` checkout with [uv](https://docs.astral.sh/uv/getting-started/installation/) and network access.

```sh
uv sync --python 3.12 --extra mcp && uv run research-mcp --help
uv run research-mcp search "mcp server" --limit 5 --format json
uv run research-mcp fetch https://example.com --format json
```


Default-branch MCP examples are not ready yet; use the [preview configuration](docs/readme/reference.en.md) for now.

<details><summary>Claude Code / Codex / Cursor · MCP configuration</summary>

**Claude Code · .mcp.json**

```json
{"mcpServers":{"research":{"command":"uvx","args":["--from","git+https://github.com/bmdy6/research-mcp","research-mcp","mcp"]}}}
```



**Codex · .codex/config.toml**

```toml
[mcp_servers.research]
command = "uvx"
args = ["--from", "git+https://github.com/bmdy6/research-mcp", "research-mcp", "mcp"]
```



**Cursor · .cursor/mcp.json**

```json
{"mcpServers":{"research":{"command":"uvx","args":["--from","git+https://github.com/bmdy6/research-mcp","research-mcp","mcp"]}}}
```




</details>

## What you get

- **Search several channels at once.** GitHub, HN, Bilibili, Juejin and Exa run concurrently and return source links.
- **Read sources beyond snippets.** Webpages return Markdown; GitHub PRs retain reviews, inline comments and pagination coverage.
- **See incomplete reads.** Results expose partial status, failed channels and truncation; Bilibili webpages never stand in for transcripts.

## Capabilities and recorded output

These are real excerpts recorded on 2026-11-09 with original field names; see [execution evidence](docs/readme/reference.en.md) for provenance and reproduction. Network results vary; arXiv requires explicit selection.

| Capability | Input | Recorded excerpt |
|---|---|---|
| Multi-channel search | `mcp server` | `"status": "partial"`; `"succeeded_channels": ["hacker_news", "bilibili", "juejin"]` |
| Webpage body | `https://example.com` | `"via": "jina"`; `Title: Example Domain` |
| GitHub PR | `https://github.com/cli/cli/pull/14976` | `coverage.reviews.returned: 3`; `coverage.review_comments.returned: 2` |
| Bilibili fallback | `https://www.bilibili.com/video/BV1xx411c7mD` | `"status": "partial"`; `"hint": "设置 GROQ_API_KEY 环境变量"` |
| Chart data | An original JSON-LD Dataset fixture through the real extractor | `### Sample`; `\| A \| 1.5 \|` |

The chart row is a local fixture run, not evidence of an online page read; extraction uses embedded data, not image recognition. The Bilibili example retrieved a webpage title and fallback content without transcribing audio.

## How it works

1. Search keywords; `--providers` accepts `github_search,hacker_news,bilibili,exa,juejin,arxiv`.
2. Pass a candidate URL to `fetch`; the single MCP tool `research_search` accepts keywords or URLs.
3. Inspect `status`, `errors`, `truncated` and `coverage` before searching more or following pages.

## Options

Requires Python >=3.10; Quick start selects verified Python 3.12. The CLI has no required Python runtime dependencies; stdio MCP needs the optional `[mcp]` SDK.

| Option | Purpose |
|---|---|
| `--limit` | CLI search defaults to 10, accepts 1–40; MCP defaults to 12. |
| `--providers` | A comma-separated CLI value or an MCP string array. |
| `--format json` | Emit v1 JSON; text currently also emits readable JSON. |

### Environment variables

| Variable | Purpose | When unset |
|---|---|---|
| `GROQ_API_KEY` | Bilibili audio transcription. | Skip transcription, attempt webpage fallback and suggest setting the key. |
| `EXA_API_KEY` | Your own Exa search quota. | Use the free MCP endpoint, which may rate-limit requests. |
| `AI_GATEWAY_API_KEY` | Optional relevance filtering. | Report `rerank: skipped_no_key` without paid filtering. |
| `RESEARCH_MCP_ALLOW_FAKE_IP` | Exactly `1` permits `198.18.0.0/15`. | Block that range and explain proxy fake-IP mode. |

### External commands

| Command | Impact when missing or unavailable | Installation |
|---|---|---|
| `gh` | GitHub search and repository/Issue/PR/file reading fail; other channels continue. The signed-in identity affects visibility. | [GitHub CLI](https://cli.github.com), then `gh auth login`. |
| `bili` | No backup search after Bilibili API failure or audio download; public API search and webpage fallback may still work. | `uv tool install 'bilibili-cli[audio]'`. |
| `curl` | Affects HN, Juejin, Bilibili API searches and Groq transcription; direct Exa and safe webpage reading do not require it. | Install curl with the system package manager. |

## Comparison with Agent Reach

Read on **2026-11-09**. This compares current statements and implementation, not every version or environment.

| Aspect | research-mcp | Agent Reach |
|---|---|---|
| Entry point | Packaged CLI and single-tool stdio MCP. | README demonstrates `agent-reach <channel> <action>`, `read` and natural-language installation/management. |
| Platforms | Five default search channels, explicit arXiv; webpage, GitHub and Bilibili reads. | README lists Bilibili, V2EX, GitHub, YouTube and web as defaults, with optional and experimental channels. |
| Identity/config | Three optional keys from environment variables; GitHub reuses gh identity. | README describes copying existing browser login for some platforms, dedicated browsers and session management. |
| Failure/coverage | v1 JSON partial status, error codes, truncation and GitHub coverage. | README describes ordered preferred/backup tool lists per platform. |

Sources: [CLI](research_mcp/cli.py), [search/read core](research_mcp/research.py), [result contract](research_mcp/result_contract.py), [Agent Reach pinned README](https://github.com/Panniantong/Agent-Reach/blob/c70ae6da5d39bc90e09ad2ae0575ccc89f31e284/README.md) and [CLI implementation](https://github.com/Panniantong/Agent-Reach/blob/c70ae6da5d39bc90e09ad2ae0575ccc89f31e284/agent_reach/runtime/cli.py).

## FAQ and boundaries

**Which addresses can it read?** It is intended for read-only public HTTPS; HTTP upgrades to HTTPS on the same host. The safe webpage path blocks private, loopback and link-local addresses and validates redirect hops; GitHub uses authenticated gh APIs, so this is not an anonymous public-access boundary or a public gateway.

**When should I enable fake-IP?** Only after confirming a trusted proxy maps domains into `198.18.0.0/15`; setting `1` relaxes that special non-public range and a wrong DNS/proxy configuration may reach unintended targets. Other private ranges remain blocked; keep it unset on ordinary networks.

**Why are results or transcripts missing?** Free Exa requests can be rate-limited; failure does not prove absence. Bilibili transcription needs `bili`, `GROQ_API_KEY` and downloadable audio; downloads may additionally need login/audio dependencies, and no-key webpage fallback can also fail.

**Why does HTTPS fail on a Mac?** With python.org Python, run `Install Certificates.command` in its installation directory as the [official Python instructions](https://docs.python.org/3/using/mac.html) require; keep certificate validation enabled. The default-branch MCP examples above also lack the optional SDK; use the linked preview configuration for now, and note that GUI integration with the three clients has not been tested.

## Contributing

Include your Python version, command and redacted error codes in an issue; CI runs offline tests on Ubuntu/macOS × Python 3.10/3.13 and a separate optional MCP stdio job.

## License

[MIT](LICENSE). Source and transcript rights remain with their owners; follow upstream terms.
