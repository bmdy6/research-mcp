"""Export-specific regressions: keys, dependency isolation and safe URL reads."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from research_mcp import commands, research, service
from research_mcp import rerank
from research_mcp.channels import exa
from research_mcp.tools import bili_transcript


class ExportTest(unittest.TestCase):
    def test_environment_keys_only_and_no_key_is_normal(self):
        item = {"title": "Synthetic", "source": "hacker_news", "url": "https://example.com/1"}
        with patch.dict(os.environ, {}, clear=True), patch.object(research, "_default_providers", return_value={"hacker_news": lambda _: [item]}), patch.object(rerank, "judge_candidates") as judge, patch.object(subprocess, "run", side_effect=AssertionError("no subprocess needed")):
            self.assertEqual(rerank.get_ai_gateway_key(), "")
            self.assertEqual(exa.exa_api_key(), "")
            self.assertEqual(bili_transcript.groq_key(), "")
            result = service.search("synthetic")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rerank"], "skipped_no_key")
        self.assertNotIn("rerank_downgraded", result)
        self.assertNotIn("相关性重排已降级。", result["display_notes"])
        judge.assert_not_called()

    def test_missing_commands_retain_safe_install_hints(self):
        for name, hint in commands.HINTS.items():
            with self.subTest(name=name), patch.object(subprocess, "run", side_effect=FileNotFoundError):
                with self.assertRaisesRegex(RuntimeError, "安装") as caught:
                    commands.run([name])
                self.assertEqual(service._safe_failure_reason(str(caught.exception)), hint)

    def test_login_failure_is_unavailable_without_breaking_other_provider(self):
        def failed(_):
            commands.run(["gh"], capture_output=True, text=True)
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "not logged in")), patch.object(research, "_default_providers", return_value={"github_search": failed, "hacker_news": lambda _: []}):
            result = service.search("synthetic", rerank=False)
        self.assertEqual(result["status"], "partial")
        self.assertIn("gh auth login", result["failed_channels"][0]["error"])
        self.assertEqual(result["succeeded_channels"], ["hacker_news"])

    def test_private_url_is_blocked_before_any_reader_or_transcription(self):
        with patch.object(research, "_curl_text") as curl:
            result = service.fetch("https://127.0.0.1/private")
        self.assertEqual(result["status"], "error")
        curl.assert_not_called()

    def test_video_fallback_retains_key_hint(self):
        with patch.object(research, "_validate_fetch_url"), patch.object(research, "_readable_content", return_value=("Synthetic title and description", "jina")), patch.object(bili_transcript, "groq_key", return_value=""):
            result = service.fetch("https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertEqual(result["status"], "partial")
        self.assertIn("GROQ_API_KEY", result["data"]["hint"])

    def test_arxiv_is_opt_in(self):
        with patch.object(research, "_default_providers", return_value={"arxiv": lambda _: [], "hacker_news": lambda _: []}):
            self.assertEqual(research.search("q", rerank=False)["attempted_channels"], ["hacker_news"])
            self.assertEqual(research.search("q", providers=["arxiv"], rerank=False)["attempted_channels"], ["arxiv"])
