"""Jev 语义筛选模块：在搜索返回前让 Jev 直接判断哪些条目值得保留。

Jev 是 TypeSafe AI 的 System One 决策模型，输出的是带概率的判定而不是文本。
这里用的是官方推荐的 speculative fan-out：整池候选放进同一个 state，一次请求并行问
N 个 boolean（“这条值得保留吗”），拿回概率后由代码决定去留。

相比逐条打分的旧实现：从 N 次 HTTP 降到 1 次，返回条数真的减少（下游 LLM 只读留下来的），
而不是给每条附一个分数让 LLM 自己再判断一遍。

无 API key、网络超时或调用异常时降级为原始候选，并通过降级原因让调用方看得见。
"""

from __future__ import annotations

import concurrent.futures
import os


from research_mcp.research import _http_json

JEV_ENDPOINT = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
JEV_MODEL_ID = "typesafe-ai/jev"
def get_ai_gateway_key() -> str:
    """Read the optional API key only from the environment."""
    return os.environ.get("AI_GATEWAY_API_KEY", "").strip()


def _jev_headers(api_key: str) -> dict[str, str]:
    """构造 Jev 评估端点所需请求头。"""
    return {
        "ai-evaluation-model-specification-version": "4",
        "ai-gateway-protocol-version": "0.0.1",
        "ai-model-id": JEV_MODEL_ID,
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }


def _describe_candidate(index: int, item: dict) -> str:
    """把一条候选项压成一行 state 文本：编号 + 来源 + 标题 + 摘要。"""
    source = item.get("source") or "unknown"
    title = (item.get("title") or "").strip()
    summary = " ".join((item.get("summary") or "").split())[:150]
    line = f"{index}. [{source}] {title}"
    return f"{line}\n   摘要: {summary}" if summary else line


def _judge_batch(
    query: str,
    candidates: list[dict],
    api_key: str,
) -> list[float] | None:
    """对一批候选发一次 Jev 请求，返回按序对应的保留概率。"""
    questions = {
        f"keep_{i}": {
            "type": "boolean",
            "instructions": (
                f"第 {i} 条对技术调研查询「{query}」是否具备实质参考价值，"
                "值得保留给工程师/AI 深读？只保留有具体架构设计、技术实现、代码、"
                "实操复盘或核心原理的内容；泛概念介绍、纯工具安装、卖课引流广告一律判否。"
            ),
        }
        for i in range(1, len(candidates) + 1)
    }
    state = f"搜索目标: {query}\n\n候选内容:\n" + "\n".join(
        _describe_candidate(i, item) for i, item in enumerate(candidates, 1)
    )

    try:
        data = _http_json(
            JEV_ENDPOINT,
            method="POST",
            payload={"state": state, "questions": questions},
            headers=_jev_headers(api_key),
        )
    except Exception:
        return None

    answers = data.get("answers") or {}
    probabilities: list[float] = []
    for i in range(1, len(candidates) + 1):
        raw = (answers.get(f"keep_{i}") or {}).get("probability")
        if raw is None:
            return None
        probabilities.append(float(raw))
    return probabilities


def judge_candidates(
    query: str,
    candidates: list[dict],
    api_key: str,
    *,
    batch_size: int = 12,
    max_workers: int = 4,
) -> list[float] | None:
    """让 Jev 判断每条候选是否值得保留，返回与输入同序的概率列表。

    官方 jaggedness 文档指出 state 里无关内容越多准确率越差，所以要分批：每批最多
    batch_size 条（实测超过 12 条后偏差明显变大）。各批并行发出，延迟不随批数线性增长。
    任何一批失败都返回 None，由上层统一降级，避免部分打分的批次混进完整结果。
    """
    if not candidates:
        return []

    batches = [
        candidates[i : i + batch_size] for i in range(0, len(candidates), batch_size)
    ]

    if len(batches) == 1:
        return _judge_batch(query, batches[0], api_key)

    results: list[list[float] | None] = [None] * len(batches)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(batches), max_workers)
    ) as executor:
        future_to_index = {
            executor.submit(_judge_batch, query, batch, api_key): index
            for index, batch in enumerate(batches)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                results[index] = future.result()
            except Exception:
                results[index] = None

    if any(result is None for result in results):
        return None
    return [prob for result in results for prob in result]


def filter_candidates(
    query: str,
    candidates: list[dict],
    *,
    limit: int = 10,
    min_keep_prob: float = 0.35,
) -> tuple[list[dict], str | None]:
    """让 Jev 判断候选去留，只返回它判定值得保留的条目。

    返回 (候选列表, 降级原因)。降级原因非空时说明这批结果没经过 Jev 判断，
    下游必须能区分「Jev 都留下了」和「Jev 根本没跑」。

    阈值 0.35 来自本地标注数据标定（research/tests/eval_jev_threshold.py，192 条候选、
    8 个查询）：F1 0.851，准确率 0.885，优于 0.5 的 F1 0.812。Jev 对中文偏保守，
    漏掉该留的比放进来该弃的多，所以阈值比直觉值低。
    """
    api_key = get_ai_gateway_key()
    if not api_key:
        return candidates[:limit], "skipped_no_key"
    if not candidates:
        return [], None

    probabilities = judge_candidates(query, candidates, api_key)
    if probabilities is None:
        return candidates[:limit], "rerank_all_failed"

    scored = [
        ({**item, "jev_keep": round(prob, 2)}, prob)
        for item, prob in zip(candidates, probabilities)
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    kept = [item for item, prob in scored if prob >= min_keep_prob]

    # Jev 全判否时不能返回空结果：保留概率最高的前 limit 条，让下游自己判断
    if not kept:
        kept = [item for item, _ in scored[:limit]]
    return kept[:limit], None
