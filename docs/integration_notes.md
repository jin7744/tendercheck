# Integration notes — `engine/pipeline.py`, `tools/run_scenario.py` (2026-10-27)

Wave 2: the modules of `pipeline_interfaces.md` (with the deviations in `a1_notes.md`, `a2_notes.md`,
`a3_notes.md`) wired end to end. Nothing here was run live; every result below comes from the fake and
replay modes.

Test command: `PYTHONUTF8=1 python3 -m unittest discover -s tests -v` (558 tests, all pass).
New tests: `tests/test_pipeline_e2e.py` (package A), `tests/test_pipeline_e2e_b.py` (package B),
`tests/test_pipeline_loop.py` (loop branches, fail-closed, conditional skip, attribute findings, the real
`MetaClient` class behind the tap with `live=False` and a dummy key: nothing reserved, recorded or sent),
plus one class each in `tests/test_report.py` and `tests/test_templates.py`.

## 1. Cross-module fixes (each has a test)

| # | Module | Problem found when wiring | Fix | Test |
|---|---|---|---|---|
| X1 | `report.py` | KO reports got English FileCheck `detail` strings appended to `reason` (a2 open item). | `_check_details(..., lang)` returns `""` unless `lang == "en"`; the details stay in `x_meta.rows[].checks`. | `test_report.FileCheckTextInReason.test_korean_reason_names_the_file_without_english_detail`; every KO reason in `test_pipeline_e2e_b` is scanned for English check words. |
| X2 | `report.py` | A general file rule has no matched rows, so its error reason read "File format check failed for -." (and, after X1, a KO reason named no file at all; 09 §2-6 wants the violating file names). | `_failing_files()` takes the failing file names from `FileCheck.readings[Fn]`; they replace the evidence list in the `CHECK_FAILED` sentence. | `test_report.FileCheckTextInReason` (EN and KO literals); `test_pipeline_e2e_b.test_b_m2` (R15, R18 name the .hwp, scan_0012.pdf and 제안서최종(2).pdf). |
| X3 | `templates.py` | `particle()` raised `KeyError: '⑥'` for a KO location ending in a circled number (`공고문 3.⑥`, used as bidder-question context): `'⑥'.isdigit()` is true but it is not an ASCII digit. ⑩–⑳ (not `isdigit`) silently fell back to `이(가)`. | `_last_digit()` maps any numeric character to its last ASCII digit via `unicodedata.numeric` (⑥ → 6 → 육, ⑫ → 2 → 이, ⑳ → 0 → 십). | `test_templates.KoreanParticles.test_circled_number_locations`. Found by the package B e2e run (B-M3). |
| X4 | pipeline ↔ `rules.py` / `filechecks.py` | 09 puts S4 above S5/S6: a `CONFLICT_DEPENDENT` F7 result on a row whose condition is unknown or not triggered turned it into NC (a2 open item). | The pipeline decides each row once without a FileCheck; if that gives S5 or S6, file checks are skipped and that decision stands. Triggered rows still get F1–F14. | `test_pipeline_loop.ConditionalFileChecks` (unknown → conditional/unknown, single firm → conditional/not_triggered, JV → NC + `C1`). Mutation check: with the skip removed the first two give NC. |
| X5 | pipeline ↔ `stages.run_match` / `filechecks.py` | Verified M `attribute_findings` were never read by anyone. | `apply_attribute_findings()` copies verified findings into `row["parsed"]` only where code read nothing (`signed_by`, `member`, `headcount`, `signature_count`, `seal_kind` → `seal_kinds`). Code-read facts win (09 §2-6). | `test_pipeline_loop.AttributeFindings`. |

## 2. Wiring decisions (interfaces left room)

