# Pipeline interfaces (fixed 2026-10-27, binding for wave-1 modules)

Design source: `09_설계_빌드계획_20261022.md` §2 (flow, N2, M+F, K+C, questions, output, filter), §3-2 (loop), §4 (schemas), §5 (rules S0–S15).
All structures are plain dicts/lists (JSON-serializable). No null in model-facing data; `None` is allowed in report fields only where the grading contract allows null.

## Owners

| Module | Owner | Status |
|---|---|---|
| `engine/normalize.py`, `mask.py`, `injection.py`, `dates.py`, `checks.py` | done (Day 1) | `build_source_bundle`, `check_requirements`, `evaluate_validity`, `parse_datetime_text`, `mask_text`, `detect_forced_verdict` |
| `engine/model_client.py`, `ledger.py`, `replay.py` | done (Day 1) | `client.complete(stage, attempt, messages, schema_name) -> ModelResult` |
| `engine/manifest.py`, `engine/filechecks.py` | wave-1 agent A1 | N2, F1–F14 |
| `engine/conflicts.py`, `engine/rules.py`, `engine/report.py`, `engine/textfilter.py`, `engine/templates.py` | wave-1 agent A2 | K1–K5 + D18 merge, S0–S15, report/grading/txt, forbidden filter, question templates |
| `engine/prompts/*.md`, `engine/stages.py` | wave-1 agent A3 | U, E1–E4, M, C prompts and stage functions |
| `engine/pipeline.py` | wave 2 (orchestrator) | wires everything, loop of §3-2 |

## 1. Manifest (A1 produces)

```
parse_manifest(text: str, *, lang: "en"|"ko") -> Manifest
Manifest = {
  "label_line": str,                 # first line like "[A-M2] ..." - stripped, never sent to a model
  "profile": Profile,                # design 4-2: company, short_name, participation(single|jv|unknown),
                                     #   members[{name, role(lead|member), share_pct}], seal_type(corporate|usage|unknown),
                                     #   planned_upload (ISO with offset or ""), facts{consortium, usage_seal, foreign_entity,
                                     #   subcontracting, sme: yes|no|unknown}
  "rows": [Row],                     # design 4-2 Manifest.rows: row_id "M1".., doc_label, file_name, ext, declared_format,
                                     #   size_mb (number | "merged" | "unknown"), signed, sealed (yes|no|unknown),
                                     #   sign_seal_text, issue_date ("YYYY-MM-DD" | "unknown"), notes (masked),
                                     #   parsed{...}, bundle, scan_ref, pii (bool: this row had masked values)
  "pii_counts": {type: count},       # from engine.mask, header + rows
  "warnings": [str],                 # unparsed lines kept as notes
  "masked_text": str                 # the manifest re-rendered with masking, label line removed (for model M)
}
```

## 2. Verified requirements (pipeline assembles; A2/A1 consume)

```
VReq = {"req": Req (contracts/model_x_extract Req fields), "check": check_requirement(...) result}
```
Only VReqs with `check["remove"] == False` and not merged away are passed downstream. `check["verified"]` / `check["grounded"]` drive S1/S2.

## 3. Match (A3 produces from validated X-Match)

```
Match = {
  "by_req": {tmp_id: {"row_ids": [row_id], "status_hint": "no_concern"|"needs-confirmation", "hint_reason": str, "rationale": str}},
  "attribute_findings": [{row_id, attribute, value, note_quote, verified: bool}],   # verified = note_quote found in that row's notes/sign_seal_text
  "unmatched_rows": [row_id],
  "dropped": [str]                    # matches pointing to unknown req/row ids (logged, ignored)
}
```

## 4. File checks (A1 produces)

```
run_file_checks(vreq, rows: [Row], manifest: Manifest, *, as_of: str, anchors: {name: iso_datetime}) -> FileCheck
FileCheck = {"checks": [{"id": "F1".."F14", "result": "pass"|"fail"|"unknown"|"not_applicable", "detail": str}],
             "quantity_short": bool, "unknown_fields": [str], "reason_codes": [str], "readings": {...}}
```
`anchors` = candidate deadline datetimes, e.g. `{"Body §5": "2026-11-30T14:00:00+03:00", "Annex 1 ITB 23.1": "..."}` when the deadline is in conflict; a single entry otherwise. Validity uses `engine.dates.evaluate_validity`.
General file rules (all files PDF, size, filename template) apply to every row (09 §2-6).

## 5. Conflicts (A2)

```
find_candidates(vreqs) -> [Candidate{id "K1".., rule "K1".."K5", a: tmp_id, b: tmp_id, values: {a: str, b: str}}]
apply_adjudication(candidates, x_conflict_parsed | None, bundle) -> {"conflicts": [Conflict], "closed": [...], "log": [...]}
Conflict = {"id": "C1".., "topic", "members": [tmp_id_a, tmp_id_b], "sides": [{quote, location}] x2, "agency_question": str,
            "values": {...}}
merge_conflicting(vreqs, conflicts) -> vreqs with the D18 single-row merge applied (earlier doc order keeps the row)
```
Closing a candidate needs `same_obligation == "no"` AND both distinguishing quotes verified by Q1/Q2. Model failure → keep all candidates.

## 6. Rules (A2)

```
decide(vreq, *, file_check: FileCheck | None, match: dict | None, conflict_info: {...}, profile: Profile,
       as_of: str, mandatory: str) -> Decision
Decision = {"status", "condition_state" (None|unknown|not_triggered|triggered), "reason_codes": [...],
            "depends_on": None|"Rxx", "depends_on_conflict": None|"Cn", "bidder_question_keys": [...]}
```
First matching rule of 09 §5 wins. Model hint only moves met → needs-confirmation (S15).

## 7. Report (A2)

```
build_report(run_id, decisions_in_doc_order, conflicts, *, lang, manifest, bundle, untrusted, not_in_source, flags, x_meta) -> report
report["grading"] -> grading.json (contract fields only); render_text(report, lang) -> report.txt
```
- IDs `R01..` in document order of the quote; `status_counts` = sum of rows.
- Fixed phrases from `contracts/fixed_text.json`. **PII notice placement (decided):** append the `pii` fixed text to `bidder_action` of every row whose evidence rows had `pii == true`; if PII came only from the header or source, append it once to `bidder_questions`. The forced-verdict refusal and the injection badge go to `x_meta.flags.notices` (screen), not to graded fields.
- Every generated text field passes `textfilter` (A-grade blocks → template replacement + `x_meta.flags.filtered`).

## 8. Stages (A3)

```
run_ask(client, user_message_masked) -> {"asked_items": [...], "error": str|None}
run_extract(client, bundle, *, lang, attempt=0) -> X-Extract parsed or error         (E1; E3 uses run_recheck)
run_fix(client, bundle, failures: [{tmp_id, failures, req}], attempt) -> X-Fix
run_recheck(client, bundle, seg_ids, attempt=0) -> X-Extract subset                  (E3, ≤5 segments)
run_coverage(client, bundle, uncited_seg_ids) -> X-Coverage                          (E4, ≤30 segments)
run_match(client, vreqs, manifest) -> Match                                          (M; input = verified reqs + masked rows + profile facts)
run_conflict(client, candidates, vreqs, bundle) -> X-Conflict
```
Every stage: builds messages from a versioned prompt file in `engine/prompts/` (data inside `<<<SOURCE ... SOURCE>>>`), calls `client.complete`, returns `{"parsed", "error", "model_resolved", "usage"}`; never passes tools; `reasoning_effort` per stage from a single config dict in `stages.py`.
