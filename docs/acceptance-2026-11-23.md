# 免费渠道第一批验收（2026-11-23）

关联 [Issue #12](https://github.com/bmdy6/research-mcp/issues/12)。本轮交付目录、显式选源、四个网页引擎和 Exa 传输修复；官方 API 与购物/社区后端留后续。

## 代码与离线检查

- 原 main 基线：250 项，8 项可选依赖跳过。改动后：267 项；Python 3.10/3.13 基础环境通过（8 项可选依赖跳过），Python 3.13 + MCP SDK 1.30.0 全套通过，SDK 2.x 全套 267 项及 stdio 7 项通过。
- 实际 stdio 工具发现包含 `research_list_channels` 和 `research_search`；无 providers 不访问网络，返回 `CHANNELS_REQUIRED`。取消、阻塞时目录发现仍响应，既有清理测试保留。
- `python -m build` 构建 sdist/wheel；发行包包含 JS bridge 和第三方许可证。基础 Python 运行依赖仍为空。
- 可选安装器实际完成固定 commit 的 `npm ci --ignore-scripts` 与 TypeScript 构建；没有安装/启动浏览器。
- 付费 Exa key 被忽略，共享搜索即使传入旧参数 `rerank=True` 也不调用重排模型；上游 401/403/429、缺依赖、坏格式、超时、SSE identity/session、临时文件清理有离线回归。URL 读取的旧转写能力不在本轮免费搜索声明内。

## 20 个问题：接入前与选源后

基线为 `7a3365d` 原 main 的五个默认关键词渠道（GitHub/HN/Exa/掘金/B站，不含显式 arXiv）。同一台 Mac、同一 python.org CPython 3.13，前后隔离 HOME/gh 配置并清除所有 key，没有登录和付费模型。先连续跑基线 20 题，再跑新版本 20 题；每次最终 limit=5，新版本按题选两源。旧源保留原每源预算（20/30 等），新网页源每源 5 条；原始池大小不同，不能把重复率当等预算检索实验。

人工逐项看标题和摘要，标记是否直接对应问题的具体子主题；词典、广告、泛首页、没有体现特定问题的宽泛教程计 0；不足 5 条按 0 补足。P@5 是相关数/5，不是事实准确率、全文可读率或权威程度。由实现者单人、非盲评；这是探索样本，不能推出普遍质量提升。每条候选、逐排名标签、失败状态和耗时在[公开脱敏样本](acceptance/free-search-2026-11-23.json)。没有逐条读落地页。

| 指标 | 接入前 | 按题选源后 |
|---|---:|---:|
| 前五相关比例（100 个位置） | 32% | 56% |
| 渠道失败率 | 40/100 = 40% | 8/40 = 20% |
| 去重前按规范化 URL/仓库键重复率 | 4/748 = 0.53% | 0/160 = 0.00% |
| p50 / p95 墙钟秒（nearest-rank） | 1.583 / 2.652 | 2.562 / 3.667 |
| 完全无成功渠道的问题数 | 0/20 | 0/20 |

基线 40 次失败为 GitHub 无登录身份、Exa 的 Python CA 失败各 20 次。新版本 8 次失败为搜狗反爬 6 次、Exa 免费限流 2 次。原始样本中搜狗先记为 `connection_failed`；后续查看上游固定错误 `Sogou returned a verification or anti-bot page` 后已补回归，最终代码与一次真实复测均报告 `blocked`。没有将本地身份/证书故障说成服务固有不可用。

新增网页源在这组题中更相关，但更慢。Bing 多条英文长查询退成词典或泛首页，第 16 题在 Exa 限流后得到 0/5；DDG 的官方文档补充是主要增益。百度仍可能返回广告、聚合卡和跳转链接，URL 去重不识别同文转载；语义重复率未测。不能按工具包装器个数计算独立证据。

| # | 分组 / 问题 | 新选渠道 | 前→后相关数/5 | 前→后秒 |
|---|---|---|---:|---:|
| 1 | zh · 检索增强生成 原理 | baidu, sogou | 4 → 4 | 2.294 → 2.329 |
| 2 | zh · 个人知识库 笔记 双向链接 | baidu, sogou | 2 → 5 | 1.394 → 1.526 |
| 3 | zh · 杭州 西湖 博物馆 预约 | baidu, sogou | 0 → 4 | 1.285 → 1.742 |
| 4 | zh · 热泵 工作原理 | baidu, sogou | 3 → 5 | 3.760 → 2.280 |
| 5 | zh · 如何改善 睡眠 光照 作息 | baidu, sogou | 3 → 3 | 1.611 → 1.745 |
| 6 | en · retrieval augmented generation | bing, duckduckgo | 5 → 2 | 1.258 → 2.763 |
| 7 | en · open source personal knowledge management | bing, duckduckgo | 2 → 2 | 1.553 → 2.826 |
| 8 | en · how heat pumps work | bing, duckduckgo | 3 → 2 | 2.652 → 2.480 |
| 9 | en · SQLite write ahead log documentation | bing, duckduckgo | 0 → 2 | 1.500 → 4.679 |
| 10 | en · accessible web forms WAI tutorial | bing, duckduckgo | 0 → 2 | 1.954 → 2.428 |
| 11 | tech · Python asyncio TaskGroup exception handling | bing, duckduckgo | 0 → 2 | 1.998 → 3.564 |
| 12 | tech · React useEffect cleanup strict mode | bing, duckduckgo | 1 → 2 | 2.643 → 3.190 |
| 13 | tech · Docker compose depends_on healthcheck | bing, duckduckgo | 0 → 2 | 1.625 → 2.591 |
| 14 | tech · PostgreSQL EXPLAIN ANALYZE tutorial | bing, duckduckgo | 0 → 2 | 1.556 → 2.822 |
| 15 | tech · MCP Streamable HTTP specification | bing, duckduckgo | 0 → 2 | 1.829 → 2.765 |
| 16 | mixed · retrieval augmented generation survey arxiv | exa, bing | 2 → 0 | 1.789 → 3.667 |
| 17 | mixed · circadian rhythm morning light review | exa, duckduckgo | 1 → 5 | 1.428 → 2.642 |
| 18 | mixed · 人体工学椅 腰部支撑 选购 | baidu, sogou | 3 → 3 | 1.518 → 1.811 |
| 19 | mixed · 降噪耳机 地铁 通话 评测 | baidu, sogou | 3 → 5 | 1.472 → 1.960 |
| 20 | mixed · mechanical keyboard hot swap switch comparison | bing, duckduckgo | 0 → 2 | 1.337 → 2.533 |

## 每源双语实测

输入中文 `检索增强生成`、英文 `retrieval augmented generation`，每源 5 条；不带 key，不登录。耗时包含子进程启动，不含依赖安装。

| 渠道 | 中文 | 英文 | 观察 |
|---|---|---|---|
| 百度 | 1.332s，5 条 | 0.957s，5 条 | 有相关线索，也有聚合卡/广告风险 |
| 搜狗 | 0.364s，反爬失败 | 0.419s，反爬失败 | 20 题首题曾成功，后续返回校验页；最终状态修正并实测为 blocked |
| Bing | 0.682s，5 条 | 0.997s，5 条 | 能取结果；英文相关性不稳定，不是稳定质量承诺 |
| DuckDuckGo | 2.288s，5 条 | 6.722s，5 条 | 双语相关，有官方资料；英文此次较慢 |
| Exa 免 key | 2.860s，quota_exhausted | 0.000s，本进程熔断跳过 | 英文调用已提交给适配器，但未再打上游；没有将跳过写成搜索成功 |

Exa 连接根因为 python.org 解释器未配置 CA 路径：urllib 直接报 CERTIFICATE_VERIFY_FAILED，同环境正常验证证书的系统 curl 初始化成功。修复改为有时间/响应大小预算的 curl，保留会话、JSON/SSE identity 和清理。搜索结果目前被上游免费额度限制，不能宣称已完成双语真实结果验收。

## 免费 key 注册

只使用已运行 Ego Lite 的同一任务空间，没有另开浏览器实例。

| 渠道 | 领取结果 | 免费限制 / 当前边界 |
|---|---|---|
| Tavily | 成功 | Researcher 1,000 credits/月，PAYG Disabled；创建专用 key；vault 先备份、写入并回读，权限 600。没有 API 适配或 key 搜索质量验收 |
| OpenAlex | 未完成 | 已提交免费注册、收到验证邮件并打开官方登录页；最终确认时被浏览器级本地网络权限提示打断，工具交还用户，未领取/保存 key |
| 其他 | 不适用 / 未注册 | 本轮 Issue 范围中另外需要免费 key 的只有上述两家；PubMed key 等是可选吞吐提升。材料中 Brave/Serper/智谱等不合免费白名单的渠道不注册、不绑卡 |

没有输入信用卡、购买套餐、启用 PAYG、读取 Exa 付费 key 或调用收费路由模型。系统不会从 vault 自动加载凭据；后续 API PR 再通过调用进程环境注入已确认免费账户的 key。

## 购物与社区：浏览器边界

**本轮尚未完成逐平台搜索结果页实测。** Ego Lite 在 OpenAlex 最终登录动作时弹出浏览器级本地网络访问提示，工具自动将原任务空间交还用户，并要求用户确认后才可继续。未另开实例、未操作其他任务空间，也未改用后台抓取替代被阻塞的本机浏览器验证。

| 平台 | 本轮实际结果 / 后续动作 |
|---|---|
| 京东 | 原任务空间已有登录墙页面，但属于前次任务，不计本轮实测；待权限提示处理后重新查询 |
| 淘宝 | 未测；浏览器控制被中断 |
| 天猫 | 未测；浏览器控制被中断 |
| 拼多多 | 未测；浏览器控制被中断 |
| 什么值得买 | 未测；浏览器控制被中断 |
| 闲鱼 | 未测；浏览器控制被中断 |
| 知乎 | 未测；浏览器控制被中断 |
| 小红书 | 未测；浏览器控制被中断 |
| 抖音 | 未测；浏览器控制被中断 |
| 微博 | 未测；浏览器控制被中断 |
| 微信公众号 | 未测；浏览器控制被中断，候选是搜狗微信文章搜索页 |
| V2EX | 未测；浏览器控制被中断 |
| linux.do | 未测；浏览器控制被中断 |

提供的上一轮调研材料不作为本轮逐平台重测的替代。未导出 Cookie、绕验证码、自动发帖或下单。草稿 PR 保留这些未完成项，不把 Issue #12 关闭。

复核方式：已完成按需求、调用路径、凭据边界、子进程/临时文件清理和发行包内容的结构化自审；没有使用独立审查代理。当前只适合草稿审查，浏览器剩余验收仍需完成。
