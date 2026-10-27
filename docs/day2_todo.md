# Carry-over from Day 1 (2026-10-27)

- Live smoke (SM1–SM8) with a card-phase global cap of $10 (`Caps(global_stop_e8=10 * E8_PER_USD)`); return to $140 once the hackathon credit shows under Billing → free credits. Open items V1–V14 in `model_layer_notes.md`.
- Strip the first line of manifest files (`[A-M2] Missing and errors mixed` etc.) before sending to the model — it reveals the test intent (`fixture_notes.md`).
- Grading contract has no dedicated slot for the PII notice, the forced-verdict refusal and the injection badge. Decide where the report builder places them so T05 (PII notice) and T04 (`not_in_source`) checks can see them (`fixture_notes.md`, contract concern).
- X-Fix can only requote/retract; a Q4/Q5 field-value failure ends as S2. Consider a `replace` action carrying a full Req when wiring E2 (`engine_notes.md`).
- Grader reads `"model"` from each `calls.jsonl` line; confirm against the recorder's actual field name (`model_resolved`).
- Not built yet: N2 manifest parsing, F1–F14, K1–K5, S0–S15, report renderer, prompts, web UI, deploy.

## Added 2026-10-27 night (after the first live runs, see live_runs_20261027.md)
- K=3 live repeats for T01–T07 and the six regression runs (cost so far ~$0.11–0.14 per run).
- Validity record with no parent document and no matched file → needs-confirmation, not S10 `missing` (bid validity case).
- Background sentences extracted as requirements (e.g. the invitation sentence) → steer to `not_requirements` in the E1 prompt with a generic example.
