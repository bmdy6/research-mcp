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

1. Call `research_list_channels` (CLI: `research-mcp channels`), then explicitly select `providers`. Omitted/empty providers use `bing,baidu,exa`, with `used_default_providers=true` and a display note. Override with `RESEARCH_DEFAULT_PROVIDERS=hacker_news,baidu`; blank restores the built-in group.
2. Pass a candidate URL to `fetch`; the single MCP tool `research_search` accepts keywords or URLs.
3. Inspect `status`, `errors`, `truncated` and `coverage` before searching more or following pages.

## 🛡️ Reliability

- **Offline regression tests.** Run `python3 -m unittest discover -s tests`; CI covers Ubuntu / macOS × Python 3.10 / 3.13, plus a separate MCP stdio job.
- **Public HTTPS reads by default.** Private, loopback and link-local addresses are blocked; redirects are checked at every hop.
- **Keyword search uses free paths only.** Paid Exa and reranking keys are ignored; quota and dependency failures are explicit. URL transcription retains its separate optional configuration.

## Options

Requires Python >=3.10; Quick start installs directly from main without cloning, and needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and network access. The CLI has no required Python runtime dependencies; stdio MCP needs the optional `[mcp]` SDK.

| Option | Purpose |
|---|---|
| `--limit` | CLI search defaults to 10, accepts 1–40; MCP defaults to 12. |
| `--providers` | A comma-separated CLI value or an MCP string array. |
| `--max-age-days` | Optional 1–3650-day window on parseable dates; unknown dates remain. Default: no age limit, including classic papers. MCP: `max_age_days`; ignored for URLs. |
| `--format json` | Emit v1 JSON; text currently also emits readable JSON. |

PubMed explicitly requests relevance ordering; other providers keep their existing ordering. Explicit age windows filter parseable dates before candidate truncation. Missing/unparseable dates remain, so this is not a complete date-filtered literature database.

### Fixed evidence-retrieval evaluation

Run `python3 scripts/evaluate_evidence.py` for 12 deterministic offline cases. Add `--live` for public Exa, Europe PMC, PubMed and AAD requests, without a model or automated browser. `--output report.json` exports public queries, source titles/URLs, status, timings and rule hits only, never full text or credentials. Offline adapters use synthetic sources; live mode skips the two synthetic shell/challenge cases and checks public retrieval plus explicit rejections.

When Jina is unavailable, static HTML fallback extracts visible main/article text before output clipping. Empty JS shells and high-confidence access challenges fail explicitly. It does not execute JavaScript or compute external CSS. Rule hits measure retrieval and source accessibility, **not medical correctness, product efficacy or general search accuracy**. Same-case baseline and changed-code results are in the [2026-11-24 acceptance report](docs/acceptance-evidence-quality-2026-11-24.md).

### Environment variables

| Variable | Purpose | When unset |
|---|---|---|
| `GROQ_API_KEY` | Bilibili audio transcription. | Skip transcription, attempt webpage fallback and suggest setting the key. |
| `EXA_API_KEY` | Ignored by search; fixed keyless endpoint. | Quota errors stop immediately; five-minute process-local cooldown. |
| `AI_GATEWAY_API_KEY` | Ignored by CLI/MCP/shared service search; no model reranking. | `rerank_downgraded: disabled_by_transport_policy`. |
| `OPEN_WEBSEARCH_ROOT` | Optional pinned source installation directory. | Use the fixed revision in the user cache; missing installation is `startup_failed`. |
| `TAVILY_API_KEY` / `OPENALEX_API_KEY` | Official free-only APIs; require matching `*_FREE_ONLY=1`. | Environment only; never put keys in argv, tools or repository. |
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
| `arxiv` | Papers | All papers by default; opt into GitHub-only with ARXIV_REQUIRE_GITHUB=1 |
| `tavily`, `openalex` | Web / papers | Recurring free key + explicit `*_FREE_ONLY=1`; quota preflight, no paid fallback |

### Browser and login requirements (complete list)

These disjoint groups cover all 59 catalog channels. Browser-free does not mean credential-free.

