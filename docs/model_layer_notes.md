# Model-call layer — notes and open items

Files: `engine/model_client.py`, `engine/ledger.py`, `engine/replay.py`.
Tests: `tests/test_model_validator.py`, `tests/test_model_client.py`, `tests/test_model_http.py`,
`tests/test_ledger.py`, `tests/test_replay.py` (stdlib `unittest`, zero network).

Run: `cd tendercheck && PYTHONUTF8=1 python3 -m unittest discover -s tests -v`

## 1. Verify in the live smoke test (open items)

Everything below is configurable. None of it was guessed silently; the defaults are the conservative choice.

| # | Open question | Current default | Knob | How to check |
|---|---|---|---|---|
| V1 | Name of the reasoning-effort parameter on **Chat Completions**. The docs only show `reasoning.effort` for the Responses API. | Nothing is sent. | `extra_body={...}` (e.g. `{"reasoning_effort": "high"}` or `{"reasoning": {"effort": "high"}}`) | One call per form; 400 vs 200 and `usage...reasoning_tokens` changing. |
| V2 | Does strict `json_schema` accept union / nullable `type: ["string", "null"]`? | Contracts never use it (D5). The in-code validator supports type lists. | — | One tiny call with a nullable field before any contract uses one. |
| V3 | Is `strict: true` inside `response_format.json_schema` accepted on Chat? (The Chat example in the docs shows `{name, schema}` only.) | `strict: true` is sent. | `strict=True / False / None` (None omits the key) | First real call: 200 vs 400. |
| V4 | `max_completion_tokens` on Meta Chat; per-model maximum output is undocumented. | `max_completion_tokens = 32768` | `max_tokens_param`, `max_output_tokens` | Truncation (`finish_reason=length`) and 400s. Note the budget interaction in section 3. |
| V5 | Usage field names in the Chat response. | Parser accepts `prompt_tokens / completion_tokens / prompt_tokens_details.cached_tokens / completion_tokens_details.reasoning_tokens` and the Responses-style `input_tokens / output_tokens / *_details`. No usage block → `Usage.reported=False` and the ledger keeps the estimate. | — | Look at `usage` in the first recorded `calls.jsonl` line. |
| V6 | Does the reported input-token count already include cached tokens? | No (`Pricing.prompt_includes_cached=False`): the literal design formula `in*1.25e-6 + cached_in*0.15e-6 + out*4.25e-6` with `in` = reported input. Over-charges by `cached*0.15e-6` if the count is inclusive. | `Pricing(prompt_includes_cached=True)` | Compare ledger totals with the Billing page after a few calls. |
| V7 | Error body shape. Assumed `{"error": {"type", "code", "message"}}` (notes cite `type: billing_error`, `code: billing_not_configured`). | 402 is mapped by **status alone** or by the body code. | — | Record the body of the first real error (a call with a deliberately wrong key returns 401). |
| V8 | `Retry-After` on 429: present? seconds or HTTP date? | Both formats parsed; without it 1 s / 2 s / 4 s; max 3 retries; a Retry-After above 60 s gives up at once. | `max_429_retries`, `max_retry_after_s`, injected `sleep` | `response_headers` of a recorded 429. |
| V9 | Name of the rate-limit headers (`x-ratelimit-remaining-tokens` …). | All response headers are recorded (credential-like ones redacted). | — | `response_headers` in `calls.jsonl`. |
| V10 | Are 4xx responses billed? | Ledger settles 4xx at $0; 5xx, timeouts and responses without usage keep the full estimate. | — | Billing page after the smoke test. |
| V11 | `finish_reason` values and the `message.refusal` field on Meta Chat. | OpenAI-compatible names assumed (`length`, `content_filter`, `refusal`). | — | First truncated / refused reply. |
| V12 | Input-token estimate before a call. There is no stdlib tokenizer. | UTF-8 bytes of the whole request body (upper bound for text: a byte-level token covers at least one byte). Very pessimistic for base64 PDF/images: a 1 MB PDF estimates about $1.7 and would hit the $2 run cap. | `estimate_input_tokens` | Calibrate with `POST /v1/responses/input_tokens` (design 10-4) before sending files. |
| V13 | Non-Meta hosts (Baseten, Fireworks, llama.cpp): json_schema support, `max_tokens` vs `max_completion_tokens`, model strings, prices. | `max_tokens`; json_schema sent but never trusted (parse + validate + ≥1 retry, code fences tolerated); Meta Standard prices as placeholder; `local_client` uses zero prices. | `response_format_mode` (`json_schema` / `json_object` / `none` → schema goes into the prompt), `max_tokens_param`, `pricing=` | One call per host. |
| V14 | Real latency of E1 with reasoning. | HTTP timeout 300 s. | `timeout_s` | `latency_ms` in `calls.jsonl`. |

