# Live runs 2026-11-02: stage V on scanned pages

## Rules fixed before any run (committed before the runs below)

- Answer keys: `scenarios/pages/A-M1/answers.json` and `scenarios/pages/gate/answers.json` (commit 9c5e337,
  before any page-reading code).
- Gate: 12 fresh-seed pages, one V call per page, scored once against the gate key with `tools.page_gate`.
- Demo runs: three hybrid runs of A-M1 with the three demo pages (`--pages A-M1 --hybrid-replay replays/A-M1`):
  every recorded text stage (U, E1, E4, M, C) is replayed from the 11/1 recording and only V is called live, so any
  change against the list-only result comes from the pages. **Run r1 is the one shown on the page and in the video,
  whatever it returns.** All three are graded with `tools.page_grade` and listed below, misses included.
- These results are on our own synthetic pages (generator, key and prompt by the same author) and are reported
  separately from the text-engine results (17/18, 21/21), which were measured without V.

## Gate (run `live_20261102_pagegate`)

12/12 pages fully correct, 0 seal fields in the wrong state: PASS under the pre-registered rule.

| Pages | Truth | Read |
|---|---|---|
| G01-G04 BF-1 page 2 | seal field stamped | stamped (model and code agree) 4/4 |
| G05-G08 BF-1 page 2 | seal field empty | empty (model and code agree) 4/4 |
| G09-G12 Tax Clearance | printed date of issue | 4/4 dates read exactly |

The run stopped once after 9 pages: the ledger's per-run cap of 9 calls applied to the whole 12-page gate. The tool
was changed to give each page its own ledger run; the 9 pages already read were replayed from their recording at $0
and the 3 remaining pages were read live (`--resume`). No page was read twice.

## Demo runs

(filled in after the runs)
