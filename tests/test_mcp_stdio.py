"""Real MCP stdio round trips with synthetic adapters and no provider calls.

Run: uv run --with mcp python -m unittest research.tests.test_mcp_stdio -v
The base offline suite may run without this optional transport dependency.
"""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HAS_MCP = importlib.util.find_spec("mcp") is not None
FIXTURE_SERVER = r"""
import sys
sys.path.insert(0, sys.argv[1])
from research_mcp import mcp_server as server

def search(query, *, limit, providers, rerank=True):
    if query == "exception":
        raise RuntimeError("SECRET-fixture")
    items = [] if query in ("empty", "failed") else [
        {"title": "合成标题🙂", "url": f"https://fixture.invalid/{n}", "summary": "合成资料" * 3000}
        for n in range(limit)
    ]
    failed = ([{"channel": "b", "error": "synthetic unavailable"}]
              if query == "partial" else [])
    succeeded = ["a"] if failed else ["a", "b"]
    if query == "failed":
        failed = [{"channel": name, "error": "synthetic unavailable"} for name in ("a", "b")]
        succeeded = []
    return {"query": query, "candidates": items, "total": len(items),
            "pooled": len(items), "returned": len(items), "truncated": False,
            "truncated_by": [], "attempted_channels": ["a", "b"],
            "succeeded_channels": succeeded, "failed_channels": failed}

def fetch_url(url):
    if url.endswith("/video"):
        return {"kind": "bilibili", "url": url, "content": "只有网页简介",
                "via": "jina", "transcript_error": "synthetic unavailable"}
    return {"kind": "article", "url": url, "ok": False,
            "error": "synthetic not found", "retryable": False}

server.S.R.search = search
server.S.R.fetch_url = fetch_url
server.mcp.run()
"""

CORE_LIMIT_SERVER = r"""
import sys
sys.path.insert(0, sys.argv[1])
from research_mcp import research, mcp_server
research._default_providers = lambda: {"hacker_news": lambda config: [
    {"title": f"Synthetic {index}", "source": "hacker_news",
     "url": f"https://fixture.invalid/{index}"} for index in range(12)
]}
mcp_server.mcp.run()
"""

SAFE_DNS_SERVER = r"""
import sys
sys.path.insert(0, sys.argv[1])
from research_mcp import safe_dns, mcp_server
safe_dns.SubprocessResolver.__call__ = lambda self, host, **kwargs: [sys.argv[2]]
mcp_server.mcp.run()
"""

SAFE_ERROR_SERVER = r"""
import sys
sys.path.insert(0, sys.argv[1])
from research_mcp import research, safe_fetch, mcp_server
research._validate_fetch_url = lambda url: None
def fetch(self, url):
    code = url.rsplit('/', 1)[-1]
    if code == 'success':
        return safe_fetch.FetchDocument(url, 'text/markdown', b'Synthetic body.')
    raise safe_fetch.SafeFetchError(code)
safe_fetch.SafeFetcher.fetch = fetch
mcp_server.mcp.run()
"""


BLOCKING_SERVER = r"""
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from research_mcp import mcp_server as server

root = Path(sys.argv[2])
def query(text, **kwargs):
    if text.startswith("blocked"):
        try:
            with tempfile.TemporaryDirectory(dir=root, prefix="worker-") as workspace:
                (root / "workspace").write_text(workspace)
                (root / "started").touch()
                deadline = time.monotonic() + 5
                while not (root / "release").exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("fixture release not received")
                    time.sleep(.01)
                if text == "blocked-error":
                    raise RuntimeError("synthetic late failure")
        finally:
            (root / "finished").touch()
    return {"api_version": "1", "operation": "search", "status": "empty",
            "ok": True, "candidates": [], "total": 0, "returned": 0, "errors": []}

server.S.query = query
server.mcp.run()
"""