| Requirement | Channel IDs |
|---|---|
| **No browser (35; all API / HTTP / request backends)** | `github_search`, `hacker_news`, `arxiv`, `exa`, `juejin`, `bilibili`, `baidu`, `sogou`, `bing`, `duckduckgo`, `csdn`, `tavily`, `openalex`, `wikipedia`, `wikidata`, `crossref`, `stackexchange`, `huggingface`, `europe_pmc`, `pubmed`, `openlibrary`, `itunes_podcasts`, `mdn`, `google_news_rss`, `npm`, `crates_io`, `docker_hub`, `itunes_apps`, `sogou_wechat`, `hupu`, `douban`, `youtube`, `devto`, `segmentfault`, `suning` |
| **Browser, no login requirement (10)** | `smzdm`, `kr36`, `amazon`, `medium`, `pypi`, `gitee`, `mafengwo`, `sspai`, `huxiu`, `lobsters` |
| **Browser with an existing login (14)** | `xianyu`, `weibo`, `jd`, `taobao`, `tmall`, `zhihu`, `xiaohongshu`, `douyin`, `boss`, `liepin`, `x`, `maimai`, `dianping`, `tieba` |

`github_search` needs an existing gh login; Tavily/OpenAlex need free keys and free-only flags; Google News RSS needs its local personal opt-in. The five open-webSearch engines need the pinned Node request backend and never launch a browser. All browser channels require explicit Ego Lite opt-in. Login requirements are conservative catalog requirements, not conclusions from HTTP blocking; no fresh/incognito browser check was performed. Unimplemented platforms such as VIP and NGA are outside these lists.

