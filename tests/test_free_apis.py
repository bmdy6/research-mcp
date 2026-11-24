"""Offline official API fixtures and free-only/security contract checks."""
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from research_mcp.channels import free_apis as api
from research_mcp.channels.errors import ChannelError
from research_mcp.commands import CommandUnavailableError


TAVILY_USAGE = {"account": {"current_plan": "Researcher", "plan_usage": 1,
    "plan_limit": 1000, "paygo_usage": 0, "paygo_limit": 0}}
OPENALEX_USAGE = {"rate_limit": {"daily_budget_usd": 1, "daily_used_usd": 0.01,
    "daily_remaining_usd": 0.99, "prepaid_balance_usd": 0,
    "endpoint_costs_usd": {"search": 0.001}}}
FIXTURES = {
    "tavily": [TAVILY_USAGE, {"results": [{"title": "AI search", "url": "https://example.org/ai",
        "content": "<b>Search</b> snippet", "published_date": "2026-11-20"}]}],
    "openalex": [OPENALEX_USAGE, {"results": [{"id": "https://openalex.org/W123", "display_name": "Attention",
        "doi": "https://doi.org/10.123/abc", "publication_date": "2017-06-12",
        "abstract_inverted_index": {"attention": [1], "All": [0]}}]}],
    "wikipedia": [{"pages": [{"key": "Artificial_intelligence", "title": "Artificial intelligence",
        "excerpt": "<span>AI</span> is a field"}]}],
    "wikidata": [{"search": [{"id": "Q11660", "label": "artificial intelligence", "description": "field"}]}],
    "crossref": [{"message": {"items": [{"title": ["Attention"], "DOI": "10.123/abc",
        "abstract": "<jats:p>Abstract</jats:p>", "published": {"date-parts": [[2017, 6, 12]]}}]}}],
    "stackexchange": [{"items": [{"title": "A &amp; B", "link": "https://stackoverflow.com/questions/123/ai",
        "body": "<p>Question text</p>", "creation_date": 1700000000}], "quota_remaining": 299}],
    "huggingface": [[{"id": "org/model", "tags": ["text-generation", "en"], "lastModified": "2026-11-20T00:00:00Z"}]],
    "europe_pmc": [{"resultList": {"result": [{"id": "123", "source": "MED", "title": "Cancer study",
        "abstractText": "Results", "firstPublicationDate": "2026-11-10"}]}}],
    "pubmed": [{"esearchresult": {"idlist": ["123"]}}, {"result": {"uids": ["123"],
        "123": {"title": "Cancer study", "fulljournalname": "Medical journal", "pubdate": "2026 Nov 10"}}}],
    "openlibrary": [{"docs": [{"key": "/works/OL123W", "title": "Book", "author_name": ["A. Author"], "first_publish_year": 2010}]}],
    "itunes_podcasts": [{"results": [{"collectionName": "AI podcast", "collectionViewUrl": "https://podcasts.apple.com/podcast/id123",
        "artistName": "Host", "releaseDate": "2026-11-15T00:00:00Z"}]}],
}
EMPTY = {"tavily": [TAVILY_USAGE, {"results": []}], "openalex": [OPENALEX_USAGE, {"results": []}],
    "wikipedia": [{"pages": []}], "wikidata": [{"search": []}], "crossref": [{"message": {"items": []}}],
    "stackexchange": [{"items": []}], "huggingface": [[]], "europe_pmc": [{"resultList": {"result": []}}],
    "pubmed": [{"esearchresult": {"idlist": []}}], "openlibrary": [{"docs": []}], "itunes_podcasts": [{"results": []}]}
ENV = {"TAVILY_API_KEY": "secret-tavily", "TAVILY_FREE_ONLY": "1",
       "OPENALEX_API_KEY": "secret-openalex", "OPENALEX_FREE_ONLY": "1"}


