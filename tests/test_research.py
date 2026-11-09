import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_mcp import research


class ResearchTest(unittest.TestCase):
    def test_requested_window_filters_before_candidate_limit(self):
        now = datetime.now(timezone.utc)
        old = [(now - research.timedelta(days=120 + index)).isoformat() for index in range(8)]
        recent = (now - research.timedelta(days=1)).isoformat()
        rows = [{"title": f"old high-ranked {index}", "url": f"https://fixture.invalid/old-{index}",
                 "published_at": stamp, "source": "hacker_news"} for index, stamp in enumerate(old)]
        rows.append({"title": "recent result after old hits", "url": "https://fixture.invalid/recent",
                     "published_at": recent, "source": "hacker_news"})
        with patch.object(research, "_default_providers", return_value={"hacker_news": lambda _: rows}):
            result = research.search("synthetic recent query", limit=8, providers=["hacker_news"],
                                     rerank=False, max_age_days=90)
        self.assertEqual([item["title"] for item in result["candidates"]], ["recent result after old hits"])
        self.assertEqual(result["filtered_by"], {"outside_time_range": 8})
        self.assertEqual(result["total"], 1)

    def test_legacy_search_default_age_remains_3650_days(self):
        old = datetime.now(timezone.utc) - research.timedelta(days=120)
        row = {"title": "still within legacy default", "url": "https://fixture.invalid/legacy",
               "published_at": old.isoformat(), "source": "hacker_news"}
        observed = []
        def provider(config):
            observed.append(config["max_age_days"])
            return [row]
        with patch.object(research, "_default_providers", return_value={"hacker_news": provider}):
            result = research.search("synthetic query", providers=["hacker_news"], rerank=False)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(observed, [3650])

    def test_exa_x_site_empty_has_conditional_note_without_failure(self):
        for query in ('site:x.com agents', 'agent site:twitter.com', 'SITE:WWW.X.COM agents',
                      'site:https://twitter.com/example agents', '(site:x.com OR site:twitter.com) agents'):
            with self.subTest(query=query), patch.object(research, '_default_providers', return_value={'exa': lambda _: []}):
                result = research.search(query, providers=['exa'], rerank=False)
            self.assertEqual(result['display_notes'], [research.EXA_X_EMPTY_NOTE])
            self.assertEqual(result['succeeded_channels'], ['exa'])
            self.assertEqual(result['total'], 0)
            self.assertEqual(result['returned'], 0)
            self.assertFalse(result['truncated'])
            self.assertNotIn('failed_channels', result)

    def test_exa_x_note_ignores_mentions_exclusions_quotes_and_lookalike_domains(self):
        queries = ['x.com agents', 'twitter.com agents', 'site:example.com x.com',
                   '-site:x.com agents', 'NOT site:x.com', 'NOT (site:x.com OR site:twitter.com)',
                   '(NOT site:x.com)', 'NOT(site:x.com OR site:twitter.com)',
                   '((-(site:x.com OR site:twitter.com)))',
                   '-(site:x.com OR site:twitter.com)', 'site:x.invalid', 'site:x.com.attacker.invalid',
                   'site:https://x.com@attacker.invalid/path', 'prefix-site:x.com',
                   '"site:x.com"', '"literal site:x.com example"', '"unclosed site:x.com',
                   'site:twitter.com.evil/path', 'https://example.com/?q=site:x.com']
        for query in queries:
            with self.subTest(query=query), patch.object(research, '_default_providers', return_value={'exa': lambda _: []}):
                result = research.search(query, providers=['exa'], rerank=False)
            self.assertNotIn('display_notes', result)
            self.assertNotIn('failed_channels', result)

    def test_exa_x_note_requires_exa_execution_not_other_empty_channels(self):
        with patch.object(research, '_default_providers', return_value={'hacker_news': lambda _: []}):
            result = research.search('site:x.com agents', providers=['hacker_news'], rerank=False)
        self.assertNotIn('display_notes', result)
        with patch.object(research, '_default_providers', return_value={'hacker_news': lambda _: []}):
            missing = research.search('site:x.com agents', providers=['exa'], rerank=False)
        self.assertNotIn('display_notes', missing)
        self.assertEqual(missing['failed_channels'][0]['channel'], 'exa')

    def test_exa_x_rate_limit_stays_failure_without_empty_note(self):
        def fail(_):
            raise RuntimeError('429 rate limit')
        with patch.object(research, '_default_providers', return_value={'exa': fail}):
            result = research.search('site:x.com agents', providers=['exa'], rerank=False)
        self.assertNotIn('display_notes', result)
        self.assertEqual(result['succeeded_channels'], [])
        self.assertEqual(result['failed_channels'], [{'channel': 'exa', 'error': '429 rate limit'}])

    def test_exa_x_note_uses_raw_channel_result_not_filtered_or_deduped_result(self):
        item = {'title': 'Synthetic X post', 'url': 'https://x.com/example/status/1', 'source': 'exa'}
        with patch.object(research, '_default_providers', return_value={'exa': lambda _: [item]}), \
             patch('research_mcp.rerank.filter_candidates', return_value=([], None)):
            result = research.search('site:x.com agents', providers=['exa'])
        self.assertEqual(result['returned'], 0)
        self.assertEqual(result['total'], 1)
        self.assertNotIn('display_notes', result)

    def test_exa_empty_note_survives_other_channel_success(self):
        item = {'title': 'Synthetic item', 'url': 'https://example.invalid/one', 'source': 'hacker_news'}
        with patch.object(research, '_default_providers', return_value={
                'exa': lambda _: [], 'hacker_news': lambda _: [item]}):
            result = research.search('site:x.com agents', rerank=False)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['candidates'], [item])
        self.assertEqual(result['display_notes'], [research.EXA_X_EMPTY_NOTE])

    def test_normalize_url_removes_tracking_fragment_and_github_suffix(self):
        self.assertEqual(
            research.normalize_url("HTTPS://GitHub.com/Owner/Repo.git/?utm_source=x#readme"),
            "https://github.com/owner/repo",
        )
        self.assertEqual(
            research.normalize_url("https://example.com/post/?utm_medium=x&ref=home#part"),
            "https://example.com/post",
        )

    def test_candidate_key_uses_repo_name_or_normalized_article_url(self):
        self.assertEqual(
            research.candidate_key({"kind": "github", "repo": {"full_name": "Owner/Repo"}}),
            "github:owner/repo",
        )
        self.assertEqual(
            research.candidate_key({"kind": "article", "url": "https://EXAMPLE.com/a/?utm_source=x"}),
            "url:https://example.com/a",
        )

    def test_candidate_key_uses_github_url_from_scored_snapshot(self):
        self.assertEqual(
            research.candidate_key({
                "kind": "github",
                "title": "Owner/Repo",
                "url": "https://github.com/Owner/Repo",
            }),
            "github:owner/repo",
        )

    def test_candidate_key_uses_code_repository_for_paper(self):
        self.assertEqual(
            research.candidate_key({"kind": "article", "url": "https://arxiv.org/abs/1234.5678", "code_url": "https://github.com/Owner/Repo"}),
            "github:owner/repo",
        )

    def test_github_search_merges_short_queries(self):
        config = {"github_queries": ["agentic-ai", "multimodal"], "max_age_days": 90, "github_limit": 10}
        rows = {
            "agentic-ai": [{"fullName": "acme/one", "url": "https://github.com/acme/one"}],
            "multimodal": [
                {"fullName": "acme/one", "url": "https://github.com/acme/one"},
                {"fullName": "acme/two", "url": "https://github.com/acme/two"},
            ],
        }

        def fake_run(command):
            query = command[3]
            return rows[query]

        with patch("research_mcp.research._run_json", side_effect=fake_run):
            result = research._github_search(config)

        self.assertEqual([item["title"] for item in result], ["acme/one", "acme/two"])

    def test_http_json_uses_bounded_curl(self):
        completed = type("Result", (), {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""})()
        with patch("research_mcp.research.subprocess.run", return_value=completed) as run:
            self.assertEqual(research._http_json("https://example.com/data"), {"ok": True})
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["curl", "-sS", "-L", "--fail", "--max-time"])
        self.assertIn("https://example.com/data", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 25)



    def test_search_calls_every_keyword_channel_but_skips_query_agnostic_ones(self):
        """关键词渠道要全部打到；榜单/订阅渠道不能进来（它们不理会查询词）。

        回归背景：search() 曾硬编码一份名单，新增的掘金渠道注册了却永远不会被调用，
        且结果看不出差异。
        """
        called = []

        def spy(name):
            def inner(_cfg):
                called.append(name)
                return []
            return inner

        providers = {name: spy(name) for name in research._default_providers()}
        with patch("research_mcp.research._default_providers", return_value=providers):
            research.search("prism", limit=5)

        self.assertEqual(sorted(called), sorted({"github_search", "hacker_news", "exa", "juejin", "bilibili"}))
        for name in research.QUERY_AGNOSTIC_CHANNELS:
            self.assertNotIn(name, called)

    def test_search_can_narrow_to_requested_providers(self):
        """只传渠道子集时只打这些（站内检索靠它：exa + site: 限定）。"""
        seen = {}

        def provider_for(name):
            def inner(_cfg):
                seen[name] = _cfg
                return [{"kind": "article", "title": name, "url": f"https://example.com/{name}", "source": name}]
            return inner

        providers = {name: provider_for(name) for name in research._default_providers()}
        with patch("research_mcp.research._default_providers", return_value=providers):
            result = research.search("site:example.com prism", limit=5, providers=["exa"])

        self.assertEqual(list(seen), ["exa"])
        # 查询词要带进 exa 的单查询词 key，否则站内搜索拿不到词
        self.assertEqual(seen["exa"]["exa_query"], "site:example.com prism")
        self.assertEqual([item["title"] for item in result["candidates"]], ["exa"])

    def test_search_skips_unknown_and_query_agnostic_providers(self):
        """渠道名写错、或点名了榜单渠道，只跳过，不报错也不返回无关内容。"""
        called = []

        def spy(name):
            def inner(_cfg):
                called.append(name)
                return [{"kind": "article", "title": name, "url": f"https://example.com/{name}", "source": name}]
            return inner

        providers = {name: spy(name) for name in research._default_providers()}
        with patch("research_mcp.research._default_providers", return_value=providers):
            result = research.search("prism", limit=5, providers=["rss", "exa", "no_such_channel"])

        self.assertEqual(called, ["exa"])
        self.assertEqual([item["title"] for item in result["candidates"]], ["exa"])

    def test_search_keeps_running_when_a_channel_fails(self):
        def broken(_cfg):
            raise RuntimeError("上游挂了")

        providers = {"exa": broken, "juejin": lambda _cfg: [{"kind": "article", "title": "ok", "url": "https://example.com/a", "source": "juejin"}]}
        with patch("research_mcp.research._default_providers", return_value=providers):
            result = research.search("prism", limit=5)

        self.assertEqual([item["title"] for item in result["candidates"]], ["ok"])

    def test_search_reports_which_channels_failed(self):
        """渠道挂了要说出来：少打一个渠道和“全网都没有”是两回事。"""
        def broken(_cfg):
            raise RuntimeError("连接超时")

        providers = {"hacker_news": broken, "exa": lambda _cfg: []}
        with patch("research_mcp.research._default_providers", return_value=providers):
            result = research.search("prism", limit=5)

        self.assertEqual(result["failed_channels"], [{"channel": "hacker_news", "error": "连接超时"}])

    def test_search_reports_unknown_provider_name(self):
        providers = {"exa": lambda _cfg: []}
        with patch("research_mcp.research._default_providers", return_value=providers):
            result = research.search("prism", limit=5, providers=["exa", "nope"])

        self.assertEqual(result["failed_channels"], [{"channel": "nope", "error": "没有这个渠道"}])

    def test_search_omits_failed_channels_when_all_ok(self):
        providers = {"exa": lambda _cfg: []}
        with patch("research_mcp.research._default_providers", return_value=providers):
            result = research.search("prism", limit=5)

        self.assertNotIn("failed_channels", result)

    def test_search_runs_channels_concurrently(self):
        """渠道要并发打：它们互不依赖也不共享状态，串行是白等。

        用两个各自 sleep 0.4 秒的假渠道验证：并发总耗时要明显小于两者之和。
        """
        import time as _time

        def slow(_cfg):
            _time.sleep(0.4)
            return []

        providers = {"github_search": slow, "hacker_news": slow, "arxiv": slow}
        with patch("research_mcp.research._default_providers", return_value=providers):
            started = _time.monotonic()
            research.search("prism", limit=5, rerank=False)
            elapsed = _time.monotonic() - started

        # 串行要 1.2s 以上；并发约 0.4s。留出足够的宽量避免脆断言
        self.assertLess(elapsed, 0.9, f"三个 0.4s 的渠道耗时 {elapsed:.2f}s，看起来还是串行的")

    def test_search_keeps_result_order_stable_despite_concurrency(self):
        """并发收集后结果顺序要稳定（按渠道声明顺序），不能变成谁先返回谁在前。

        断言期望顺序而不是“跑两遍相等”：两个固定延迟的渠道即使按完成顺序收集，
        两遍也会得到同一个结果，那样断言抓不到回归。
        """
        import time as _time

        def make(name, delay):
            def inner(_cfg):
                _time.sleep(delay)
                return [{"kind": "article", "title": name, "url": f"https://example.com/{name}", "source": name}]
            return inner

        # 先申明的慢、后申明的快：按完成顺序会得到 ["exa", "arxiv"]（错的），
        # 按声明顺序才是 ["arxiv", "exa"]
        providers = {"arxiv": make("arxiv", 0.3), "exa": make("exa", 0.0)}
        with patch("research_mcp.research._default_providers", return_value=providers):
            titles = [item["title"] for item in research.search("prism", limit=5, providers=["arxiv", "exa"], rerank=False)["candidates"]]

        self.assertEqual(titles, ["arxiv", "exa"])

    def test_search_reports_truncation(self):
        """结果被截断时要给出总数，不能静默截断（否则会以为看到的就是全部）。"""
        def many(_cfg):
            return [
                {"kind": "article", "title": f"t{i}", "url": f"https://example.com/{i}", "source": "exa"}
                for i in range(50)
            ]

        providers = {"exa": many}
        with patch("research_mcp.research._default_providers", return_value=providers):
            result = research.search("prism", limit=5, rerank=False)

        self.assertEqual(result["returned"], 5)
        self.assertEqual(result["total"], 50)
        self.assertIs(result["truncated"], True)
        self.assertEqual(result["truncated_by"], ["limit"])

    def test_search_distinguishes_limit_truncation_from_relevance_filter(self):
        """截断原因要分开：调 limit 和被相关性筛掉是两回事，混着说会误导。

        回归：最初只报一个 truncated，returned=1 / total=100 这种
        “实际是相关性过滤淘汰”的情况，会被误读成“调大 limit 就能拿回来”。
        """
        def many(_cfg):
            return [
                {"kind": "article", "title": f"t{i}", "url": f"https://example.com/{i}", "source": "exa"}
                for i in range(50)
            ]

        # 伪造 rerank：limit=5 却只留 1 条（没顶到 limit），且没降级（= Jev 真跑了）。
        # 只有这种“明确没顶到 limit”的情况才判 relevance。
        providers = {"exa": many}
        with patch("research_mcp.research._default_providers", return_value=providers), \
                patch("research_mcp.rerank.filter_candidates", return_value=([{"kind": "article", "title": "t0", "url": "https://example.com/0", "source": "exa"}], None)):
            result = research.search("prism", limit=5)

        self.assertEqual(result["returned"], 1)
        self.assertIs(result["truncated"], True)
        self.assertEqual(sorted(result["truncated_by"]), ["limit", "relevance"])

    def test_search_does_not_claim_relevance_cut_when_result_hits_limit(self):
        """顶到 limit 时不能把截断归给相关性：

        filter_candidates 正常分支最后也是 kept[:limit]，所以 len(result) == limit
        分不清“被相关性筛掉”还是“恰好被 limit 截掉”。宁可不报 relevance，
        也不能把“调大 limit 能拿回来”的说成拿不回来。
        """
        def many(_cfg):
            return [
                {"kind": "article", "title": f"t{i}", "url": f"https://example.com/{i}", "source": "exa"}
                for i in range(50)
            ]

        # limit=5，rerank 也返回 5 条（顶到了）
        kept = [{"kind": "article", "title": f"k{i}", "url": f"https://example.com/k{i}", "source": "exa"} for i in range(5)]
        providers = {"exa": many}
        with patch("research_mcp.research._default_providers", return_value=providers), \
                patch("research_mcp.rerank.filter_candidates", return_value=(kept, None)):
            result = research.search("prism", limit=5)

        self.assertEqual(result["returned"], 5)
        self.assertEqual(result["truncated_by"], ["limit"])

    def test_search_marks_not_truncated_when_everything_fits(self):
        def few(_cfg):
            return [{"kind": "article", "title": "only", "url": "https://example.com/a", "source": "exa"}]

        providers = {"exa": few}
        with patch("research_mcp.research._default_providers", return_value=providers):
            result = research.search("prism", limit=5, rerank=False)

        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["total"], 1)
        self.assertIs(result["truncated"], False)
        self.assertEqual(result["truncated_by"], [])

    def test_search_rerank_downgrade_is_not_counted_as_relevance_cut(self):
        """rerank 降级（没真跑）时剩下的条数不算“相关性截断”，否则会把降级误报成筛选。"""
        def many(_cfg):
            return [
                {"kind": "article", "title": f"t{i}", "url": f"https://example.com/{i}", "source": "exa"}
                for i in range(30)
            ]

        providers = {"exa": many}
        with patch("research_mcp.research._default_providers", return_value=providers), \
                patch("research_mcp.rerank.filter_candidates", return_value=([{"kind": "article", "title": "t0", "url": "https://example.com/0", "source": "exa"}], "no_api_key")):
            result = research.search("prism", limit=5)

        self.assertEqual(result["truncated_by"], ["limit"])

    def test_fetch_url_reports_github_failure_instead_of_raising(self):
        """GitHub 分支也不能抛栈（README 承诺的是 ok=false）。"""
        with patch.object(research, "_github_repo", side_effect=research.FetchError("仓库不存在（owner/repo）")):
            result = research.fetch_url("https://github.com/owner/repo")
        self.assertIs(result["ok"], False)
        self.assertEqual(result["kind"], "github")
        self.assertEqual(result["error"], "仓库不存在（owner/repo）")

    def test_fetch_url_reports_github_readme_failure(self):
        """仓库存在但没有 README 时也一样。"""
        with patch.object(research, "_github_repo", return_value={"name": "repo"}), patch.object(
            research, "_github_readme", side_effect=research.FetchError("仓库不存在（owner/repo）")
        ):
            result = research.fetch_url("https://github.com/owner/repo")
        self.assertIs(result["ok"], False)

    def test_describe_gh_failure_turns_stderr_into_one_line(self):
        self.assertEqual(research._describe_gh_failure("gh: Not Found (HTTP 404)", "owner/repo"), "GitHub 资源不存在或当前身份不可见（404）")
        self.assertIn("限流", research._describe_gh_failure("gh: API rate limit exceeded", "owner/repo"))

    def test_placeholder_only_counts_empty_body(self):
        """空 body 当成功和空壳是一回事。"""
        self.assertTrue(research._is_placeholder_only(""))
        self.assertTrue(research._is_placeholder_only("   \n  "))

    def test_placeholder_only_counts_multi_word_login_wall(self):
        """“Sign in to continue”这类多词登录墙要认得。"""
        self.assertTrue(research._is_placeholder_only("Sign in to continue"))
        self.assertTrue(research._is_placeholder_only("Log in to continue"))
        self.assertFalse(research._is_placeholder_only("这里是真正的正文，足够长的一句话在这里。"))

    def test_placeholder_only_treats_jina_shell_marker_as_shell(self):
        """带 Jina 标记却无正文，不能因为标记本身有字就判成有内容。"""
        self.assertTrue(research._is_placeholder_only("Title: 某页\n\nMarkdown Content:\n"))

    def test_fetch_url_uses_github_readme_for_repo_urls(self):
        payload = {"name": "repo", "description": "demo", "default_branch": "main"}
        with patch("research_mcp.research._github_repo", return_value=payload) as repo_call, patch(
            "research_mcp.research._github_readme", return_value="# Demo"
        ) as readme_call:
            result = research.fetch_url("https://github.com/Owner/Repo")

        self.assertEqual(result["kind"], "github")
        self.assertEqual(result["repo"]["full_name"], "owner/repo")
        self.assertEqual(result["content"], "# Demo")
        repo_call.assert_called_once_with("owner/repo")
        readme_call.assert_called_once_with("owner/repo")




