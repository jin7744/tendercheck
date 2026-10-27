# A3 notes — prompts and model stages (`engine/prompts/*.md`, `engine/stages.py`)

## Deviations from `pipeline_interfaces.md` §8 (all additive; positional signatures unchanged)

| # | What | Why |
|---|---|---|
| D-A3-1 | Every stage returns an envelope `{stage, attempt, prompt_version, called, parsed, error, model_resolved, usage (dict), latency_ms, dropped, notes}`. `run_ask` adds `asked_items`; `run_coverage` adds `unexplained_seg_ids`. `run_match`'s `parsed` **is** the §3 `Match` (`{by_req, attribute_findings, unmatched_rows, dropped}`), and those keys are also mirrored on the envelope (`env["by_req"]` …). | §8 asks for `{parsed, error, model_resolved, usage}` on every stage and also names `asked_items` / `Match` as results; the envelope carries both. |
| D-A3-2 | Optional keyword args: `run_ask(..., *, lang="en", attempt=0)`, `run_match(..., *, lang="en", attempt=0)`, `run_recheck(..., *, existing_reqs=())`, `run_coverage(..., *, existing_reqs=(), attempt=0)`, `run_conflict(..., *, attempt=0)`. | U item text and M rationale need an output language (no bundle there). E3/E4 need existing ids so new `tmp_id`s do not collide, and E4 needs existing subjects/quotes to recognise `duplicate`. `existing_reqs` takes Req or VReq dicts. |
| D-A3-2a | **`run_ask` and `run_match` default to `lang="en"`. For a Korean run (package B) the pipeline MUST pass `lang="ko"`**, or U item text and M rationale come out in English. E2/E3/E4/C read `bundle["output_lang"]`; E1 takes `lang`. | §8 call shapes have no language argument for U and M. |
| D-A3-2b | `engine/prompts/_req_rules.md` is a shared fragment (what counts, quote rules, Req fields) included by E1/E3/E4 via `{{include:_req_rules.md}}`; versions concatenate (`e1_extract/1.0.0+req_rules/1.0.0`), `prompt_sha256(stage)` covers both. | Keeps the three extraction prompts identical on the rules. |
| D-A3-3 | `run_recheck` accepts seg ids as strings **or** `{seg_id, reason, note}` dicts (E1 `uncertain_segments` can be passed as is). | The reason helps the re-read. |
| D-A3-4 | E3 gets the full SOURCE block (same bytes as E1) plus the ≤5 listed segments, not the listed segments alone (design 09 §2-2). New records may only quote listed segments (others → `dropped`). | Cross-references need the target clause; the source prefix is cached. |
| D-A3-5 | `STAGE_CONFIG` reaches the client through `stage_settings()`: it sets the public attributes `client.max_output_tokens` and (provider `meta` only) `client.extra_body["reasoning_effort"]` for one call and restores them in `finally`. | `client.complete()` has no per-call knobs and `model_client.py` is not mine. Not thread-safe; the pipeline is sequential. A per-call override in `model_client` would be cleaner (open question). |
| D-A3-6 | Exceptions: `PROPAGATE_ERRORS = (BudgetExceeded, BillingNotConfigured, LedgerCorrupt)` re-raise; every other `MODEL_LAYER_ERRORS` exception (HTTP 4xx/5xx, 429 after retries, transport, `LiveCallsDisabled`, `ReplayMismatch`, `FixtureMissing` …) becomes `error = "<ClassName>: <message>"`, `parsed = None`. | Task rule "never raise into the pipeline except budget/billing". A stale replay therefore shows up as a stage error, not an exception. |

## Message layout

- One `user` message per call, no system message: `data blocks` + blank line + `instructions` (prompt file, rendered).
- E1/E2/E3/E4/C start with the identical SOURCE block (`source_block(bundle)`: `# DOCUMENT <doc_id> title="…"` per document, `[seg_id] text` per segment as in `render_segments`, a blank line between blocks). U starts with `<<<USER_MESSAGE` (the message as one JSON string), M with `<<<REQUIREMENTS` + `<<<MANIFEST`.
- Every data line starts with `[`, `{` or `"`, so no data line can equal a closing marker such as `SOURCE>>>`.
- Deterministic text only (no timestamps, fixed key order), so `request_hash` replays.

## Post-validation (what lands in `dropped`)

- E1/E3/E4: a repeated `tmp_id` within one reply → later record dropped; malformed id, or an id already used by `existing_reqs` → renamed `R<n>` (note; `attribute_of`/`merge_scope` inside the reply follow). Quote/location problems are left to `engine.checks` (Q1–Q6) and E2. List entries (`uncertain_segments` ≤5, `not_requirements`, `skips`) with unknown or out-of-scope seg ids are dropped; `injection_suspects` are kept only when the quote is found in that segment.
- E2: fixes for tmp_ids not in the failures list, second fixes, and requotes without a quote are dropped.
- M: unknown `req_id` → match dropped; unknown `row_id` inside a match → that id dropped; repeated `req_id` → merged (`needs-confirmation` wins). `attribute_findings[].verified` = `norm08(note_quote)` is a substring of `norm08` of that row's masked `notes` or `sign_seal_text` (the text the model saw). `unmatched_rows` is recomputed by code from the kept matches (model list only noted). M receives only `check.verified and not check.remove` requirements.
- C: unknown `candidate_id`, new conflicts naming unknown/unverified/identical ids or a known pair, and questions whose `for_id` is neither a candidate id nor a sent `req_id` are dropped. Distinguishing quotes and QV1–QV5 are left to `engine.conflicts` / templates.
- U: ≤10 items, ≤5 terms each (trimmed, case-insensitive dedupe, ≥2 chars); an item with no usable term falls back to the item text as its only term.

## Open questions for the live run

1. `reasoning_effort: "minimal"` for U is assumed valid on Chat (smoke tested `low` only).
2. `max_output_tokens` values are first guesses; E1 at 32,768 reserves $0.139 per try. Watch `finish_reason=length` on E1 and reasoning share on M.
3. Whether the provider prefix cache hits with the SOURCE block at the start of a user message (check `cached_input_tokens` on E2/E4/C).
4. Does the model keep `requirement` text free of verdict words without the filter? (textfilter catches it anyway.)
5. E1 quote granularity vs the grader's over-merge rule (one obligation per record): count records that hold two gold anchors, and attribute splitting (a property inside a list entry must stay in the document record, or `status_counts` inflates).
6. `run_conflict` always calls the model, even with zero candidates (it may still add `new_conflicts`); skip the call in the pipeline if that is not wanted. `run_fix`/`run_recheck`/`run_coverage`/`run_match`/`run_ask` make no call when they have nothing to send (`called: False`).
