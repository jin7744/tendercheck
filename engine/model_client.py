"""Model-call layer for TenderCheck (design 09 sections 2-4, 2-13, 2-14, 2-15). Standard library only.

One interface for every provider::

    client.complete(stage, attempt, messages, schema_name) -> ModelResult

* ``FakeClient``  - hand-written fixture responses keyed by (stage, attempt). Unit tests, $0.
* ``ReplayClient`` - serves the responses recorded in ``runs/<run_id>/calls.jsonl``. Demo and regression, $0.
* ``MetaClient``  - Meta Model API, Chat Completions, ``muse-spark-1.3`` only, ``response_format`` json_schema.
* ``OpenAICompatibleClient`` - any OpenAI-compatible host (Baseten, Fireworks, local llama.cpp via ``local_client``).

``schema_name`` names a contract file ``contracts/model_<schema_name>.schema.json`` (``{schema_version, name,
schema}``); its ``schema`` is what is sent as the json_schema and what the reply is validated against.

Output handling (all clients, fail-closed): the reply text is parsed as strict JSON and validated against the
contract in code. A reply that fails parsing/validation is retried ``invalid_output_retries`` times (default 1;
always >= 1 for non-Meta hosts), then returned with ``parsed=None`` and ``error`` set. Truncation
(``finish_reason=length``) and refusals are reported, not retried.

HTTP failures raise typed errors: 402 ``BillingNotConfigured`` (callers switch to replay), 401
``InvalidApiKey``, 404 ``ModelNotFound``, 400 ``BadRequest``, 429 ``RateLimited`` after at most 3 retries
honouring ``Retry-After``, 5xx ``ServerError``, network ``TransportError``.

Live guard: ``MetaClient`` / ``OpenAICompatibleClient`` send nothing unless BOTH the constructor flag
``live=True`` AND the environment variable ``TENDERCHECK_LIVE=1`` are set; a live call additionally needs a
``Ledger`` (budget caps), a ``Recorder`` (replay) and a ``run_id``. The guard runs before anything else in
``complete()`` and again immediately before ``urlopen``.
"""
from __future__ import annotations

import email.utils
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .ledger import (DEFAULT_PRICING, ZERO_PRICING, BudgetExceeded, Ledger, LedgerCorrupt, Pricing, Reservation,
                     e8_to_usd)
from .replay import (REDACTED, Recorder, ReplayError, ReplayFormatError, ReplayMismatch, ReplayMissing,
                     canonical_json, load_calls, redact_headers, request_hash, run_dir_for, scrub_text, sha256_hex)

log = logging.getLogger("tendercheck.model")

META_BASE_URL = "https://api.meta.ai/v1"
META_MODEL_ID = "muse-spark-1.3"  # Standard id only (design D15)
CONTRIBUTOR_SUFFIX = "-contributor"  # Contributor ids feed Meta training: always refused
LIVE_ENV = "TENDERCHECK_LIVE"
API_KEY_ENV = "MODEL_API_KEY"
CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"

DEFAULT_MAX_OUTPUT_TOKENS = 32_768  # reasoning tokens share this budget (06 section 2-3); per-model max unknown
DEFAULT_TIMEOUT_S = 300.0
MAX_429_RETRIES = 3
DEFAULT_MAX_RETRY_AFTER_S = 60.0  # a longer Retry-After gives up instead of hanging a live demo
BACKOFF_BASE_S = 1.0  # 1, 2, 4 s when the 429 carries no Retry-After
ERROR_BODY_LIMIT = 2000
RETRYABLE_OUTPUT_ERRORS = frozenset({"json_parse_error", "schema_invalid", "empty_content"})


# =============================================================================================
# errors
# =============================================================================================

class ModelClientError(RuntimeError):
    """Base class for model-layer failures. Callers fail closed on any of these (design 09 section 2-13)."""


class LiveCallsDisabled(ModelClientError):
    """A live client was asked to send without live=True and TENDERCHECK_LIVE=1. Nothing was sent."""


class ContributorModelRefused(ModelClientError):
    """A '-contributor' model id was requested (inputs would be used for Meta training)."""


class UnsupportedModel(ModelClientError):
    """MetaClient is fixed to muse-spark-1.3."""


class MissingApiKey(ModelClientError):
    pass


class MissingLiveDependency(ModelClientError):
    """Live calls need a Ledger, a Recorder and a run_id."""


class SchemaError(ModelClientError):
    """Contract file missing/malformed, or it uses a keyword the in-code validator does not implement."""


class FixtureMissing(ModelClientError):
    """FakeClient has no fixture for the requested (stage, attempt, try)."""


class TransportError(ModelClientError):
    """Network failure or timeout. The request may have been processed: the ledger keeps the estimate."""


class ModelApiError(ModelClientError):
    """HTTP error response from the model API. ``body_excerpt`` is truncated and scrubbed of secrets."""

    def __init__(self, status: int, *, error_type: str = "", error_code: str = "", message: str = "",
                 body_excerpt: str = ""):
        self.status = int(status)
        self.error_type = error_type
        self.error_code = error_code
        self.message = message
        self.body_excerpt = body_excerpt
        label = error_code or error_type or "error"
        super().__init__(f"HTTP {self.status} {label}" + (f": {message}" if message else ""))


class BadRequest(ModelApiError):
    """400: schema or parameter problem (log the body; do not retry)."""


class InvalidApiKey(ModelApiError):
    """401 invalid_api_key."""


class BillingNotConfigured(ModelApiError):
    """402 billing_not_configured: stop live mode and switch to replay (design 09 section 2-13)."""


class ModelNotFound(ModelApiError):
    """404 model_not_found: wrong id or no access."""


class RateLimited(ModelApiError):
    """429 after the retry budget (max 3) or with a Retry-After above the cap."""


class ServerError(ModelApiError):
    """5xx. Not retried (the request may have been billed); the ledger keeps the estimate."""