| # | Point | Decision | Why |
|---|---|---|---|
| W1 | Order of F | U → E1 → V/E2/E3/E4 → M → K → C → D18 merge → **F** → S → report. F runs after C. | F7 needs the conflict's candidate deadlines as `anchors` (interfaces §4, `conflicts.deadline_anchors`); with F before K/C a deadline-anchored validity rule would see no anchors. Model call order is unchanged (M before C). |
| W2 | Loop (§3-2) | E2 while a kept record is not verified+grounded and fewer than 2 rounds were used; then E3 once if E1 listed `uncertain_segments`; then E4 once if trigger segments are uncited. Records added by E3/E4 can use remaining E2 rounds. | 09 §3-2. |
| W3 | E4 targets | Segments matching `TRIGGER_RE` (generic obligation words EN/KO) that no kept record quotes (quote span or condition quote), that E1 did not list in `not_requirements` or `uncertain_segments`, and that are not code-detected injection suspects. Over 30 → `info_gaps`. E4 `unexplained_seg_ids` → `info_gaps`. | A segment E1 already explained needs no second call. No trigger list existed in the engine. |
| W4 | C call | Called when K found a candidate or at least two usable requirements exist (the model may add `new_conflicts`). | a3 open question 6. |
| W5 | Stops | `BudgetExceeded`, `BillingNotConfigured`, `LedgerCorrupt` (propagated by stages) and the pipeline's own `CallCapReached` (9 calls) / `RunBudgetReached` ($2.00 from recorded usage) end the model phase. Code phase still runs; banner = `Incomplete analysis — <reason>` in the output language; `x_meta.stop = {key, class, stage}` where `stage` is the call that was refused or failed. | 09 §2-13, §2-14. The ledger counts tries, the tap counts calls; whichever trips first stops the run. |
| W6 | E1 failure | Error after the client's own retry (two schema errors) → zero rows, banner "requirement extraction failed …". | 09 §2-2 E1 row. |
| W7 | M failure or stop before M | Run is partial. Rows decided by S10–S15 that need a file match become NC `ATTRIBUTE_UNKNOWN:evidence` (the same shape `rules.decide` uses for "no evidence"), rule `S14`, with info code `DEMOTED_NO_MATCH:<original rule>` in `x_meta.rows[].info_codes`. General file rules (all manifest rows, no match needed), S3/S4 conflict rows, S5/S6 conditional rows and deadline rows keep their decision. Conflicts are still found by K and kept (C not run = model failure). | Fail-closed: no `met`, and no `missing` claim, without the file match. |
| W8 | U / E3 / E4 / C failure | Not partial. U → `info_gaps` note, no `not_in_source`; E3 → `info_gaps` per segment; E4 → listed segments in `info_gaps`; C → all candidates stay conflicts, template question (`apply_adjudication(…, None)`). | Each has a conservative default in 09 §2-2. |
| W9 | U | Called first, only with a non-empty user message; input = masked message only. Each asked item's search terms are looked up in the source by code (whole word for Latin/digit terms, spacing-insensitive otherwise). Nothing found → `not_in_source` (fixed template); found → `x_meta.asked_items[].seg_ids` (no new requirement). The forced-verdict check is code (`detect_forced_verdict`) and sets the flag/notice. | D6, 08 T04/T07. |
| W10 | `untrusted_instructions` | Code-detected suspects first; model suspects (E1/E3) only for segments with no code suspect, and the quote is re-cut from the segment text (exact source span, 08 F2). | interfaces §7, 09 §4-6. |
| W11 | Manifest language | `parse_manifest(lang=…)` gets `en` when the manifest text has no Hangul (`normalize.detect_lang`), else `ko`; independent of the output language. | The parser's `lang` drives the KO `(주)` filename ban, which belongs to the manifest, not to the report language. |
| W12 | Replay byte identity | `report.json` holds nothing that differs between fake/live and replay: no `created_at`, no `mode`, no `provider`, no requested model name. `x_meta.model.resolved` comes from the replies. Mode, provider, invariants and grades go to `run.json` (tool). Call cost in `x_meta` = usage × `client.pricing` (replay: default Meta pricing), so live → replay stays identical only while live runs use the default pricing. | 09 §2-15 byte comparison. 09 §4-6 lists `mode`/`created_at` under `x_meta`; they moved to `run.json`. |
| W13 | Output path | `runs/<run_id>/<run>/{report.json, grading.json, report.txt, calls.jsonl, run.json, grade.json[, grade_<card>.json]}` as the task asked (09 §6-6 says `eval/runs/…`). | Task brief. |
| W14 | Invariants | I1 grading view vs `contracts/report_grading.schema.json` (engine validator); I2 every `met` quote is a substring of a normalized source document; I3 each row's location equals its segment's location; I4 basis/disclaimer fixed text; I5 no B1–B7 hit in generated text (+ banner); I6 two sides and a question per conflict; I7 `status_counts` = row sums; I8 no `status` property in any `contracts/model_*.schema.json`, `status_hint` only the two values; I9 no `SYNTH-…` marker in report, grading, any request sent or any reply received (counts only); I10 every call that returned has token counts and a cost. Stored in `x_meta.invariants` and returned. | 09 §6-4. |

