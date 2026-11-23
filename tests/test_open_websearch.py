import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from research_mcp.channels import open_websearch as web
from research_mcp.channels.errors import ChannelError


def envelope(rows=None, failures=None, engine="bing"):
    return {"status": "ok", "data": {"engines": [engine], "results": rows or [], "partialFailures": failures or []}}


class OpenWebSearchTest(unittest.TestCase):
    def test_empty_and_failed_success_envelope_are_distinct(self):
        self.assertEqual(web._parse(envelope(), "bing", 5), [])
        for message, expected in [("HTTP 403 CAPTCHA", "blocked"), ("Sogou returned a verification or anti-bot page", "blocked"), ("HTTP 429", "quota_exhausted"),
                                  ("HTTP 401", "auth_required"), ("timed out", "timeout")]:
            with self.subTest(expected=expected), self.assertRaises(ChannelError) as error:
                web._parse(envelope(failures=[{"engine": "bing", "message": message}]), "bing", 5)
            self.assertEqual(error.exception.status, expected)

    def test_mapping_preserves_backend_and_rank_and_cleans_html(self):
        row = {"title": "<em>RAG</em>指南", "url": "https://example.org/", "description": "<b>简介</b>", "engine": "bing"}
        items = web._parse(envelope([row, row]), "bing", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "RAG指南")
        self.assertEqual(items[0]["summary"], "简介")
        self.assertEqual(items[0]["rank"], 1)
        self.assertEqual(items[0]["actual_backend"], "open-webSearch/bing")

    def test_malformed_or_wrong_engine_never_becomes_empty(self):
        for payload in ({}, {"status": "ok", "data": {}}, envelope(engine="exa"),
                        envelope([{"title": "Missing URL", "engine": "bing"}]),
                        envelope([{"title": "x", "url": "https://example.org/", "engine": "exa"}])):
            with self.subTest(payload=payload), self.assertRaises(ChannelError) as error:
                web._parse(payload, "bing", 5)
            self.assertEqual(error.exception.status, "parse_error")

    def test_no_query_no_subprocess_and_unsupported_engine_is_rejected(self):
        with patch.object(web, "_execute") as execute:
            self.assertEqual(web.search({}, engine="bing"), [])
            with self.assertRaises(ChannelError):
                web.search({"web_query": "x"}, engine="exa")
        execute.assert_not_called()

    def test_subprocess_receives_only_requested_engine_and_request_mode(self):
        commands = []
        def run(command, **kwargs):
            commands.append((command, kwargs))
            if command[0] == "git":
                return subprocess.CompletedProcess(command, 0, web.REVISION, "")
            kwargs["stdout"].write(json.dumps(envelope()).encode())
            return subprocess.CompletedProcess(command, 0)
        with patch.object(web, "installation_ready", return_value=True), \
                patch.object(web.subprocess, "run", side_effect=run), \
                patch.dict(os.environ, {"EXA_API_KEY": "SECRET", "TAVILY_API_KEY": "SECRET", "NODE_OPTIONS": "SECRET", "OPEN_WEBSEARCH_DAEMON_URL": "SECRET", "SEARCH_MODE": "playwright"}):
            web.search({"web_query": "--search-mode playwright", "web_limit": 3}, engine="bing")
        command, kwargs = commands[-1]
        self.assertEqual(command[0], "node")
        self.assertNotIn("SECRET", str(command))
        self.assertNotIn("SECRET", str(kwargs["env"]))
        self.assertEqual(kwargs["env"]["SEARCH_MODE"], "request")
        self.assertEqual(kwargs["env"]["ALLOWED_SEARCH_ENGINES"], "bing")
        self.assertEqual(json.loads(kwargs["input"])["query"], "--search-mode playwright")
        self.assertEqual(kwargs["timeout"], web.TIMEOUT)

    def test_missing_installation_and_timeout(self):
        with patch.object(web, "installation_ready", return_value=False), self.assertRaises(ChannelError) as error:
            web._execute("x", "bing", 5)
        self.assertEqual(error.exception.status, "startup_failed")
        with patch.object(web, "installation_ready", return_value=True), \
                patch.object(web.subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 35)), \
                self.assertRaises(ChannelError) as error:
            web._execute("x", "bing", 5)
        self.assertEqual(error.exception.status, "timeout")

    def test_wrong_revision_does_not_execute_node(self):
        with patch.object(web, "installation_ready", return_value=True), \
                patch.object(web.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "wrong", "")) as run, \
                self.assertRaises(ChannelError) as error:
            web._execute("x", "bing", 5)
        self.assertEqual(error.exception.status, "startup_failed")
        self.assertEqual(run.call_count, 1)

    def test_node_timeout_closes_output_files_and_output_budget_is_enforced(self):
        files = []
        def timeout(command, **kwargs):
            if command[0] == "git":
                return subprocess.CompletedProcess(command, 0, web.REVISION, "")
            files.extend([kwargs["stdout"], kwargs["stderr"]])
            raise subprocess.TimeoutExpired(command, web.TIMEOUT)
        with patch.object(web, "installation_ready", return_value=True), patch.object(web.subprocess, "run", side_effect=timeout), self.assertRaises(ChannelError) as error:
            web._execute("x", "bing", 5)
        self.assertEqual(error.exception.status, "timeout")
        self.assertTrue(files and all(file.closed for file in files))

        def oversized(command, **kwargs):
            if command[0] == "git":
                return subprocess.CompletedProcess(command, 0, web.REVISION, "")
            kwargs["stdout"].write(b"x" * (web.MAX_BYTES + 1))
            return subprocess.CompletedProcess(command, 0)
        with patch.object(web, "installation_ready", return_value=True), patch.object(web.subprocess, "run", side_effect=oversized), self.assertRaises(ChannelError) as error:
            web._execute("x", "bing", 5)
        self.assertEqual(error.exception.status, "parse_error")
