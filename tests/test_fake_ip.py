"""Synthetic DNS regression coverage for the explicit fake-IP exception."""
import asyncio
import contextlib
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch

from research_mcp import cli, commands, research, safe_dns, service
from research_mcp.safe_fetch import SafeFetcher, SafeFetchError

FLAG = "RESEARCH_MCP_ALLOW_FAKE_IP"
BLOCKED = "目标地址不是公网地址，已拦截"
FAKE_IP = "目标域名解析到 198.18.0.0/15，常见于代理软件的 fake-ip 模式；确认是代理造成的，可以设置 RESEARCH_MCP_ALLOW_FAKE_IP=1"
PUBLIC = "93.184.216.34"


class Response:
    status = 200
    headers = [("Content-Type", "text/plain")]

    def __init__(self, request, redirect=None):
        self.peer_address = request.address
        self.tls_verified_hostname = request.hostname
        self.body = b"Synthetic body"
        self.closed = False
        if redirect:
            self.status = 302
            self.headers = [("Location", redirect)]

    def read(self, size, *, deadline):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def close(self):
        self.closed = True


def fetcher(addresses):
    resolver = Mock(return_value=addresses)
    transport = Mock(side_effect=lambda request, **_: Response(request))
    return SafeFetcher(resolver=resolver, transport=transport), transport


class FakeIPPolicyTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {FLAG: ""})
        env.start()
        self.addCleanup(env.stop)

    def test_default_fake_ip_has_fixed_hint_and_original_code(self):
        reader, transport = fetcher(["198.18.0.1"])
        with self.assertRaises(SafeFetchError) as caught:
            reader.fetch("https://fixture.invalid/private-query")
        self.assertEqual(caught.exception.code, "UNSAFE_ADDRESS")
        self.assertEqual(str(caught.exception), FAKE_IP)
        self.assertNotIn("fixture.invalid", str(caught.exception))
        self.assertNotIn("198.18.0.1", str(caught.exception))
        transport.assert_not_called()

    def test_only_exact_flag_value_one_allows_the_entire_fake_ip_range(self):
        for value in ("", "0", "true", "yes", " 1", "1 "):
            with self.subTest(value=value), patch.dict(os.environ, {FLAG: value}):
                reader, transport = fetcher(["198.18.0.1"])
                with self.assertRaises(SafeFetchError):
                    reader.fetch("https://fixture.invalid/")
                transport.assert_not_called()
        with patch.dict(os.environ, {FLAG: "1"}):
            for address in ("198.18.0.0", "198.18.0.1", "198.19.255.255"):
                with self.subTest(address=address):
                    reader, transport = fetcher([address])
                    self.assertEqual(reader.fetch("https://fixture.invalid/").body, b"Synthetic body")
                    self.assertEqual(transport.call_args.args[0].address, address)

    def test_opt_in_keeps_all_other_non_public_and_mixed_answers_blocked(self):
        addresses = ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1",
                     "169.254.169.254", "::1", "fc00::1", "fe80::1", "0.0.0.0",
                     "100.64.0.1", "192.0.2.1", "::ffff:198.18.0.1")
        with patch.dict(os.environ, {FLAG: "1"}):
            for address in addresses:
                with self.subTest(address=address):
                    reader, transport = fetcher(["198.18.0.1", address])
                    with self.assertRaises(SafeFetchError) as caught:
                        reader.fetch("https://fixture.invalid/")
                    self.assertEqual(caught.exception.code, "UNSAFE_ADDRESS")
                    self.assertEqual(str(caught.exception), BLOCKED)
                    transport.assert_not_called()

    def test_redirects_recheck_resolved_and_literal_destinations(self):
        for enabled in ("", "1"):
            for target in ("https://next.invalid/", "https://198.18.0.1/"):
                with self.subTest(enabled=enabled, target=target), patch.dict(os.environ, {FLAG: enabled}):
                    resolver = Mock(side_effect=lambda host, **_: [PUBLIC] if host == "start.invalid" else ["198.18.0.1"])
                    responses = []
                    def transport(request, **_):
                        response = Response(request, target if len(responses) == 0 else None)
                        responses.append(response)
                        return response
                    reader = SafeFetcher(resolver=resolver, transport=transport)
                    if enabled:
                        self.assertEqual(reader.fetch("https://start.invalid/").body, b"Synthetic body")
                        self.assertEqual(len(responses), 2)
                    else:
                        with self.assertRaises(SafeFetchError) as caught:
                            reader.fetch("https://start.invalid/")
                        self.assertEqual(str(caught.exception), FAKE_IP)
                        self.assertEqual(len(responses), 1)
                    self.assertTrue(all(response.closed for response in responses))
        with patch.dict(os.environ, {FLAG: "1"}):
            resolver = Mock(side_effect=[[PUBLIC], ["10.0.0.1"]])
            transport = Mock(side_effect=lambda request, **_: Response(request, "https://private.invalid/"))
            with self.assertRaises(SafeFetchError):
                SafeFetcher(resolver=resolver, transport=transport).fetch("https://start.invalid/")
            self.assertEqual(transport.call_count, 1)

    def test_pinned_transport_uses_the_same_fake_ip_policy(self):
        from research_mcp.safe_fetch import Deadline, PinnedRequest
        from research_mcp.safe_transport import PinnedIPTransport
        request = PinnedRequest("https://fixture.invalid/", "fixture.invalid", "198.18.0.1",
                                "fixture.invalid", "fixture.invalid")
        for enabled in ("", "1"):
            with self.subTest(enabled=enabled), patch.dict(os.environ, {FLAG: enabled}), patch("research_mcp.safe_transport.socket.socket", side_effect=RuntimeError("synthetic socket reached")) as socket:
                if enabled:
                    with self.assertRaisesRegex(RuntimeError, "synthetic socket reached"):
                        PinnedIPTransport()(request, deadline=Deadline(10, lambda: 0))
                    socket.assert_called_once()
                else:
                    with self.assertRaises(SafeFetchError) as caught:
                        PinnedIPTransport()(request, deadline=Deadline(10, lambda: 0))
                    self.assertEqual(str(caught.exception), FAKE_IP)
                    socket.assert_not_called()