class ApiError(ModelApiError):
    """Any other non-2xx status."""


_STATUS_ERRORS: dict[int, type[ModelApiError]] = {400: BadRequest, 401: InvalidApiKey, 402: BillingNotConfigured,
                                                  404: ModelNotFound, 429: RateLimited}
_API_ERROR_CLASSES: dict[str, type[ModelApiError]] = {
    c.__name__: c for c in (ModelApiError, BadRequest, InvalidApiKey, BillingNotConfigured, ModelNotFound,
                            RateLimited, ServerError, ApiError)}

# Everything a pipeline stage should catch to fail closed.
MODEL_LAYER_ERRORS = (ModelClientError, BudgetExceeded, LedgerCorrupt, ReplayError)


def api_error_from_response(status: int, payload: bytes, secrets: Sequence[str] = (), note: str = "") -> ModelApiError:
    """Map an HTTP error response to a typed error. The body is scrubbed and truncated before it is kept."""
    text = payload.decode("utf-8", errors="replace") if payload else ""
    error_type = error_code = message = ""
    try:
        body = json.loads(text) if text else None
    except ValueError:
        body = None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        error_type = str(err.get("type") or "")
        error_code = str(err.get("code") or "")
        message = str(err.get("message") or "")
    elif isinstance(err, str):
        message = err
    if status >= 500:
        cls: type[ModelApiError] = ServerError
    else:
        cls = _STATUS_ERRORS.get(status, ApiError)
    if error_code == "billing_not_configured":
        cls = BillingNotConfigured
    message = scrub_text(message, secrets)[:500]
    if note:
        message = f"{message} ({note})" if message else note
    return cls(status, error_type=scrub_text(error_type, secrets)[:100], error_code=scrub_text(error_code, secrets)[:100],
               message=message, body_excerpt=scrub_text(text, secrets)[:ERROR_BODY_LIMIT])


def _error_to_dict(exc: Exception) -> dict:
    if isinstance(exc, ModelApiError):
        return {"class": type(exc).__name__, "status": exc.status, "error_type": exc.error_type,
                "error_code": exc.error_code, "message": exc.message, "body_excerpt": exc.body_excerpt}
    return {"class": type(exc).__name__, "message": str(exc)}


def _error_from_dict(d: Mapping[str, Any]) -> Exception:
    name = d.get("class")
    if name in _API_ERROR_CLASSES:
        return _API_ERROR_CLASSES[name](int(d["status"]), error_type=d.get("error_type", ""),
                                        error_code=d.get("error_code", ""), message=d.get("message", ""),
                                        body_excerpt=d.get("body_excerpt", ""))
    if name == "TransportError":
        return TransportError(d.get("message", ""))
    raise ReplayFormatError(f"recorded error class {name!r} cannot be replayed")


# =============================================================================================
# minimal JSON-schema validator (subset used by contracts/model_*.schema.json)
# =============================================================================================

_SUPPORTED_KEYWORDS = frozenset({"type", "properties", "required", "additionalProperties", "enum", "items",
                                 "minItems", "maxItems", "description", "title"})
_JSON_TYPES = frozenset({"string", "integer", "number", "boolean", "object", "array", "null"})


def check_schema_supported(schema: Any, path: str = "$") -> None:
    """Raise SchemaError if the schema uses anything the validator would silently ignore."""
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: a schema must be a JSON object")
    unknown = sorted(set(schema) - _SUPPORTED_KEYWORDS)
    if unknown:
        raise SchemaError(f"{path}: unsupported keyword(s) {unknown}; the in-code validator does not implement them")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not types or any(t not in _JSON_TYPES for t in types):
            raise SchemaError(f"{path}: bad type {schema['type']!r}")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        raise SchemaError(f"{path}: properties must be an object")
    for name, sub in props.items():
        check_schema_supported(sub, f"{path}.properties.{name}")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
        raise SchemaError(f"{path}: required must be a list of strings")
    additional = schema.get("additionalProperties", True)
    if isinstance(additional, dict):
        check_schema_supported(additional, f"{path}.additionalProperties")
    elif not isinstance(additional, bool):
        raise SchemaError(f"{path}: additionalProperties must be a boolean or a schema")
    if "items" in schema:
        if not isinstance(schema["items"], dict):
            raise SchemaError(f"{path}: only single-schema 'items' is supported")
        check_schema_supported(schema["items"], f"{path}.items")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise SchemaError(f"{path}: enum must be a non-empty list")
    for bound in ("minItems", "maxItems"):
        if bound in schema and (isinstance(schema[bound], bool) or not isinstance(schema[bound], int) or schema[bound] < 0):
            raise SchemaError(f"{path}: {bound} must be a non-negative integer")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number" if math.isfinite(value) else "non-finite number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value: Any, expected: str) -> bool:
    actual = _json_type(value)
    if expected == "integer":  # JSON Schema: a number with zero fractional part is an integer (1.0 ok, True not)
        return actual == "integer" or (actual == "number" and float(value).is_integer())
    if expected == "number":
        return actual in ("integer", "number")
    return actual == expected


def _enum_has(value: Any, options: list) -> bool:
    for option in options:
        if isinstance(option, bool) or isinstance(value, bool):
            if isinstance(option, bool) and isinstance(value, bool) and option is value:
                return True
        elif isinstance(option, (int, float)) and isinstance(value, (int, float)):
            if option == value:
                return True
        elif type(option) is type(value) and option == value:
            return True
    return False


