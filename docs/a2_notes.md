# A2 notes — conflicts, rules, templates, filter, report (2026-10-27)

Files: `engine/conflicts.py`, `engine/rules.py`, `engine/templates.py`, `engine/textfilter.py`, `engine/report.py`.
Tests: `tests/test_conflicts.py`, `test_rules.py`, `test_templates.py`, `test_textfilter.py`, `test_report.py`.

## 1. Interface additions and deviations (read before wiring — interfaces §5–§7)

All additions are extra dict keys or optional keyword arguments; nothing in §5–§7 was removed or renamed,
except item D1.

| # | Interface | What the code does | Why |
|---|---|---|---|
| D1 | §7 `report["grading"]` | **`report.grading_view(report)`** returns grading.json. The report dict has no `grading` key. | `contracts/report.schema.json` has `additionalProperties: false`; a `grading` key would break report.json. |
| D2 | §5 Candidate | Extra keys `sides` [{tmp_id, doc_id, seg_id, seg_ids, location, quote, iso_local, offset}] ×2, `subject`, `deadline_kind`. `values` keys are literally `a`/`b`. | `apply_adjudication` has no `vreqs` argument but must build `Conflict.sides` and verify quotes. |
| D3 | §5 `apply_adjudication` | Optional kwarg `vreqs=None`. Without it, model `new_conflicts` are logged and ignored. | `new_conflicts` are accepted only between verified requirements (09 §2-8); that needs the VReqs. |
| D4 | §5 Conflict | Extra keys `rule` (K1–K5 or `model`), `candidate_id`, `subject`, `deadline_kind`, `side_info` (sides + document `position`), `anchors` {location: iso_local} (K1), `question_source` (model/template). `members`, `sides` and `values.a/b` are in document order. | `anchors` is the input `run_file_checks(anchors=...)` needs; `side_info` feeds the D18 merge and the time-zone sentence. |
| D5 | §5 `merge_conflicting` | The kept VReq (earliest in document order) is a copy with `conflict_id` (first id), `conflict_ids`, `merged_members`. Other members are dropped. A group linked by several conflicts becomes one row. **Callers MUST use the returned list from here on; a dropped member must not reach `decide` or `build_report`** (it would add a second R01-type row). | `rules.decide` needs a marker to fire S3. |
| D6 | §6 `decide` | `match` = the full Match (`{"by_req": ...}`) **or** one entry. `conflict_info` = `{"conflict_id": "Cn"\|None, "depends_on_conflict": "Cn"\|None}` — build it with `conflicts.conflict_info_for(vreq, conflicts)`. `mandatory` optional (default `req.mandatory`). New optional kwargs `parent` (Decision of the `attribute_of` requirement, for S11) and `review_flag` (S15 REVIEW_FLAG). | §6 leaves `conflict_info: {...}` open. Pass the full Match so S11 can see the parent's rows, or decide parents first and pass `parent`. |
| D7 | §6 Decision | Extra keys `rule` (S-id), `agency_question_keys` (`["counting"]`), `info_codes`, `evidence_row_ids`, `timing`. **`depends_on` is the parent's tmp_id**; `build_report` maps it to R01… | Final IDs exist only after sorting in the report. |
| D8 | §7 `build_report` items | `decisions_in_doc_order` = `[{"vreq", "decision", "file_check"?, "match"? (this req's entry), "action_draft"?}]`. Items are re-sorted by quote position anyway. `not_in_source` = item names (rendered with the fixed template). `flags` keys read: `forced_verdict_request_ignored`, `incomplete_reason`, `notices`, `pii_masked`, `filtered`. | Shapes were open in §7. |
| D9 | S0 | `rules.source_missing(bundle)`; `build_report` applies it (no rows, `x_meta.analysis_state = "source_missing"`, fixed text in report.txt). | S0 is a report-level outcome, not a per-row decision. |
| D10 | — | Helper `conflicts.deadline_anchors(vreqs, conflicts)` → `(anchors, conflict_id)` for `run_file_checks`. | Wiring convenience for wave 2. |
| D11 | Unverified rows | An S1 row's report `quote` is the **whole claimed source line** (code-cut, D19); if that segment does not exist, the row is left out of `requirements` and listed in `x_meta.unverified_rows`. | `check["quote"]` is empty for S1; an empty or model-written quote is an 08 F2 FAIL for the whole run. |

## 2. Interpretations of 08 / 09

