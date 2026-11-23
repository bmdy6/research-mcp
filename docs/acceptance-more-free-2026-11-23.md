# Free channel expansion — 2026-11-23

Baseline: origin/main 20a1930 (includes #13/#14/#15/#17). Read-only, no keys, no login attempts, no CAPTCHA bypass, no cookie export. Local browser task space 15 only; space 14 was not accessed.

## API adapter results

Queries: `人工智能`, `python`, limit 2. Fixed official endpoints; one request per query, no automatic retries/fallback. Live adapter calls spaced at least 5 seconds apart.

| Provider | Chinese | English | Boundary |
|---|---|---|---|
| npm | ok 2 | ok 2 | package metadata only |
| crates_io | ok 2 | ok 2 | crate metadata only |
| docker_hub | empty 0 | ok 2 | repository metadata only |
| mdn | ok 2 | ok 2 | en-US corpus; Chinese relevance limited |
| itunes_apps | ok 2 | ok 2 | CN storefront; app cost is separate |
| google_news_rss | raw HTTP 200; adapter 429 then ok 2 | raw HTTP 200; adapter 429 then ok 2 | local opt-in only, no quota guarantee |

The real RSS payloads parse as RSS/channel/item. A subsequent live adapter run hit upstream quota; after a cooldown, one fresh call per query returned two normalized items each. No automatic retry was added. Offline fixtures verify parser behavior separately. No production deployment or ongoing availability claim.

## Candidate triage in progress

GDELT returned 429 for both queries. Gitee v5 returned empty arrays for both queries, including a broad English query; this does not establish useful search coverage. Lobsters /search.json returned 404; the HTML search remains under investigation. dev.to /api/articles supports tag filtering, not keyword search; no misleading keyword adapter was added. Reddit JSON returned 403. The repository's existing open-webSearch integration supports search engines/CSDN, not dedicated adapters for these candidates.

Ego Lite claimed task space 15 successfully. Initial browser probes read search lists on SegmentFault, Medium, Product Hunt, Hupu and 36kr. These are not API acceptance and do not yet establish adapter readiness. At Ctrip a location permission prompt transferred control to the user; browser work paused according to SDK rules. No takeover was attempted without user confirmation. Full candidate matrix follows in the browser increment.

## Verification

Baseline: 311 unittest cases, 8 optional integration skips. New tests cover schema drift versus true empty, canonical URLs, encoding, opt-in, bounded transport text, malformed RSS/DTD, and exclusion from public search. Wheel build succeeds. CI status is reported on the PR, separately from live probes.
