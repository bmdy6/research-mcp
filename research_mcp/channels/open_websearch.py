"""Four opt-in request-only engines via pinned upstream open-webSearch.

No automatic install, daemon discovery, shell, cookies, browser or paid API.
The JS bridge calls upstream's search service directly, avoiding the upstream
CLI's automatic localhost daemon routing. Parsing belongs to upstream.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from research_mcp.channels._util import clean_html
from research_mcp.channels.errors import ChannelError, failure_status

REVISION = "2562c9233a945c18fa9d08b7e450ca218a8853b6"
VERSION = "2.2.0"
ENGINES = ("baidu", "sogou", "bing", "duckduckgo", "csdn")
MAX_BYTES = 2_000_000
TIMEOUT = 35
BRIDGE = Path(__file__).with_name("open_websearch_bridge.mjs")


def installation_root() -> Path:
    override = os.environ.get("OPEN_WEBSEARCH_ROOT")
    return Path(override).expanduser() if override else Path.home() / ".cache/research-mcp/open-websearch" / REVISION


def installation_ready() -> bool:
    root = installation_root()
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        return (package.get("name") == "open-websearch" and package.get("version") == VERSION
                and (root / "build/runtime/createRuntime.js").is_file() and shutil.which("node") is not None)
    except (OSError, ValueError, AttributeError):
        return False


def _environment(engine: str) -> dict[str, str]:
    # Credentials, NODE_OPTIONS, remote browsers and daemon settings are not inherited.
    allowed = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SystemRoot", "LANG", "LC_ALL",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR")
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.update(SEARCH_MODE="request", MODE="stdio", DEFAULT_SEARCH_ENGINE=engine,
               ALLOWED_SEARCH_ENGINES=engine, OPEN_WEBSEARCH_QUIET_STARTUP="true")
    return env


def _execute(query: str, engine: str, limit: int) -> dict:
    if not installation_ready():
        raise ChannelError("startup_failed")
    root = installation_root().resolve()
    try:
        pinned = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=5)
        if pinned.returncode or pinned.stdout.strip() != REVISION:
            raise ChannelError("startup_failed")
        # File outputs bound memory even if the optional process emits junk.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(["node", str(BRIDGE), str(root)],
                                    input=json.dumps({"query": query, "engine": engine, "limit": limit}).encode(),
                                    stdout=stdout, stderr=stderr, env=_environment(engine), timeout=TIMEOUT)
            stdout.seek(0)
            raw = stdout.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ChannelError("parse_error")
        if result.returncode:
            raise ChannelError("startup_failed")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ChannelError("parse_error")
        return payload
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise ChannelError(failure_status(error)) from None
    except (ValueError, OSError):
        raise ChannelError("parse_error") from None


def _parse(payload: dict, engine: str, limit: int) -> list[dict]:
    if payload.get("status") != "ok":
        error = payload.get("error")
        if not isinstance(error, dict):
            raise ChannelError("parse_error")
        raise ChannelError(failure_status(error.get("message", "")))
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("engines") != [engine]:
        raise ChannelError("parse_error")
    failures = data.get("partialFailures")
    rows = data.get("results")
    if not isinstance(failures, list) or not isinstance(rows, list):
        raise ChannelError("parse_error")
    if failures:
        # One selected engine: a partialFailure invalidates even status:ok/zero rows.
        if any(not isinstance(f, dict) or f.get("engine") != engine for f in failures):
            raise ChannelError("parse_error")
        raise ChannelError(failure_status(" ".join(str(f.get("message", "")) for f in failures)))
    items = []
    for rank, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or row.get("engine") != engine
                or not isinstance(row.get("title"), str) or not row["title"].strip()
                or not isinstance(row.get("url"), str) or not row["url"].startswith(("https://", "http://"))
                or not isinstance(row.get("description", ""), str)):
            raise ChannelError("parse_error")
        items.append({"kind": "article", "title": clean_html(row["title"]), "url": row["url"],
                      "source": engine, "summary": clean_html(row.get("description", ""))[:1500],
                      "published_at": None, "rank": rank, "actual_backend": "open-webSearch/" + engine})
    return items[:limit]


def search(config: dict, *, engine: str) -> list[dict]:
    if engine not in ENGINES:
        raise ChannelError("unsupported")
    query = config.get("web_query")
    if not query:
        return []
    limit = max(1, min(int(config.get("web_limit", 5)), 40))
    return _parse(_execute(str(query), engine, limit), engine, limit)
