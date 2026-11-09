"""渠道层共用小工具：HTML 清洗与时间转换。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_EM_TAG = re.compile(r"</?em[^>]*>")
_OTHER_TAGS = re.compile(r"<[^>]+>")


def clean_html(text: str) -> str:
    """去掉搜索接口塞进标题/摘要的标签和多余空白。

    `<em>` 是关键词高亮（B站/CSDN/掘金都用它包关键词），去掉时不能补空格，
    否则「英语时态」会被拆成「英语 时态」；其他标签（p/br 等）当分隔符处理。
    """
    plain = _OTHER_TAGS.sub(" ", _EM_TAG.sub("", text or ""))
    return " ".join(plain.split())


def iso_from_epoch(value: object, *, millis: bool = False) -> str | None:
    """Unix 时间戳 → ISO8601（UTC）；解析不了返回 None。"""
    try:
        ts = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if millis:
        ts /= 1000
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def iso_from_feed_date(value: str) -> str | None:
    """RSS 的 RFC822 或 Atom 的 ISO8601 时间 → ISO8601（UTC）。"""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None
