"""Offline result envelopes and bounded JSON. No transport, credentials or I/O.

`returned` is the upstream count; `delivered` is the post-budget candidate count.
`truncated_by` keeps search semantics; `output_truncation` describes serialization.
"""
from __future__ import annotations

import copy
import json
import math
from collections import Counter
from dataclasses import dataclass

LIST_FIELDS = ("source", "title", "url", "author", "play", "views", "published_at", "summary", "jev_relevance", "jev_keep")
FETCH_FIELDS = ("hint", "object_type", "file", "discussion", "coverage", "coverage_note", "kind", "url", "ok", "repo", "content", "text", "title", "via", "language", "duration", "duration_seconds", "truncated", "transcript_error", "error", "retryable", "transcript", "source", "bvid", "char_count", "duration_used", "partial", "failed_segments")
EXA_X_EMPTY_NOTE = (
    "本次包含 X/Twitter site: 限定的 Exa 查询成功，但没有返回结果；"
    "这只说明此检索路径未命中，不能据此断言 X 上没有相关讨论。"
)


@dataclass(frozen=True)
class ResultBudget:
    max_chars: int = 200_000
    field_chars: int = 8_000
    max_items: int = 200

    def __post_init__(self):
        if any(type(value) is not int for value in (self.max_chars, self.field_chars, self.max_items)):
            raise ValueError("Budgets must be integers")
        if self.max_chars < 1024 or self.field_chars < 32 or self.max_items < 1:
            raise ValueError("Budget minima: max_chars=1024, field_chars=32, max_items=1")


def error_result(operation: str, code: str, message: str) -> dict:
    return {"api_version": "1", "operation": operation, "status": "error", "ok": False,
            "errors": [{"code": code, "message": message}], "display_notes": []}


def search_result(payload: object) -> dict:
    if not isinstance(payload, dict):
        return error_result("search", "INVALID_RESULT", "搜索结果格式无效")
    if not isinstance(payload.get("query"), str):
        return error_result("search", "INVALID_RESULT", "查询字段格式无效")
    candidates = payload.get("candidates")
    failed = payload.get("failed_channels", [])
    attempted = payload.get("attempted_channels")
    succeeded = payload.get("succeeded_channels")
    notes = payload.get("display_notes", [])
    filtered = payload.get("filtered_by", {})
    if (not isinstance(candidates, list) or any(not isinstance(item, dict) for item in candidates)
            or not isinstance(failed, list) or any(not isinstance(item, dict) for item in failed)
            or not isinstance(attempted, list) or not isinstance(succeeded, list)
            or any(not isinstance(name, str) for name in attempted + succeeded)
            or not isinstance(notes, list) or any(not isinstance(note, str) for note in notes)
            or not isinstance(filtered, dict)
            or any(key != "outside_time_range" or type(count) is not int or count < 1
                   for key, count in filtered.items())
            or not set(succeeded).issubset(attempted)):
        return error_result("search", "INVALID_RESULT", "缺少有效候选或渠道执行证据，不能视为成功空结果")
    if (len(set(attempted)) != len(attempted) or len(set(succeeded)) != len(succeeded)
            or type(payload.get("truncated")) is not bool
            or ("rerank_downgraded" in payload and not isinstance(payload["rerank_downgraded"], str))):
        return error_result("search", "INVALID_RESULT", "渠道或裁剪元数据格式无效")
    failed_names = [item.get("channel") for item in failed]
    if (any(not isinstance(name, str) for name in failed_names)
            or len(set(failed_names)) != len(failed_names)
            or set(failed_names) != set(attempted) - set(succeeded)
            or not isinstance(payload.get("truncated_by", []), list)
            or any(not isinstance(reason, str) for reason in payload.get("truncated_by", []))):
        return error_result("search", "INVALID_RESULT", "渠道结果或裁剪原因不完整")
    for key in ("total", "pooled", "returned"):
        if type(payload.get(key)) is not int or payload[key] < 0:
            return error_result("search", "INVALID_RESULT", "候选数量格式无效")
    if payload["returned"] != len(candidates) or not payload["returned"] <= payload["pooled"] <= payload["total"]:
        return error_result("search", "INVALID_RESULT", "候选数量与返回记录不一致")
    if not attempted:
        status, code = "error", "NO_QUERY_CHANNELS"
    elif not succeeded:
        status, code = "error", "ALL_CHANNELS_FAILED"
    else:
        status, code = ("partial" if failed else "ok" if payload["total"] > 0 else "empty"), None
    result = {"api_version": "1", "operation": "search", "status": status, "ok": status != "error",
              "query": payload.get("query", ""), "candidates": [{k: item[k] for k in LIST_FIELDS if item.get(k) not in (None, "")} for item in candidates],
              "total": payload["total"], "pooled": payload["pooled"], "returned": payload["returned"],
              "truncated": bool(payload.get("truncated")), "truncated_by": payload.get("truncated_by", []),
              "attempted_channels": attempted[:], "succeeded_channels": succeeded[:],
              "attempted_channels_count": len(attempted), "succeeded_channels_count": len(succeeded),
              "failed_channels": [{"channel": item.get("channel", "unknown"), "error": item.get("error", "unknown")} for item in failed],
              "failed_channels_count": len(failed), "errors": [], "display_notes": []}
    if code:
        result["errors"].append({"code": code, "message": "没有渠道成功完成查询"})
    if failed:
        result["display_notes"].append("失败渠道未提供完整结果，不能据此断言没有相关内容。")
    if payload.get("rerank") == "skipped_no_key":
        result["rerank"] = "skipped_no_key"
    if payload.get("rerank_downgraded"):
        result["rerank_downgraded"] = payload["rerank_downgraded"]
        result["display_notes"].append("相关性重排已降级。")
    if filtered:
        result["filtered_by"] = dict(filtered)
        result["display_notes"].append(
            f"{filtered['outside_time_range']} 个结果超出请求的时间窗，已在候选截断前排除。")
    if payload["total"] > 0 and not candidates:
        result["display_notes"].append("渠道找到过候选，但筛选后没有保留条目，不等于上游没有结果。")
    if result["truncated"]:
        result["display_notes"].append("搜索结果已按 truncated_by 中的原因裁剪；与输出预算裁剪分别记录。")
    # Only transport-owned, fixed notes may pass through. Never echo arbitrary
    # provider text; a coverage caveat is not an error or a failed channel.
    if "exa" in succeeded and EXA_X_EMPTY_NOTE in notes:
        result["display_notes"].append(EXA_X_EMPTY_NOTE)
    return result


