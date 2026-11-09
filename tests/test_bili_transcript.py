"""B 站音频转写单测：全部离线，不真调 bili / Groq。"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_mcp.tools import bili_transcript as bt


class ParseBvidTest(unittest.TestCase):
    def test_from_url_and_bare_bv(self):
        self.assertEqual(bt.parse_bvid("https://www.bilibili.com/video/BV18UP4efEpf?p=1"), "BV18UP4efEpf")
        self.assertEqual(bt.parse_bvid("BV18UP4efEpf"), "BV18UP4efEpf")
        self.assertIsNone(bt.parse_bvid("https://example.com/x"))


class GroqKeyTest(unittest.TestCase):
    def test_env_takes_priority(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": "k-env"}):
            self.assertEqual(bt.groq_key(), "k-env")

    def test_missing_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(bt.subprocess, "run", side_effect=RuntimeError("不应调用外部命令")):
            self.assertEqual(bt.groq_key(), "")


class TranscriptTest(unittest.TestCase):
    def test_invalid_url(self):
        self.assertEqual(bt.transcript("https://example.com/x")["error"], "invalid_url")

    def test_missing_key(self):
        with patch.object(bt, "groq_key", return_value=""):
            self.assertEqual(bt.transcript("BV18UP4efEpf")["error"], "groq_key_missing")

    def test_happy_path_joins_segments(self):
        fake_wavs = [Path(f"/tmp/s{i}.wav") for i in range(3)]
        with patch.object(bt, "groq_key", return_value="k"), \
                patch.object(bt, "_download_segments", return_value=fake_wavs), \
                patch.object(bt, "_transcribe_segment", side_effect=["第一段", "第二段", "第三段"]):
            result = bt.transcript("BV18UP4efEpf", max_seconds=75)
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "whisper")
        self.assertEqual(result["duration_used"], 75)
        self.assertIn("第二段", result["transcript"])

    def test_partial_marks_failed_segments(self):
        fake_wavs = [Path(f"/tmp/s{i}.wav") for i in range(2)]

        def flaky(path, key, model, index):
            if index == 1:
                raise RuntimeError("该段失败")
            return "只有这段成功"

        with patch.object(bt, "groq_key", return_value="k"), \
                patch.object(bt, "_download_segments", return_value=fake_wavs), \
                patch.object(bt, "_transcribe_segment", side_effect=flaky):
            result = bt.transcript("BV18UP4efEpf", max_seconds=50)
        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["failed_segments"], 1)

    def test_keeps_segment_order_when_later_segment_finishes_first(self):
        """后一段先转完也要按提交顺序拼接——曾用 as_completed 导致文字稿乱序。"""
        fake_wavs = [Path(f"/tmp/s{i}.wav") for i in range(3)]

        def uneven(path, key, model, index):
            if index == 0:
                time.sleep(0.15)  # 第一段故意最慢，逼出乱序
            return f"第{index}段"

        with patch.object(bt, "groq_key", return_value="k"), \
                patch.object(bt, "_download_segments", return_value=fake_wavs), \
                patch.object(bt, "_transcribe_segment", side_effect=uneven):
            result = bt.transcript("BV18UP4efEpf", max_seconds=75)
        self.assertEqual(result["transcript"], "第0段\n第1段\n第2段")

    def test_download_failure(self):
        with patch.object(bt, "groq_key", return_value="k"), \
                patch.object(bt, "_download_segments", side_effect=RuntimeError("视频无音轨")):
            self.assertEqual(bt.transcript("BV18UP4efEpf")["error"], "download_failed")


if __name__ == "__main__":
    unittest.main()
