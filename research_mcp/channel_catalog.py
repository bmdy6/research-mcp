"""Offline channel capabilities and process-local observations, without secrets.

Configured/unchecked is not a live availability promise. No API is probed by
listing, and unknown quota is null, never unlimited. Future API providers are
listed as unimplemented so possessing a key cannot silently enable a call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

from research_mcp.channels.errors import MESSAGES


def _entry(name, languages, content_type, backend, *, cost="keyless", key=False,
           login=False, license="upstream data terms", implemented=True, terms=""):
    return {"id": name, "cost_class": cost, "requires_key": key, "requires_login": login,
            "languages": languages, "content_type": content_type, "quota_remaining": None,
            "status": "unchecked" if implemented else "not_implemented", "last_checked_at": None,
            "license": license, "actual_backend": backend, "implemented": implemented,
            "upstream_terms": terms}


CATALOG = (
    _entry("github_search", ["en", "zh"], ["repository"], "GitHub CLI", login=True,
           terms="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service"),
    _entry("hacker_news", ["en"], ["discussion"], "HN Algolia API", terms="https://hn.algolia.com/api"),
    _entry("arxiv", ["en"], ["paper"], "arXiv Atom API (code-linked papers)",
           terms="https://info.arxiv.org/help/api/tou.html"),
    _entry("exa", ["zh", "en"], ["web"], "Exa official keyless MCP", terms="https://exa.ai/mcp"),
    _entry("juejin", ["zh"], ["technical_article"], "Juejin public search"),
    _entry("bilibili", ["zh", "en"], ["video"], "Bilibili public search / optional bili CLI"),
    *(_entry(engine, ["zh", "en"], ["web"], "open-webSearch/" + engine,
             license="Apache-2.0 (open-webSearch); upstream data terms",
             terms="https://github.com/Aas-ee/open-webSearch")
      for engine in ("baidu", "sogou", "bing", "duckduckgo")),
    _entry("tavily", ["zh", "en"], ["web"], "Tavily Researcher (future API adapter)",
           cost="free_recurring", key=True, implemented=False,
           terms="https://docs.tavily.com/documentation/api-credits"),
    _entry("openalex", ["en", "zh"], ["paper"], "OpenAlex (future API adapter)",
           cost="free_recurring", key=True, implemented=False, license="CC0 metadata",
           terms="https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication"),
)

_lock = Lock()
_observed: dict[str, dict] = {}


def record_observation(name: str, status: str) -> dict:
    observation = {"channel": name, "status": status,
                   "last_checked_at": datetime.now(timezone.utc).isoformat()}
    with _lock:
        # Do not grow an unbounded cache from arbitrary misspelled provider names.
        if any(row["id"] == name for row in CATALOG):
            _observed[name] = dict(observation)
    return observation


def reset_observations() -> None:
    with _lock:
        _observed.clear()


def list_channels() -> list[dict]:
    from research_mcp.channels.open_websearch import installation_ready, ENGINES
    with _lock:
        observed = {name: dict(value) for name, value in _observed.items()}
    rows = []
    for definition in CATALOG:
        row = dict(definition)
        row["languages"] = list(definition["languages"])
        row["content_type"] = list(definition["content_type"])
        if row["id"] in ENGINES and not installation_ready():
            row["status"] = "startup_failed"
            row["message"] = MESSAGES["startup_failed"]
        else:
            health = observed.get(row["id"])
            if health:
                row.update({k: health[k] for k in ("status", "last_checked_at")})
        rows.append(row)
    return rows
