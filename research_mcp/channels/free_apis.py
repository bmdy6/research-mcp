"""Bounded official search APIs; no scraping, inference, paid fallback or retries.

Tavily and OpenAlex require both an environment key and *_FREE_ONLY=1. This
asserts that the owner has disabled paid usage. We additionally preflight usage:
Tavily must report a Researcher/free plan with zero PAYG, OpenAlex must report a
daily budget <= $1 with no prepaid balance. A live Tavily account can return an
explicit null PAYG limit; this is ambiguous, so it additionally requires
TAVILY_PAYG_DISABLED=1, set only after the owner verifies PAYG is disabled in the
account UI. Null alone never establishes free-only status; absent fields fail
closed. Checks cannot reserve account credit atomically against other clients;
use a free-only account. No key creation, account changes, vault access or logs.

Official contracts (checked 2026-11-23):
https://docs.tavily.com/documentation/api-reference/endpoint/search
https://docs.tavily.com/documentation/api-reference/endpoint/usage
https://help.openalex.org/api/authentication/
https://www.mediawiki.org/wiki/API:REST_API/Reference
https://www.mediawiki.org/wiki/Wikibase/API
https://www.crossref.org/documentation/retrieve-metadata/rest-api/
https://api.stackexchange.com/docs/advanced-search
https://huggingface.co/docs/hub/api
https://europepmc.org/RestfulWebService
https://eutilities.github.io/site/Quick_Start/eu_quick/
https://openlibrary.org/dev/docs/api/search
https://performance-partners.apple.com/search-api
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import subprocess
import tempfile
import time
from functools import partial
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlencode, urlsplit

from research_mcp.commands import CommandUnavailableError, run as run_command
from ._util import clean_html, iso_from_epoch
from .errors import ChannelError

MAX_BYTES = 2_000_000
MAX_RESULTS = 50
BACKENDS = {
    "tavily": "Tavily official basic Search API (free-only)",
    "openalex": "OpenAlex official works API (free-only)",
    "wikipedia": "Wikipedia official MediaWiki REST API",
    "wikidata": "Wikidata official wbsearchentities API",
    "crossref": "Crossref official REST API",
    "stackexchange": "Stack Exchange official API (Stack Overflow)",
    "huggingface": "Hugging Face official public model API",
    "europe_pmc": "Europe PMC official REST API",
    "pubmed": "NCBI PubMed official E-utilities",
    "openlibrary": "Open Library official Search API",
    "itunes_podcasts": "Apple iTunes official podcast Search API",
}
_lock = Lock()
_limited_until: dict[str, float] = {}


def _remember_limit(provider: str, seconds: float = 300) -> None:
    with _lock:
        _limited_until[provider] = time.monotonic() + max(1, min(seconds, 86400))


def _reset_rate_limit_state() -> None:
    """Test isolation; process-local observations are never persisted."""
    with _lock:
        _limited_until.clear()


def _config_value(value: str) -> str:
    # curl config quoting is not shell quoting. Reject header/control injection.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ChannelError("parse_error")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _request(url: str, *, deadline: float, body: dict | None = None,
             headers: dict | None = None, raw_text: bool = False) -> object:
    """TLS verified curl; secrets only enter stdin, never argv or error text.

    No redirects, retry, curlrc, response logging, cookies, or output URL. Body
    and transfer size plus wall time are bounded. Only fixed HTTP statuses escape.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ChannelError("timeout")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password:
        raise ChannelError("parse_error")
    configuration = ["url = " + _config_value(url)]
    for name, value in {"Accept": "application/json", "User-Agent":
                        "research-mcp/0.1 (https://github.com/bmdy6/research-mcp)",
                        **(headers or {})}.items():
        configuration.append("header = " + _config_value(f"{name}: {value}"))
    if body is not None:
        configuration.extend(['header = "Content-Type: application/json"',
                              "data = " + _config_value(json.dumps(body, ensure_ascii=True))])
    try:
        with tempfile.TemporaryDirectory(prefix="research-free-api-") as directory:
            output = Path(directory) / "body"
            command = ["curl", "-q", "--config", "-", "--silent", "--show-error",
                       "--proto", "=https", "--compressed", "--max-time", str(remaining),
                       "--connect-timeout", str(min(10, remaining)),
                       "--max-filesize", str(MAX_BYTES), "--output", str(output),
                       "--write-out", "%{http_code}"]
            result = run_command(command, input="\n".join(configuration) + "\n",
                                 capture_output=True, text=True, timeout=remaining + 1,
                                 env={name: value for name, value in os.environ.items()
                                      if name not in ("TAVILY_API_KEY", "OPENALEX_API_KEY")})
            if result.returncode:
                raise ChannelError("timeout" if result.returncode == 28 else
                                   "parse_error" if result.returncode == 63 else "connection_failed")
            status = int(result.stdout.strip())
            if status == 401:
                raise ChannelError("auth_required")
            if status == 403:
                raise ChannelError("blocked")
            if status in (402, 429, 432, 433):
                raise ChannelError("quota_exhausted")
            if not 200 <= status < 300:
                raise ChannelError("connection_failed")
            with output.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ChannelError("parse_error")
            return data.decode("utf-8") if raw_text else json.loads(data)
    except ChannelError:
        raise
    except subprocess.TimeoutExpired:
        raise ChannelError("timeout") from None
    except (FileNotFoundError, CommandUnavailableError):
        raise ChannelError("startup_failed") from None
    except (ValueError, TypeError, UnicodeError):
        raise ChannelError("parse_error") from None
    except OSError:
        raise ChannelError("connection_failed") from None
    except RuntimeError:
        raise ChannelError("startup_failed") from None


