"""Catalog and free-search policy tests; no upstream calls or credentials."""
import json
import os
import unittest
from unittest.mock import patch

from research_mcp import research, service
from research_mcp import channel_catalog as catalog
from research_mcp.channels.errors import ChannelError


class CatalogTests(unittest.TestCase):
    def setUp(self):
        catalog.reset_observations()
        self.addCleanup(catalog.reset_observations)

    def test_catalog_is_offline_and_never_exposes_keys(self):
        with patch.dict(os.environ, {"EXA_API_KEY": "SECRET-paid", "TAVILY_API_KEY": "SECRET-free"}), \
                patch.object(research, "_default_providers") as calls:
            result = service.list_channels()
        calls.assert_not_called()
        self.assertTrue(result["ok"])
        rows = {row["id"]: row for row in result["channels"]}
        for name in ("baidu", "sogou", "bing", "duckduckgo", "exa", "tavily", "openalex"):
            row = rows[name]
            for field in ("cost_class", "requires_key", "requires_login", "languages", "content_type",
                          "quota_remaining", "status", "last_checked_at", "license", "actual_backend"):
                self.assertIn(field, row)
        self.assertIsNone(rows["exa"]["last_checked_at"])
        self.assertFalse(rows["exa"]["requires_key"])
        self.assertTrue(rows["tavily"]["implemented"])
        self.assertNotIn("SECRET", json.dumps(result))

    def test_missing_selection_uses_named_free_defaults(self):
        for providers in (None, []):
            with patch.dict(os.environ, {}, clear=True), patch.object(research, "_default_providers", return_value={name: lambda _: [] for name in ("bing", "baidu", "exa")}):
                result = service.search("test", providers=providers)
            self.assertTrue(result["used_default_providers"])
            self.assertEqual(result["attempted_channels"], ["bing", "baidu", "exa"])
            self.assertTrue(any("默认" in n for n in result["display_notes"]))

    def test_configured_defaults_and_explicit_override(self):
        with patch.dict(os.environ, {"RESEARCH_DEFAULT_PROVIDERS": "hacker_news,hacker_news"}), patch.object(research, "_default_providers", return_value={"hacker_news": lambda _: []}):
            self.assertEqual(service.search("test")["attempted_channels"], ["hacker_news"])
            result = service.search("test", providers=["other"])
            self.assertFalse(result["used_default_providers"])
            self.assertEqual(result["attempted_channels"], ["other"])

    def test_blank_default_env_restores_builtin_group(self):
        with patch.dict(os.environ, {"RESEARCH_DEFAULT_PROVIDERS": " , "}):
            self.assertEqual(research.default_search_providers(), ["bing", "baidu", "exa"])

    def test_selected_channels_record_empty_and_safe_failure_separately(self):
        def broken(_):
            raise ChannelError("blocked")
        with patch.object(research, "_default_providers", return_value={"exa": broken, "hacker_news": lambda _: []}):
            result = service.search("test", providers=["exa", "hacker_news", "exa"])
        self.assertEqual(result["status"], "partial")
        statuses = {row["channel"]: row for row in result["channel_statuses"]}
        self.assertEqual(statuses["exa"]["status"], "blocked")
        self.assertEqual(statuses["hacker_news"]["status"], "empty")
        self.assertEqual(result["failed_channels"][0]["status"], "blocked")
        rows = {row["id"]: row for row in service.list_channels()["channels"]}
        self.assertEqual(rows["exa"]["status"], "blocked")
        self.assertIsNotNone(rows["exa"]["last_checked_at"])

    def test_bad_channel_does_not_stop_a_valid_channel(self):
        with patch.object(research, "_default_providers", return_value={"hacker_news": lambda _: []}):
            result = service.search("test", providers=["no_such_channel", "hacker_news"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_channels"][0]["status"], "unsupported")

    def test_default_service_never_invokes_paid_reranker(self):
        with patch.object(research, "_default_providers", return_value={"hacker_news": lambda _: []}), \
                patch("research_mcp.rerank.filter_candidates") as rerank, \
                patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "SECRET-paid", "EXA_API_KEY": "SECRET-paid"}):
            service.query("test", providers=["hacker_news"])
            service.search("test", providers=["hacker_news"], rerank=True)
        rerank.assert_not_called()

    def test_failure_states_are_safe_even_when_upstream_contains_secrets(self):
        for error, expected in [(RuntimeError("HTTP 401 SECRET"), "auth_required"),
                                (RuntimeError("HTTP 403 SECRET"), "blocked"),
                                (RuntimeError("HTTP 429 SECRET"), "quota_exhausted"),
                                (ValueError("SECRET"), "parse_error"),
                                (FileNotFoundError("SECRET"), "startup_failed")]:
            with self.subTest(expected=expected), patch.object(research, "_default_providers", return_value={"exa": lambda _, e=error: (_ for _ in ()).throw(e)}):
                result = service.search("test", providers=["exa"])
            self.assertEqual(result["failed_channels"][0]["status"], expected)
            self.assertNotIn("SECRET", json.dumps(result))
