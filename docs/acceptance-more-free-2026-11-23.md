# Free channel expansion — 2026-11-23

**Browser continuation completed:** see [26-platform browser acceptance](acceptance-round-six-browser-2026-11-23.md) for the updated results, adapters and login list. The observations below preserve the initial #18 snapshot.

Baseline: origin/main 20a1930 (includes #13/#14/#15/#17). Read-only, no keys, no login attempts, no CAPTCHA bypass, no cookie export. Local browser task space 15 only; space 14 was not accessed.

## API adapter results

Queries: `人工智能`, `python`, limit 2. Fixed official endpoints; one request per query, no automatic retries/fallback. Live adapter calls spaced at least 5 seconds apart.

| Provider | Chinese | English | Boundary |
|---|---|---|---|
| npm | ok 2 | ok 2 | package metadata only |
| crates_io | ok 2 | ok 2 | crate metadata only |
| docker_hub | empty 0 | ok 2 | repository metadata only |
| mdn | ok 2 | ok 2 | en-US corpus; Chinese relevance limited |
| itunes_apps | ok 2 | ok 2 | CN storefront; app cost is separate |
| google_news_rss | raw HTTP 200; adapter 429 then ok 2 | raw HTTP 200; adapter 429 then ok 2 | local opt-in only, no quota guarantee |

The real RSS payloads parse as RSS/channel/item. A subsequent live adapter run hit upstream quota; after a cooldown, one fresh call per query returned two normalized items each. No automatic retry was added. Offline fixtures verify parser behavior separately. No production deployment or ongoing availability claim.

## Candidate triage in progress

GDELT returned 429 for both queries. Gitee v5 returned empty arrays for both queries, including a broad English query; this does not establish useful search coverage. Lobsters /search.json returned 404; the HTML search remains under investigation. dev.to /api/articles supports tag filtering, not keyword search; no misleading keyword adapter was added. Reddit JSON returned 403. The repository's existing open-webSearch integration supports search engines/CSDN, not dedicated adapters for these candidates.

Ego Lite claimed task space 15 successfully. Initial browser probes read search lists on SegmentFault, Medium, Product Hunt, Hupu and 36kr. These are not API acceptance and do not yet establish adapter readiness. At Ctrip a location permission prompt transferred control to the user; browser work paused according to SDK rules. No takeover was attempted without user confirmation. Full candidate matrix follows in the browser increment.

## Verification

Baseline: 311 unittest cases, 8 optional integration skips. New tests cover schema drift versus true empty, canonical URLs, encoding, opt-in, bounded transport text, malformed RSS/DTD, and exclusion from public search. Wheel build succeeds. CI status is reported on the PR, separately from live probes.

## Full candidate matrix (43 platforms)

Each row uses 人工智能 / python. Browser observations are initial DOM probes, not completed parser acceptance. After the location handoff, only unauthenticated bounded HTTP requests continued; HTTP shells/redirects are not counted as successful search. Ctrip English is explicitly untested. No automatic login or permission changes were made. These are current-environment findings, not permanent claims about platforms.

| Platform | Chinese | English | Result / reason |
|---|---|---|---|
| npm | ok 2 | ok 2 | 已接入 npm |
| PyPI | Client Challenge | 搜索列表可读 | 暂不支持；中文遇挑战，浏览器适配未完成 |
| crates.io | ok 2 | ok 2 | 已接入 crates_io |
| Docker Hub | empty 0 | ok 2 | 已接入 docker_hub |
| Gitee | v5 []；网页 301 | v5 []；网页 301 | 暂不支持；未验证到有效搜索结果，网页需要浏览器复核 |
| 博客园 | 请完成人机验证 | 请完成人机验证 | 不支持；不绕验证 |
| SegmentFault | 列表可读 | 列表可读 | 待接入；浏览器控制权中断，解析器尚未实测 |
| dev.to | 标签 API 非关键词；网页壳 | 标签 API 非关键词；网页壳 | 暂不支持；网页关键词结果待浏览器验证 |
| MDN | ok 2 | ok 2 | 已接入 mdn；en-US 库 |
| Reddit | JSON 403；网页挑战 | JSON 403；网页挑战 | 不支持当前匿名请求；登录态浏览器待验证 |
| YouTube | 页面已提交但加载超时 | 视频列表可读 | 待浏览器复核与适配；未使用付费 API |
| X | 已登录，仅搜索壳 | 已登录，仅搜索壳 | 待浏览器延迟渲染复核；不据此称无结果 |
| Product Hunt | 页面有产品卡片 | Python 产品卡片 | 待浏览器适配与查询相关性核验 |
| Lobsters | JSON 404；HTML 可读 | JSON 404；HTML 含结果 | 待浏览器适配；HTML 默认是评论搜索 |
| Medium | 文章列表可读 | 文章列表可读 | 待浏览器适配；不保证会员正文 |
| 豆瓣 | 搜索框架无结果卡 | 搜索框架无结果卡 | 待浏览器延迟渲染复核；不推断需要登录 |
| 百度贴吧 | 登录浮层，推荐内容 | 登录浮层，推荐内容 | 不支持当前状态；需要用户登录后再验搜索 |
| 酷安 | 页面不存在 | 页面不存在 | 不支持当前网页搜索入口 |
| 虎扑 | 帖子列表可读 | 帖子列表可读 | 待浏览器适配 |
| NGA | 登录/威望门槛 | 登录/威望门槛 | 不支持当前身份；需要用户登录且威望大于 0 |
| 少数派 | 旧路由不存在；新路由壳 | 旧路由不存在；新路由壳 | 待浏览器核验正确搜索入口 |
| IT之家 | 旧路由 404；候选搜索域 DNS 失败 | 同左 | 暂不支持本轮入口；未确认正确入口，不能据此宣称平台无搜索 |
| 36氪 | 文章列表可读 | 文章列表可读 | 待浏览器适配 |
| 虎嗅 | 旧路由落到门户；候选新路由 405 | 同左 | 暂不支持；没有把推荐文章当搜索结果 |
| GDELT | 429 | 429 | 不支持本轮环境；未拿到有效列表，不自动重试 |
| Google News RSS | ok 2（冷却后） | ok 2（冷却后） | 已接入 google_news_rss；本地个人显式开启，曾遇 429 |
| BOSS直聘 | 搜索框架，无职位卡片 | 搜索框架，无职位卡片 | 待等待渲染复核；已登录，不点沟通，不碰空间 14 |
| 猎聘 | 搜索框架，无职位卡片 | 搜索框架，无职位卡片 | 待等待渲染复核；已登录 |
| 拉勾 | 滑动验证 | 滑动验证 | 不支持；不绕验证码 |
| 脉脉 | 扫码/手机号登录 | 扫码/手机号登录 | 不支持当前身份；需要用户登录 |
| 大众点评 | 扫码登录 | 扫码登录 | 不支持当前身份；需要用户登录 |
| 美团 | 企业门户 Not Found | 企业门户 Not Found | 不支持本轮入口；消费搜索待正确入口核验 |
| 携程 | 定位权限提示，中断 | 未运行（控制权中断） | 待用户处理定位权限；首个 URL 为首页，不能算搜索验收 |
| 飞猪 | 公开请求 302 | 公开请求 302 | 待浏览器验证重定向与搜索列表；不推断需要登录 |
| 马蜂窝 | 公开请求 202/脚本页 | 公开请求 202/脚本页 | 暂不支持；未验证到结果列表 |
| 得物 | 官网介绍页 | 官网介绍页 | 不支持本轮网页入口；没有商品搜索列表 |
| 唯品会 | 公开请求 200 搜索壳 | 公开请求 200 搜索壳 | 待浏览器渲染复核 |
| 1688 | 脚本风控页 | 脚本风控页 | 不支持当前请求；不绕风控 |
| 苏宁 | 404 | 404 | 不支持本轮测试路由；正确入口待核验 |
| 亚马逊 | 公开 HTML 搜索结果 | 公开 HTML 搜索结果 | 待本机浏览器适配；未调用付费 API |
| 天眼查 | 418 风控 | 418 风控 | 不支持当前请求；未取公司详情 |
| 企查查 | 脚本挑战壳 | 脚本挑战壳 | 不支持当前请求；未取公司详情 |
| App Store | ok 2 | ok 2 | 已接入 itunes_apps；CN 区 |
