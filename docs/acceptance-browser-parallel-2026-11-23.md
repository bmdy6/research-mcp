# 本机浏览器并发、匿名 HTTP 和登录状态验收（2026-11-23）

基线：main `28c5f92`（含 #19），从最新 origin/main 新建分支。仅操作空间 15，claim 后使用自己的 newPage；原有 7 个登录标签页全部保留，空间 14 未操作。结束 completeTaskSpace(15,{keep:true})。

## 并发对比

相同关键词「轻薄本」，CLI 共享 service.search 入口，providers=jd,taobao,smzdm,xianyu，limit=8，每个平台各返回 2 条，均无失败。

| 模式 | 总耗时 | 结果 |
|---|---:|---|
| main 全局文件锁串行，完成后间隔 5 秒 | 24.41 秒 | 4/4 成功 |
| 修改后首次并发 | 4.44 秒 | 4/4 成功 |
| 最终代码复测并发 | 3.52 秒 | 4/4 成功 |

最终耗时下降 85.6%，约 6.9 倍。单组顺序实测，受缓存、网络和站点响应影响，不是统计性能承诺。RESEARCH_BROWSER_CONCURRENCY 默认 4（范围 1–16，本进程上限）；按平台独立文件锁跨进程限流，淘宝/天猫共用。空间 15 有 6 个已管理登录页和 1 个未管理页，SDK 默认 8 个已管理页预算，因此本次实际可同时打开 2 个新页，另两个渠道等空位；从未为释放预算关闭登录页。子进程 45 秒兜底，页面分配最多等待 12 秒，页面工作 27 秒到期主动清理；正常、异常均清理自己的页。

## 登录需求和匿名 HTTP 探测

旧表已撤回：上一轮 Python urllib 的本机 CA 错误被误当作平台结果，原 `URLError` 表不能支持渠道可用性或登录结论。

以下为系统 curl 重测。复现命令：`python3 scripts/probe_anonymous_http.py --output docs/acceptance/anonymous-http-2026-11-23.json --markdown /tmp/anonymous-http-table.md`。

统一复用生产 HTTP 传输：普通浏览器 UA、校验 TLS、禁用 curlrc、不带 Cookie/Authorization，单请求最多 18 秒、5 MB、5 次 HTTPS 重定向；每站不重试。豆瓣分别请求公开书籍/电影/音乐分类页。表中耗时包含该渠道所有请求，HTTP 状态按请求顺序列出。

登录要求来自既有目录：浏览器“需登录”是保守使用要求，未作无痕浏览器复核。HTTP 拦截、JS 空壳、TLS/网络错误均不能证明必须登录或平台不可用。`result_link_hints` 仅为 URL 线索，未据此迁移浏览器渠道；虎嗅首页探测不等于完成其搜索。

采集时间（UTC）：2026-11-23T13:44:58.770976+00:00。原始状态见 [JSON](acceptance/anonymous-http-2026-11-23.json)。

| 渠道 | 登录要求 | 当前实现 | HTTP 状态 | 耗时 | curl / 解析观察 |
|---|---|---|---|---:|---|
| xianyu | 需登录（目录保守要求） | browser | 200 | 0.074s | unverified_html_or_js (curl=0, results=0) |
| weibo | 需登录（目录保守要求） | browser | 200 | 0.297s | auth_required (curl=0, results=None) |
| jd | 需登录（目录保守要求） | browser | 200 | 0.479s | unverified_html_or_js (curl=0, results=0) |
| taobao | 需登录（目录保守要求） | browser | 200 | 0.070s | unverified_html_or_js (curl=0, results=0) |
| tmall | 需登录（目录保守要求） | browser | 200 | 0.063s | unverified_html_or_js (curl=0, results=0) |
| smzdm | 不要求登录 | browser | 202 | 0.171s | http_non_200 (curl=0, results=None) |
| zhihu | 需登录（目录保守要求） | browser | 403 | 0.137s | blocked (curl=0, results=None) |
| xiaohongshu | 需登录（目录保守要求） | browser | 301 | 0.112s | connection_failed (curl=1, results=None) |
| douyin | 需登录（目录保守要求） | browser | 200 | 0.165s | unverified_html_or_js (curl=0, results=0) |
| sogou_wechat | 不要求登录 | HTTP/API | 200 | 0.284s | parsed_results (curl=0, results=10) |
| boss | 需登录（目录保守要求） | browser | 200 | 0.174s | unverified_html_or_js (curl=0, results=0) |
| liepin | 需登录（目录保守要求） | browser | 200 | 0.207s | unverified_html_or_js (curl=0, results=0) |
| hupu | 不要求登录 | HTTP/API | 200 | 0.363s | parsed_results (curl=0, results=20) |
| kr36 | 不要求登录 | browser | 200 | 0.210s | unverified_html_or_js (curl=0, results=0) |
| amazon | 不要求登录 | browser | 200 | 2.001s | result_link_hints (curl=0, results=9) |
| douban | 不要求登录 | HTTP/API | 200/200/200 | 2.298s | parsed_results (curl=0, results=20); parsed_results (curl=0, results=20); parsed_results (curl=0, results=20) |
| youtube | 不要求登录 | HTTP/API | 200 | 1.655s | parsed_results (curl=0, results=20) |
| x | 需登录（目录保守要求） | browser | — | 5.247s | tls_error (curl=35, results=None) |
| medium | 不要求登录 | browser | — | 5.032s | tls_error (curl=35, results=None) |
| devto | 不要求登录 | HTTP/API | — | 5.016s | tls_error (curl=35, results=None) |
| segmentfault | 不要求登录 | HTTP/API | 200 | 0.297s | parsed_results (curl=0, results=20) |
| pypi | 不要求登录 | browser | 200 | 0.586s | unverified_html_or_js (curl=0, results=0) |
| gitee | 不要求登录 | browser | 200 | 0.277s | unverified_html_or_js (curl=0, results=0) |
| mafengwo | 不要求登录 | browser | 202 | 0.187s | http_non_200 (curl=0, results=None) |
| sspai | 不要求登录 | browser | 200 | 0.215s | unverified_html_or_js (curl=0, results=0) |
| suning | 不要求登录 | HTTP/API | 200 | 0.291s | parsed_results (curl=0, results=30) |
| huxiu | 不要求登录 | browser | 405 | 0.260s | blocked (curl=0, results=None) |
| lobsters | 不要求登录 | browser | 200 | 1.080s | unverified_html_or_js (curl=0, results=0) |
| maimai | 需登录（目录保守要求） | browser | 200 | 0.198s | auth_required (curl=0, results=None) |
| dianping | 需登录（目录保守要求） | browser | 200 | 0.285s | unverified_html_or_js (curl=0, results=0) |
| tieba | 需登录（目录保守要求） | browser | 403 | 0.217s | blocked (curl=0, results=None) |