@unittest.skipUnless(HAS_MCP, "optional mcp SDK missing; run the documented uv command")
class MCPStdioTest(unittest.IsolatedAsyncioTestCase):
    async def wait_for_file(self, path):
        async def wait():
            while not path.exists():
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), timeout=3)

    @asynccontextmanager
    async def blocked_call(self, query="blocked"):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-c", BLOCKING_SERVER, str(ROOT), str(root)], env={})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    # The SDK exposes no public request ID on call_tool(). Capture
                    # the next ID before scheduling, as in the issue's fixture.
                    request_id = session._request_id
                    call = asyncio.create_task(session.call_tool(
                        "research_search", {"query": query}))
                    try:
                        await self.wait_for_file(root / "started")
                        yield session, call, request_id, root
                    finally:
                        # Always release blocking work, including on a pre-fix
                        # assertion failure, and retrieve any tool exception.
                        (root / "release").touch()
                        await asyncio.wait_for(
                            asyncio.gather(call, return_exceptions=True), timeout=5)
                        await self.wait_for_file(root / "finished")

    async def test_tools_list_responds_while_query_is_blocked(self):
        async with self.blocked_call() as (session, call, _, root):
            listing = await asyncio.wait_for(session.list_tools(), timeout=1)
            self.assertEqual([tool.name for tool in listing.tools], ["research_search"])
            self.assertFalse(call.done())
            self.assertFalse((root / "finished").exists())
            (root / "release").touch()
            self.assertEqual(self.envelope(await call)["status"], "empty")

    async def test_cancellation_discards_result_and_worker_finishes_cleanup(self):
        from mcp.shared.exceptions import McpError
        from mcp.types import ClientNotification, CancelledNotification, CancelledNotificationParams

        for query in ("blocked", "blocked-error"):
            with self.subTest(query=query):
                async with self.blocked_call(query) as (session, call, request_id, root):
                    await session.send_notification(ClientNotification(
                        CancelledNotification(method="notifications/cancelled",
                            params=CancelledNotificationParams(requestId=request_id,
                                                               reason="synthetic cancellation"))))
                    # Cancellation must finish the protocol request before the
                    # synchronous worker is released, never return tool success.
                    with self.assertRaises(McpError) as cancelled:
                        await asyncio.wait_for(asyncio.shield(call), timeout=1)
                    self.assertIn("cancel", str(cancelled.exception).lower())
                    listing = await asyncio.wait_for(session.list_tools(), timeout=1)
                    self.assertEqual([tool.name for tool in listing.tools], ["research_search"])
                    self.assertFalse((root / "finished").exists())
                    workspace = Path((root / "workspace").read_text())
                    self.assertTrue(workspace.is_dir())
                    # A running thread cannot be killed: it keeps its normal
                    # cleanup and its eventual result/exception is discarded.
                    (root / "release").touch()
                    await self.wait_for_file(root / "finished")
                    self.assertFalse(workspace.exists())
                    result = self.envelope(await asyncio.wait_for(session.call_tool(
                        "research_search", {"query": "after-cancel"}), timeout=1))
                    self.assertEqual(result["status"], "empty")

    async def test_final_search_limit_survives_real_stdio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable, args=["-c", CORE_LIMIT_SERVER, str(ROOT)], env={})
        async with asyncio.timeout(20), stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = self.envelope(await session.call_tool(
                    "research_search", {"query": "synthetic", "limit": 5,
                                        "providers": ["hacker_news"]}))
                self.assertEqual((result["total"], result["pooled"], result["returned"]), (12, 12, 5))
                self.assertEqual(result["delivered"], 5)
                self.assertEqual(result["rerank"], "skipped_no_key")
                self.assertTrue(result["truncated"])
                self.assertEqual(result["truncated_by"], ["limit"])
                self.assertFalse(result["output_truncation"]["truncated"])

    async def test_all_safe_errors_and_http_upgrade_survive_real_stdio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from research_mcp.safe_fetch import SAFE_FETCH_MESSAGES

        params = StdioServerParameters(command=sys.executable, args=["-c", SAFE_ERROR_SERVER, str(ROOT)])
        async with asyncio.timeout(20), stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for code, message in SAFE_FETCH_MESSAGES.items():
                    with self.subTest(code=code):
                        result = self.envelope(await session.call_tool(
                            "research_search", {"query": "https://fixture.invalid/" + code}))
                        self.assertEqual(result["errors"][0]["code"], code)
                        self.assertEqual(result["errors"][0]["message"], message)
                result = self.envelope(await session.call_tool(
                    "research_search", {"query": "http://fixture.invalid/success"}))
                self.assertTrue(result["data"]["upgraded_to_https"])
                result = self.envelope(await session.call_tool(
                    "research_search", {"query": "http://fixture.invalid/TRANSPORT_FAILED"}))
                self.assertEqual(result["errors"][0]["code"], "UNSAFE_URL")
                self.assertEqual(result["errors"][0]["message"], SAFE_FETCH_MESSAGES["UNSAFE_URL"])

    async def test_safety_diagnostics_survive_real_stdio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        cases = (
            ("10.0.0.1", "目标地址不是公网地址，已拦截"),
            ("198.18.0.1", "目标域名解析到 198.18.0.0/15，常见于代理软件的 fake-ip 模式；确认是代理造成的，可以设置 RESEARCH_MCP_ALLOW_FAKE_IP=1"),
        )
        for address, message in cases:
            with self.subTest(address=address):
                params = StdioServerParameters(
                    command=sys.executable, args=["-c", SAFE_DNS_SERVER, str(ROOT), address],
                    env={**os.environ, "RESEARCH_MCP_ALLOW_FAKE_IP": ""},
                )
                async with asyncio.timeout(20), stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = self.envelope(await session.call_tool(
                            "research_search", {"query": "https://fixture.invalid/"}))
                        self.assertEqual(result["errors"][0]["code"], "UNSAFE_ADDRESS")
                        self.assertEqual(result["errors"][0]["message"], message)
                        self.assertEqual(result["data"]["error"], message)

    @asynccontextmanager
    async def session(self, *, fixtures):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        args = ["-c", FIXTURE_SERVER, str(ROOT)] if fixtures else ["-m", "research_mcp.mcp_server"]
        async with stdio_client(StdioServerParameters(command=sys.executable, args=args)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    def envelope(self, response):
        wire = response.model_dump(by_alias=True)
        self.assertFalse(wire["isError"])
        text = wire["content"][0]["text"]
        self.assertLessEqual(len(text), 200_000)
        payload = json.loads(text)
        self.assertEqual(payload["api_version"], "1")
        # String annotations produce a result wrapper on structured SDKs.
        if wire.get("structuredContent") is not None:
            self.assertEqual(wire["structuredContent"]["result"], text)
        return payload

    async def test_unmodified_server_schema_and_validation_round_trip(self):
        async with asyncio.timeout(20), self.session(fixtures=False) as session:
            tools = (await session.list_tools()).model_dump(by_alias=True)["tools"]
            self.assertEqual([tool["name"] for tool in tools], ["research_search"])
            schema = tools[0]["inputSchema"]
            self.assertEqual(schema["required"], ["query"])
            self.assertEqual(set(schema["properties"]), {"query", "limit", "providers"})
            self.assertEqual(schema["properties"]["limit"]["default"], 12)
            annotations = tools[0].get("annotations") or {}
            self.assertIs(annotations.get("readOnlyHint"), True)
            self.assertIs(annotations.get("destructiveHint"), False)
            result = self.envelope(await session.call_tool("research_search", {"query": ""}))
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["errors"][0]["code"], "INVALID_INPUT")

    async def test_search_and_fetch_envelopes_survive_real_stdio_transport(self):
        async with asyncio.timeout(20), self.session(fixtures=True) as session:
            for query, status in (("ok", "ok"), ("empty", "empty"), ("partial", "partial"),
                                  ("failed", "error"), ("exception", "error")):
                with self.subTest(query=query):
                    result = self.envelope(await session.call_tool(
                        "research_search", {"query": query, "limit": 100, "providers": ["a", "b"]}))
                    self.assertEqual(result["status"], status)
                    self.assertNotIn("SECRET-fixture", json.dumps(result))
                    if query == "ok":
                        self.assertEqual(result["returned"], 40)
                        self.assertEqual(result["delivered"], len(result["candidates"]))
                        self.assertTrue(result["output_truncation"]["truncated"])
                    if query == "failed":
                        self.assertEqual(result["errors"][0]["code"], "ALL_CHANNELS_FAILED")
            video = self.envelope(await session.call_tool(
                "research_search", {"query": "https://fixture.invalid/video"}))
            self.assertEqual(video["status"], "partial")
            self.assertEqual(video["data"]["content"], "只有网页简介")
            self.assertEqual(video["errors"][0]["code"], "TRANSCRIPT_UNAVAILABLE")
            failed = self.envelope(await session.call_tool(
                "research_search", {"query": "https://fixture.invalid/missing"}))
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["errors"][0]["code"], "READ_FAILED")


if __name__ == "__main__":
    unittest.main()
