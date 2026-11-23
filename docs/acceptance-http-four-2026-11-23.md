# 四渠道匿名 HTTP 迁移验收（2026-11-23）

基线：最新 origin/main `28116ae`（含 #20），新分支 `fix/http-four-anonymous-probe`。生产查询复用原 `suning/sogou_wechat/segmentfault` HTTP 路径，使用系统 curl、完整普通浏览器 UA、校验 TLS、忽略 curlrc、不携带 Cookie/Authorization；没有操作 Ego Lite。默认组、匿名公开适配器白名单均未扩大。

## 中英文实测

调用真实 `service.search(query, providers=[channel], limit=3)`，耗时为本次调用墙钟时间，含 curl 启动、网络、解析与结果契约。以下列每个语言输入的成功查询，各返回 3 条；查询语言不保证结果语言（YouTube 英文 `python` 本次有日文结果，受上游地区/排序影响）。成功样本不是稳定可用性承诺。

| 渠道 | 输入 | 状态 | 返回条数 | 耗时 |
|---|---|---|---:|---:|
| youtube | Python 教程 | ok | 3 | 3.671s |
| youtube | python | ok | 3 | 1.320s |
| douban | 三体 | ok | 3 | 3.108s |
| douban | Dune | ok | 3 | 3.077s |
| hupu | 篮球 | ok | 3 | 0.221s |
| hupu | NBA | ok | 3 | 0.275s |
| devto | 中文 | ok | 3 | 0.997s |
| devto | python | ok | 3 | 0.818s |

首次查询 YouTube 双语和 dev.to 英文出现 `connection_failed`；随后 YouTube 英文 `python tutorial` 还遇到 20.022 秒超时，改用 `python` 后也记录过连接失败。后续诊断和带计时的 service 调用均取得真实结果。未加自动重试；初次失败、人工重测、最终成功的公开链接与耗时均保存在 [JSON](acceptance/http-four-2026-11-23.json)，没有将失败改成成功记录。

## 实现范围

- YouTube：从 `ytInitialData` 的主搜索列表抽取 `videoRenderer`，校验 11 位视频 ID、标题，排除侧栏和重复视频；不下载、不转写。JS 空壳或格式变化返回 `parse_error`。
- 豆瓣：当前默认“全部”搜索页会返回 JS 空壳，不能把 200 当作结果。公开 `cat=1001/1002/1003` 分类搜索可直接返回书籍/电影/音乐，三页共用 20 秒查询预算，按分类交错去重；只从结果标题解出受限的规范 subject 或 dispatch 链接，不抓成员/日记内容。
- 虎扑：只接受 `content-wrap-span` 标题里的帖子链接，忽略板块与侧栏、移除跟踪参数。最初简短 UA 请求曾遇 405，使用完整普通浏览器 UA 的双语生产查询通过。
- dev.to：官方 [`GET /api/articles`](https://developers.forem.com/api/v1#tag/articles/operation/getArticles) 支持精确 `tag`，没有全文 `q` 参数。查询作为标签传入，支持中文与可选 `#`；校验返回标签，防止未生效的筛选混入热门 feed。任意短语可能没有对应标签；这是检索范围限制，README 已明确。
- 正常注册的四个渠道均覆盖旧浏览器 provider，返回 `actual_backend=http/...`，目录登记免 key、无需登录。浏览器实现只保留作显式代码诊断回退；HTTP 失败不会自动启动浏览器。

## 修正匿名探测证据

原先 urllib 的本机 CA `URLError` 表已撤回。新增可重复运行的 `scripts/probe_anonymous_http.py`，直接复用生产 curl 传输，重测全部 31 个历史浏览器平台，生成 [逐请求 JSON](acceptance/anonymous-http-2026-11-23.json) 与[替换后的原验收表](acceptance-browser-parallel-2026-11-23.md#登录需求和匿名-http-探测)。`curl_exit_code`、HTTP 状态、TLS/连接失败、解析和目录登录要求分别保存；HTTP 202 明确标为 `http_non_200`。本次探测中的部分 curl 35 为 TLS 握手错误观察，不推断成 Python CA 故障、平台拦截或必须登录；dev.to 该次探测失败与双语 service 成功都保留。

没有重新做无痕浏览器登录需求验收。浏览器“需登录”维持目录保守要求；HTML 空壳/被拦只说明该请求没有取得可解析结果。PyPI、Gitee、Lobsters、SMZDM、马蜂窝、虎嗅、Medium、知乎、京东、亚马逊、抖音、36氪等继续保留浏览器。

## 本地验证

- 回归先失败、实现后通过：四渠道真实 service 注册、YouTube 主列表与空结果、豆瓣三分类与外链白名单、虎扑标题范围、dev.to 标签/JSON/schema、curl/TLS/HTTP 状态分类与错误脱敏。
- 探测回归覆盖 TLS 不等于拦截/登录、200 空壳不等于空结果、URL 提示不等于可迁移渠道；README 中英文三类清单与目录全部 59 个渠道一致。
- 完整离线测试：`python3 -m unittest discover -s tests`，347 项，无失败（8 项可选依赖跳过）。另装 `mcp==1.30.0` 后运行真实 MCP stdio，7/7 通过；`uv build` 成功生成源码包和 wheel，`compileall` 与 `git diff --check` 通过。CI 状态见 PR。真实调用仅验收搜索结果，未逐条打开落地页，也未复核其他浏览器渠道。

公开证据仅保留状态、耗时与公开结果 URL；原始 HTTP 页面留本地临时文件，没有上传账号、Cookie 或未脱敏日志。
