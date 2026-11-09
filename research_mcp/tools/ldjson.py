"""抽取页面里挂在图表上的数值：给「正文只剩图注」的页面兜底。

Jina 只能拿到渲染后的正文，抓不到挂在图表上的数值。评测站/榜单/看板把数字
放在页面**内嵌的结构化载荷**里，正文往往只剩 “higher is better” 这类图注：

- 标准做法：`<script type="application/ld+json">` 里带 schema.org `Dataset` 的 `data[]`
- Next.js 的 App Router：数据在流式载荷里，形如
  `\"data\":[{\"label\":\"...\",\"value\":1.85,...}]`，JSON 被整段转义

两种都抽。抽到的是「谁在哪个指标上是什么值」，比判成「需要浏览器」有用得多。
"""

from __future__ import annotations

import html
import json
import re

# 图表页特征：正文里图注文案占比明显偏高——图表的说明都渲染出来了，数值却挂图上。
# 光看绝对条数不够：正常评测/定价文章也会多次提到 “higher is better”。
CHART_ANNOTATION_RE = re.compile(
    r"(?:higher|lower)\s+is\s+better|log\s+scale|per\s+1M\s+tokens|USD\s+per",
    re.I,
)
MIN_CHART_ANNOTATIONS = 3
MIN_ANNOTATION_LINE_RATIO = 0.10

_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
# Next.js 流式载荷里的数组：JSON 被转义成 \" 和 \\n，值形如 {"label":..,"value":..}
_FLIGHT_ARRAY_RE = re.compile(r'\\"data\\":\[(.*?)\](?=,\\"|,\\"|$)', re.S)
_FLIGHT_OBJECT_RE = re.compile(r'\{\\"id\\".*?\}')
# 图表标题通常是数组前面最近的一个短 children 字符串（"Intelligence" / "Cost per Task"）
_FLIGHT_TITLE_RE = re.compile(r'\\"children\\":\\"([^\\"]{2,120})\\"')


def looks_like_chart_page(text: str) -> bool:
    """正文是不是「只剩图注、没带出数值」的图表页。

    判定用「图注条数」加「图注占正文行数的比例」两个条件：只看条数会误伤正常
    评测/定价文章（它们也会多次提到 “higher is better”）。实测 AA 对比页
    比例 0.150，AA 长篇文章 0.034，正常长文里出现 3 次图注也只到 0.091。
    """
    body = text.split("Markdown Content:", 1)[-1]
    annotations = len(CHART_ANNOTATION_RE.findall(body))
    if annotations < MIN_CHART_ANNOTATIONS:
        return False
    lines = [line for line in body.splitlines() if line.strip()]
    return annotations / max(len(lines), 1) >= MIN_ANNOTATION_LINE_RATIO


def extract_chart_datasets(raw_html: str) -> list[tuple[str, list[dict]]]:
    """抽取页面里带标签和值的结构化数据集，返回 [(名称, 行列表)]。

    名称取不回来时用 `数据组 N` 占位——有值没标题也比整条丢掉强。
    """
    datasets: list[tuple[str, list[dict]]] = []
    for name, rows in _from_ldjson(raw_html):
        datasets.append((name, rows))
    for index, (name, rows) in enumerate(_from_flight_payload(raw_html), start=1):
        datasets.append((name or f"内嵌图表数据组 {index}", rows))
    return datasets


def _from_ldjson(raw_html: str) -> list[tuple[str, list[dict]]]:
    """标准 JSON-LD 数据集（schema.org Dataset + data[]）。"""
    found: list[tuple[str, list[dict]]] = []
    for match in _SCRIPT_RE.finditer(raw_html):
        payload = _parse_json(match.group(1))
        for block in payload if isinstance(payload, list) else [payload]:
            if not isinstance(block, dict):
                continue
            data = block.get("data")
            if not isinstance(data, list):
                continue
            rows = _rows_from_entries(data)
            if rows:
                found.append((str(block.get("name") or "Dataset"), rows))
    return found


def _from_flight_payload(raw_html: str) -> list[tuple[str, list[dict]]]:
    """Next.js 流式载荷：转义 JSON 里的 {\"label\":..,\"value\":..} 数组。"""
    groups: list[tuple[str, list[dict]]] = []
    for match in _FLIGHT_ARRAY_RE.finditer(raw_html):
        rows: list[dict] = []
        for obj_match in _FLIGHT_OBJECT_RE.finditer(match.group(1)):
            entry = _parse_json(obj_match.group(0))
            if not isinstance(entry, dict):
                continue
            label = entry.get("label") or entry.get("name")
            value = entry.get("displayValue") or entry.get("value")
            if label is None or value is None:
                continue
            rows.append({"label": str(label), "values": {"value": value}})
        if rows:
            groups.append((_nearest_title(raw_html, match.start()), rows))
    return groups


def _nearest_title(raw_html: str, position: int, *, window: int = 2000) -> str:
    """取数组前面最近的图表标题；取不到就用占位名，有值没标题也比丢掉强。"""
    names = _FLIGHT_TITLE_RE.findall(raw_html[max(0, position - window):position])
    if names and _looks_like_title(names[-1]):
        return names[-1].strip()
    return "内嵌图表数据（标题未取到）"


def _looks_like_title(text: str) -> bool:
    """标题是短名词短语；长的多是说明段，不当标题。"""
    stripped = text.strip()
    if not 2 <= len(stripped) <= 80:
        return False
    return " " in stripped or stripped[0].isupper()


def _rows_from_entries(data: list) -> list[dict]:
    """把 data[] 条目压成 {label, values}。"""
    rows: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label") or entry.get("name")
        if not label:
            continue
        values = {
            key: value
            for key, value in entry.items()
            if key not in ("label", "name", "detailsUrl", "url", "@type")
        }
        if values:
            rows.append({"label": str(label), "values": values})
    return rows


def _parse_json(text: str):
    """解析可能带转义、HTML 实体或尾逗号的 JSON 片段。"""
    candidates = [html.unescape(text).strip()]
    unescaped = candidates[0].replace('\\"', '"').replace("\\\\", "\\")
    if unescaped != candidates[0]:
        candidates.append(unescaped)
    for candidate in candidates:
        if not candidate:
            continue
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                return json.loads(attempt)
            except ValueError:
                continue
    return None


def render_chart_datasets(datasets: list[tuple[str, list[dict]]], *, per_set: int = 40) -> str:
    """把数据集渲染成 Markdown 表格，供正文拼接。"""
    parts: list[str] = []
    for name, rows in datasets:
        columns: list[str] = []
        for row in rows:
            for key in row["values"]:
                if key not in columns:
                    columns.append(key)
        if not columns:
            continue
        lines = [
            f"### {name}",
            "",
            "| " + " | ".join(["label", *columns]) + " |",
            "| " + " | ".join(["---"] * (len(columns) + 1)) + " |",
        ]
        for row in rows[:per_set]:
            cells = [_format_value(row["values"].get(column)) for column in columns]
            lines.append("| " + " | ".join([row["label"], *cells]) + " |")
        if len(rows) > per_set:
            lines.append(f"\n（共 {len(rows)} 行，这里只列前 {per_set} 行）")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _format_value(value) -> str:
    """嵌套值（如 Elo 的 mid/lower/upper）压成一行，不铺成多层表格。"""
    if value is None:
        return ""
    if isinstance(value, dict):
        return ", ".join(f"{key}={_format_value(inner)}" for key, inner in value.items())
    if isinstance(value, list):
        return "; ".join(_format_value(item) for item in value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)