## 3. Results (fake mode, hand-written perfect model replies; replay of each run)

| Run | Status match (grader step 6) | `status_counts` | Regression verdict | Replay `report.json` |
|---|---|---|---|---|
| A-M1 | 19/19 | 15/0/0/3/1 | PASS | identical |
| A-M2 | 19/19 | 6/3/9/0/1 | PASS | identical |
| A-M3 | 19/19 | 13/0/1/3/2 | PASS | identical |
| B-M1 | 18/18 | 17/0/0/0/1 | PASS | identical |
| B-M2 | 18/18 | 3/3/7/4/1 | PASS | identical |
| B-M3 | 18/18 | 10/0/0/4/4 | PASS | identical |

Cards on these fake runs: T01 PASS (aux B-M1 PASS), T02 PASS, T03 PASS (aux B-M3 PASS), T06 PASS (aux B-M2 PASS);
in-process tests: T04 PASS, T05 (B-M2-PII) PASS, T07 PASS. Calls per run: U, E1, M, C (4).
These prove the wiring and the code path under a perfect model only; they say nothing about live extraction quality.

## 4. Tool (`tools/run_scenario.py`)

- Inputs from `scenarios/packages.json`; the manifest file is passed whole (the parser strips the label line); the
  user message is the card's message for that run (`--test`), else the package default.
- `--mode fake` needs `--fake-fixture` (`FakeClient.from_file` JSON) and records `calls.jsonl`; `--mode replay` reads
  `calls.jsonl` from the output directory (or `--replay-from`); `--mode live` refuses unless `TENDERCHECK_LIVE=1`,
  the run id starts with `live_` and `--global-cap-usd` is given, then uses `MetaClient.from_env(live=True, ledger=
  Ledger(caps=Caps(global_stop_e8=cap)), recorder=Recorder(out_dir), run_id=<run_id>-<run>)`. An existing
  `calls.jsonl` in the output directory is never appended to (fake/live refuse).
- Grading runs `python3 -m eval.grade regression <run>` (A-M1…B-M3) and, with `--test`, `card <T>` over every
  sibling run directory of the same run id that has a `grading.json`. The child process gets the environment
  without `MODEL_API_KEY`, `META_API_KEY`, `TENDERCHECK_LIVE`.
- day2_todo "grader reads `model` from calls.jsonl": confirmed on the fake runs — `grade.json` `record.model_ids` =
  `["fake-model"]`, read from `raw_response.model` of the recorder lines.
- Recovery: after a live run, a code fix is re-checked at $0 with `--mode replay` on the same run directory; a
  request whose prompt or input changed raises `ReplayMismatch` (shows as that stage's error, never served).

## 5. 08 vs engine (not changed, recorded)

- 08 §3.4 B R04 (`사업자등록증 사본: 사본에는 '원본대조필'을 표기하고 인감을 날인한다.`) has kind `attribute`, but 08 has no separate record for the document it would be an attribute of. Under the prompt rule ("a property written inside the document's own list entry stays in that record's fields") the record carries `copy_type`/`sign_seal` and gets kind `document`. `kind` is not graded.
- A2 note: S4′ also sets `depends_on_conflict` on `met`/`error` rows whose result is the same under every reading (A-M1/A-M2 R07, B-M1 R05/R06/R11). 08 shows the link only on NC rows; the grader checks it only for A-M3 R07.

## 6. Open items

- Not run live. The fake replies are perfect; live E1 granularity (over-merge, attribute splitting), E2/E3/E4 behaviour and M accuracy are unmeasured.
- `TRIGGER_RE` is a first guess; watch `x_meta.verification_log.coverage` and E4 cost on the first live runs.
- S2 second review (`X-Review`, AG8) is not wired: no stage function exists; `review_flag` is always False.
- During a partial run, general file-rule rows may still be `met` (they need no file match). Tighten if the reviewers want "no met at all" in partial reports.
- `info_gaps` and the banner reason are pipeline strings (EN/KO), not contract fixed text.
- `x_meta.mode` (09 §4-6) lives in `run.json`; the web layer must read it from there (or add it to its own response) to show "Mode: Replay".