def fetch_result(payload: object) -> dict:
    if not isinstance(payload, dict):
        return error_result("fetch", "INVALID_RESULT", "读取结果格式无效")
    if any(key in payload and type(payload[key]) is not bool for key in ("ok", "partial", "retryable")):
        return error_result("fetch", "INVALID_RESULT", "读取成功标记格式无效")
    failed = payload.get("ok") is False
    empty_file = payload.get("object_type") == "file" and isinstance(payload.get("content"), str)
    if not failed and not empty_file and not any(isinstance(payload.get(key), str) and payload[key].strip() for key in ("content", "text", "transcript")):
        return error_result("fetch", "INVALID_RESULT", "读取没有返回正文，不能视为成功")
    # fetch_url falls back to page text when video transcription is unavailable.
    # Preserve the useful page, while making the missing transcript visible to
    # clients that use the envelope status rather than inspect provider fields.
    transcript_unavailable = bool(payload.get("transcript_error"))
    partial = bool(payload.get("partial")) or transcript_unavailable
    result = {"api_version": "1", "operation": "fetch", "status": "error" if failed else "partial" if partial else "ok", "ok": not failed,
              "data": {key: payload[key] for key in FETCH_FIELDS if key in payload},
              "errors": [], "display_notes": []}
    if failed:
        result["errors"].append({"code": "READ_FAILED", "message": payload.get("error") or "读取失败", "retryable": bool(payload.get("retryable"))})
    elif transcript_unavailable:
        result["errors"].append({"code": "TRANSCRIPT_UNAVAILABLE", "message": "转写不可用，当前仅保留回退读取的网页内容"})
        result["display_notes"].append("网页正文或简介不等于视频文字稿；视频内容仍未完整读取。")
    return result


def serialize_result(result: dict, budget: ResultBudget = ResultBudget()) -> str:
    """Clip structured fields/entries then serialize whole JSON, never a JSON slice."""
    reasons = Counter()

    def bounded(value, depth=0):
        if depth > 8:
            reasons["depth_limit"] += 1
            return None
        if isinstance(value, str):
            if len(value) > budget.field_chars:
                reasons["field_chars"] += 1
                return value[:budget.field_chars]
            return value
        if value is None or type(value) is bool:
            return value
        if type(value) is int:
            if value.bit_length() > 1024:
                reasons["invalid_value"] += 1
                return None
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if isinstance(value, list):
            if len(value) > budget.max_items:
                reasons["item_limit"] += len(value) - budget.max_items
            return [bounded(item, depth + 1) for item in value[:budget.max_items]]
        if isinstance(value, dict):
            normalized = {}
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > budget.field_chars:
                    reasons["invalid_value"] += 1
                    continue
                normalized[key] = bounded(item, depth + 1)
            return normalized
        reasons["invalid_value"] += 1
        return None

    if not isinstance(result, dict):
        result = error_result("unknown", "INVALID_RESULT", "结果必须是对象")
    output = bounded(copy.deepcopy(result))
    if reasons.get("invalid_value") or reasons.get("depth_limit"):
        output["status"] = "error"
        output["ok"] = False
        output.setdefault("errors", []).append({"code": "INVALID_RESULT", "message": "结果包含无法表示的值"})
    def render():
        if "candidates" in output:
            output["delivered"] = len(output["candidates"])
        output["output_truncation"] = {"truncated": bool(reasons), "reasons": dict(sorted(reasons.items()))}
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    text = render()
    while len(text) > budget.max_chars:
        # Prefer shrinking long explanatory text before dropping complete items.
        leaves = []
        def strings(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "output_truncation":
                        continue
                    if isinstance(item, str) and len(item) > 64:
                        leaves.append((len(item), value, key))
                    elif isinstance(item, (dict, list)):
                        strings(item)
            elif isinstance(value, list):
                for key, item in enumerate(value):
                    if isinstance(item, str) and len(item) > 64:
                        leaves.append((len(item), value, key))
                    elif isinstance(item, (dict, list)):
                        strings(item)
        strings(output)
        if leaves:
            _, parent, key = max(leaves, key=lambda item: item[0])
            parent[key] = parent[key][:max(64, len(parent[key]) // 2)]
            reasons["output_field_budget"] += 1
        elif output.get("candidates"):
            output["candidates"].pop()
            reasons["output_candidate_budget"] += 1
        else:
            # Preserve status/counts/error codes; shorten explanatory arrays only.
            arrays = [(key, output[key]) for key in ("display_notes", "failed_channels", "attempted_channels", "succeeded_channels") if output.get(key)]
            if arrays:
                arrays[0][1].pop()
                reasons["output_metadata_budget"] += 1
            else:
                # An abnormal deeply nested payload is an explicit contract error.
                output = error_result(result.get("operation", "unknown"), "OUTPUT_BUDGET_EXCEEDED", "结果无法在输出预算内完整表示")
                reasons["output_budget_error"] += 1
        text = render()
    return text
