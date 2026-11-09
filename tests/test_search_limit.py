"""Offline regressions for the final search result limit."""
import io
import json
import os
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

from research_mcp import cli, research, service


class SearchLimitTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"title": f"Synthetic {index}", "source": "hacker_news",
             "url": f"https://fixture.invalid/{index}"}
            for index in range(12)
        ]

    def test_final_limit_is_reported_without_successful_reranking(self):
        for mode in ("no_key", "disabled", "error", "downgraded"):
            with self.subTest(mode=mode), ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, {}, clear=True))
                stack.enter_context(patch.object(research, "_default_providers", return_value={
                    "hacker_news": lambda _: self.rows,
                }))
                if mode == "error":
                    stack.enter_context(patch("research_mcp.rerank.filter_candidates",
                                              side_effect=RuntimeError("synthetic failure")))
                elif mode == "downgraded":
                    stack.enter_context(patch("research_mcp.rerank.filter_candidates",
                                              return_value=(self.rows[:5], "judge_unavailable")))
                result = service.search("synthetic", limit=5, providers=["hacker_news"],
                                        rerank=mode != "disabled")
                self.assertEqual(result["status"], "ok")
                self.assertEqual((result["total"], result["pooled"], result["returned"]), (12, 12, 5))
                self.assertEqual([row["url"] for row in result["candidates"]],
                                 [row["url"] for row in self.rows[:5]])
                self.assertTrue(result["truncated"])
                self.assertEqual(result["truncated_by"], ["limit"])

    def test_results_that_fit_do_not_report_truncation(self):
        for count in (0, 4, 5):
            with self.subTest(count=count), patch.dict(os.environ, {}, clear=True), \
                    patch.object(research, "_default_providers", return_value={
                        "hacker_news": lambda _: self.rows[:count],
                    }):
                result = service.search("synthetic", limit=5, providers=["hacker_news"])
                self.assertEqual(result["returned"], count)
                self.assertFalse(result["truncated"])
                self.assertEqual(result["truncated_by"], [])

    def test_cli_json_retains_final_limit_metadata(self):
        stdout = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(research, "_default_providers", return_value={
                    "hacker_news": lambda _: self.rows,
                }), patch.object(sys, "argv", ["research-mcp", "search", "synthetic",
                                               "--limit", "5", "--providers", "hacker_news",
                                               "--format", "json"]), redirect_stdout(stdout):
            exit_code = cli.main()
        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["returned"], 5)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["truncated_by"], ["limit"])
