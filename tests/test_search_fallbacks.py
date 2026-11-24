import unittest
from unittest.mock import patch

from research_mcp import research
from research_mcp import channels


class GithubLongQueryTest(unittest.TestCase):
    def test_keywords_keep_short_query(self):
        self.assertEqual(research._github_keywords("shared agent memory"), "shared agent memory")

    def test_keywords_drop_stopwords(self):
        self.assertEqual(research._github_keywords("shared memory for AI agents across Claude Code"), "shared memory AI")

    def test_empty_long_query_retries_with_keywords(self):
        calls = []

        def fake_run(command):
            calls.append(command[3])
            return [] if len(calls) == 1 else [{"fullName": "acme/mem", "url": "https://github.com/acme/mem"}]

        with patch("research_mcp.research._run_json", side_effect=fake_run):
            result = research._github_search({"github_query": "shared memory for coding agents across tools"})
        self.assertEqual(calls, ["shared memory for coding agents across tools", "shared memory coding"])
        self.assertEqual([r["title"] for r in result], ["acme/mem"])


class XFallbackTest(unittest.TestCase):
    def test_without_ego_browser_uses_exa_site_x(self):
        seen = {}

        def fake_exa(config):
            seen["q"] = config["exa_query"]
            return [{"url": "https://x.com/a/status/1", "title": "t", "source": "exa"}]

        with patch("shutil.which", return_value=None), patch.object(channels, "exa_search", side_effect=fake_exa):
            items = channels.x_search({"exa_query": "agent memory"})
        self.assertEqual(seen["q"], "site:x.com agent memory")
        self.assertEqual(items[0]["source"], "x")

    def test_x_not_marked_startup_failed_without_browser(self):
        from research_mcp.channel_catalog import list_channels
        with patch("shutil.which", return_value=None):
            row = next(r for r in list_channels() if r["id"] == "x")
        self.assertNotEqual(row.get("status"), "startup_failed")


if __name__ == "__main__":
    unittest.main()