class ProviderTests(unittest.TestCase):
    def test_pubmed_explicitly_requests_relevance(self):
        with patch.object(api, "_request", side_effect=FIXTURES["pubmed"]) as request:
            api.PROVIDERS["pubmed"]({"web_query": "niacinamide"})
        self.assertIn("sort=relevance", request.call_args_list[0].args[0])

    def setUp(self):
        api._reset_rate_limit_state()
        self.addCleanup(api._reset_rate_limit_state)
        self.environment = patch.dict(os.environ, ENV, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def assert_status(self, expected, callable_, *args, **kwargs):
        with self.assertRaises(ChannelError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.status, expected)
        self.assertNotIn("secret", str(raised.exception))

    def test_every_provider_normalizes_official_fixture(self):
        self.assertEqual(set(FIXTURES), set(api.PROVIDERS))
        for name, fixtures in FIXTURES.items():
            with self.subTest(provider=name), patch.object(api, "_request", side_effect=fixtures) as request:
                result = api.PROVIDERS[name]({"web_query": "AI", "web_limit": 2})
                self.assertEqual(len(result), 1)
                item = result[0]
                self.assertEqual(set(item), {"kind", "title", "url", "summary", "published_at", "source", "rank", "actual_backend"})
                self.assertEqual(item["source"], name)
                self.assertEqual(item["actual_backend"], api.BACKENDS[name])
                self.assertEqual(item["rank"], 1)
                self.assertNotIn("<", item["summary"])
                self.assertEqual(request.call_count, len(fixtures))

    def test_real_empty_is_distinct_from_schema_drift_for_every_provider(self):
        for name in api.PROVIDERS:
            with self.subTest(provider=name), patch.object(api, "_request", side_effect=EMPTY[name]):
                self.assertEqual(api.PROVIDERS[name]({"web_query": "AI"}), [])
            for malformed in ({}, None, {"results": {}}, [None]):
                fixtures = ([TAVILY_USAGE] if name == "tavily" else [OPENALEX_USAGE] if name == "openalex" else []) + [malformed]
                with self.subTest(provider=name, malformed=malformed), patch.object(api, "_request", side_effect=fixtures):
                    self.assert_status("parse_error", api.PROVIDERS[name], {"web_query": "AI"})

    def test_every_provider_rejects_malformed_rows(self):
        malformed = {"tavily": [TAVILY_USAGE, {"results": [{}]}], "openalex": [OPENALEX_USAGE, {"results": [{}]}],
            "wikipedia": [{"pages": [{}]}], "wikidata": [{"search": [{}]}], "crossref": [{"message": {"items": [{}]}}],
            "stackexchange": [{"items": [{}]}], "huggingface": [[{}]], "europe_pmc": [{"resultList": {"result": [{}]}}],
            "pubmed": [{"esearchresult": {"idlist": ["123"]}}, {"result": {"uids": ["123"], "123": {}}}],
            "openlibrary": [{"docs": [{}]}], "itunes_podcasts": [{"results": [{}]}]}
        for name, fixtures in malformed.items():
            with self.subTest(provider=name), patch.object(api, "_request", side_effect=fixtures):
                self.assert_status("parse_error", api.PROVIDERS[name], {"web_query": "AI"})

    def test_no_query_never_requests_or_reads_credentials(self):
        with patch.object(api, "_request") as request:
            for provider in api.PROVIDERS.values():
                self.assertEqual(provider({}), [])
            request.assert_not_called()

    def test_keys_require_explicit_free_only_opt_in(self):
        for name in ("tavily", "openalex"):
            for env in ({}, {name.upper() + "_API_KEY": "secret"}, {name.upper() + "_FREE_ONLY": "1"}):
                with self.subTest(provider=name, env=list(env)), patch.dict(os.environ, env, clear=True), patch.object(api, "_request") as request:
                    self.assert_status("auth_required", api.PROVIDERS[name], {"web_query": "AI"})
                    request.assert_not_called()

    def test_tavily_fixed_basic_cost_no_auto_upgrade(self):
        with patch.object(api, "_request", side_effect=FIXTURES["tavily"]) as request:
            api.PROVIDERS["tavily"]({"web_query": "AI", "web_limit": 999, "search_depth": "advanced", "auto_parameters": True})
        body = request.call_args.kwargs["body"]
        self.assertEqual(body["search_depth"], "basic")
        self.assertFalse(body["auto_parameters"])
        self.assertFalse(body["include_raw_content"])
        self.assertEqual(body["max_results"], 20)

    def test_tavily_budget_preflight_fails_closed(self):
        for change, status in [({"current_plan": "Bootstrap"}, "blocked"), ({"paygo_limit": 1}, "blocked"),
            ({"paygo_usage": 1}, "blocked"), ({"plan_limit": 1001}, "blocked"), ({"plan_usage": 1000}, "quota_exhausted"),
            ({"plan_limit": None}, "parse_error"), ({"paygo_limit": float("nan")}, "parse_error")]:
            api._reset_rate_limit_state()
            payload = {"account": {**TAVILY_USAGE["account"], **change}}
            with self.subTest(change=change), patch.object(api, "_request", return_value=payload) as request:
                self.assert_status(status, api.PROVIDERS["tavily"], {"web_query": "AI"})
                self.assertEqual(request.call_count, 1)

    def test_tavily_null_paygo_requires_independently_verified_disabled_setting(self):
        usage = {"account": {**TAVILY_USAGE["account"], "paygo_limit": None}}
        with patch.object(api, "_request", return_value=usage) as request:
            self.assert_status("auth_required", api.PROVIDERS["tavily"], {"web_query": "AI"})
            self.assertEqual(request.call_count, 1)
        with patch.dict(os.environ, {"TAVILY_PAYG_DISABLED": "1"}), \
                patch.object(api, "_request", side_effect=[usage, FIXTURES["tavily"][1]]):
            self.assertEqual(len(api.PROVIDERS["tavily"]({"web_query": "AI"})), 1)
        # An explicit assertion never permits missing fields, paid usage or a
        # positive PAYG cap. It only resolves a present null on a free account.
        for account in ({k: v for k, v in usage["account"].items() if k != "paygo_limit"},
                        {**usage["account"], "paygo_usage": 1}, {**usage["account"], "paygo_limit": 1}):
            with patch.dict(os.environ, {"TAVILY_PAYG_DISABLED": "1"}), \
                    patch.object(api, "_request", return_value={"account": account}) as request:
                self.assert_status("parse_error" if "paygo_limit" not in account else "blocked",
                                   api.PROVIDERS["tavily"], {"web_query": "AI"})
                self.assertEqual(request.call_count, 1)

    def test_openalex_budget_preflight_fails_closed(self):
        for change, status in [({"daily_budget_usd": 2}, "blocked"), ({"prepaid_balance_usd": 1}, "blocked"),
            ({"daily_remaining_usd": 0}, "quota_exhausted"), ({"daily_used_usd": 1}, "quota_exhausted"),
            ({"endpoint_costs_usd": {"search": 0.002}}, "quota_exhausted"),
            ({"endpoint_costs_usd": {}}, "parse_error"), ({"daily_budget_usd": None}, "parse_error")]:
            api._reset_rate_limit_state()
            payload = {"rate_limit": {**OPENALEX_USAGE["rate_limit"], **change}}
            with self.subTest(change=change), patch.object(api, "_request", return_value=payload) as request:
                self.assert_status(status, api.PROVIDERS["openalex"], {"web_query": "AI"})
                self.assertEqual(request.call_count, 1)

    def test_no_retry_or_fallback_after_quota(self):
        for name in api.PROVIDERS:
            with self.subTest(provider=name), patch.object(api, "_request", side_effect=ChannelError("quota_exhausted")) as request:
                self.assert_status("quota_exhausted", api.PROVIDERS[name], {"web_query": "AI"})
                self.assert_status("quota_exhausted", api.PROVIDERS[name], {"web_query": "AI"})
                self.assertEqual(request.call_count, 1)

    def test_stackexchange_obeys_backoff_and_zero_quota(self):
        for extra in ({"backoff": 15}, {"quota_remaining": 0}):
            api._reset_rate_limit_state()
            with patch.object(api, "_request", return_value={**FIXTURES["stackexchange"][0], **extra}) as request:
                self.assertEqual(len(api.PROVIDERS["stackexchange"]({"web_query": "AI"})), 1)
                self.assert_status("quota_exhausted", api.PROVIDERS["stackexchange"], {"web_query": "AI"})
                self.assertEqual(request.call_count, 1)

    def test_language_selected_from_query_and_query_is_encoded(self):
        for query, language in (("人工智能 & 安全", "zh"), ("AI & safety", "en")):
            with patch.object(api, "_request", return_value=EMPTY["wikipedia"][0]) as request:
                api.PROVIDERS["wikipedia"]({"web_query": query})
                self.assertTrue(request.call_args.args[0].startswith(f"https://{language}.wikipedia.org/"))
                self.assertIn("%26", request.call_args.args[0])
            with patch.object(api, "_request", return_value=EMPTY["wikidata"][0]) as request:
                api.PROVIDERS["wikidata"]({"web_query": query})
                self.assertIn("language=" + language, request.call_args.args[0])

    def test_openalex_inverted_abstract_and_crossref_partial_date(self):
        with patch.object(api, "_request", side_effect=FIXTURES["openalex"]):
            self.assertEqual(api.PROVIDERS["openalex"]({"web_query": "AI"})[0]["summary"], "All attention")
        with patch.object(api, "_request", side_effect=FIXTURES["crossref"]):
            self.assertEqual(api.PROVIDERS["crossref"]({"web_query": "AI"})[0]["published_at"], "2017-06-12")

    def test_pubmed_requires_complete_summary(self):
        with patch.object(api, "_request", side_effect=[FIXTURES["pubmed"][0], {"result": {"uids": []}}]):
            self.assert_status("parse_error", api.PROVIDERS["pubmed"], {"web_query": "AI"})


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.deadline = time.monotonic() + 5

    def fake_run(self, command, *, input, **kwargs):
        self.command, self.stdin, self.kwargs = command, input, kwargs
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(self.body)
        return subprocess.CompletedProcess(command, self.returncode, self.status, "secret diagnostic")

    def request(self, response_body=b'{"ok":true}', status="200", returncode=0, **kwargs):
        self.body, self.status, self.returncode = response_body, status, returncode
        with patch.object(api, "run_command", side_effect=self.fake_run):
            return api._request("https://api.openalex.org/works?api_key=secret", deadline=self.deadline, **kwargs)

    def test_config_stdin_secrets_tls_limits_no_retry_redirect_or_curlrc(self):
        self.assertEqual(self.request(headers={"Authorization": "Bearer secret"}, body={"query": 'a "quoted" \\ term'}), {"ok": True})
        argv = " ".join(self.command)
        self.assertNotIn("secret", argv)
        self.assertNotIn("api.openalex", argv)
        self.assertIn("api_key=secret", self.stdin)
        self.assertIn("Bearer secret", self.stdin)
        self.assertEqual(self.command[:4], ["curl", "-q", "--config", "-"])
        for forbidden in ("-k", "--insecure", "-L", "--location", "--retry", "--verbose", "--trace"):
            self.assertNotIn(forbidden, self.command)
        self.assertIn("--max-filesize", self.command)
        self.assertIn("--max-time", self.command)
        self.assertLessEqual(self.kwargs["timeout"], 6)
        self.assertNotIn("TAVILY_API_KEY", self.kwargs["env"])
        self.assertNotIn("OPENALEX_API_KEY", self.kwargs["env"])

    def test_bounded_utf8_text_uses_same_transport(self):
        self.assertEqual(self.request(response_body=b"<rss/>", raw_text=True), "<rss/>")

    def test_http_statuses_never_expose_body_or_stderr(self):
        for http, expected in [(401, "auth_required"), (403, "blocked"), (402, "quota_exhausted"),
            (429, "quota_exhausted"), (432, "quota_exhausted"), (433, "quota_exhausted"),
            (302, "connection_failed"), (500, "connection_failed")]:
            with self.subTest(http=http), self.assertRaises(ChannelError) as raised:
                self.request(response_body=b"secret upstream error", status=str(http))
            self.assertEqual(raised.exception.status, expected)
            self.assertNotIn("secret", str(raised.exception))

    def test_curl_failures_missing_dependency_and_timeout(self):
        for code, expected in ((28, "timeout"), (63, "parse_error"), (60, "connection_failed"), (6, "connection_failed")):
            with self.subTest(code=code), self.assertRaises(ChannelError) as raised:
                self.request(returncode=code)
            self.assertEqual(raised.exception.status, expected)
        for error, expected in ((FileNotFoundError("secret"), "startup_failed"),
            (CommandUnavailableError("secret"), "startup_failed"),
            (subprocess.TimeoutExpired("secret", 5), "timeout"), (OSError("secret"), "connection_failed")):
            with patch.object(api, "run_command", side_effect=error), self.assertRaises(ChannelError) as raised:
                api._request("https://example.org", deadline=self.deadline)
            self.assertEqual(raised.exception.status, expected)
            self.assertNotIn("secret", str(raised.exception))

    def test_oversize_malformed_json_utf8_and_http_code(self):
        for body, status in ((b"x" * (api.MAX_BYTES + 1), "200"), (b"secret not json", "200"),
            (b"\xff", "200"), (b"{}", "secret")):
            with self.subTest(size=len(body), status=status), self.assertRaises(ChannelError) as raised:
                self.request(response_body=body, status=status)
            self.assertEqual(raised.exception.status, "parse_error")

    def test_control_injection_and_non_https_rejected_before_process(self):
        with patch.object(api, "run_command") as process:
            for url, headers in (("http://example.org", {}), ("https://example.org", {"Authorization": "secret\nurl=https://evil.org"}),
                                 ("https://user:secret@example.org", {})):
                with self.assertRaises(ChannelError):
                    api._request(url, deadline=self.deadline, headers=headers)
            process.assert_not_called()

    def test_deadline_shared_across_provider_subrequests(self):
        with patch.dict(os.environ, ENV, clear=True), patch.object(api, "_request", side_effect=FIXTURES["pubmed"]) as request:
            api.PROVIDERS["pubmed"]({"web_query": "AI", "web_timeout_s": 2})
        self.assertEqual(request.call_args_list[0].kwargs["deadline"], request.call_args_list[1].kwargs["deadline"])
        with patch.object(api, "run_command") as process, self.assertRaises(ChannelError) as raised:
            api._request("https://example.org", deadline=time.monotonic() - 1)
        self.assertEqual(raised.exception.status, "timeout")
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
