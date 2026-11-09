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

SAFE_DNS_SERVER = r"""
import sys
sys.path.insert(0, sys.argv[1])
from research_mcp import safe_dns, mcp_server
safe_dns.SubprocessResolver.__call__ = lambda self, host, **kwargs: [sys.argv[2]]
mcp_server.mcp.run()
"""


@unittest.skipUnless(HAS_MCP, "optional mcp SDK missing; run the documented uv command")
class MCPStdioTest(unittest.IsolatedAsyncioTestCase):
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
