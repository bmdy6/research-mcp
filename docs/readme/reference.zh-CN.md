# 试用与运行证据

先检出 `feat/v0.1`，再执行 README 的三条命令；仓库若不可见，需要有权限的 GitHub 身份，本文不承诺匿名安装。

`git clone --branch feat/v0.1 https://github.com/bmdy6/research-mcp.git`

当前默认分支只有种子文件，因此要求保留的 `uvx --from git+https://github.com/bmdy6/research-mcp research-mcp mcp` 尚不能安装；即使代码进入默认分支，基础包也不会自动安装 `[mcp]`。不要把这三个配置片段当作已经完成客户端接入验收。

当前检出目录可用 `uv run research-mcp mcp` 启动；也可把三个配置中的 args 替换为下列数组，保留 `command = uvx`：

```json
["--with", "mcp", "--from", "git+https://github.com/bmdy6/research-mcp@feat/v0.1", "research-mcp", "mcp"]
```

此预览使用有权限的 GitHub 身份；stdio 协议有独立实测，未实际改写客户端配置文件。

## 输出来源

[output-excerpts.json](output-excerpts.json) 保存命令、真实字段节选与原始捕获哈希；完整原始日志保存在任务归档，不提交可能含临时路径的安装日志。首图的真实捕获、输入脚本和字段投影脚本都随本仓保存，可在安装的 README 技能目录运行 `node scripts/run.mjs art --repo REPO --spec REPO/docs/readme/art.json --out REPO/docs/readme` 重建。

首图运行在独立 HOME，无 provider key、无 gh 凭据，所以 GitHub 渠道失败；这并不证明安装了 gh 的用户也会失败。首图用四行字段投影展示真实结果，完整 JSON 结构见能力表。网页及 B站实测使用 Python 3.12；本机 python.org 版 Python 3.13 缺证书时失败，未改变系统证书或关闭验证。

图表证据使用原创 JSON-LD 样本的真实 `extract_chart_datasets` / `render_chart_datasets` 调用；一次在线原始 JSON 地址因内容类型不支持被拒绝，未据此宣称能读取任意图表或原始 JSON API。未调用付费音频转写或相关性筛选。

## 配置依据

[Claude Code MCP](https://code.claude.com/docs/en/mcp) · [Codex MCP](https://developers.openai.com/codex/mcp) · [Cursor MCP](https://cursor.com/docs/mcp) · [Python macOS certificates](https://docs.python.org/3/using/mac.html).

## 校验器兼容性

本轮技能只识别英文 `README.md` 和中文 `README.zh-CN.md`。正式输出遵守中文主 README 与 `README.en.md` 的命名；全部 V1–V11 在临时镜像中按文件名映射运行，内容逐字节相同，并额外检查真实中文首屏。前后均使用 offline 本地 Markdown 渲染；不把它称为 GitHub 在线截图。