本次四渠道双语查询与失败重测详见 [HTTP 迁移验收](acceptance-http-four-2026-11-23.md)。此前苏宁、搜狗微信、思否耗时属于历史实测，本表仅列本次重新执行的 curl 结果。

新增浏览器渠道：脉脉 3.23 秒，大众点评 1.84 秒，贴吧 2.63 秒，均有真实搜索正文和规范链接。脉脉最多读取 2 条临时详情页以获取帖子 ID；大众点评当前默认上海（城市 1）；贴吧读取搜索区 thread-content-box，排除侧栏热榜。

## login-status 实际输出

```json
{
  "ok": true,
  "operation": "login-status",
  "platforms": [
    {
      "channel": "xianyu",
      "status": "unknown",
      "seconds": 4.06
    },
    {
      "channel": "weibo",
      "status": "unknown",
      "seconds": 3.981
    },
    {
      "channel": "jd",
      "status": "logged_in",
      "seconds": 0.968
    },
    {
      "channel": "taobao",
      "status": "logged_in",
      "seconds": 1.673
    },
    {
      "channel": "tmall",
      "status": "logged_in",
      "seconds": 1.671
    },
    {
      "channel": "zhihu",
      "status": "unknown",
      "seconds": 4.457
    },
    {
      "channel": "xiaohongshu",
      "status": "logged_in",
      "seconds": 2.854
    },
    {
      "channel": "douyin",
      "status": "unknown",
      "seconds": 5.678
    },
    {
      "channel": "boss",
      "status": "unknown",
      "seconds": 4.037
    },
    {
      "channel": "liepin",
      "status": "unknown",
      "seconds": 4.198
    },
    {
      "channel": "x",
      "status": "logged_in",
      "seconds": 10.729
    },
    {
      "channel": "maimai",
      "status": "logged_in",
      "seconds": 1.249
    },
    {
      "channel": "dianping",
      "status": "logged_in",
      "seconds": 1.918
    },
    {
      "channel": "tieba",
      "status": "logged_in",
      "seconds": 1.015
    },
    {
      "channel": "vip",
      "status": "expired",
      "seconds": 1.597
    },
    {
      "channel": "nga",
      "status": "restricted",
      "seconds": 0.631
    }
  ],
  "expired": [
    "vip"
  ],
  "needs_attention": [
    "xianyu",
    "weibo",
    "zhihu",
    "douyin",
    "boss",
    "liepin",
    "vip",
    "nga"
  ]
}
```

此输出采自登录检测实测；交付审查随后进一步收紧小红书为侧栏个人入口，并要求账号标记可见，避免把公开作者链接/隐藏元素当作已登录。这项保守收紧未再次实机运行，小红书与其他账号的最新结果以实际重新运行命令为准。

unknown 是账号标记尚不能确认，不等于登录过期；本轮没有自动登录、密码保存或会话导出。唯品会需要用户重新登录/确认保存；NGA 搜索明确要求注册用户且威望大于 0，需用户处理权限。IT之家按要求不测、不接入。

## 保留登录设置

先备份 com.citrolabs.ego.lite defaults，再从当前 App Framework 二进制 strings 核对 ClearBrowsingDataOnExitList、SavingBrowserHistoryDisabled 两个真实策略名。只写入这两个键：空数组、false；重新加载 chrome://policy 后两者状态均为“正常”。设备端网站数据设置选择“允许网站将数据保存在设备上”，退出删除站点列表为空。未重启浏览器做退出/再启动持久性验收，站点自身仍可主动使会话过期。备份留在本机，未提交用户配置和账号数据。

## 验证

离线单测覆盖独立平台并发上限、跨调用时间间隔、URL 白名单和参数清洗、SDK newPage/异常清理、匿名 HTTP 提取、login-status unknown/过期分类和错误脱敏。README、CLI、MCP 工具及包内 .mjs 资源均更新。完整测试与 CI 结果见 PR 检查。

原始搜索页和未脱敏浏览器快照仅留本机；公开证据 JSON 只保留计时、状态和公开结果链接。
