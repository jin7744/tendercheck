"""Recording and replay storage for model calls (design 09 section 2-15). Standard library only.

Every model request a client makes is written as one line of ``runs/<run_id>/calls.jsonl``:
stage, attempt, try_index, the request hash, the raw response JSON (or the typed error), usage,
resolved model name, cost and latency. ``engine.model_client.ReplayClient`` reads the file back and
serves the stored responses through the same parse/validate code, so a replayed pipeline produces
byte-identical output with zero API calls.

What is never written
---------------------
* the request messages (they can contain user input; only their hash is stored),
* the API key or any ``Authorization``-like header: header dicts go through ``redact_headers`` and
  every secret registered with the recorder is replaced by ``[REDACTED]`` before the line is written.

Generic key patterns (``Bearer ...``) are scrubbed only from error bodies and header values, never from
raw model responses: rewriting model output would break replay fidelity (a tender may say "bearer bond").

Live run ids should start with ``live_`` (``runs/live_*/`` is git-ignored); replay bundles meant for the
public demo use other ids and must only contain synthetic data.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from .ledger import atomic_append_line, read_jsonl_rows

CALLS_FILENAME = "calls.jsonl"
RECORD_VERSION = 1
REDACTED = "[REDACTED]"

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SENSITIVE_HEADER_PARTS = ("auth", "cookie", "api-key", "apikey", "api_key", "secret", "password", "session")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s\"',;]+")
_META_KEY_RE = re.compile(r"\bLLM_[0-9]{6,}_[A-Za-z0-9_\-]{8,}")
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")


class ReplayError(RuntimeError):
    """Base class for replay failures. A replayed pipeline fails closed on any of these."""


class ReplayFormatError(ReplayError):
    """calls.jsonl is damaged or has an unexpected shape."""


class ReplayMissing(ReplayError):
    """The pipeline asked for a (stage, attempt, try) that was never recorded."""


class ReplayMismatch(ReplayError):
    """The pipeline's request differs from the recorded one (stale recording). Never serve it silently."""


# ---------------------------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """Stable JSON text: sorted keys, no whitespace, UTF-8 characters kept (ensure_ascii=False), no NaN."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def logical_request(messages: list, schema_name: str, schema_version: str, schema_sha256: str) -> dict:
    """What the pipeline asked for, independent of provider, model id and transport parameters."""
    return {"messages": messages, "schema": {"name": schema_name, "sha256": schema_sha256, "version": schema_version}}


def request_hash(messages: list, schema_name: str, schema_version: str, schema_sha256: str) -> str:
    return "sha256:" + sha256_hex(canonical_json(logical_request(messages, schema_name, schema_version, schema_sha256)))


# ---------------------------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------------------------

def _is_sensitive_header(name: str) -> bool:
    if any(part in name for part in _SENSITIVE_HEADER_PARTS):
        return True
    return "token" in name and not name.startswith("x-ratelimit")


def redact_headers(headers: Mapping[str, Any] | Iterable[tuple[str, Any]] | None) -> dict[str, str]:
    """Lower-case header names, sorted; values of credential-like headers replaced by ``[REDACTED]``."""
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    out: dict[str, str] = {}
    for key, value in items:
        name = str(key).strip().lower()
        out[name] = REDACTED if _is_sensitive_header(name) else str(value)
    return dict(sorted(out.items()))


def scrub_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove registered secrets and key-shaped strings from free text (error bodies, messages)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = _BEARER_RE.sub("Bearer " + REDACTED, text)
    text = _META_KEY_RE.sub(REDACTED, text)
    return _SK_KEY_RE.sub(REDACTED, text)


# ---------------------------------------------------------------------------------------------
# run directories, recorder, loader
# ---------------------------------------------------------------------------------------------

def run_dir_for(runs_root: Path | str, run_id: str) -> Path:
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid run_id {run_id!r}: use letters, digits, '.', '_' or '-' (max 80)")
    return Path(runs_root) / run_id


def _key(entry: Mapping[str, Any]) -> tuple[str, int, int]:
    try:
        stage, attempt, try_index = entry["stage"], entry["attempt"], entry["try_index"]
    except KeyError as exc:
        raise ReplayFormatError(f"record without {exc.args[0]!r}") from None
    if not isinstance(stage, str) or isinstance(attempt, bool) or not isinstance(attempt, int) \
            or isinstance(try_index, bool) or not isinstance(try_index, int):
        raise ReplayFormatError(f"record key has wrong types: {stage!r}, {attempt!r}, {try_index!r}")
    return stage, attempt, try_index


class Recorder:
    """Appends one line per model request to ``<run_dir>/calls.jsonl``."""

    def __init__(self, run_dir: Path | str, *, secrets: Iterable[str] = ()):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / CALLS_FILENAME
        self._secrets: list[str] = []
        for secret in secrets:
            self.register_secret(secret)
        self._lock = threading.Lock()
        self._keys = set(load_calls(self.run_dir)) if self.path.exists() else set()

    @classmethod
    def for_run(cls, runs_root: Path | str, run_id: str, **kwargs: Any) -> "Recorder":
        return cls(run_dir_for(runs_root, run_id), **kwargs)

    def register_secret(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def record(self, entry: Mapping[str, Any]) -> dict:
        key = _key(entry)
        row = dict(entry)
        row["v"] = RECORD_VERSION
        row.setdefault("run_id", self.run_dir.name)
        for header_field in ("request_headers", "response_headers"):
            if header_field in row:
                row[header_field] = redact_headers(row[header_field])
        line = canonical_json(row)
        for secret in self._secrets:
            line = line.replace(secret, REDACTED)
        with self._lock:
            if key in self._keys:
                raise ValueError(f"duplicate call record for stage={key[0]} attempt={key[1]} try={key[2]}")
            atomic_append_line(self.path, line)
            self._keys.add(key)
        return json.loads(line)


def load_calls(run_dir: Path | str) -> dict[tuple[str, int, int], dict]:
    """Read ``<run_dir>/calls.jsonl`` keyed by (stage, attempt, try_index). Damage raises ReplayFormatError."""
    path = Path(run_dir) / CALLS_FILENAME
    if not path.exists():
        raise ReplayMissing(f"no {CALLS_FILENAME} in run directory {Path(run_dir).name!r}")
    entries: dict[tuple[str, int, int], dict] = {}
    for n, row in enumerate(read_jsonl_rows(path, error=ReplayFormatError), 1):
        if row.get("v") != RECORD_VERSION:
            raise ReplayFormatError(f"line {n}: unsupported record version {row.get('v')!r}")
        key = _key(row)
        if key in entries:
            raise ReplayFormatError(f"line {n}: duplicate record for stage={key[0]} attempt={key[1]} try={key[2]}")
        entries[key] = row
    return entries