Smoke-test sketch (not executed; needs `TENDERCHECK_LIVE=1` in the shell and a key in `MODEL_API_KEY`):

```python
from engine.ledger import Ledger
from engine.replay import Recorder
from engine.model_client import MetaClient

client = MetaClient.from_env(live=True, ledger=Ledger(), recorder=Recorder.for_run("runs", "live_smoke_01"),
                             run_id="live_smoke_01", max_output_tokens=2048)
result = client.complete("SMOKE", 0, [{"role": "user", "content": "Return an empty flags list."}], "x_review")
```

## 2. Interface

```python
client.complete(stage: str, attempt: int, messages: list[dict], schema_name: str) -> ModelResult
ModelResult(parsed: dict | None, raw: dict | None, usage: Usage, model_resolved: str, latency_ms: int, error: str | None)
Usage(input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, reported)
```

- `schema_name` = `x_ask`, `x_extract`, … → `contracts/model_<name>.schema.json`; its `schema` is sent as the json_schema
  and used for validation. A contract keyword the validator does not implement raises `SchemaError` at load time.
- `parsed` is set only if the reply passed strict JSON parsing (no NaN/Infinity, no duplicate keys) and validation.
- `error` = `"<code>: <detail>"`; codes: `json_parse_error`, `schema_invalid`, `empty_content` (retried once),
  `truncated`, `refusal`, `content_filter`, `bad_response_shape`, `contributor_model_resolved` (not retried).
  `ModelResult.ok` / `ModelResult.error_code` are helpers.
- `usage` and `latency_ms` are summed over the tries of one call; `raw` is the last raw response.
- HTTP failures raise: `BillingNotConfigured` (402 → stop live, switch to replay), `InvalidApiKey` (401),
  `ModelNotFound` (404), `BadRequest` (400), `RateLimited` (429 after retries), `ServerError` (5xx), `ApiError`,
  `TransportError`. Budget: `BudgetExceeded` (reason `global_stop` / `run_calls` / `run_cost`).
  `MODEL_LAYER_ERRORS` is the tuple a pipeline stage catches to fail closed.

Clients:

| Client | Use | Network |
|---|---|---|
| `FakeClient(fixtures)` | unit tests; fixtures keyed by `(stage, attempt)`, a list = one reply per try; `FakeClient.from_file(path)` | none |
| `ReplayClient(run_dir)` | demo, $0 regression, 402 fallback | none |
| `MetaClient(api_key=..., live=True, ledger=..., recorder=..., run_id=...)` | Meta Model API, `muse-spark-1.3` only; `-contributor` ids refused; base URL fixed | live guard |
| `OpenAICompatibleClient(base_url=..., model=..., ...)` / `local_client(model=...)` | fallback hosts / local llama.cpp | live guard |

## 3. Safety properties (each has a test)

- **Live guard.** A live client sends nothing unless the constructor flag `live=True` **and** `TENDERCHECK_LIVE=1`
  are both set. Checked at construction (when `live=True`), first thing in `complete()`, and again right before
  `urlopen`. `MetaClient.from_env` reads `MODEL_API_KEY` only after the guard passed. A live call also needs a
  `Ledger`, a `Recorder` and a `run_id`, so every live call is budgeted and recorded.
