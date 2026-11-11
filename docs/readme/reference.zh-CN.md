# 使用说明

## 安装与认证

上手命令直接从 GitHub 默认分支 main 安装，不需要克隆代码。需要 Python >=3.10、uv 和网络。仓库尚未公开时，Git 必须能使用有权限的本机凭据；GitHub 内容读取还需要安装 gh 并执行 `gh auth login`。provider key 均是可选的，见 README 环境变量表。

## MCP

Claude Code 的 `.mcp.json`、Codex 的 `.codex/config.toml` 与 Cursor 的 `.cursor/mcp.json` 都使用 README 中各自的配置片段。启动命令统一为 `uvx --from git+https://github.com/bmdy6/research-mcp --with "mcp>=1.0,<3" research-mcp mcp`，其中 `--with` 安装可选 MCP SDK。服务器使用 stdio；单工具 `research_search` 传关键词执行搜索，传 http(s) URL 读取内容。

三个配置已按原 argv 分别通过真实 SDK 的 `list_tools` 与一次工具调用；未修改本机客户端配置，未进行三个客户端的 GUI 操作验收。

### 取消与阻塞任务

阻塞的搜索和读取放入 asyncio 事件循环共用、线程数有界的工作池，stdio 的 `tools/list` 等请求保持可响应。取消通知由 MCP SDK 取消工具的等待；该取消请求不会收到成功的工具结果。SDK 1.x 返回取消错误；SDK 2.x 不返回该请求的响应，客户端也应在本地停止等待。

取消不会强制终止已运行的 Python 线程、子进程或网络调用。已经开始的工作仍按核心原有超时执行并正常清理，最终结果或异常被丢弃；执行器中尚未开始的任务若已被取消，则不会运行。服务器退出时可能等待运行中的线程。CLI 仍然同步执行。

## 读取结果与边界

首图来自真实读取 [cli/cli PR 14976](https://github.com/cli/cli/pull/14976)：3 条 reviews、2 条 review_comments，并节选原始行内评论的 path 和正文。它证明该次 PR 读取成功，不代表所有页面和所有分页内容都能完整取得；请检查 coverage、truncated 和续读链接。

能力表的搜索、网页、B站节选来自真实运行；图表行使用原创 JSON-LD Dataset 的真实抽取函数输出，是本地样本，不是任意在线图表读取成功的保证。B站无转写 key 时只尝试网页回退，网页内容不等于音频文字稿。

## 排障

缺少 gh、bili、curl 的影响见 README。Exa 免费端点可能限流；python.org 版 macOS Python 要先完成 `Install Certificates.command`。fake-IP 开关只在确认可信代理后开启；不要为了连通性关闭 TLS 或全面放行私网。

## 配置文档

[Claude Code MCP](https://code.claude.com/docs/en/mcp) · [Codex MCP](https://developers.openai.com/codex/mcp) · [Cursor MCP](https://cursor.com/docs/mcp) · [Python macOS certificates](https://docs.python.org/3/using/mac.html).
