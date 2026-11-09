# README acceptance evidence

Recorded on 2026-11-09. Audit: 60 → 100; V1–V11: 11 pass, 0 fail, 0 skip. Stranger: 5/5; exactly three fresh blind readers: 3/3 prefer the candidate. These are model-reader results, not a survey of human users.

The actual primary README is Chinese. The skill only recognizes English README.md and Chinese README.zh-CN.md, so complete verification uses an exact-byte filename-mapped temporary mirror. The first command remains the stranger expectation; screen measurement separately matches the entire three-command block. Both renderings use offline local Markdown with identical remote-image policy. External link HEAD checks passed.

| View | Before | Candidate Chinese | Candidate English |
|---|---|---|---|
| Desktop | [light](before-desktop.png) | [light](after-desktop.png) | [light](after-en-desktop.png) |
| Dark | [dark](before-desktop-dark.png) | [dark](after-desktop-dark.png) | [dark](after-en-desktop-dark.png) |
| Mobile | [mobile](before-mobile.png) | [mobile](after-mobile.png) | [mobile](after-en-mobile.png) |

[Verification](verification-summary.json), [stranger](stranger-result.json), [blind results](blind-result.json) and [randomization/hashes](blind-manifest.json) preserve the acceptance record. Original reader answers are unedited. Full private execution logs and unsuccessful attempts remain in the task archive.

The unchanged candidate won all three blind choices. Readers still found checkout setup, the unavailable default-branch MCP command, and the lack of a concrete result item on the first screen confusing; preserve those findings. A first-screen disclosure contains three config snippets; their fully expanded text does not all fit the desktop fold. Default-branch MCP launch is not claimed to work; the functional-branch preview with the optional SDK passed real stdio tool discovery/search. Client GUI integration and paid transcription/filtering were not tested.

No old headings, integrations or status badges were intentionally deleted. Python and JSON remain documented.