- **Key handling.** The key is passed explicitly, held in a name-mangled attribute, never in `repr`, never given
  to the recorder (request headers are recorded with `Authorization: [REDACTED]`), scrubbed from error bodies,
  exception messages and log lines, and replaced by `[REDACTED]` if it ever appears in a recorded line.
- **Model id.** Only `muse-spark-1.3` (D15). Any id ending in `-contributor` is refused before sending, and a reply
  whose `model` ends in `-contributor` is rejected.
- **No tools, no streaming.** `extra_body` may not set `tools`, `tool_choice`, `stream`, `model`, `messages`,
  `response_format` or the max-tokens key.
- **Budget before spend.** Every billable request (including the invalid-output retry) reserves its worst-case
  estimate first: `input_bytes × $1.25/1M + max_output_tokens × $4.25/1M`. With the default 32,768 output tokens
  the output part alone is $0.139. A high `max_output_tokens` therefore raises `BudgetExceeded` before any spend
  (500,000 → $2.125 > $2.00). 429 retries reuse the same reservation.

## 4. Ledger (`runs/ledger.jsonl` by default)

- Integer money in e8 units (1e-8 USD): prices 125 / 15 / 425 e8 per input / cached / output token. Sums are exact.
- Rows: `reserve` (estimate), `settle` (actual cost from usage), `unknown` (estimate stays charged), `refused`
  (audit only). Effective spend = settled cost, or the estimate while a reservation is open or unknown, so a
  crash between reserve and settle never under-counts.
- Caps (`Caps`): $2.00 and 9 billable requests per run; live stop when global spend + estimate would pass $140.
  Checked in the order global → calls → run cost.
- Atomic append: one `os.write` of one complete line on an `O_APPEND` descriptor, `fsync`, under an exclusive lock
  (in-process lock + `flock` where available). The read-check-append of `reserve` runs under the same lock, so
  concurrent threads/processes cannot slip past a cap (tested with 16 threads and with 4 processes).
- Damage (unparseable row, final line without newline, settle without reservation) raises `LedgerCorrupt` and
  blocks further reservations until a person repairs the file.

## 5. Replay (`runs/<run_id>/calls.jsonl`)

- One line per billable request: `stage, attempt, try_index, max_tries, request_hash, schema_name, schema_version,
  provider, model_requested, model_resolved, outcome (response | error | budget_exceeded), raw_response | error,
  usage, cost_e8, cost_usd, cost_basis, pricing, latency_ms, lenient_json, request_headers, response_headers`.
- The request **messages are not stored** (they can contain user input); only
  `request_hash = sha256(canonical_json({messages, schema: {name, version, sha256}}))`.
  Canonical JSON = sorted keys, `(",", ":")` separators, `ensure_ascii=False`, NaN rejected.
- `ReplayClient` re-runs the same parse/validate/retry loop on the stored raw responses and returns the stored
  latency, so it yields `ModelResult`s equal to the recorded ones. A request whose hash differs from the
  recording raises `ReplayMismatch` (never served silently); a missing call raises `ReplayMissing`; recorded HTTP
  errors and budget stops are raised again with identical messages.
- The tests prove `ModelResult` equality and byte-identical output of a stand-in pipeline. The byte comparison of
  the real `report.json` belongs to the pipeline's integration test.
- Live run ids should start with `live_` (`runs/live_*/` is git-ignored). Replay bundles for the public demo use
  other ids and must contain synthetic data only.
- Files are split on `\n` only; U+2028/U+2029 inside model text survive a round trip.

## 6. Not done / limitations

- Responses API (needed only for SAM 3.1, D4) is not implemented.
- PDF / image content parts are passed through untouched in `messages`; not exercised by the tests.
- Public-demo limits (per IP per hour, per day) belong to the web layer; per-stage budgets B0–B6 are not enforced.
- 5xx responses are not retried (the request may have been billed).
- Without `fcntl` (native Windows) the cross-process lock falls back to the in-process lock + `O_APPEND`;
  the deployment target is Linux.
