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

## Demo runs (A-M1 + three pages, text stages replayed from the 11/1 recording, V live)

| Run | V call | Rows as the key says | Moved rows |
|---|---|---|---|
| r1 (shown) | 8,549 in / 1,026 out tokens, 5.5 s, $0.0150 | 23/23 PASS | R02, R22 met -> error (seal); R14 met -> error (validity) |
| r2 | 8,549 in / 841 out tokens, 4.8 s, $0.0143 | 23/23 PASS | same three |
| r3 | 8,549 in / 759 out tokens, 4.8 s, $0.0152 | 23/23 PASS | same three |

What r1 read and checked (report.json x_meta.pages):
- QDS_BF1 page 1: no seal field reported (the red printed logo was not taken for a seal); printed Date 20 November 2026.
- QDS_BF1 page 2: "[Company seal]" field, model: empty; code: 0.0 % red ink in the box, no stamp-sized red mark on the
  page -> empty. The list says sealed = yes -> contradiction.
- QDS_TAX page 1: "Official stamp" field (the issuing authority's), model: stamped; code: 12.4 % red ink -> stamped.
  Printed Date of issue 15 July 2026 -> 2026-07-15. The list says 2026-10-15 -> contradiction. Under the printed
  date the 90-day validity ends 2026-10-13, before the 2026-11-30 deadline.

Run totals in run.json include the replayed text stages' recorded cost; only V was paid on 11/2.

## Correction after review (11/2 evening)

The code checked "no stamp-sized red mark" only where a mark overlapped the model's box, while this log and the
README described a page-wide check. The rule is now page-wide (`engine/vision.check_page`): a field reads empty only
when its box has no red ink AND no stamp-sized red mark is anywhere on the page; otherwise it reads unknown (needs
check). New test: `test_empty_box_but_a_stamp_elsewhere_on_the_page`. Checked at $0:
- the A-M1-P run r1 replays through the corrected code byte-identical (page 2 has no red mark), and so do the
  seven list-only cases;
- the gate was rescored from its recording with no API key set: 12/12, 0 seal fields in the wrong state
  (`gate_rescored.json`; the original `gate.json` is kept).

While fixing the resume path of `tools/page_gate`, one run of the old resume code made one unintended live V call
for page G10 ($0.0060, ledger run `live_20261102_pagegate-gate-09`, 09:57 UTC). The recorder refused to store it as a
duplicate, so it was neither recorded nor scored; no result above uses it.
