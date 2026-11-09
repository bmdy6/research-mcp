"""调研渠道注册表。

每个渠道模块暴露 `search(config) -> list[dict]`，返回 research 统一 item 结构：
`{kind, title, url, published_at, source, summary, ...}`。

约定：
- 没配置查询词返回 `[]`；
- 渠道不可用（外部命令缺失、上游改格式、被风控）就抛异常，由 搜索入口
  记为 unavailable 并继续跑其他渠道，不在这里静默吞掉。
- 单个平台内部多源时，单源失败只跳过该源。
"""

from .bilibili import search as bilibili_search
from .exa import search as exa_search
from .juejin import search as juejin_search

CHANNELS = {
    "bilibili": bilibili_search,
    "exa": exa_search,
    "juejin": juejin_search,
}