if __name__ == "__main__":
    unittest.main()


class ContentCleanupTest(unittest.TestCase):
    def test_removes_badge_only_lines_but_keeps_real_prose(self):
        raw = """# Demo

[![Build](https://img.shields.io/badge/build-passing-green)](https://ci.example.com)
<a href="https://discord.gg/demo"><img src="https://img.shields.io/badge/Discord-chat-blue"></a>

这里是项目真正的说明，应该保留。

正文里有一张架构图 ![arch](https://example.com/arch.png) 用来解释数据流。
"""
        cleaned = research._clean_readable_markdown(raw)
        self.assertNotIn("img.shields.io", cleaned)
        self.assertNotIn("discord.gg", cleaned)
        self.assertIn("项目真正的说明", cleaned)
        self.assertIn("架构图", cleaned)
        self.assertIn("arch.png", cleaned)

    def test_removes_explicit_sponsor_section_until_next_peer_heading(self):
        raw = """# Demo

## Usage
真实用法。

## Sponsors
Become a sponsor!
BTC: 1abc123
[Patreon](https://patreon.com/demo)

### Gold sponsors
logo logo logo

## API
真实 API 文档。
"""
        cleaned = research._clean_readable_markdown(raw)
        self.assertIn("## Usage", cleaned)
        self.assertIn("## API", cleaned)
        self.assertNotIn("Sponsors", cleaned)
        self.assertNotIn("BTC:", cleaned)
        self.assertNotIn("Patreon", cleaned)

    def test_keeps_generic_support_section_not_clearly_sponsorship(self):
        raw = """# Demo

## Support
If parsing fails, open an issue with a reproducible sample.
"""
        cleaned = research._clean_readable_markdown(raw)
        self.assertIn("## Support", cleaned)
        self.assertIn("reproducible sample", cleaned)