def _dict(value: object) -> dict:
    if not isinstance(value, dict):
        raise ChannelError("parse_error")
    return value


def _list(value: object) -> list:
    if not isinstance(value, list):
        raise ChannelError("parse_error")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChannelError("parse_error")
    return value.strip()


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ChannelError("parse_error")
    return float(value)


def _get(endpoint: str, params: dict, deadline: float) -> object:
    return _request(endpoint + "?" + urlencode(params), deadline=deadline)


def _language(query: str) -> str:
    return "zh" if re.search(r"[\u3400-\u9fff]", query) else "en"


def _row(title: object, url: object, summary: object = "", date: object = None) -> dict:
    title, url = _text(title), _text(url)
    parsed = urlsplit(url)
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in url)):
        raise ChannelError("parse_error")
    if summary is None:
        summary = ""
    if not isinstance(summary, str) or (date is not None and not isinstance(date, str)):
        raise ChannelError("parse_error")
    return {"kind": "article", "title": _text(html.unescape(clean_html(title))), "url": url,
            "summary": html.unescape(clean_html(summary))[:500], "published_at": date}


def _tavily(query: str, limit: int, deadline: float, key: str) -> list[dict]:
    headers = {"Authorization": "Bearer " + key}
    usage = _dict(_request("https://api.tavily.com/usage", deadline=deadline, headers=headers))
    account = _dict(usage.get("account"))
    if _text(account.get("current_plan")).lower() not in ("researcher", "free"):
        raise ChannelError("blocked")
    if "paygo_limit" not in account:
        raise ChannelError("parse_error")
    if account["paygo_limit"] is None:
        # The public usage docs do not define null as disabled (it may mean no
        # cap). Only an independently verified account setting permits it.
        if os.environ.get("TAVILY_PAYG_DISABLED") != "1":
            raise ChannelError("auth_required")
    elif _number(account["paygo_limit"]):
        raise ChannelError("blocked")
    if _number(account.get("paygo_usage")):
        raise ChannelError("blocked")
    plan_limit = _number(account.get("plan_limit"))
    if plan_limit > 1000:
        raise ChannelError("blocked")
    if _number(account.get("plan_usage")) + 1 > plan_limit:
        raise ChannelError("quota_exhausted")
    payload = _dict(_request("https://api.tavily.com/search", deadline=deadline, headers=headers,
                            body={"query": query, "search_depth": "basic", "topic": "general",
                                  "max_results": min(limit, 20), "auto_parameters": False,
                                  "include_answer": False, "include_raw_content": False,
                                  "include_images": False}))
    return [_row(r.get("title"), r.get("url"), r.get("content"), r.get("published_date"))
            for r in map(_dict, _list(payload.get("results")))]


