"""Bilibili audio transcription through an optional environment API key."""
from __future__ import annotations

import json
import os
import re
import subprocess
from research_mcp.commands import run as run_command
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
SEGMENT_SECONDS = 25  # 与 bili audio 的默认段长一致
SEGMENT_CURL_TIMEOUT = 45  # 单段转写的 curl 上限（Groq 单段一般 1~3 秒）
_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")


def parse_bvid(value: str) -> str | None:
    """从 BV 号或视频链接提取 BV 号。"""
    match = _BV_RE.search(value or "")
    return match.group(0) if match else None


def groq_key() -> str:
    """Read the optional API key only from the environment."""
    return os.environ.get("GROQ_API_KEY", "").strip()


def _download_segments(bvid: str, out_dir: Path, timeout: int) -> list[Path]:
    """bili audio 下载音频并按 25 秒切段，返回排好序的 WAV 列表。"""
    result = run_command(
        ["bili", "audio", bvid, "--segment", str(SEGMENT_SECONDS), "-o", str(out_dir)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "bili audio 下载失败").strip()[:200])
    wavs = sorted(out_dir.rglob("*.wav"))
    if not wavs:
        raise RuntimeError("bili audio 未产出音频段（视频可能无音轨或需要登录）")
    return wavs


def _transcribe_segment(path: Path, key: str, model: str, index: int) -> str:
    """转写单段，失败重试 1 次（0.5 秒退避），仍失败返回空串由调用方兜底。"""
    command = [
        "curl", "-sS", "--max-time", str(SEGMENT_CURL_TIMEOUT), "-X", "POST",
        "-H", f"Authorization: Bearer {key}",
        "-F", f"file=@{path}",
        "-F", f"model={model}",  # 不传 language，让 whisper 自己识别中英文
        GROQ_URL,
    ]
    last_error = ""
    for attempt in (1, 2):
        result = run_command(command, capture_output=True, text=True, timeout=SEGMENT_CURL_TIMEOUT + 5)
        if result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
                if "text" in payload:
                    return (payload.get("text") or "").strip()
                last_error = str(payload)[:150]
            except json.JSONDecodeError:
                last_error = result.stdout[:150]
        else:
            last_error = (result.stderr or "").strip()[:150]
        if attempt == 1:
            time.sleep(0.5)
    raise RuntimeError(f"第 {index} 段转写失败：{last_error}")


def transcript(
    url_or_bvid: str,
    *,
    max_seconds: int = 300,
    concurrency: int = 4,
    max_chars: int = 30000,
    total_timeout: int = 150,
    download_timeout: int = 240,
) -> dict:
    """把 B 站视频的前 N 秒音频转成文字稿。

    Args:
        url_or_bvid: 视频链接或裸 BV 号
        max_seconds: 只转前 N 秒（默认 300）
        concurrency: 并发转写段数
        max_chars: 文字稿上限
        total_timeout: 转写总预算（秒），超时返回 timeout
        download_timeout: 下载音频限制（秒）
    """
    bvid = parse_bvid(url_or_bvid)
    if not bvid:
        return {"ok": False, "error": "invalid_url"}
    key = groq_key()
    if not key:
        return {
            "ok": False,
            "error": "groq_key_missing",
            "hint": "设置 GROQ_API_KEY 环境变量",
        }

    with tempfile.TemporaryDirectory() as tmp:
        try:
            wavs = _download_segments(bvid, Path(tmp), download_timeout)
        except Exception as exc:
            return {"ok": False, "error": "download_failed", "detail": str(exc)[:200]}

        kept = wavs[: max(1, max_seconds // SEGMENT_SECONDS)]
        pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
        futures = [pool.submit(_transcribe_segment, path, key, GROQ_MODEL, i) for i, path in enumerate(kept)]
        # 必须按提交顺序取结果：as_completed 是按完成顺序返回的，直接 append 会把文字稿拼乱
        done, pending = wait(futures, timeout=total_timeout)
        texts: list[str] = []
        failed = 0
        for future in futures:
            if future in done:
                try:
                    texts.append(future.result())
                except Exception:
                    failed += 1
            else:
                failed += 1
        pool.shutdown(wait=False, cancel_futures=True)
        if not texts:
            return {"ok": False, "error": "timeout" if pending else "transcribe_failed", "failed_segments": failed}

        content = "\n".join(t for t in texts if t).strip()
        if not content:
            return {"ok": False, "error": "transcribe_failed", "failed_segments": failed}

        result = {
            "ok": True,
            "source": "whisper",
            "bvid": bvid,
            "transcript": content[:max_chars],
            "truncated": len(content) > max_chars,
            "char_count": min(len(content), max_chars),
            "duration_used": len(kept) * SEGMENT_SECONDS,
        }
        if failed:
            result["partial"] = True
            result["failed_segments"] = failed
        return result