class FetchReadableTest(unittest.TestCase):
    """fetch 取网页正文：优先 Jina Reader，失败回落原始抓取。"""

    def setUp(self):
        guard = patch.object(research, "_validate_fetch_url")
        guard.start()
        self.addCleanup(guard.stop)

    def test_readable_prefers_jina(self):
        with patch.object(research, "_safe_http_text", return_value="Title: 示例\n\n正文内容"):
            content, via = research._readable_content("https://example.com/a")
        self.assertEqual(via, "jina")
        self.assertIn("正文内容", content)

    def test_readable_cleans_jina_badges_and_sponsor_blocks(self):
        jina = """Title: Demo
URL Source: https://example.com
Markdown Content:
# Demo

[![Build](https://img.shields.io/badge/build-passing-green)](https://ci.example.com)

真正正文内容，解释这个项目怎么工作。

## Donate
ETH: 0x123456

## Usage
这样使用。
"""
        with patch.object(research, "_safe_http_text", return_value=jina):
            content, via = research._readable_content("https://example.com/a")
        self.assertEqual(via, "jina")
        self.assertNotIn("img.shields.io", content)
        self.assertNotIn("0x123456", content)
        self.assertIn("真正正文内容", content)
        self.assertIn("## Usage", content)

    def test_readable_falls_back_when_jina_reports_error(self):
        def fake(url, **_kwargs):
            if url.startswith(research.JINA_PREFIX):
                return "Title: \nURL Source: x\nWarning: Target URL returned error 403: Forbidden\n"
            return "<html><body>raw html</body></html>"

        with patch.object(research, "_safe_http_text", side_effect=fake):
            content, via = research._readable_content("https://zhuanlan.zhihu.com/p/1")
        self.assertEqual(via, "raw")
        self.assertIn("raw html", content)

    def test_readable_falls_back_when_jina_raises(self):
        def fake(url, **_kwargs):
            if url.startswith(research.JINA_PREFIX):
                raise RuntimeError("curl 失败")
            return "<html>raw</html>"

        with patch.object(research, "_safe_http_text", side_effect=fake):
            content, via = research._readable_content("https://example.com/b")
        self.assertEqual(via, "raw")
        self.assertIn("raw", content)

    def test_readable_rejects_shell_page_instead_of_reporting_success(self):
        """JS 渲染页只剩 Loading 占位时，不能当成功返回（回归：prism.openai.com 曾返回 127 字节）。"""
        shell = "Title: Prism - AI LaTeX Editor\n\nMarkdown Content:\nLoading...\n\nYour Projects\n\nLoading...\n"
        with patch.object(research, "_safe_http_text", return_value=shell):
            with self.assertRaises(research.FetchError) as caught:
                research._readable_content("https://prism.openai.com/")
        self.assertIn("空壳", str(caught.exception))

    def test_readable_keeps_short_but_real_content(self):
        """短正文不是空壳——只有占位词才算，不能把短页面误杀。"""
        with patch.object(research, "_safe_http_text", return_value="Title: 小页\n\n# 标题\n\n今晚八点发布。\n"):
            content, via = research._readable_content("https://example.com/short")
        self.assertEqual(via, "jina")
        self.assertIn("今晚八点发布", content)

    def test_readable_rejects_shell_when_raw_is_also_shell(self):
        """Jina 和直接抓都是空壳时才算失败，不能拿空壳充数。"""
        def fake(url, **_kwargs):
            if url.startswith(research.JINA_PREFIX):
                return "Title: \n\nLoading...\n"
            return "Login"

        with patch.object(research, "_safe_http_text", side_effect=fake):
            with self.assertRaises(research.FetchError):
                research._readable_content("https://example.com/shell")

    def test_readable_supplements_chart_page_with_embedded_data(self):
        """图表页的数值挂在图表上，Jina 正文只剩图注，要从页面内嵌载荷补回来。

        回归：Artificial Analysis 对比页的每任务成本、输出速度全在图表里，
        之前只能报「需要浏览器」或者返回一堆没用数字。
        """
        chart_page = (
            "Title: 对比页\n\nMarkdown Content:\n## Intelligence\n\n"
            "Artificial Analysis Intelligence Index · Higher is better\n\n"
            "Cost per Task · Lower is better\n\n"
            "Output Speed · Higher is better\n"
        )
        # 真实页面是 Next.js 流式载荷：整个 JSON 被转义。图表标题在数组之前，
        # 数组后面跟着下一个字段（这里用 focusIds 占位）。
        flight = (
            '\\"children\\":\\"Cost per Task\\"}]}],'
            '\\"data\\":[{\\"id\\":\\"a\\",\\"label\\":\\"Grok 4.6 (high)\\",'
            '\\"value\\":1.8589,\\"displayValue\\":\\"$$1.86\\"}],'
            '\\"focusIds\\":\\"$1b\\"}'
        )

        def fake(url, **_kwargs):
            if url.startswith(research.JINA_PREFIX):
                return chart_page
            return f"<html><script>self.__next_f.push([1,{flight}]);</script></html>"

        with patch.object(research, "_safe_http_text", side_effect=fake):
            content, via = research._readable_content("https://example.com/chart")
        self.assertEqual(via, "ldjson")
        # 断言补上的表格里有抽出来的标签和值，而不是撞上正文里本来就有的图注
        supplement = content.split("页面内嵌数据", 1)[1]
        self.assertIn("Cost per Task", supplement)
        self.assertIn("Grok 4.6 (high)", supplement)
        self.assertIn("1.86", supplement)

    def test_readable_chart_page_without_embedded_data_returns_marked_content(self):
        """图表页但没抽到数据时，退回正文并标出不完整，而不是把可读的文章变成失败。

        图表页判定是启发式，正常文章有被误判的可能；抽不到就报失败会把
        “本来能读”变成“读不了”。
        """
        chart_page = (
            "Title: 对比页\n\nMarkdown Content:\nHigher is better\nLower is better\nLog scale\n"
        )

        def fake(url, **_kwargs):
            if url.startswith(research.JINA_PREFIX):
                return chart_page
            return "<html><body>no structured payload</body></html>"

        with patch.object(research, "_safe_http_text", side_effect=fake):
            content, via = research._readable_content("https://example.com/chart-empty")
        self.assertEqual(via, "chart_page_incomplete")
        self.assertIn("内嵌数据没抽到", content)

    def test_readable_chart_page_survives_failed_raw_fetch(self):
        """原始页面抓不到时也不能把只剩图注的正文当成功静默返回。"""
        chart_page = (
            "Title: 对比页\n\nMarkdown Content:\nHigher is better\nLower is better\nLog scale\n"
        )

        def fake(url, **_kwargs):
            if url.startswith(research.JINA_PREFIX):
                return chart_page
            raise research.FetchError("页面取不到（HTTP 500）")

        with patch.object(research, "_safe_http_text", side_effect=fake):
            content, via = research._readable_content("https://example.com/chart-broken")
        self.assertEqual(via, "chart_page_incomplete")
        self.assertIn("内嵌数据没抽到", content)

    def test_fetch_url_reports_stale_or_missing_page_without_traceback(self):
        """404 这类失败要返回一句人话，而不是抛调用栈。"""
        with patch.object(research, "_readable_content", side_effect=research.FetchError("页面取不到（HTTP 404）")):
            result = research.fetch_url("https://example.com/gone")
        self.assertIs(result["ok"], False)
        self.assertEqual(result["error"], "页面取不到（HTTP 404）")
        self.assertIs(result["retryable"], False)

    def test_fetch_url_marks_timeout_as_retryable(self):
        with patch.object(research, "_readable_content", side_effect=research.FetchError("请求超时（20 秒内没有响应）")):
            result = research.fetch_url("https://example.com/slow")
        self.assertIs(result["ok"], False)
        self.assertIs(result["retryable"], True)

    def test_describe_curl_failure_turns_stderr_into_one_line(self):
        self.assertEqual(
            research._describe_curl_failure("curl: (56) The requested URL returned error: 404", 20),
            "页面取不到（HTTP 404）",
        )
        self.assertEqual(
            research._describe_curl_failure("curl: (28) Operation timed out after 3002 milliseconds", 20),
            "请求超时（20 秒内没有响应）",
        )
        self.assertEqual(research._describe_curl_failure("curl: (6) Could not resolve host: x", 20), "域名解析失败")

    def test_search_passes_candidates_to_reranker(self):
        fake_items = [{"title": f"item-{i}", "url": f"https://example.com/{i}", "source": "exa"} for i in range(5)]
        with patch.object(research, "_default_providers", return_value={"exa": lambda c: fake_items}):
            with patch("research_mcp.rerank.filter_candidates", return_value=([fake_items[1]], None)) as mock_rerank:
                res = research.search("test", limit=2, rerank=True)
                mock_rerank.assert_called_once()
                self.assertEqual(len(res["candidates"]), 1)
                self.assertEqual(res["candidates"][0]["title"], "item-1")
                self.assertNotIn("rerank_downgraded", res)

    def test_search_reports_rerank_downgrade(self):
        fake_items = [{"title": f"item-{i}", "url": f"https://example.com/{i}", "source": "exa"} for i in range(5)]
        with patch.object(research, "_default_providers", return_value={"exa": lambda c: fake_items}):
            with patch("research_mcp.rerank.filter_candidates", return_value=(fake_items[:2], "no_api_key")):
                res = research.search("test", limit=2, rerank=True)
                self.assertEqual(res["rerank_downgraded"], "no_api_key")

    def test_search_with_no_rerank_skips_reranker(self):
        fake_items = [{"title": f"item-{i}", "url": f"https://example.com/{i}", "source": "exa"} for i in range(5)]
        with patch.object(research, "_default_providers", return_value={"exa": lambda c: fake_items}):
            with patch("research_mcp.rerank.filter_candidates") as mock_rerank:
                res = research.search("test", limit=2, rerank=False)
                mock_rerank.assert_not_called()
                self.assertEqual(len(res["candidates"]), 2)