def _openalex(query: str, limit: int, deadline: float, key: str) -> list[dict]:
    quota = _dict(_dict(_get("https://api.openalex.org/rate-limit", {"api_key": key}, deadline)).get("rate_limit"))
    budget = _number(quota.get("daily_budget_usd"))
    used = _number(quota.get("daily_used_usd"))
    remaining = _number(quota.get("daily_remaining_usd"))
    cost = _number(_dict(quota.get("endpoint_costs_usd")).get("search"))
    if _number(quota.get("prepaid_balance_usd")) or budget > 1:
        raise ChannelError("blocked")
    # Reject zero/unknown price and allow the documented basic search cost only.
    if not 0 < cost <= 0.001 or cost > remaining or used + cost > min(budget, 1):
        raise ChannelError("quota_exhausted")
    payload = _dict(_get("https://api.openalex.org/works", {
        "search": query, "per_page": limit, "api_key": key,
        "select": "id,display_name,doi,publication_date,abstract_inverted_index"}, deadline))
    rows = []
    for r in map(_dict, _list(payload.get("results"))):
        abstract = r.get("abstract_inverted_index")
        summary = ""
        if abstract is not None:
            positions = []
            for word, offsets in _dict(abstract).items():
                for offset in _list(offsets):
                    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                        raise ChannelError("parse_error")
                    positions.append((offset, word))
            summary = " ".join(word for _, word in sorted(positions))
        rows.append(_row(r.get("display_name"), r.get("doi") or r.get("id"), summary, r.get("publication_date")))
    return rows


def _pubmed(query: str, limit: int, deadline: float) -> list[dict]:
    root = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    payload = _dict(_get(root + "esearch.fcgi", {"db": "pubmed", "term": query,
                        "retmode": "json", "retmax": limit, "sort": "relevance", "tool": "research-mcp"}, deadline))
    ids = _list(_dict(payload.get("esearchresult")).get("idlist"))
    if not ids:
        return []
    if any(not isinstance(i, str) or not i.isdigit() for i in ids):
        raise ChannelError("parse_error")
    payload = _dict(_get(root + "esummary.fcgi", {"db": "pubmed", "id": ",".join(ids[:limit]),
                        "retmode": "json", "tool": "research-mcp"}, deadline))
    result = _dict(payload.get("result"))
    uids = _list(result.get("uids"))
    if len(uids) != len(ids[:limit]) or set(uids) != set(ids[:limit]):
        raise ChannelError("parse_error")
    return [_row(r.get("title"), "https://pubmed.ncbi.nlm.nih.gov/" + uid + "/",
                 r.get("fulljournalname", ""), r.get("pubdate"))
            for uid in ids[:limit] for r in [_dict(result.get(uid))]]