class FakeIPOutputTest(unittest.TestCase):
    def assert_failure(self, result, message):
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["errors"][0]["code"], "UNSAFE_ADDRESS")
        self.assertEqual(result["errors"][0]["message"], message)
        self.assertEqual(result["data"]["error"], message)

    def test_cli_and_mcp_adapter_preserve_fixed_messages_and_code(self):
        class FakeMCP:
            def __init__(self, name): pass
            def tool(self): return lambda function: function
        stub = types.ModuleType("mcp.server.mcpserver")
        stub.MCPServer = FakeMCP
        spec = importlib.util.spec_from_file_location("offline_fake_ip_mcp", research.__file__.replace("research.py", "mcp_server.py"))
        server = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"mcp": types.ModuleType("mcp"), "mcp.server": types.ModuleType("mcp.server"), "mcp.server.mcpserver": stub}):
            spec.loader.exec_module(server)
        for address, message in (("198.18.0.1", FAKE_IP), ("10.0.0.1", BLOCKED)):
            with self.subTest(address=address), patch.dict(os.environ, {FLAG: ""}), patch.object(safe_dns.SubprocessResolver, "__call__", return_value=[address]), patch("research_mcp.safe_transport.PinnedIPTransport.__call__") as transport:
                output = io.StringIO()
                with patch.object(sys, "argv", ["research-mcp", "fetch", "https://fixture.invalid/", "--format", "json"]), contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 1)
                self.assert_failure(json.loads(output.getvalue()), message)
                self.assert_failure(json.loads(asyncio.run(server.research_search("https://fixture.invalid/"))), message)
                transport.assert_not_called()

    def test_reader_and_raw_fallback_preserve_safety_code(self):
        for address, message in (("198.18.0.1", FAKE_IP), ("10.0.0.1", BLOCKED)):
            with self.subTest(address=address), patch.dict(os.environ, {FLAG: ""}), patch.object(research, "_validate_fetch_url"), patch.object(safe_dns.SubprocessResolver, "__call__", return_value=[address]):
                self.assert_failure(service.fetch("https://fixture.invalid/"), message)

    def test_unrecognized_safety_text_cannot_bypass_sanitization(self):
        with patch.object(research, "fetch_url", return_value={
            "ok": False, "error_code": "UNSAFE_ADDRESS", "error": "SECRET-synthetic-provider-text",
        }):
            result = service.fetch("https://fixture.invalid/")
        self.assertEqual(result["errors"][0]["code"], "READ_FAILED")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_install_hints_work_across_platforms(self):
        self.assertIn("https://cli.github.com", commands.HINTS["gh"])
        self.assertEqual(commands.HINTS["curl"], "curl 不可用；用系统包管理器安装 curl")
        self.assertNotIn("brew", commands.HINTS["gh"])
        self.assertEqual(commands.HINTS["bili"], "bili 不可用；安装：uv tool install 'bilibili-cli[audio]'；登录：bili login")
