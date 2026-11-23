# API channel acceptance — 2026-11-23

Related #12. Stacked on PR13. No merge. Official APIs use environment-injected free keys; no vault reading in the server. Every new API and CSDN submitted a Chinese and English query; empty/error entries are not successful relevance validation.

| Provider | Language / query | State | Returned pool | Seconds |
|---|---|---|---:|---:|
| openalex | zh: 人工智能 | ok /  | 1 | 2.325 |
| openalex | en: artificial intelligence | ok /  | 1 | 1.913 |
| wikipedia | zh: 人工智能 | ok /  | 3 | 1.008 |
| wikipedia | en: artificial intelligence | ok /  | 3 | 1.983 |
| wikidata | zh: 人工智能 | ok /  | 3 | 2.044 |
| wikidata | en: artificial intelligence | ok /  | 3 | 1.425 |
| crossref | zh: 人工智能 | ok /  | 3 | 1.758 |
| crossref | en: artificial intelligence | error / quota_exhausted | 0 | 1.071 |
| stackexchange | zh: Python 列表 | ok /  | 2 | 3.328 |
| stackexchange | en: Python list comprehension | empty /  | 0 | 1.319 |
| huggingface | zh: 人工智能 | empty /  | 0 | 0.841 |
| huggingface | en: artificial intelligence | ok /  | 3 | 0.728 |
| europe_pmc | zh: 人工智能 | error / timeout | 0 | 20.03 |
| europe_pmc | en: artificial intelligence | error / timeout | 0 | 20.024 |
| pubmed | zh: 人工智能 | empty /  | 0 | 0.914 |
| pubmed | en: artificial intelligence | ok /  | 3 | 1.116 |
| openlibrary | zh: 人工智能 | ok /  | 3 | 1.622 |
| openlibrary | en: artificial intelligence | ok /  | 3 | 1.164 |
| itunes_podcasts | zh: 人工智能 | ok /  | 3 | 0.738 |
| itunes_podcasts | en: artificial intelligence | ok /  | 3 | 0.507 |
| csdn | zh: 人工智能 | ok /  | 3 | 1.979 |
| csdn | en: artificial intelligence | ok /  | 3 | 1.028 |
| arxiv | zh: 人工智能 | empty /  | 0 | 1.174 |
| arxiv | en: artificial intelligence | ok /  | 20 | 1.386 |
| bilibili | zh: Python 教程 | ok /  | 17 | 0.482 |
| bilibili | en: Python tutorial | ok /  | 17 | 0.376 |
| tavily | zh: 人工智能 | ok /  | 3 | 3.37 |
| tavily | en: artificial intelligence | ok /  | 3 | 4.399 |

## Interpretation and limits

- Tavily: actual /usage reported Researcher, monthly limit1000, usage0, paygo_usage0, paygo_limit=null. Initial strict parser stopped before search. Null is ambiguous, so final implementation additionally requires explicit TAVILY_PAYG_DISABLED=1 based on prior account UI verification, never silently maps missing fields/null to zero. Final basic bilingual searches succeeded. No billing/card/plan changes.
- OpenAlex: registered key in original Ego Lite space; Free Plan, daily included budget$1, zero prepaid purchase. Key backed up/saved/read back mode600; actual bilingual API requests succeeded after free-budget preflight.
- Crossref English was rate-limited. No automatic retry or paid fallback. Europe PMC official EBI endpoint timed out in both languages; independent curl checks also timed out with no response bytes, so no parser fix was inferred. This remains unverified in this local network.
- Hugging Face searches model IDs/names, not semantic full-text discovery; Chinese keyword had zero results. PubMed/arXiv mostly English indexing; Chinese query zero does not imply no related papers.
- Existing core excludes known publication dates older than3650 days. This removed some top ranked reference material (e.g. English Stack Overflow); source-level successful zero and core filtering are distinct. Unknown dates are kept.
- Bilibili uses totalrank and preserves upstream rank; real English query returned Python NumPy first, Chinese-language Python videos also remain. This fixes ordering, not an English-only language guarantee.
- Official data/content licenses vary; API access is not permission to republish all content. Catalog records terms.

## Browser scope

Original space12 resumed after user confirmation. OpenAlex completed. The first new JD navigation triggered another browser local-network permission handoff before platform probes. Awaiting confirmation; no fresh space or alternate browser, no cookies exported, no CAPTCHA bypass. All13 shopping/community probes and corresponding local adapters remain pending. No unsupported-platform verdict fabricated from this environment failure.

## Evidence

See [machine-readable queries](acceptance/api-search-2026-11-23.json). Fixture tests cover all11 API providers plus empty/malformed data, secrets over stdin, fixed failures, no automatic retry, budgets, quota/backoff; extended tests cover CSDN, arXiv opt-in and Bilibili ranking. Full-suite, wheel and CI results are in the PR.

Final base suite:295tests,8optional SDK skips. Full SDK suite:294tests passed before final launcher test was added; launcher6targeted tests passed afterward. sdist/wheel built, vault credential scan passed, independent whole-diff and launcher review no blockers. PR13 all six CI checks passed.
