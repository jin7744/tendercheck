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

## Carry-over to 10/29 (written 2026-10-28 ~20:40)
Done on 10/28: K=3 regression (21 runs) -> diagnosis -> 8 engine fixes + 3 prompt bumps; second K=3 12/18;
B x3 on the final engine 9/9; A x1 on the final engine 3/3; web page rebuilt from the design playbook (owner
approved the direction), impeccable critique applied, server hardened after a security review; README.
Ledger total about $8.06 of the $10 card-phase cap.

1. Owner: log in to dev.meta.ai in the app's browser so Billing can be read (session expired). If the $150 credit
   shows, raise the cap to $140 and run: A lists x2 more (K=3 on the final engine), then cards T01-T07 x3 with
   their own user messages (`--test TXX`, aux runs included).
2. Owner decisions: GitHub public repository + commit author name and trailer; demo subdomain; deployment
   (static page with recordings + optional live API behind cloudflared with --trust-proxy).
3. Engine text: two em dashes in the agency question template (A-M3) -> plain punctuation; long F7 detail
   strings in "Checks" could read as a sentence.
4. Grader: decide whether a gold row split across records is judged by its worst fragment (5 rows on 10/28),
   with the flip list, or leave as is and say so in the README.
5. MVP freeze 23:00. Video and submission text 11/2-11/3 (11/1 is the AI_TOP_100 judging window: no Meta work on
   the shared Claude subscription).
