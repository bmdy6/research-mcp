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

每个渠道均用不带 Cookie/Authorization 的系统 curl 直接请求搜索页（跟随重定向，最大 18 秒）；Python 自带证书链缺失，未关闭 TLS 验证，改用系统 curl。初筛后用 HTMLParser 复核相对链接，避免把 CSS、标签导航或空壳页面当结果。SDK 没有文档化的独立无痕上下文接口，本轮没有使用隔离浏览器复核。

“保守需登录”是目录原有要求或本轮带登录态接入的要求，**不意味着已证实该站禁止匿名浏览器搜索**。HTTP 拦截/JS 空壳不能证明必须登录；这些站点保留浏览器渠道。目录既有 requires_login=false 的渠道保持 false。耗时是匿名 HTTP 探测总耗时，非浏览器耗时；更改后的真实渠道耗时见下表后说明。

| 渠道 | 登录需求 | 实现 | 匿名 HTTP 耗时 | 本轮 HTTP 观察 |
|---|---|---|---:|---|
| xianyu | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.07s | URLError；未证实可直接提取搜索结果 |
| weibo | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.11s | URLError；未证实可直接提取搜索结果 |
| jd | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.08s | URLError；未证实可直接提取搜索结果 |
| taobao | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.04s | URLError；未证实可直接提取搜索结果 |
| tmall | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.03s | URLError；未证实可直接提取搜索结果 |
| smzdm | 不需要（既有登记） | 浏览器 | 0.10s | URLError；未证实可直接提取搜索结果 |
| zhihu | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.06s | URLError；未证实可直接提取搜索结果 |
| xiaohongshu | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.07s | URLError；未证实可直接提取搜索结果 |
| douyin | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.04s | URLError；未证实可直接提取搜索结果 |
| sogou_wechat | 不需要（HTTP 实证） | HTTP | 0.09s | 可读结果，已转 HTTP |
| boss | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.12s | URLError；未证实可直接提取搜索结果 |
| liepin | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.11s | URLError；未证实可直接提取搜索结果 |
| hupu | 不需要（既有登记） | 浏览器 | 0.07s | URLError；未证实可直接提取搜索结果 |
| kr36 | 不需要（既有登记） | 浏览器 | 0.07s | URLError；未证实可直接提取搜索结果 |
| amazon | 不需要（既有登记） | 浏览器 | 4.50s | 200；未证实可直接提取搜索结果 |
| douban | 不需要（既有登记） | 浏览器 | 0.11s | URLError；未证实可直接提取搜索结果 |
| youtube | 不需要（既有登记） | 浏览器 | 62.82s | URLError；未证实可直接提取搜索结果 |
| x | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.22s | URLError；未证实可直接提取搜索结果 |
| medium | 不需要（既有登记） | 浏览器 | 0.22s | URLError；未证实可直接提取搜索结果 |
| devto | 不需要（既有登记） | 浏览器 | 1.27s | URLError；未证实可直接提取搜索结果 |
| segmentfault | 不需要（HTTP 实证） | HTTP | 0.10s | 可读结果，已转 HTTP |
| pypi | 不需要（既有登记） | 浏览器 | 1.16s | URLError；未证实可直接提取搜索结果 |
| gitee | 不需要（既有登记） | 浏览器 | 0.13s | URLError；未证实可直接提取搜索结果 |
| mafengwo | 不需要（既有登记） | 浏览器 | 0.12s | URLError；未证实可直接提取搜索结果 |
| sspai | 不需要（既有登记） | 浏览器 | 0.10s | URLError；未证实可直接提取搜索结果 |
| suning | 不需要（HTTP 实证） | HTTP | 0.06s | 可读结果，已转 HTTP |
| huxiu | 不需要（既有登记） | 浏览器 | 2.25s | URLError；未证实可直接提取搜索结果 |
| lobsters | 不需要（既有登记） | 浏览器 | 21.27s | URLError；未证实可直接提取搜索结果 |
| maimai | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.17s | 200；未证实可直接提取搜索结果 |
| dianping | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.30s | 200；未证实可直接提取搜索结果 |
| tieba | 保守需登录，匿名浏览器待确认 | 浏览器 | 0.18s | 403；未证实可直接提取搜索结果 |

匿名 HTTP 实际渠道验收：苏宁 0.27 秒、搜狗微信 0.16 秒、思否 0.55 秒，均 status=ok。HTTP 200 本身不算结果证据；只接受平台规范结果路径并清除跟踪参数。搜狗只保留公开跳转 url 参数，不返回 token。

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