def _public(provider: str, query: str, limit: int, deadline: float) -> list[dict]:
    if provider == "pubmed":
        return _pubmed(query, limit, deadline)
    if provider == "wikipedia":
        lang = _language(query)
        data = _dict(_get(f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page",
                          {"q": query, "limit": limit}, deadline))
        return [_row(r.get("title"), f"https://{lang}.wikipedia.org/wiki/" + quote(_text(r.get("key")), safe=""),
                     r.get("excerpt") or r.get("description") or "") for r in map(_dict, _list(data.get("pages")))]
    if provider == "wikidata":
        data = _dict(_get("https://www.wikidata.org/w/api.php", {"action": "wbsearchentities",
                          "format": "json", "search": query, "language": _language(query), "limit": limit}, deadline))
        return [_row(r.get("label"), "https://www.wikidata.org/wiki/" + quote(_text(r.get("id")), safe=""),
                     r.get("description", "")) for r in map(_dict, _list(data.get("search")))]
    if provider == "crossref":
        data = _dict(_get("https://api.crossref.org/works", {"query": query, "rows": limit}, deadline))
        rows = []
        for r in map(_dict, _list(_dict(data.get("message")).get("items"))):
            titles = _list(r.get("title"))
            if not titles:
                raise ChannelError("parse_error")
            date = None
            if r.get("published") is not None:
                parts = _list(_dict(r["published"]).get("date-parts"))
                values = _list(parts[0]) if parts else []
                if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
                    date = "-".join([str(values[0])] + [f"{v:02}" for v in values[1:3]])
            rows.append(_row(titles[0], r.get("URL") or "https://doi.org/" + _text(r.get("DOI")), r.get("abstract", ""), date))
        return rows
    if provider == "stackexchange":
        data = _dict(_get("https://api.stackexchange.com/2.3/search/advanced", {
            "q": query, "site": "stackoverflow", "pagesize": limit, "sort": "relevance",
            "order": "desc", "filter": "withbody"}, deadline))
        if data.get("error_id") is not None:
            raise ChannelError("quota_exhausted" if data.get("error_id") in (502, 503) else
                               "auth_required" if data.get("error_id") in (401, 402) else "blocked")
        rows = _list(data.get("items"))
        if data.get("quota_remaining") is not None and _number(data["quota_remaining"]) == 0:
            _remember_limit(provider, 86400)
        if data.get("backoff") is not None:
            _remember_limit(provider, _number(data["backoff"]))
        return [_row(r.get("title"), r.get("link"), r.get("body", ""), iso_from_epoch(r.get("creation_date")))
                for r in map(_dict, rows)]
    if provider == "huggingface":
        data = _get("https://huggingface.co/api/models", {"search": query, "limit": limit}, deadline)
        return [_row(r.get("id"), "https://huggingface.co/" + quote(_text(r.get("id")), safe="/"),
                     ", ".join(_text(tag) for tag in _list(r.get("tags", []))), r.get("lastModified"))
                for r in map(_dict, _list(data))]
    if provider == "europe_pmc":
        data = _dict(_get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", {
            "query": query, "pageSize": limit, "format": "json", "resultType": "core"}, deadline))
        return [_row(r.get("title"), "https://europepmc.org/article/" +
                     quote(_text(r.get("source")), safe="") + "/" + quote(_text(r.get("id")), safe=""),
                     r.get("abstractText", ""), r.get("firstPublicationDate"))
                for r in map(_dict, _list(_dict(data.get("resultList")).get("result")))]
    if provider == "openlibrary":
        data = _dict(_get("https://openlibrary.org/search.json", {"q": query, "limit": limit,
            "fields": "key,title,author_name,first_publish_year"}, deadline))
        rows = []
        for r in map(_dict, _list(data.get("docs"))):
            key = _text(r.get("key"))
            if not re.fullmatch(r"/works/OL\d+W", key):
                raise ChannelError("parse_error")
            year = r.get("first_publish_year")
            if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
                raise ChannelError("parse_error")
            rows.append(_row(r.get("title"), "https://openlibrary.org" + key,
                ", ".join(_text(author) for author in _list(r.get("author_name", []))), str(year) if year else None))
        return rows
    if provider == "itunes_podcasts":
        data = _dict(_get("https://itunes.apple.com/search", {"term": query, "media": "podcast",
                          "entity": "podcast", "limit": limit}, deadline))
        return [_row(r.get("collectionName") or r.get("trackName"), r.get("collectionViewUrl") or r.get("trackViewUrl"),
                     r.get("artistName", ""), r.get("releaseDate")) for r in map(_dict, _list(data.get("results")))]
    raise ChannelError("unsupported")


def search(config: dict, *, provider: str) -> list[dict]:
    if provider not in BACKENDS:
        raise ChannelError("unsupported")
    query = config.get("web_query")
    if not query:
        return []
    query = _text(query)
    try:
        limit = max(1, min(MAX_RESULTS, int(config.get("web_limit", 10))))
        timeout = float(config.get("web_timeout_s", 20))
    except (ValueError, TypeError, OverflowError):
        raise ChannelError("parse_error") from None
    if not math.isfinite(timeout):
        raise ChannelError("parse_error")
    timeout = max(1, min(60, timeout))
    with _lock:
        if _limited_until.get(provider, 0) > time.monotonic():
            raise ChannelError("quota_exhausted")
    key = ""
    if provider in ("tavily", "openalex"):
        key = os.environ.get(provider.upper() + "_API_KEY", "").strip()
        if not key or os.environ.get(provider.upper() + "_FREE_ONLY") != "1":
            raise ChannelError("auth_required")
        _config_value(key)
    deadline = time.monotonic() + timeout
    try:
        if provider == "tavily":
            rows = _tavily(query, limit, deadline, key)
        elif provider == "openalex":
            rows = _openalex(query, limit, deadline, key)
        else:
            rows = _public(provider, query, limit, deadline)
    except ChannelError as error:
        if error.status == "quota_exhausted":
            _remember_limit(provider)
        raise
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ChannelError("parse_error") from None
    seen = set()
    result = []
    for rank, row in enumerate(rows, 1):
        if row["url"] not in seen:
            seen.add(row["url"])
            result.append({**row, "rank": rank, "source": provider, "actual_backend": BACKENDS[provider]})
    return result[:limit]


PROVIDERS = {name: partial(search, provider=name) for name in BACKENDS}