YouTube, Douban, Hupu and dev.to now use the same anonymous TLS-verified curl transport as Suning, Sogou WeChat and SegmentFault. YouTube parses primary search video renderers; Douban interleaves public book/movie/music category pages and emits subject URLs; Hupu parses result titles. dev.to uses official [`/api/articles`](https://developers.forem.com/api/v1#tag/articles/operation/getArticles) **exact-tag filtering** (e.g. `python` or `中文`, optional `#`), not arbitrary full-text search. Missing tags yield empty results rather than an unrelated feed. Legacy browser implementations remain explicit diagnostic fallbacks in code; HTTP calls never automatically open a browser.

See [bilingual HTTP acceptance and timings](docs/acceptance-http-four-2026-11-23.md). The previous urllib probe was invalid due to local CA errors; a reproducible curl probe now replaces its [acceptance table](docs/acceptance-browser-parallel-2026-11-23.md#登录需求和匿名-http-探测).

The calling agent chooses 1–2 complementary providers: domestic facts → Baidu/Sogou; English official docs → DDG + keyless Exa; implementation → GitHub + Juejin; papers → arXiv + Exa. Shopping can use the opt-in local browser providers documented below alongside Bilibili/Baidu reviews. No separate routing model is called. Shared service searches force model reranking off even if a legacy caller supplies `rerank=True`; the old explicitly opted-in internal Python reranker remains separate.

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
| Platforms | 22 opt-in search channels; explicit providers; webpage, GitHub and Bilibili reads. | README lists Bilibili, V2EX, GitHub, YouTube and web as defaults, with optional and experimental channels. |
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


### Additional free APIs

Optional providers: `tavily` (basic only), `openalex`, `wikipedia`, `wikidata`, `crossref`, `stackexchange` (Stack Overflow), `huggingface` (public models), `europe_pmc`, `pubmed`, `openlibrary`, `itunes_podcasts`, and pinned open-webSearch `csdn`. arXiv includes papers without code by default; opt into the old filter with `ARXIV_REQUIRE_GITHUB=1`. Bilibili preserves upstream `totalrank` order rather than sorting again by date.

Inject `TAVILY_API_KEY` / `OPENALEX_API_KEY` through environment variables only, with the respective `TAVILY_FREE_ONLY=1` / `OPENALEX_FREE_ONLY=1`. The server never opens vault files. From a source checkout, `python3 scripts/run_free_search.py --verified-free-accounts --tavily-payg-disabled -- search "AI" --providers tavily,openalex --limit 3 --format json` injects only those two keys from the local vault into the CLI subprocess; use those explicit account assertions only after checking the free plan and disabled PAYG. Missing key/flag yields auth_required. For the observed nullable Tavily `paygo_limit`, also set `TAVILY_PAYG_DISABLED=1` only after verifying PAYG Disabled in the account UI; null alone never proves this. Missing fields, positive paid cap or paid usage fail closed. Tavily preflights a free/Researcher plan, disabled PAYG and remaining monthly credits; OpenAlex checks a <=$1 daily budget, zero prepaid balance and sufficient remaining free credit. Unknown fields fail closed. No paid fallback/retry; use a free-only account because preflight cannot atomically reserve usage across other clients.

Examples: errors → github_search + stackexchange + juejin + csdn; papers → openalex + crossref + arxiv; biomedical literature → pubmed + europe_pmc; concepts → wikipedia + wikidata; books/podcasts → openlibrary + itunes_podcasts; public models → huggingface + github_search. Shopping can combine opt-in JD/Taobao/SMZDM browser results with Bilibili reviews.

Local browser providers also include JD, Taobao, Tmall, SMZDM, Xianyu, Zhihu, Xiaohongshu, Douyin and Weibo; Sogou WeChat now uses anonymous HTTP. See [local browser acceptance](docs/acceptance-local-browser-2026-11-23.md), [session follow-up](docs/acceptance-logged-in-browser-2026-11-23.md), and the round-six continuation below. Pinduoduo lacks extractable product links; V2EX hit a challenge; linux.do was declined by the user. No cookie export or CAPTCHA bypass.


### Additional free discovery channels (2026-11-23)

Explicit providers: `npm` (packages), `crates_io` (Rust crates), `docker_hub` (repositories), `mdn` (en-US documentation), `itunes_apps` (CN App Store). All use first-party keyless HTTPS endpoints. Both Chinese and English queries were tested; Docker Hub returned an explicit Chinese empty result. No packages, images or apps are installed.

`google_news_rss` is local personal use only, disabled unless `RESEARCH_GOOGLE_NEWS_RSS=1`, and excluded from the public adapter. Initial bilingual RSS responses were HTTP 200; live adapter calls hit 429, then returned two items per query after a cooldown. Rate limits remain visible failures. Defaults remain unchanged.

Examples: development `--providers npm,crates_io,mdn`; everyday app discovery `--providers itunes_apps,smzdm`; job research `--providers boss,liepin` with existing local browser sessions. See [acceptance and limitations](docs/acceptance-more-free-2026-11-23.md).


### Round-six browser continuation

This historical round added browser providers. Current browser providers from this round: `boss`, `liepin` (job search only, existing sessions); `kr36`, `sspai`, `huxiu`; `x`, `medium`; `pypi`, `gitee`, `lobsters` (stories, discussion links); `amazon`, `mafengwo`. `hupu`, `douban`, `youtube`, `devto`, `segmentfault` and `suning` now use anonymous HTTP; current bilingual acceptance supersedes their historical browser results. Existing local browser opt-in, page selection, process lock and limits apply. Defaults and public-adapter permissions are unchanged. Huxiu opens at most two read-only article popups and closes them. No salaries, prices, purchases, applications or video transcripts are inferred.

Current unsupported browser cases: Fliggy (homepage submission initially worked, later direct search hit an Alibaba slider challenge), Ctrip (autocomplete without extractable canonical result links), Product Hunt (irrelevant Chinese results; no accepted bilingual adapter), Coolapk/Dewu (landing/download pages), Meituan (consumer route redirects to corporate Not Found), VIP (login). Pending user action: **VIP re-login, NGA account permission (reputation > 0)**. ITHome was declined by the user. Maimai, Dianping and Baidu Tieba are implemented browser channels using existing sessions. BOSS, Liepin and X reused existing sessions. Fliggy hit a slider despite an existing session. Previous challenges remain documented: CNBlogs, Lagou, Reddit, 1688, Tianyancha and Qichacha; GDELT had HTTP 429. No bypass or login attempts.

Geolocation and notification policies were reloaded and verified as `2`, normal. Only task space 15 was used; space 14 was never operated. See the [completed browser matrix and evidence](docs/acceptance-round-six-browser-2026-11-23.md), which supersedes the browser-pending entries in the [initial 43-platform matrix](docs/acceptance-more-free-2026-11-23.md#full-candidate-matrix-43-platforms).