def _short(value: Any, limit: int = 60) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _validate(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_matches(value, t) for t in types):
            errors.append(f"{path}: expected {'|'.join(types)}, got {_json_type(value)}")
            return
    if "enum" in schema and not _enum_has(value, schema["enum"]):
        errors.append(f"{path}: value {_short(value)} not in enum")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        for name, sub in props.items():
            if name in value:
                _validate(value[name], sub, f"{path}.{name}", errors)
        additional = schema.get("additionalProperties", True)
        extra = sorted(k for k in value if k not in props)
        if additional is False:
            for name in extra:
                errors.append(f"{path}: additional property {_short(name)} not allowed")
        elif isinstance(additional, dict):
            for name in extra:
                _validate(value[name], additional, f"{path}.{name}", errors)
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: {len(value)} items, fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: {len(value)} items, more than maxItems {schema['maxItems']}")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                _validate(item, schema["items"], f"{path}[{i}]", errors)


def validate(instance: Any, schema: dict) -> list[str]:
    """Return a deterministic list of violations (empty = valid). Paths look like ``$.requirements[0].quote``."""
    errors: list[str] = []
    _validate(instance, schema, "$", errors)
    return errors


# =============================================================================================
# contracts
# =============================================================================================

_SCHEMA_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


@dataclass(frozen=True)
class SchemaDoc:
    name: str
    version: str
    schema: dict
    sha256: str


def load_schema(schema_name: str, contracts_dir: Path | str | None = None) -> SchemaDoc:
    """Load ``contracts/model_<schema_name>.schema.json`` and check the validator supports every keyword."""
    if not isinstance(schema_name, str) or not _SCHEMA_NAME_RE.match(schema_name):
        raise SchemaError(f"invalid schema name {schema_name!r}")
    path = Path(contracts_dir or CONTRACTS_DIR) / f"model_{schema_name}.schema.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SchemaError(f"no contract file {path.name}") from None
    except ValueError as exc:
        raise SchemaError(f"{path.name}: not valid JSON ({exc})") from None
    if not isinstance(doc, dict) or set(doc) != {"schema_version", "name", "schema"}:
        raise SchemaError(f"{path.name}: expected exactly the keys schema_version, name, schema")
    if doc["name"] != schema_name:
        raise SchemaError(f"{path.name}: name is {doc['name']!r}, expected {schema_name!r}")
    check_schema_supported(doc["schema"])
    return SchemaDoc(schema_name, str(doc["schema_version"]), doc["schema"], sha256_hex(canonical_json(doc["schema"])))


def schema_instruction_message(doc: SchemaDoc) -> dict:
    """Used only when a host cannot take response_format=json_schema: the schema goes into the prompt."""
    return {"role": "user", "content": "Return exactly one JSON object that conforms to this JSON Schema "
                                       "(no prose, no code fences):\n" + canonical_json(doc.schema)}


# =============================================================================================
# results and response parsing
# =============================================================================================

