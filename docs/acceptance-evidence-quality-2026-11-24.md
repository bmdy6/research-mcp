# Evidence retrieval acceptance — 2026-11-24

## Scope and method

Baseline: commit `d6e462967bd5c4c94b36dcf18fec8250af1b881f` (`d6e4629`), extracted into a temporary directory without changing the original checkout. Changed-code runs use the same 12 cases in `scripts/evaluate_evidence.py`. Each suite runs its cases in a fixed sequential order. The final live baseline completed before the final changed-code live run. This is a non-blind, small task-specific comparison, not a randomized benchmark or model-quality evaluation.

Offline mode uses synthetic adapters while retaining actual core filtering, HTML parsing, envelope validation and output budgeting. Live mode makes public keyless Exa, Europe PMC, PubMed and AAD requests, plus two deterministic invalid/private-target rejections. It skips the two synthetic shell/challenge cases rather than pretending these are real website tests. No paid model or automated browser is used.

Reports contain public queries, source titles/URLs, status, elapsed time and rule hits. They contain no page full text, credentials, private chats or local paths. Source matches require an HTTP(S) URL with the expected normalized hostname and directory prefix or exact document path; saved public source metadata was rechecked with this final rule. A rule hit says the selected source/paragraph was retrieved, **not that a medical claim is correct, independently established, or applicable to a particular person**.

## Same-case results

| Mode | Baseline | Changed code | Coverage |
| --- | --- | --- | --- |
| Offline synthetic | 4/12 | 12/12 | Four search, four read, four explicit-failure cases |
| Live public sources | 5/10 | 10/10 | Four search, four read, two explicit-failure cases; two synthetic cases skipped |

These denominators are separate. Do not combine synthetic scores and live scores, or infer a general retrieval-accuracy percentage. Network availability and upstream rankings can change on rerun. The PubMed title rule is a coarse topical match; it does not score study quality or position in the ranking.

- [Baseline offline metadata](acceptance/evidence-quality-2026-11-24-baseline-offline.json)
- [Changed offline metadata](acceptance/evidence-quality-2026-11-24-after-offline.json)
- [Baseline live metadata](acceptance/evidence-quality-2026-11-24-baseline-live.json)
- [Changed live metadata](acceptance/evidence-quality-2026-11-24-after-live.json)

## Observations

Baseline AAD reads returned raw HTML marked successful; navigation consumed the 8,000-character field budget before the desired paragraphs. Changed-code reads extract main/article content before budgeting, and the four source-paragraph rules pass. The 2005 Europe PMC paper was excluded by the baseline's implicit 3,650-day window; default searches now retain it, while explicit age windows still filter known dates.

PubMed ESearch formerly omitted `sort`, whose documented default is most recent; it now requests `sort=relevance`. In this live run the classic niacinamide study became the first returned item. This single query is not evidence that relevance sorting always produces better research. See [NLM E-utilities parameter documentation](https://dataguide.nlm.nih.gov/eutilities/utilities.html#sort) and [current NCBI ESearch documentation](https://www.ncbi.nlm.nih.gov/books/NBK25499/#chapter4.ESearch).

HTML regression cases additionally cover leading comments/BOM, hidden ancestors and inline visibility, empty primary regions, formatted Loading, Jina access-challenge Markdown, article headings, malformed links and short real prose. Recognized shells return `NO_READABLE_CONTENT`; high-confidence challenges return `ACCESS_CHALLENGE`. Generic transport failures do not satisfy those failure rules. Parsing is static and does not execute JavaScript, infer external CSS visibility, or guarantee detection of every access wall.

## Reproduce

```sh
python3 -m unittest discover -s tests
python3 scripts/evaluate_evidence.py --output offline.json
python3 scripts/evaluate_evidence.py --live --output live.json
# Same cases against a separately prepared baseline checkout:
python3 scripts/evaluate_evidence.py --code-root /path/to/baseline --output baseline.json
```

The CLI adds `--max-age-days`; MCP adds optional `max_age_days` with default null. Both filter only parseable known dates and retain unknown dates. Default unlimited age also removes hidden GitHub/HN provider windows. Existing provider ordering remains unchanged except PubMed relevance.

## Validation boundary

- Python 3.14: 365 tests passed, including the optional MCP transport tests.
- Independent Python 3.10 run: 365 tests completed, with 8 optional MCP tests skipped because that environment has no MCP SDK.
- Wheel and sdist builds passed. The wheel was installed without dependencies into a clean Python 3.10 environment; isolated imports verified readable fallback content, default retention of a 2005 source and explicit 90-day filtering.
- A fresh stdio MCP process returned the 2005 paper by default and excluded it for an explicit 90-day window. The AAD pore article returned 4,979 readable characters with the irritation warning and no output truncation.
- `git diff --check` passed. Cross-platform CI remains a separate GitHub delivery check.

A running MCP process must reconnect to load changed code; no existing session is restarted by this acceptance run.
