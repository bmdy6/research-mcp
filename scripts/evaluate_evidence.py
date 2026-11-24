#!/usr/bin/env python3
"""Fixed evidence-retrieval checks; offline by default, opt in with --live.

JSON reports contain metadata and rule hits only, never fetched full text.
Rule hits measure retrieval, not medical correctness or clinical efficacy.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit


AAD = "https://www.aad.org/public/"
CASES = [
    {"id": "search-dry-skin", "operation": "search", "query": "site:aad.org dry skin relief dermatologist moisturizer cream", "providers": ["exa"], "expected_url": "aad.org/public/"},
    {"id": "search-pores", "operation": "search", "query": "site:aad.org What can treat large facial pores", "providers": ["exa"], "expected_url": "aad.org/public/everyday-care/skin-care-secrets/face/treat-large-pores"},
    {"id": "search-classic-2005", "operation": "search", "query": "EXT_ID:16029679 AND SRC:MED", "providers": ["europe_pmc"], "expected_url": "europepmc.org/article/MED/16029679"},
    {"id": "search-pubmed-relevance", "operation": "search", "query": 'niacinamide aging facial skin appearance', "providers": ["pubmed"], "expected_title": "niacinamide"},
    {"id": "read-pores", "operation": "fetch", "query": AAD + "everyday-care/skin-care-secrets/face/treat-large-pores", "phrases": ["salicylic acid", "irritat"]},
    {"id": "read-adult-acne", "operation": "fetch", "query": AAD + "diseases/acne/diy/adult-acne-treatment", "phrases": ["salicylic acid", "blackheads"]},
    {"id": "read-workout", "operation": "fetch", "query": AAD + "diseases/acne/causes/workouts", "phrases": ["sweat", "wash"]},
    {"id": "read-dry-skin", "operation": "fetch", "query": AAD + "everyday-care/skin-care-basics/dry/dermatologists-tips-relieve-dry-skin", "phrases": ["cream", "fragrance"]},
    {"id": "reject-private-target", "operation": "fetch", "query": "https://127.0.0.1/", "expect_error": "UNSAFE_ADDRESS"},
    {"id": "reject-invalid-scheme", "operation": "fetch", "query": "file:///example-public-fixture", "expect_error": "INVALID_INPUT"},
    {"id": "reject-js-shell", "operation": "fetch", "query": "https://example.com/evaluation-js-shell", "expect_error": "NO_READABLE_CONTENT", "fixture_only": True},
    {"id": "reject-access-challenge", "operation": "fetch", "query": "https://example.com/evaluation-access-challenge", "expect_error": "ACCESS_CHALLENGE", "fixture_only": True},
]


def _fixture_context(case, service, research):
    """Synthetic source adapters; retain real selection, parsing and budgeting."""
    stack = ExitStack()
    if case["operation"] == "search":
        provider = case["providers"][0]
        if provider == "pubmed":
            from research_mcp.channels import free_apis
            def get(endpoint, params, deadline):
                if endpoint.endswith("esearch.fcgi"):
                    return {"esearchresult": {"idlist": ["16029679" if params.get("sort") == "relevance" else "99999999"]}}
                uid = params["id"]
                return {"result": {"uids": [uid], uid: {
                    "title": "Niacinamide: A B vitamin that improves aging facial skin appearance." if uid == "16029679" else "Unrelated recent research record.",
                    "pubdate": "2005 Jul" if uid == "16029679" else "2026 Nov", "fulljournalname": "Synthetic journal"}}}
            stack.enter_context(patch.object(free_apis, "_get", side_effect=get))
            stack.enter_context(patch.object(research, "_default_providers", return_value={provider: free_apis.PROVIDERS[provider]}))
        else:
            url = "https://" + case["expected_url"]
            row = {"title": "Synthetic source for " + case["id"], "url": url, "source": provider,
                   "published_at": "2005-07-01T00:00:00Z" if provider == "europe_pmc" else "2026-11-01T00:00:00Z"}
            stack.enter_context(patch.object(research, "_default_providers", return_value={provider: lambda config: [row]}))
    elif case["id"] not in ("reject-private-target", "reject-invalid-scheme"):
        stack.enter_context(patch.object(research, "_validate_fetch_url"))
        if case["id"] == "reject-js-shell":
            html = "<html><body><main><h1>Loading...</h1></main><script>renderApp()</script></body></html>"
        elif case["id"] == "reject-access-challenge":
            html = "<html><body><main><h1>Just a moment...</h1><p>Verify you are human to continue.</p></main></body></html>"
        else:
            text = " ".join(case["phrases"])
            prefix = "\ufeff<!-- synthetic page comment -->" if case["id"] == "read-dry-skin" else ""
            html = (prefix + "<!doctype html><html><head><script>tracking()</script></head><body><nav>"
                    + "Navigation menu " * 1000 + "</nav><main><header><h1>Public source</h1></header><p>"
                    + text + " — synthetic evidence fixture.</p></main></body></html>")
        stack.enter_context(patch.object(research, "_safe_http_text", side_effect=[research.FetchError("Synthetic reader unavailable"), html]))
    return stack


def _source_matches(url, expected):
    try:
        actual, target = urlsplit(url), urlsplit("https://" + expected)
        if actual.scheme not in ("http", "https"):
            return False
        host = lambda parsed: (parsed.hostname or "").lower().rstrip(".").removeprefix("www.")
        if host(actual) != host(target):
            return False
        return (actual.path.startswith(target.path) if target.path.endswith("/")
                else actual.path.rstrip("/") == target.path.rstrip("/"))
    except (TypeError, ValueError):
        return False


def assess(case, result):
    hits = {}
    if case.get("expect_error"):
        hits["explicit_failure"] = result.get("status") == "error" and result.get("ok") is False
        hits["expected_error_code"] = any(error.get("code") == case["expect_error"] for error in result.get("errors", []))
    elif case["operation"] == "search":
        candidates = result.get("candidates", [])
        hits["successful_source"] = result.get("status") == "ok"
        if "expected_url" in case:
            hits["expected_source_hit"] = any(_source_matches(row.get("url", ""), case["expected_url"]) for row in candidates)
        if "expected_title" in case:
            hits["relevant_title_hit"] = any(case["expected_title"] in row.get("title", "").lower() for row in candidates)
    else:
        content = (result.get("data") or {}).get("content", "")
        hits["successful_read"] = result.get("status") == "ok"
        hits["readable_not_raw_html"] = not any(tag in content.lower() for tag in ("<html", "<!doctype", "<script", "<nav"))
        for phrase in case.get("phrases", []):
            hits["paragraph:" + phrase] = phrase.lower() in content.lower()
    return hits


def evaluate(*, live=False):
    from research_mcp import research, service
    from research_mcp.result_contract import serialize_result
    rows = []
    for case in CASES:
        row = {key: case[key] for key in ("id", "operation", "query")}
        if live and case.get("fixture_only"):
            rows.append({**row, "status": "skipped", "elapsed_seconds": 0, "rule_hits": {}})
            continue
        started = time.monotonic()
        context = ExitStack() if live else _fixture_context(case, service, research)
        with context:
            if case["operation"] == "search":
                result = service.search(case["query"], providers=case["providers"], limit=5)
            else:
                result = service.fetch(case["query"])
            result = json.loads(serialize_result(result))
        hits = assess(case, result)
        row.update(status=result.get("status"), elapsed_seconds=round(time.monotonic() - started, 3),
                   rule_hits=hits, passed=bool(hits) and all(hits.values()))
        row["sources"] = [{key: item[key] for key in ("url", "title") if key in item}
                          for item in result.get("candidates", [])]
        if case["operation"] == "fetch":
            row["url"] = (result.get("data") or {}).get("url", case["query"])
        rows.append(row)
    checked = [row for row in rows if row["status"] != "skipped"]
    return {"evaluated_at": datetime.now(timezone.utc).isoformat(), "mode": "live" if live else "offline",
            "scope": "Fixed retrieval rules; hits do not establish medical correctness. Offline sources are synthetic.",
            "cases": rows, "summary": {"passed": sum(row["passed"] for row in checked), "checked": len(checked),
                                         "skipped": len(rows) - len(checked)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Read public sources over the network; no model or browser")
    parser.add_argument("--code-root", type=Path, default=Path(__file__).resolve().parents[1], help="Checkout to evaluate for the same-case A/B")
    parser.add_argument("--output", type=Path, help="Write metadata-only JSON report")
    args = parser.parse_args()
    sys.path.insert(0, str(args.code_root))
    report = evaluate(live=args.live)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    print(json.dumps(report["summary"]))
    return 0 if report["summary"]["passed"] == report["summary"]["checked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