@dataclass(frozen=True)
class Usage:
    """Token usage as reported by the provider. ``output_tokens`` includes reasoning tokens (billed as output)."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    reported: bool = True  # False: the response had no usable usage block (ledger keeps the estimate)

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens, self.cached_input_tokens + other.cached_input_tokens,
                     self.output_tokens + other.output_tokens, self.reasoning_tokens + other.reasoning_tokens,
                     self.reported and other.reported)

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "cached_input_tokens": self.cached_input_tokens,
                "output_tokens": self.output_tokens, "reasoning_tokens": self.reasoning_tokens,
                "reported": self.reported}


@dataclass(frozen=True)
class ModelResult:
    """``parsed`` is set only when the reply passed strict JSON parsing and contract validation.

    ``error`` is None or ``"<code>: <detail>"`` with code in json_parse_error, schema_invalid, empty_content,
    truncated, refusal, content_filter, bad_response_shape, contributor_model_resolved.
    ``usage`` and ``latency_ms`` are summed over the tries of this call; ``raw`` is the last raw response.
    """

    parsed: dict | None
    raw: dict | None
    usage: Usage
    model_resolved: str
    latency_ms: int
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None and self.parsed is not None

    @property
    def error_code(self) -> str | None:
        return None if self.error is None else self.error.split(":", 1)[0]


def _tok(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _first_tok(*values: Any) -> int | None:
    for value in values:
        if _tok(value) is not None:
            return value
    return None


def _sub(d: Mapping[str, Any], key: str, sub: str) -> Any:
    inner = d.get(key)
    return inner.get(sub) if isinstance(inner, dict) else None


def parse_usage(raw: Any) -> Usage:
    """Accepts Chat Completions names (prompt_tokens/completion_tokens/prompt_tokens_details.cached_tokens) and
    Responses-style names (input_tokens/output_tokens/input_tokens_details.cached_tokens). Which one the Meta
    Chat endpoint uses is unverified (docs/model_layer_notes.md)."""
    usage = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict):
        return Usage(reported=False)
    inp = _first_tok(usage.get("prompt_tokens"), usage.get("input_tokens"))
    out = _first_tok(usage.get("completion_tokens"), usage.get("output_tokens"))
    cached = _first_tok(_sub(usage, "prompt_tokens_details", "cached_tokens"),
                        _sub(usage, "input_tokens_details", "cached_tokens"), usage.get("cached_tokens"))
    reasoning = _first_tok(_sub(usage, "completion_tokens_details", "reasoning_tokens"),
                           _sub(usage, "output_tokens_details", "reasoning_tokens"), usage.get("reasoning_tokens"))
    return Usage(inp or 0, cached or 0, out or 0, reasoning or 0, reported=inp is not None and out is not None)


def parse_json_strict(text: str) -> Any:
    """json.loads that rejects NaN/Infinity and duplicate keys (ambiguous output fails closed)."""

    def reject_constant(name: str) -> Any:
        raise ValueError(f"non-standard JSON constant {name}")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
        out: dict = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate key {_short(key)}")
            out[key] = value
        return out

    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=no_duplicates)


_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)\r?\n?```$", re.S)


def strip_code_fence(text: str) -> str:
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text


def extract_message_text(raw: Any) -> tuple[str | None, str | None]:
    """Return (text, None) or (None, error) from a Chat Completions response."""
    choices = raw.get("choices") if isinstance(raw, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "bad_response_shape: no choices[0]"
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        return None, "bad_response_shape: no choices[0].message"
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        return None, "refusal: " + refusal.strip()[:200]
    finish = choice.get("finish_reason")
    if finish == "length":
        return None, "truncated: finish_reason=length (output budget used up; raise max_output_tokens)"
    if finish == "content_filter":
        return None, "content_filter: finish_reason=content_filter"
    content = message.get("content")
    if isinstance(content, list):
        parts = [p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)]
        content = "".join(parts) if parts else None
    if content is None or (isinstance(content, str) and not content.strip()):
        return None, "empty_content"
    if not isinstance(content, str):
        return None, "bad_response_shape: message content is not text"
    return content, None


def postprocess(raw: Any, schema: dict, *, lenient: bool = False) -> tuple[dict | None, str | None]:
    """Parse + validate one raw response. Deterministic, shared by live and replay paths."""
    text, error = extract_message_text(raw)
    if error is not None:
        return None, error
    if lenient:
        text = strip_code_fence(text)
    try:
        obj = parse_json_strict(text)
    except ValueError as exc:
        return None, f"json_parse_error: {exc}"
    errors = validate(obj, schema)
    if errors:
        more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
        return None, "schema_invalid: " + "; ".join(errors[:5]) + more
    return obj, None


def corrective_message(error: str) -> dict:
    """Appended to the original messages for the retry after an invalid reply (deterministic text)."""
    return {"role": "user", "content": "A previous reply to this request was rejected by the output validator ("
                                       + error[:400] + "). Reply with exactly one JSON object that conforms to the "
                                       "required JSON Schema: no prose, no code fences, no keys outside the schema."}


def _resolved_model(raw: Any) -> str:
    model = raw.get("model") if isinstance(raw, dict) else None
    return model if isinstance(model, str) else ""


def estimate_input_tokens(body: Mapping[str, Any]) -> int:
    """Upper bound for text input: UTF-8 bytes of the serialized request (a byte-level BPE token covers >= 1
    byte). Very pessimistic for base64 images/PDF; there is no stdlib tokenizer."""
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


# =============================================================================================
# shared call loop
# =============================================================================================

_STAGE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_ROLES = frozenset({"system", "developer", "user", "assistant"})


def _check_call_args(stage: Any, attempt: Any, messages: Any) -> list:
    if not isinstance(stage, str) or not _STAGE_RE.match(stage):
        raise ValueError(f"stage must match {_STAGE_RE.pattern}, got {stage!r}")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        raise ValueError(f"attempt must be a non-negative int, got {attempt!r}")
    if not isinstance(messages, (list, tuple)) or not messages:
        raise ValueError("messages must be a non-empty list")
    for i, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in _ROLES \
                or not isinstance(message.get("content"), (str, list)):
            raise ValueError(f"messages[{i}] must be a dict with role in {sorted(_ROLES)} and str/list content")
    return list(messages)


@dataclass(frozen=True)
class _Exchange:
    raw: dict
    latency_ms: int
    headers: dict


def _try_loop(messages: list, doc: SchemaDoc, max_tries: int, lenient: bool,
              fetch: Callable[[int, list], _Exchange]) -> ModelResult:
    """Identical for live, fake and replay: fetch try 0; if the reply is invalid and retryable, fetch try 1 ..."""
    usage: Usage | None = None
    latency = 0
    raw: dict | None = None
    model_resolved = ""
    parsed: dict | None = None
    error: str | None = None
    for try_index in range(max_tries):
        msgs = messages if try_index == 0 else [*messages, corrective_message(error or "")]
        exchange = fetch(try_index, msgs)
        raw = exchange.raw
        try_usage = parse_usage(raw)
        usage = try_usage if usage is None else usage + try_usage
        latency += exchange.latency_ms
        model_resolved = _resolved_model(raw)
        parsed, error = postprocess(raw, doc.schema, lenient=lenient)
        if error is None and model_resolved.endswith(CONTRIBUTOR_SUFFIX):
            parsed, error = None, f"contributor_model_resolved: {model_resolved}"
        if error is None or error.split(":", 1)[0] not in RETRYABLE_OUTPUT_ERRORS:
            break
    return ModelResult(parsed, raw, usage if usage is not None else Usage(reported=False), model_resolved, latency, error)


class ModelClient(Protocol):
    def complete(self, stage: str, attempt: int, messages: Sequence[dict], schema_name: str) -> ModelResult: ...


# =============================================================================================
# base client (fake + HTTP)
# =============================================================================================

class BaseClient:
    provider = "base"

    def __init__(self, *, model: str, run_id: str | None = None, ledger: Ledger | None = None,
                 recorder: Recorder | None = None, pricing: Pricing = DEFAULT_PRICING, invalid_output_retries: int = 1,
                 lenient_json: bool = False, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                 contracts_dir: Path | str | None = None):
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty string")
        if model.endswith(CONTRIBUTOR_SUFFIX):
            raise ContributorModelRefused(f"model id {model!r} is a Contributor id (inputs are used for Meta "
                                          "training, design D15); refused")
        if isinstance(invalid_output_retries, bool) or not isinstance(invalid_output_retries, int) \
                or invalid_output_retries < 0:
            raise ValueError("invalid_output_retries must be a non-negative int")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive int")
        self.model = model
        self.run_id = run_id
        self.ledger = ledger
        self.recorder = recorder
        self.pricing = pricing
        self.invalid_output_retries = invalid_output_retries
        self.lenient_json = lenient_json
        self.max_output_tokens = max_output_tokens
        self.contracts_dir = contracts_dir

    # -- public ---------------------------------------------------------------------------------
    def complete(self, stage: str, attempt: int, messages: Sequence[dict], schema_name: str) -> ModelResult:
        messages = _check_call_args(stage, attempt, messages)
        self._preflight()  # live guard first: nothing is built, read or written before it passes
        doc = load_schema(schema_name, self.contracts_dir)
        rhash = request_hash(messages, doc.name, doc.version, doc.sha256)
        max_tries = 1 + self.invalid_output_retries

        def fetch(try_index: int, msgs: list) -> _Exchange:
            return self._one_try(stage, attempt, try_index, max_tries, msgs, doc, rhash)

        return _try_loop(messages, doc, max_tries, self.lenient_json, fetch)

    # -- hooks ----------------------------------------------------------------------------------
    def _preflight(self) -> None:
        pass

    def _build_body(self, msgs: list, doc: SchemaDoc) -> dict:
        return {"model": self.model, "messages": msgs}

    def _exchange(self, stage: str, attempt: int, try_index: int, body: dict) -> _Exchange:
        raise NotImplementedError

    def _request_headers_for_record(self) -> dict:
        return {}

    # -- one billable request -------------------------------------------------------------------
    def _record(self, entry: dict) -> None:
        if self.recorder is not None:
            self.recorder.record(entry)

    def _one_try(self, stage: str, attempt: int, try_index: int, max_tries: int, msgs: list, doc: SchemaDoc,
                 rhash: str) -> _Exchange:
        body = self._build_body(msgs, doc)
        base = {"run_id": self.run_id or "", "stage": stage, "attempt": attempt, "try_index": try_index,
                "max_tries": max_tries, "request_hash": rhash, "schema_name": doc.name,
                "schema_version": doc.version, "provider": self.provider, "model_requested": self.model,
                "lenient_json": self.lenient_json}
        reservation: Reservation | None = None
        if self.ledger is not None:
            try:
                reservation = self.ledger.reserve(
                    run_id=self.run_id or "", stage=stage, attempt=attempt, try_index=try_index,
                    est_input_tokens=estimate_input_tokens(body), max_output_tokens=self.max_output_tokens,
                    provider=self.provider, model=self.model, pricing=self.pricing)
            except BudgetExceeded as exc:
                self._record({**base, "outcome": "budget_exceeded", "error": {"class": "BudgetExceeded",
                                                                              **exc.to_dict()}, "latency_ms": 0})
                raise
        try:
            exchange = self._exchange(stage, attempt, try_index, body)
        except ModelApiError as exc:
            if reservation is not None:
                if exc.status >= 500:
                    self.ledger.settle_unknown(reservation, reason=f"http_{exc.status}")
                else:  # rejected before processing: assumed unbilled (verify in live smoke test)
                    self.ledger.settle(reservation, input_tokens=0, cached_input_tokens=0, output_tokens=0,
                                       outcome=f"http_{exc.status}", pricing=self.pricing)
            self._record({**base, "outcome": "error", "error": _error_to_dict(exc), "latency_ms": 0})
            raise
        except TransportError as exc:
            if reservation is not None:
                self.ledger.settle_unknown(reservation, reason="transport_error")
            self._record({**base, "outcome": "error", "error": _error_to_dict(exc), "latency_ms": 0})
            raise
        usage = parse_usage(exchange.raw)
        if reservation is not None:
            if usage.reported:
                cost = self.ledger.settle(reservation, input_tokens=usage.input_tokens,
                                          cached_input_tokens=usage.cached_input_tokens,
                                          output_tokens=usage.output_tokens, pricing=self.pricing)
                basis = "usage"
            else:
                cost = self.ledger.settle_unknown(reservation, reason="usage_missing")
                basis = "estimate"
        else:
            cost = self.pricing.cost_e8(usage.input_tokens, usage.cached_input_tokens, usage.output_tokens)
            basis = "usage" if usage.reported else "none"
        self._record({**base, "outcome": "response", "raw_response": exchange.raw, "usage": usage.as_dict(),
                      "model_resolved": _resolved_model(exchange.raw), "cost_e8": cost, "cost_usd": e8_to_usd(cost),
                      "cost_basis": basis, "pricing": self.pricing.name, "latency_ms": exchange.latency_ms,
                      "request_headers": self._request_headers_for_record(), "response_headers": exchange.headers})
        return exchange


# =============================================================================================
# FakeClient
# =============================================================================================

@dataclass(frozen=True)
class FakeResponse:
    """One fixture reply. ``content``: dict/list -> JSON-encoded; str -> sent as-is (may be broken on purpose)."""

    content: Any = None
    usage: Mapping[str, Any] | None = None  # OpenAI-style usage block; None -> zeros
    finish_reason: str = "stop"
    refusal: str | None = None
    latency_ms: int = 0
    model: str | None = None
    omit_usage: bool = False


class FakeClient(BaseClient):
    """Fixture-driven client. ``fixtures[(stage, attempt)]`` is a reply (dict, str, FakeResponse), an exception
    instance to raise (e.g. ``BillingNotConfigured(402)``), or a list of those, one per try."""

    provider = "fake"

    def __init__(self, fixtures: Mapping[tuple[str, int], Any], *, model: str = "fake-model", **kwargs: Any):
        super().__init__(model=model, **kwargs)
        self.fixtures = {(str(k[0]), int(k[1])): v for k, v in fixtures.items()}
        self.calls: list[tuple[str, int, int]] = []
        self.bodies: list[dict] = []

    @classmethod
    def from_file(cls, path: Path | str, **kwargs: Any) -> "FakeClient":
        """JSON file: ``{"model": "...", "fixtures": [{"stage", "attempt", "responses": [reply, ...]}]}`` where a
        reply is ``{"content": {...}}`` or ``{"content_text": "..."}`` (+ usage, finish_reason, refusal,
        latency_ms, model, omit_usage) or ``{"error": {"status": 402, "body": {...}}}``."""
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        fixtures: dict[tuple[str, int], Any] = {}
        for item in doc["fixtures"]:
            replies = []
            for spec in item["responses"]:
                if "error" in spec:
                    body = json.dumps(spec["error"].get("body", {})).encode("utf-8")
                    replies.append(api_error_from_response(int(spec["error"]["status"]), body))
                    continue
                content = spec["content_text"] if "content_text" in spec else spec.get("content")
                replies.append(FakeResponse(content=content, usage=spec.get("usage"),
                                            finish_reason=spec.get("finish_reason", "stop"),
                                            refusal=spec.get("refusal"), latency_ms=int(spec.get("latency_ms", 0)),
                                            model=spec.get("model"), omit_usage=bool(spec.get("omit_usage", False))))
            fixtures[(item["stage"], int(item["attempt"]))] = replies
        kwargs.setdefault("model", doc.get("model", "fake-model"))
        return cls(fixtures, **kwargs)

    def _exchange(self, stage: str, attempt: int, try_index: int, body: dict) -> _Exchange:
        self.calls.append((stage, attempt, try_index))
        self.bodies.append(body)
        spec = self.fixtures.get((stage, attempt))
        if spec is None:
            raise FixtureMissing(f"no fixture for stage={stage} attempt={attempt}")
        if isinstance(spec, list):
            if try_index >= len(spec):
                raise FixtureMissing(f"no fixture for stage={stage} attempt={attempt} try={try_index}")
            spec = spec[try_index]
        if isinstance(spec, BaseException):
            raise spec
        if not isinstance(spec, FakeResponse):
            spec = FakeResponse(content=spec)
        text = spec.content if spec.content is None or isinstance(spec.content, str) \
            else json.dumps(spec.content, ensure_ascii=False)
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if spec.refusal is not None:
            message["refusal"] = spec.refusal
        raw: dict[str, Any] = {"id": f"fake-{stage}-{attempt}-{try_index}", "object": "chat.completion",
                               "model": spec.model or self.model,
                               "choices": [{"index": 0, "message": message, "finish_reason": spec.finish_reason}]}
        if not spec.omit_usage:
            raw["usage"] = dict(spec.usage) if spec.usage is not None else \
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return _Exchange(raw=json.loads(json.dumps(raw)), latency_ms=spec.latency_ms, headers={})


# =============================================================================================
# HTTP clients
# =============================================================================================

def live_enabled_by_env() -> bool:
    return os.environ.get(LIVE_ENV) == "1"


def parse_retry_after(value: str, now: float) -> float | None:
    """Retry-After as delta-seconds or HTTP-date. None if unparseable."""
    text = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - now)


def _headers_dict(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(k): str(v) for k, v in headers.items()}


def _decode_body(data: bytes) -> dict:
    text = data.decode("utf-8", errors="replace")
    try:
        obj = parse_json_strict(text)
    except ValueError:
        return {"_non_json_body": text[:20000]}
    return obj if isinstance(obj, dict) else {"_non_object_body": obj}


_RESERVED_BODY_KEYS = frozenset({"model", "messages", "response_format", "stream", "tools", "tool_choice",
                                 "functions", "function_call", "max_tokens", "max_completion_tokens"})


class _HttpChatClient(BaseClient):
    """POST {base_url}/chat/completions with stdlib urllib. See module docstring for the live guard."""

    def __init__(self, *, base_url: str, model: str, api_key: str | None, live: bool, provider: str,
                 require_api_key: bool, response_format_mode: str = "json_schema", strict: bool | None = True,
                 max_tokens_param: str | None = "max_completion_tokens", extra_body: Mapping[str, Any] | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic, wall_clock: Callable[[], float] = time.time,
                 max_429_retries: int = MAX_429_RETRIES, max_retry_after_s: float = DEFAULT_MAX_RETRY_AFTER_S,
                 **kwargs: Any):
        super().__init__(model=model, **kwargs)
        self._live = live is True
        if self._live and not live_enabled_by_env():
            raise LiveCallsDisabled(f"live=True but environment variable {LIVE_ENV} is not '1'")
        if not isinstance(base_url, str) or not re.match(r"^https?://[^\s/]+", base_url):
            raise ValueError(f"base_url must be an http(s) URL, got {base_url!r}")
        if response_format_mode not in ("json_schema", "json_object", "none"):
            raise ValueError("response_format_mode must be json_schema, json_object or none")
        extra = dict(extra_body or {})
        clash = sorted(set(extra) & (_RESERVED_BODY_KEYS | ({max_tokens_param} if max_tokens_param else set())))
        if clash:
            raise ValueError(f"extra_body may not set {clash} (core request keys; no tools, no streaming)")
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.__api_key = api_key or ""
        self._require_api_key = require_api_key
        self.response_format_mode = response_format_mode
        self.strict = strict
        self.max_tokens_param = max_tokens_param
        self.extra_body = extra
        self.timeout_s = timeout_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self.max_429_retries = max_429_retries
        self.max_retry_after_s = max_retry_after_s

    def __repr__(self) -> str:  # never shows the key
        return (f"{type(self).__name__}(provider={self.provider!r}, model={self.model!r}, "
                f"base_url={self.base_url!r}, live={self._live})")

    def _secrets(self) -> tuple[str, ...]:
        return (self.__api_key,) if self.__api_key else ()

    def _assert_live_allowed(self) -> None:
        if not self._live:
            raise LiveCallsDisabled(f"{type(self).__name__} was constructed with live=False and never sends requests")
        if not live_enabled_by_env():
            raise LiveCallsDisabled(f"environment variable {LIVE_ENV} is not '1'")

    def _preflight(self) -> None:
        self._assert_live_allowed()
        if self._require_api_key and not self.__api_key:
            raise MissingApiKey(f"{type(self).__name__} needs api_key (pass it explicitly or use from_env)")
        if self.ledger is None:
            raise MissingLiveDependency("live calls need a Ledger (budget caps, design 09 section 2-14)")
        if self.recorder is None:
            raise MissingLiveDependency("live calls need a Recorder (replay, design 09 section 2-15)")
        if not self.run_id:
            raise MissingLiveDependency("live calls need a run_id")
        for secret in self._secrets():
            self.recorder.register_secret(secret)

    def _request_headers_for_record(self) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.__api_key:
            headers["Authorization"] = REDACTED  # the real value is never handed to the recorder
        return headers

    def _build_body(self, msgs: list, doc: SchemaDoc) -> dict:
        msgs = list(msgs)
        response_format: dict | None = None
        if self.response_format_mode == "json_schema":
            json_schema: dict[str, Any] = {"name": doc.name, "schema": doc.schema}
            if self.strict is not None:
                json_schema["strict"] = bool(self.strict)
            response_format = {"type": "json_schema", "json_schema": json_schema}
        else:
            if self.response_format_mode == "json_object":
                response_format = {"type": "json_object"}
            msgs.append(schema_instruction_message(doc))
        body: dict[str, Any] = {"model": self.model, "messages": msgs}
        if response_format is not None:
            body["response_format"] = response_format
        if self.max_tokens_param:
            body[self.max_tokens_param] = self.max_output_tokens
        body.update(self.extra_body)
        return body

    def _post(self, payload: bytes) -> tuple[int, dict[str, str], bytes, int]:
        self._assert_live_allowed()  # second check, immediately before the network
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.__api_key:
            headers["Authorization"] = "Bearer " + self.__api_key
        request = urllib.request.Request(self.base_url + "/chat/completions", data=payload, headers=headers,
                                         method="POST")
        started = self._monotonic()
        try:
            # module attribute lookup at call time (tests replace urllib.request.urlopen)
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                status = response.status if getattr(response, "status", None) is not None else response.getcode()
                response_headers = _headers_dict(response.headers)
                data = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_headers = _headers_dict(exc.headers)
            try:
                data = exc.read() or b""
            except (OSError, AttributeError, ValueError):
                data = b""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise TransportError(f"{type(exc).__name__}: {scrub_text(str(reason), self._secrets())}") from None
        latency_ms = int(round((self._monotonic() - started) * 1000))
        return int(status), response_headers, data, latency_ms

    def _retry_delay(self, retry_number: int, headers: Mapping[str, str]) -> float:
        for key, value in headers.items():
            if key.lower() == "retry-after":
                seconds = parse_retry_after(value, self._wall_clock())
                if seconds is not None:
                    return seconds
        return BACKOFF_BASE_S * (2 ** retry_number)

    def _exchange(self, stage: str, attempt: int, try_index: int, body: dict) -> _Exchange:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        retries = 0
        while True:
            status, headers, data, latency_ms = self._post(payload)
            if status == 429:
                delay = self._retry_delay(retries, headers)
                if retries >= self.max_429_retries or delay > self.max_retry_after_s:
                    note = (f"gave up after {retries} retries" if retries >= self.max_429_retries
                            else f"Retry-After {delay:g}s exceeds cap {self.max_retry_after_s:g}s")
                    exc = api_error_from_response(429, data, self._secrets(), note=note)
                    log.warning("model API %s %s/%s: %s | body=%s", self.provider, stage, attempt, exc, exc.body_excerpt)
                    raise exc
                log.info("model API %s %s/%s: 429, sleeping %.3gs (retry %d/%d)", self.provider, stage, attempt,
                         delay, retries + 1, self.max_429_retries)
                self._sleep(delay)
                retries += 1
                continue
            if not 200 <= status < 300:
                exc = api_error_from_response(status, data, self._secrets())
                log.warning("model API %s %s/%s: %s | body=%s", self.provider, stage, attempt, exc, exc.body_excerpt)
                raise exc
            return _Exchange(raw=_decode_body(data), latency_ms=latency_ms, headers=redact_headers(headers))


class MetaClient(_HttpChatClient):
    """Meta Model API (Chat Completions + response_format json_schema strict), model fixed to muse-spark-1.3."""

    def __init__(self, *, api_key: str | None = None, live: bool = False, model: str = META_MODEL_ID,
                 strict: bool | None = True, max_tokens_param: str | None = "max_completion_tokens",
                 extra_body: Mapping[str, Any] | None = None, **kwargs: Any):
        if isinstance(model, str) and model.endswith(CONTRIBUTOR_SUFFIX):
            raise ContributorModelRefused(f"model id {model!r} is a Contributor id (inputs are used for Meta "
                                          "training, design D15); refused")
        if model != META_MODEL_ID:
            raise UnsupportedModel(f"MetaClient is fixed to {META_MODEL_ID!r} (design D15), got {model!r}")
        super().__init__(base_url=META_BASE_URL, model=model, api_key=api_key, live=live, provider="meta",
                         require_api_key=True, response_format_mode="json_schema", strict=strict,
                         max_tokens_param=max_tokens_param, extra_body=extra_body, lenient_json=False, **kwargs)

    @classmethod
    def from_env(cls, *, live: bool = False, **kwargs: Any) -> "MetaClient":
        """Reads MODEL_API_KEY only after the live guard passed; the key is passed explicitly (06 section 2-1)."""
        if live is not True or not live_enabled_by_env():
            raise LiveCallsDisabled(f"MetaClient.from_env needs live=True and {LIVE_ENV}=1; the key is not read otherwise")
        key = os.environ.get(API_KEY_ENV, "")
        if not key:
            raise MissingApiKey(f"environment variable {API_KEY_ENV} is empty")
        return cls(api_key=key, live=True, **kwargs)


class OpenAICompatibleClient(_HttpChatClient):
    """OpenAI-compatible host (Baseten, Fireworks, llama.cpp ...). json_schema support is NOT assumed: the reply is
    always parsed and validated in code with at least one retry, and code fences are tolerated (lenient_json)."""

    def __init__(self, *, base_url: str, model: str, api_key: str | None = None, live: bool = False,
                 provider: str = "openai_compatible", require_api_key: bool = False,
                 response_format_mode: str = "json_schema", strict: bool | None = True,
                 max_tokens_param: str | None = "max_tokens", lenient_json: bool = True,
                 invalid_output_retries: int = 1, pricing: Pricing = DEFAULT_PRICING, **kwargs: Any):
        if isinstance(invalid_output_retries, bool) or not isinstance(invalid_output_retries, int) \
                or invalid_output_retries < 1:
            raise ValueError("non-Meta providers always keep the parse+validate+retry layer: "
                             "invalid_output_retries must be >= 1 (design 09 section 2-4)")
        super().__init__(base_url=base_url, model=model, api_key=api_key, live=live, provider=provider,
                         require_api_key=require_api_key, response_format_mode=response_format_mode, strict=strict,
                         max_tokens_param=max_tokens_param, lenient_json=lenient_json,
                         invalid_output_retries=invalid_output_retries, pricing=pricing, **kwargs)

    @classmethod
    def from_env(cls, *, live: bool = False, key_env: str, **kwargs: Any) -> "OpenAICompatibleClient":
        if live is not True or not live_enabled_by_env():
            raise LiveCallsDisabled(f"from_env needs live=True and {LIVE_ENV}=1; the key is not read otherwise")
        key = os.environ.get(key_env, "")
        if not key:
            raise MissingApiKey(f"environment variable {key_env} is empty")
        return cls(api_key=key, live=True, require_api_key=True, **kwargs)


def local_client(*, model: str, base_url: str = "http://127.0.0.1:8080/v1", live: bool = False,
                 **kwargs: Any) -> OpenAICompatibleClient:
    """Local OpenAI-compatible server (e.g. llama.cpp serving Muse Glimmer). Zero pricing; same live guard."""
    kwargs.setdefault("pricing", ZERO_PRICING)
    return OpenAICompatibleClient(base_url=base_url, model=model, api_key=None, live=live, provider="local", **kwargs)


# =============================================================================================
# ReplayClient
# =============================================================================================

class ReplayClient:
    """Serves ``runs/<run_id>/calls.jsonl`` through the same parse/validate loop. Zero network, zero cost.

    The request hash of every call must match the recording (``ReplayMismatch`` otherwise): a stale recording
    is never served silently. Recorded HTTP errors and budget stops are raised again, so a partial run replays
    to the same partial report."""

    provider = "replay"

    def __init__(self, run_dir: Path | str, *, strict_hash: bool = True, contracts_dir: Path | str | None = None):
        self.run_dir = Path(run_dir)
        self.entries = load_calls(self.run_dir)
        self.strict_hash = strict_hash
        self.contracts_dir = contracts_dir
        self.calls: list[tuple[str, int, int]] = []

    @classmethod
    def for_run(cls, runs_root: Path | str, run_id: str, **kwargs: Any) -> "ReplayClient":
        return cls(run_dir_for(runs_root, run_id), **kwargs)

    def complete(self, stage: str, attempt: int, messages: Sequence[dict], schema_name: str) -> ModelResult:
        messages = _check_call_args(stage, attempt, messages)
        doc = load_schema(schema_name, self.contracts_dir)
        rhash = request_hash(messages, doc.name, doc.version, doc.sha256)
        first = self.entries.get((stage, attempt, 0))
        if first is None:
            raise ReplayMissing(f"no recorded call for stage={stage} attempt={attempt} in run {self.run_dir.name!r}")
        if first.get("schema_name") != doc.name:
            raise ReplayMismatch(f"stage={stage} attempt={attempt}: recorded schema {first.get('schema_name')!r}, "
                                 f"requested {doc.name!r}")
        if self.strict_hash and first.get("request_hash") != rhash:
            raise ReplayMismatch(f"stage={stage} attempt={attempt}: request differs from the recording "
                                 f"(recorded {first.get('request_hash')}, now {rhash})")
        max_tries = int(first.get("max_tries", 1))
        lenient = bool(first.get("lenient_json", False))

        def fetch(try_index: int, msgs: list) -> _Exchange:
            entry = self.entries.get((stage, attempt, try_index))
            if entry is None:
                raise ReplayMissing(f"no recorded call for stage={stage} attempt={attempt} try={try_index}")
            self.calls.append((stage, attempt, try_index))
            outcome = entry.get("outcome")
            if outcome == "response":
                raw = entry.get("raw_response")
                if not isinstance(raw, dict):
                    raise ReplayFormatError(f"stage={stage} attempt={attempt} try={try_index}: raw_response missing")
                return _Exchange(raw=raw, latency_ms=int(entry.get("latency_ms", 0)), headers={})
            if outcome == "error":
                raise _error_from_dict(entry.get("error") or {})
            if outcome == "budget_exceeded":
                raise BudgetExceeded.from_dict(entry["error"])
            raise ReplayFormatError(f"stage={stage} attempt={attempt} try={try_index}: unknown outcome {outcome!r}")

        return _try_loop(messages, doc, max_tries, lenient, fetch)


__all__ = [
    "API_KEY_ENV", "CONTRIBUTOR_SUFFIX", "LIVE_ENV", "META_BASE_URL", "META_MODEL_ID", "MODEL_LAYER_ERRORS",
    "ApiError", "BadRequest", "BaseClient", "BillingNotConfigured", "BudgetExceeded", "ContributorModelRefused",
    "FakeClient", "FakeResponse", "FixtureMissing", "InvalidApiKey", "LiveCallsDisabled", "MetaClient",
    "MissingApiKey", "MissingLiveDependency", "ModelApiError", "ModelClient", "ModelClientError", "ModelNotFound",
    "ModelResult", "OpenAICompatibleClient", "RateLimited", "ReplayClient", "SchemaDoc", "SchemaError",
    "ServerError", "TransportError", "UnsupportedModel", "Usage", "api_error_from_response",
    "check_schema_supported", "estimate_input_tokens", "live_enabled_by_env", "load_schema", "local_client",
    "parse_json_strict", "parse_retry_after", "parse_usage", "postprocess", "validate",
]