Conflicts (09 §2-8)
- **K1** fires when the written date or time differs, or both sides carry offsets that differ. Equal date and time is not a candidate (09: "해석된 일시가 다름"). The same instant written in two zones (14:00 UTC+03:00 vs 20:00 UTC+09:00) **is** a candidate (brief: same instant, different notation is still a conflict).
- A candidate never closes on notation: for K1, a distinguishing quote that only restates a date/time (or sits inside the side's raw value) is rejected (08 T03 aux: "10:00 / 오전 10시").
- Closing verification: Q1 = found in that side's document (exact → casefold → nospace); Q2 = inside that side's clause (same block); and the quote must not occur in the other side's quote. This is stricter than Q2 for requirement quotes (same document allowed). It only ever keeps more conflicts open.
- Candidates use VReqs that are verified **and** grounded; an S2 row stays its own NC row.
- At most one candidate per pair (first of K1…K5). K4/K5 compare `sign_seal`/`copy_type` and `(rule_type, days, months, until)`.
- Values (`values.a/b`): K1 `deadline.raw` cut verbatim from the quote; K2 size text (`10 MB`, `30MB`); K3 extension tokens as written; K4 the quote (≤120 chars); K5 `validity.raw`.
- **QV3** checks the date/time tokens as written (K1), numbers (K2/K5) or extension tokens (K3), case-insensitive. Requiring the whole raw value would reject 08's own Q-A1 ("Date: 2 December 2026; Time: 14:00 hours" is not verbatim in it); 09 §2-9 says "시각·숫자". QV1 accepts `?` followed by `(Yes/No)` / `(예/아니오)` (08 Q-A1, Q-B1 end that way). QV5 = `mask_text` finds anything.
- The KO conflict template reproduces 08 Q-B1 exactly when given Q-B1's subject ("전자입찰서(제안서 포함) 제출 마감"); the live path uses the generic label "제출 마감", so the text differs but keeps every element the grader checks. The counting template reproduces 08 Q-B2 exactly with document "입찰보증보험증권" (tests). Particles 은/는, 으로/로, 을/를 are chosen by the last syllable.

Rules (09 §5, 08 §1.2)
- S10 applies to non-attribute rows of category document / signature_seal / original_copy / validity. Attribute rows go to S11 (08 A-M2 R13 = missing + depends_on R12).
- S11 "parent absent": parent Decision is missing with NO_MATCHING_FILE/PARENT_MISSING; else the parent's `row_ids` in the full Match are empty; with neither, own rows empty.
- An attribute row without its own condition whose parent is conditional (unknown / not_triggered) inherits that state, `depends_on` = parent.
- Several conditions: any unknown fact → S5 (table order), else any condition not met → S6, else triggered. `mandatory = conditional` with no condition record → conditional/unknown + `fact:other`. `fact_key = other` is always unknown.
- S4 fires on `CONFLICT_DEPENDENT` in the FileCheck codes even without a conflict id (fail-closed). S4′ (`SAME_UNDER_ALL_READINGS` + dependent conflict) sets `depends_on_conflict` also on met/error rows (08 §1.3 shows it only on NC; the grader checks it only for A-M3 R07). The S3 row carries its own conflict id in `depends_on_conflict`.
- Deadline type = category deadline/submission_method or `deadline.kind ≠ none`. S8 compares only `kind = submission`; an unzoned or date-only deadline, or an upload at the same minute, gives NC ACTION_PENDING (not compared). A submission-method rule without a date → NC ACTION_PENDING (09 has no row; 08 B 공고문 4. is an allowed extra).
- F8 never changes a status (09 F8: info only). A row with no evidence rows and no passing check is NC `ATTRIBUTE_UNKNOWN:evidence`, never met. A counting-only split gives `COUNTING_AMBIGUOUS` without `ATTRIBUTE_UNKNOWN:validity`. `anchor_date`, `period`, `required_until`, `evidence` get no bidder question.

Report (09 §2-10, §4-6, §4-7)
- `requirements[]` stay in ID order (document order of the quote, `R01`, three digits only past 99 rows). D16 order is `x_meta.display_order`, used by report.txt and the action lists.
- PII notice per interfaces §7. `{types}` = manifest `pii_counts` + bundle `pii_masked` + `flags.pii_masked`.
- Q-B2-style counting questions have no graded slot: `x_meta.agency_questions` and report.txt.
- Top actions ≤3 = non-met rows except conditional ones, in D16 order (deadline rows first). "원문에 마감·영향이 적힌 항목만" is approximated by that order; no separate impact detection. Pre-submission checklist ≤10 = all pending rows incl. conditional/unknown, then deadline reminders.
- Times are shown as `YYYY-MM-DD HH:MM UTC+hh:mm`; +09:00 is not labelled KST (not unique to Korea). 08 T03 aux passes via `14:30` + `UTC+03:00` in the conflict row's reason.
- Safe action ("on time under every reading") only when both sides have a time and are both zoned (earliest, converted to the upload offset) or both unzoned (raw value of the earlier side). Otherwise just "wait for the agency's answer".

Filter (09 §2-12)
- A grade = 08 B1–B7 (copied; a test keeps them equal to `eval/patterns.py`) + X1 (법적으로 유효, 접수 완료, guaranteed, legally valid, chance(s) of winning) + P1 practice claims (standard/common/industry practice; usually/normally/typically/generally/always required; 관행상; 통상적으로 필수; 일반적으로 필수). B grade (eligible, compliant, 적격, qualified) → `x_meta.flags.warned` only. Fixed phrases are not run through the filter at build time (the KO disclaimer contains 적격); a test proves none of them has an A-grade hit.

## 3. Open items
- A quote cut from a source line that held masked PII contains `[MASKED:x]` and would fail 08 F2 against the unmasked source. The 08 sources have no such line.
- FileCheck `readings` shape is not fixed in §4; the report looks for an `evaluate_validity`-shaped list anywhere under `readings`. FileCheck `detail` strings are appended to `reason` (≤200 chars) as written by A1, so English details would appear in KO reports.
- Model `hint_reason` / `rationale` / conflict `explanation` are not shown in graded fields (explanation is kept, filtered, in the adjudication log).
- 09 puts S4 above S5/S6, so a `CONFLICT_DEPENDENT` FileCheck on a row whose condition is unknown or not triggered would make it NC instead of conditional. **A1 / wave 2: do not evaluate F7 for rows whose condition is unknown or not_triggered.** No 08 conditional row carries a validity rule, so the 08 set cannot catch this.
- Not yet run against real A1 (`run_file_checks`) or A3 (`run_match`, `run_conflict`) output; only hand-built inputs.
