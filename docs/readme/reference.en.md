# Usage notes

## Installation and identity

Quick start installs from the GitHub default branch, main, without cloning. It requires Python >=3.10, uv and network access. While the repository is private, Git needs an authorized local credential; reading GitHub content additionally needs gh and `gh auth login`. Provider keys are optional; see the README environment table.

## MCP

Use the corresponding README configuration for Claude Code `.mcp.json`, Codex `.codex/config.toml`, or Cursor `.cursor/mcp.json`. Each runs `uvx --from git+https://github.com/bmdy6/research-mcp --with "mcp>=1.0,<3" research-mcp mcp`; `--with` installs the optional MCP SDK. The stdio server exposes one tool, `research_search`, accepting either keywords for search or an http(s) URL for reading.

Each configuration's exact argv passed real SDK `list_tools` and a tool call independently. Local client configurations were not changed, and GUI operation in the three clients was not tested.

### Cancellation and blocking work

Blocking searches and reads run in the asyncio event loop's shared, bounded worker pool, keeping stdio requests such as `tools/list` responsive. A cancellation notification cancels the tool's await through the MCP SDK; no successful tool result is delivered for that cancelled request.

Cancellation does not forcibly stop a running Python thread, subprocess or network call. Work already started continues under the core's existing timeouts and performs its normal cleanup; its eventual result or exception is discarded. Work cancelled before its executor job starts does not run. Server shutdown may wait for running workers. The CLI remains synchronous.

## Results and boundaries

The hero comes from a real read of [cli/cli PR 14976](https://github.com/cli/cli/pull/14976): 3 reviews, 2 review_comments, and an original inline comment path and excerpt. This proves that read succeeded, not that every page or discussion page is complete; inspect coverage, truncated and continuation links.

The capability table uses recorded search, webpage and Bilibili excerpts. The chart row runs the real extractor on an original JSON-LD Dataset fixture; it does not guarantee arbitrary online chart reading. Without a transcription key, Bilibili only attempts webpage fallback, which is not an audio transcript.

## Troubleshooting

The README lists the impact of missing gh, bili and curl. Free Exa requests may be rate-limited; python.org Python on macOS requires `Install Certificates.command`. Enable fake-IP only after confirming a trusted proxy; keep TLS verification and private-address restrictions enabled.

## Configuration documentation

[Claude Code MCP](https://code.claude.com/docs/en/mcp) · [Codex MCP](https://developers.openai.com/codex/mcp) · [Cursor MCP](https://cursor.com/docs/mcp) · [Python macOS certificates](https://docs.python.org/3/using/mac.html).
