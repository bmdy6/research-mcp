# Preview and execution evidence

Check out `feat/v0.1` before running the three README commands; if the repository is inaccessible, an authorized GitHub identity is required. Anonymous installation is not promised.

`git clone --branch feat/v0.1 https://github.com/bmdy6/research-mcp.git`

The default branch currently contains only seed files, so the requested `uvx --from git+https://github.com/bmdy6/research-mcp research-mcp mcp` cannot install yet. Even after integration, the base package does not install `[mcp]` automatically; the three snippets are not verified client integrations.

Inside the synced checkout, launch with `uv run research-mcp mcp`. Alternatively keep `command = uvx` and replace each configuration args array with:

```json
["--with", "mcp", "--from", "git+https://github.com/bmdy6/research-mcp@feat/v0.1", "research-mcp", "mcp"]
```

This preview requires an authorized GitHub identity. Stdio protocol behavior was tested separately; no client configuration files were changed.

## Output provenance

[output-excerpts.json](output-excerpts.json) stores commands, real field excerpts and raw capture hashes. Full originals stay in the task archive; installer logs containing temporary paths are excluded from Git. The hero capture, input script and field projection script are bundled; from an installed README skill, regenerate with `node scripts/run.mjs art --repo REPO --spec REPO/docs/readme/art.json --out REPO/docs/readme`.

The hero ran with a separate home, no provider keys and no gh credentials, so GitHub failed. This does not predict failure for users with configured gh. Its four-line field projection shows real results; the capability table retains JSON fields. Webpage and Bilibili reads used Python 3.12; python.org Python 3.13 failed without certificates, and system certificates/validation were not changed.

Chart evidence uses the real `extract_chart_datasets` / `render_chart_datasets` functions on an original JSON-LD fixture. A live raw-JSON URL was rejected for unsupported content type, so arbitrary chart/API reading is not claimed. No paid audio transcription or relevance filtering was invoked.

## Configuration sources

[Claude Code MCP](https://code.claude.com/docs/en/mcp) · [Codex MCP](https://developers.openai.com/codex/mcp) · [Cursor MCP](https://cursor.com/docs/mcp) · [Python macOS certificates](https://docs.python.org/3/using/mac.html).

## Verifier compatibility

The skill only recognizes English `README.md` and Chinese `README.zh-CN.md`. Delivery keeps the requested Chinese primary README and `README.en.md`; V1–V11 run in a temporary filename-mapped mirror with byte-identical content, plus an actual Chinese first-screen inspection. Both baseline and candidate use offline local Markdown rendering, not live GitHub screenshots.
