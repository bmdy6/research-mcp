"""HTTP upgrades and exhaustive safe-fetch diagnostics without network I/O."""
import asyncio
import contextlib
import importlib.util
import io
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

from research_mcp import cli, research, safe_dns, safe_fetch, service

HTTPS_ONLY = "只支持公开的 https 链接；这个链接不符合读取规则"


def offline_mcp():
    class FakeMCP:
        def __init__(self, name): pass
        def tool(self): return lambda function: function
    stub = types.ModuleType("mcp.server.mcpserver")
    stub.MCPServer = FakeMCP
    spec = importlib.util.spec_from_file_location("offline_https_mcp", research.__file__.replace("research.py", "mcp_server.py"))
    server = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"mcp": types.ModuleType("mcp"), "mcp.server": types.ModuleType("mcp.server"), "mcp.server.mcpserver": stub}):
        spec.loader.exec_module(server)
    return server


class HTTPSUpgradeTest(unittest.TestCase):
    def test_http_success_upgrades_same_authority_path_query_and_marks_result(self):
        url = "http://fixture.invalid/article/?q=a%2Fb&view=full&ref=keep"
        with patch.object(safe_dns.SubprocessResolver, "__call__", return_value=["93.184.216.34"]) as resolver, patch.object(research, "_safe_http_text", return_value="# Synthetic\n\nSynthetic body") as reader:
            result = service.fetch(url)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["upgraded_to_https"])
        self.assertEqual(result["data"]["url"], url.replace("http://", "https://", 1))
        self.assertTrue(resolver.called)
        reader.assert_called_once_with(research.JINA_PREFIX + url.replace("http://", "https://", 1))

    def test_upgrade_transport_failure_has_fixed_unsafe_url_diagnosis(self):
        with patch.object(research, "_validate_fetch_url"), patch.object(safe_fetch.SafeFetcher, "fetch", side_effect=safe_fetch.SafeFetchError("TRANSPORT_FAILED")):
            result = service.fetch("http://fixture.invalid/article")
        self.assertEqual(result["errors"][0]["code"], "UNSAFE_URL")
        self.assertEqual(result["errors"][0]["message"], HTTPS_ONLY)
        self.assertNotIn("upgraded_to_https", result.get("data", {}))

    def test_upgraded_reader_still_runs_safe_fetcher_and_closes_response(self):
        responses = []
        def transport(request, **kwargs):
            response = Mock(status=200, headers=[("Content-Type", "text/markdown")],
                            peer_address=request.address, tls_verified_hostname=request.hostname)
            response.read.side_effect = [b"Synthetic body.", b""]
            responses.append(response)
            return response
        with patch.object(safe_dns.SubprocessResolver, "__call__", return_value=["93.184.216.34"]) as resolver, patch("research_mcp.safe_transport.PinnedIPTransport.__call__", side_effect=transport) as wire:
            result = service.fetch("http://fixture.invalid/article/?ref=keep")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["upgraded_to_https"])
        self.assertEqual(wire.call_args.args[0].url, research.JINA_PREFIX + "https://fixture.invalid/article/?ref=keep")
        self.assertIn("r.jina.ai", [call.args[0] for call in resolver.call_args_list])
        responses[0].close.assert_called_once()

    def test_http_private_addresses_still_fail_before_network(self):
        for host in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "[::1]"):
            with self.subTest(host=host), patch("research_mcp.safe_transport.socket.socket") as socket:
                result = service.fetch("http://" + host + "/")
                self.assertEqual(result["errors"][0]["code"], "UNSAFE_ADDRESS")
                self.assertEqual(result["errors"][0]["message"], "目标地址不是公网地址，已拦截")
                socket.assert_not_called()

    def test_http_credentials_and_unsafe_ports_are_not_normalized_away(self):
        for url in ("http://user:pass@fixture.invalid/path", "http://fixture.invalid:80/", "http://fixture.invalid:444/", "http://fixture.invalid/#part", "https://[invalid"):
            with self.subTest(url=url), patch.object(safe_dns.SubprocessResolver, "__call__") as resolver, patch("research_mcp.safe_transport.socket.socket") as socket:
                result = service.fetch(url)
                self.assertEqual(result["errors"][0]["code"], "UNSAFE_URL")
                self.assertEqual(result["errors"][0]["message"], HTTPS_ONLY)
                resolver.assert_not_called()
                socket.assert_not_called()

    def test_https_success_has_no_upgrade_marker(self):
        with patch.object(research, "_validate_fetch_url"), patch.object(research, "_safe_http_text", return_value="Synthetic body."):
            result = service.fetch("https://fixture.invalid/")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("upgraded_to_https", result["data"])

    def test_upgrade_marker_survives_cli_and_mcp_output(self):
        server = offline_mcp()
        with patch.object(research, "_validate_fetch_url"), patch.object(research, "_safe_http_text", return_value="Synthetic body."):
            output = io.StringIO()
            with patch.object(sys, "argv", ["research-mcp", "fetch", "http://fixture.invalid/", "--format", "json"]), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            self.assertTrue(json.loads(output.getvalue())["data"]["upgraded_to_https"])
            self.assertTrue(json.loads(asyncio.run(server.research_search("http://fixture.invalid/")))["data"]["upgraded_to_https"])


class SafeErrorContractTest(unittest.TestCase):
    def test_every_registered_safe_error_reaches_cli_and_mcp_with_fixed_text(self):
        server = offline_mcp()
        messages = safe_fetch.SAFE_FETCH_MESSAGES
        for code, message in messages.items():
            with self.subTest(code=code), patch.object(research, "_validate_fetch_url"), patch.object(safe_fetch.SafeFetcher, "fetch", side_effect=safe_fetch.SafeFetchError(code)):
                output = io.StringIO()
                with patch.object(sys, "argv", ["research-mcp", "fetch", "https://fixture.invalid/", "--format", "json"]), contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 1)
                for result in (json.loads(output.getvalue()), json.loads(asyncio.run(server.research_search("https://fixture.invalid/")))):
                    self.assertEqual(result["errors"][0]["code"], code)
                    self.assertEqual(result["errors"][0]["message"], message)
                    self.assertEqual(result["data"]["error"], message)
        self.assertEqual(str(safe_fetch.SafeFetchError("UNSAFE_URL")), HTTPS_ONLY)

    def test_future_registered_error_is_automatically_preserved(self):
        messages = safe_fetch.SAFE_FETCH_MESSAGES
        with patch.dict(messages, {"SYNTHETIC_FUTURE_ERROR": "Synthetic fixed diagnosis"}), patch.object(research, "_validate_fetch_url"), patch.object(safe_fetch.SafeFetcher, "fetch", side_effect=safe_fetch.SafeFetchError("SYNTHETIC_FUTURE_ERROR")):
            result = service.fetch("https://fixture.invalid/")
        self.assertEqual(result["errors"][0]["code"], "SYNTHETIC_FUTURE_ERROR")
        self.assertEqual(result["errors"][0]["message"], "Synthetic fixed diagnosis")

    def test_forged_known_code_with_dynamic_text_is_not_trusted(self):
        with patch.object(research, "fetch_url", return_value={"ok": False, "error_code": "DNS_FAILED", "error": "SECRET-synthetic"}):
            result = service.fetch("https://fixture.invalid/")
        self.assertEqual(result["errors"][0]["code"], "READ_FAILED")
        self.assertNotIn("SECRET", json.dumps(result))
