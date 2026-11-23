# Round-six browser acceptance — 2026-11-23

Baseline: `origin/main` 35587a6 (includes #18). Only Ego Lite task space 15 was used. Space 14 was never selected or operated. No login, communication, application, purchase, CAPTCHA bypass, cookie export or private API calls.

## Policy and control

The first claim encountered the retained location handoff; the user had explicitly confirmed handling it, so `takeOverTaskSpace(15)` resumed that same space. On `chrome://policy`, Reload policies was clicked. `DefaultGeolocationSetting=2` and `DefaultNotificationsSetting=2` both showed platform/current-user/recommended/normal, followed by the reload confirmation. No new browser permission prompt occurred in subsequent probes. Completed with `completeTaskSpace(15,{keep:true})`.

## Live adapter matrix

Queries: `人工智能` and `python`, limit 2. Actual packaged local_browser JavaScript and Python URL validation; CLI subprocesses serialized with >=5 seconds between completed calls. No automatic fallback/retry. Manual discovery observations are separate from adapter acceptance.

| Platform / ID | Chinese | English | Scope |
|---|---|---|---|
| BOSS直聘 / `boss` | ok 2 | ok 2 | 职位标题/链接；已有登录态，不解码薪资字体 |
| 猎聘 / `liepin` | ok 2 | ok 2 | 职位卡片/链接；已有登录态，可能含推广职位 |
| 虎扑 / `hupu` | ok 2 | ok 2 | 搜索页标题和规范链接 |
| 36氪 / `kr36` | ok 2 | ok 2 | 搜索页标题和规范链接 |
| 亚马逊 / `amazon` | ok 2 | ok 2 | 商品标题/ASIN链接；不解析币种和价格 |
| 豆瓣 / `douban` | ok 2 | ok 2 | 书影音条目，保留公开 dispatch uri，不含个人内容 |
| YouTube / `youtube` | ok 2 | ok 2 | 视频搜索标题/链接；当前地区和语言会影响排序，无转写 |
| X / `x` | ok 2 | ok 2 | 已有登录态；帖子文本和链接，仅为发帖者陈述 |
| Medium / `medium` | ok 2 | ok 2 | 文章搜索；不保证会员正文可读 |
| dev.to / `devto` | startup_failed 0 | ok 2 | 英文适配通过；中文手动DOM可读，但适配调用startup_failed，不计双语适配通过 |
| SegmentFault / `segmentfault` | ok 2 | ok 2 | 搜索页标题和规范链接 |
| PyPI / `pypi` | ok 2 | ok 2 | 包搜索列表；此前 HTTP challenge 不代表本次浏览器失败，不安装包 |
| Gitee / `gitee` | ok 2 | ok 2 | so.gitee.com 仓库结果；v5 API 空结果与此分开 |
| 马蜂窝 / `mafengwo` | ok 2 | ok 1 | 旅行商城产品标题/链接，不解析价格 |
| 少数派 / `sspai` | ok 2 | ok 2 | 文章标题/链接；包括付费或会员内容线索 |
| 苏宁 / `suning` | ok 2 | ok 2 | 商品标题/链接；正确入口末尾含 /，排除广告跳转链接 |
| 虎嗅 / `huxiu` | ok 2 | ok 2 | 首页浮层，点击卡片取得公开文章链接；最多2条并关闭临时页 |
| Lobsters / `lobsters` | ok 1 | ok 2 | what=stories&order=relevance；返回讨论页，非默认评论搜索 |

Public normalized examples and exact returned counts: [JSON evidence](acceptance/round-six-browser-2026-11-23.json). Raw snapshots, account navigation and tracking URLs are excluded. Earlier failed attempts are retained before later manual rechecks; Amazon locale prefixes were fixed and Medium Chinese navigation was rechecked once after a timeout. Fliggy failed adapter attempts remain evidence of the excluded candidate, not support. Search-page acceptance does not establish full-text access, ongoing availability or downstream factual accuracy.

## Unsupported in this browser increment

| Platform | Evidence / reason |
|---|---|
| 飞猪 | 首页提交曾显示中文酒店结果和英文明确无结果；后续适配调用失败，独立检查英文直接搜索明确触发阿里滑块验证（拖动下方滑块）。停止，不绕过，适配器未进入本次提交。 |
| 携程 | 双语联想项可读；Enter 后仍在首页，联想链接为 javascript:void(0)，未验证到正式搜索列表和可直接提取的规范链接；首页推荐不计结果。 |
| Product Hunt | 英文 Python 产品卡可读；中文人工智能返回 Apple iOS 等不相关卡片，未验证可靠中文搜索。结果卡未提供可直接提取的规范链接，本轮不接入。 |
| 酷安 | 双语搜索地址均回 App 下载首页，无网页关键词结果。 |
| 美团 | 广州消费搜索地址双语均落到企业官网 Not Found；未用企业内容代替消费搜索。 |
| 得物 | 官网仅介绍、社区精选和 App 下载，无网页商品关键词搜索入口。 |
| 唯品会 | 双语均跳 passport.vip.com 登录页；未尝试登录。 |
| IT之家 | 从首页输入框提交双语，均跳 user-login，明确显示“登录以查看搜索结果”；未尝试登录。 |

## Login and previous barriers

User login needed: **唯品会、IT之家、NGA（另需威望 > 0）、脉脉、大众点评、百度贴吧**. The latter four retain round-six findings; no login was attempted. BOSS, Liepin and X used existing sessions; Fliggy hit a slider despite its existing session; this is not anonymous-access acceptance.

Previous round-six CAPTCHA/risk-control failures remain: 博客园、拉勾、Reddit、1688、天眼查、企查查. GDELT returned 429. These were not bypassed or retested in this browser increment.

## Offline verification

Offline tests execute the packaged JavaScript against small DOM fixtures and validate canonical URLs, tracking removal, hostile hosts/credentials, duplicate links, query encoding, query-key restrictions, public-adapter exclusion and bounded Huxiu popup cleanup. Full offline suite: 324 tests, 8 optional integration skips. Wheel build succeeded. CI results are reported on the PR. Browser observations and offline tests are separate evidence.
