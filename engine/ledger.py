"""Append-only budget ledger for model calls (design 09 section 2-14). Standard library only.

Meta's billing has no hard spend cap (only e-mail alerts, API notes 06 section 5), so the app enforces one:

* per run: at most $2.00 and at most 9 billable model requests,
* global: live calls stop once total spend would pass $140 (the $150 credit minus a $10 reserve).

Money is kept as integer units of 1e-8 USD ("e8") so running sums are exact. Standard
``muse-spark-1.3`` prices (06 section 1-3) per token:
input $1.25/1M = 125 e8, cached input $0.15/1M = 15 e8, output $4.25/1M = 425 e8.

Fail-closed rules
-----------------
* ``reserve()`` runs before every billable request. It checks the caps against
  (spend so far + a worst-case estimate for this request) and appends a ``reserve`` row.
  The read-check-append runs under a lock, so concurrent runs cannot both slip under a cap.
* A reservation counts at its estimate until ``settle()`` records the actual usage.
  ``settle_unknown()`` (timeout, 5xx, response without a usage block) keeps the estimate for good.
  A process that dies between reserve and settle therefore leaves the estimate charged, never zero.
* A row that cannot be parsed, or a final line without a newline, makes the ledger refuse every
  further reservation (``LedgerCorrupt``) until a person repairs the file.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

try:  # POSIX only; Windows falls back to the in-process lock + O_APPEND (see docs/model_layer_notes.md)
    import fcntl
except ImportError:  # pragma: no cover - not exercised on the Linux/WSL test host
    fcntl = None  # type: ignore[assignment]

E8_PER_USD = 100_000_000
LEDGER_VERSION = 1
DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent.parent / "runs" / "ledger.jsonl"


def e8_to_usd(e8: int) -> float:
    return e8 / E8_PER_USD


def _nonneg_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    return value


@dataclass(frozen=True)
class Pricing:
    """Per-token prices in e8 units (1e-8 USD).

    ``prompt_includes_cached``: whether the provider's reported input-token count already contains the
    cached tokens. Default False charges the reported input count at the full rate *and* the cached
    count at the cached rate, i.e. the literal design formula ``in*1.25e-6 + cached_in*0.15e-6`` with
    ``in`` = reported input tokens. If the provider counts cached tokens inside ``in`` (OpenAI
    convention) this over-charges by ``cached*0.15e-6`` - safe side. Unverified: live smoke test.
    """

    input_e8: int = 125
    cached_input_e8: int = 15
    output_e8: int = 425
    prompt_includes_cached: bool = False
    name: str = "meta-standard"

    def cost_e8(self, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> int:
        i = _nonneg_int("input_tokens", input_tokens)
        c = _nonneg_int("cached_input_tokens", cached_input_tokens)
        o = _nonneg_int("output_tokens", output_tokens)
        if self.prompt_includes_cached:
            i = max(i - c, 0)
        return i * self.input_e8 + c * self.cached_input_e8 + o * self.output_e8

    def estimate_e8(self, est_input_tokens: int, max_output_tokens: int) -> int:
        """Worst case for one request: no cache discount and the whole output budget used."""
        return self.cost_e8(est_input_tokens, 0, max_output_tokens)


DEFAULT_PRICING = Pricing()
ZERO_PRICING = Pricing(0, 0, 0, name="zero")


def cost_e8(input_tokens: int, cached_input_tokens: int, output_tokens: int, pricing: Pricing = DEFAULT_PRICING) -> int:
    return pricing.cost_e8(input_tokens, cached_input_tokens, output_tokens)


def cost_usd(input_tokens: int, cached_input_tokens: int, output_tokens: int, pricing: Pricing = DEFAULT_PRICING) -> float:
    return e8_to_usd(pricing.cost_e8(input_tokens, cached_input_tokens, output_tokens))


@dataclass(frozen=True)
class Caps:
    run_cost_e8: int = 2 * E8_PER_USD  # $2.00 per run
    run_calls: int = 9  # billable model requests per run
    global_stop_e8: int = 140 * E8_PER_USD  # live stop at $140


class LedgerCorrupt(RuntimeError):
    """The ledger file cannot be trusted; no reservation is possible until a person repairs it."""


class BudgetExceeded(RuntimeError):
    """Raised before a request that would break a cap. Nothing has been sent."""

    REASONS = ("global_stop", "run_calls", "run_cost")

    def __init__(self, reason: str, *, run_id: str, spent_e8: int, estimate_e8: int, cap: int, calls: int = 0):
        if reason not in self.REASONS:
            raise ValueError(f"unknown budget reason {reason!r}")
        self.reason = reason
        self.run_id = run_id
        self.spent_e8 = spent_e8
        self.estimate_e8 = estimate_e8
        self.cap = cap
        self.calls = calls
        if reason == "run_calls":
            msg = f"budget: run {run_id} already made {calls} model requests (cap {cap})"
        elif reason == "run_cost":
            msg = (f"budget: run {run_id} spent ${e8_to_usd(spent_e8):.6f} + estimate ${e8_to_usd(estimate_e8):.6f} "
                   f"would exceed the run cap ${e8_to_usd(cap):.2f}")
        else:
            msg = (f"budget: global spend ${e8_to_usd(spent_e8):.6f} + estimate ${e8_to_usd(estimate_e8):.6f} "
                   f"would exceed the live stop ${e8_to_usd(cap):.2f}")
        super().__init__(msg)

    def to_dict(self) -> dict:
        return {"reason": self.reason, "run_id": self.run_id, "spent_e8": self.spent_e8,
                "estimate_e8": self.estimate_e8, "cap": self.cap, "calls": self.calls}

    @classmethod
    def from_dict(cls, d: dict) -> "BudgetExceeded":
        return cls(d["reason"], run_id=d["run_id"], spent_e8=int(d["spent_e8"]), estimate_e8=int(d["estimate_e8"]),
                   cap=int(d["cap"]), calls=int(d.get("calls", 0)))


# ---------------------------------------------------------------------------------------------
# atomic append (shared with replay.Recorder)
# ---------------------------------------------------------------------------------------------

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.abspath(path)
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def _locked_append_fd(path: Path) -> Iterator[int]:
    """Open ``path`` for appending and hold an exclusive lock (thread lock + best-effort flock)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(path):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except OSError:
                    pass  # filesystem without flock: thread lock + O_APPEND only (documented limitation)
            yield fd
        finally:
            os.close(fd)  # also releases the flock


def _write_line(fd: int, line: str) -> None:
    if "\n" in line or "\r" in line:
        raise ValueError("a JSONL record must be a single line")
    data = (line + "\n").encode("utf-8")
    written = os.write(fd, data)  # one write() on an O_APPEND fd: the whole line lands at the end
    if written != len(data):
        raise OSError(f"short write ({written}/{len(data)} bytes); the file now ends with a partial line")
    os.fsync(fd)


def atomic_append_line(path: Path | str, line: str) -> None:
    """Append one complete line (newline added) with a single write under an exclusive lock."""
    with _locked_append_fd(Path(path)) as fd:
        _write_line(fd, line)


def _dumps(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def read_jsonl_rows(path: Path, error: type[Exception] = LedgerCorrupt) -> list[dict]:
    """Read every row; any damage raises ``error``. Lines are split on b"\\n" only (not U+2028)."""
    path = Path(path)
    if not path.exists():
        return []
    data = path.read_bytes()
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise error(f"{path.name}: last line is incomplete (no trailing newline)")
    rows = []
    for n, line in enumerate(data[:-1].split(b"\n"), 1):
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise error(f"{path.name} line {n}: not valid JSON ({exc})") from None
        if not isinstance(obj, dict):
            raise error(f"{path.name} line {n}: not a JSON object")
        rows.append(obj)
    return rows


# ---------------------------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Reservation:
    rid: str
    run_id: str
    stage: str
    attempt: int
    try_index: int
    estimate_e8: int


@dataclass(frozen=True)
class Totals:
    global_e8: int
    run_e8: int
    run_calls: int
    pending: int

    @property
    def global_usd(self) -> float:
        return e8_to_usd(self.global_e8)

    @property
    def run_usd(self) -> float:
        return e8_to_usd(self.run_e8)


@dataclass
class _Entry:
    run_id: str
    estimate_e8: int
    final_e8: int | None = None


class Ledger:
    """Append-only JSONL ledger. Rows: ``reserve`` / ``settle`` / ``unknown`` / ``refused``."""

    def __init__(self, path: Path | str = DEFAULT_LEDGER_PATH, *, caps: Caps = Caps(), pricing: Pricing = DEFAULT_PRICING,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.caps = caps
        self.pricing = pricing
        self._clock = clock

    # -- reading ------------------------------------------------------------------------------
    def _state(self) -> dict[str, _Entry]:
        state: dict[str, _Entry] = {}
        for n, row in enumerate(read_jsonl_rows(self.path), 1):
            try:
                event, rid = row["event"], row.get("rid")
                if event == "reserve":
                    if rid in state:
                        raise LedgerCorrupt(f"line {n}: duplicate reservation {rid}")
                    state[rid] = _Entry(str(row["run_id"]), _nonneg_int("estimate_e8", row["estimate_e8"]))
                elif event in ("settle", "unknown"):
                    entry = state.get(rid)
                    if entry is None or entry.final_e8 is not None:
                        raise LedgerCorrupt(f"line {n}: {event} for unknown or already settled reservation {rid}")
                    entry.final_e8 = (_nonneg_int("cost_e8", row["cost_e8"]) if event == "settle" else entry.estimate_e8)
                elif event == "refused":
                    pass  # audit only; nothing was sent
                else:
                    raise LedgerCorrupt(f"line {n}: unknown event {event!r}")
            except (KeyError, TypeError, ValueError) as exc:
                raise LedgerCorrupt(f"line {n}: malformed row ({exc!r})") from None
        return state

    @staticmethod
    def _totals(state: dict[str, _Entry], run_id: str | None) -> Totals:
        global_e8 = run_e8 = run_calls = pending = 0
        for entry in state.values():
            effective = entry.estimate_e8 if entry.final_e8 is None else entry.final_e8
            global_e8 += effective
            if entry.final_e8 is None:
                pending += 1
            if run_id is not None and entry.run_id == run_id:
                run_e8 += effective
                run_calls += 1
        return Totals(global_e8, run_e8, run_calls, pending)

    def totals(self, run_id: str | None = None) -> Totals:
        with _locked_append_fd(self.path):  # same lock as writers, so a half-written row is never read
            return self._totals(self._state(), run_id)

    # -- writing ------------------------------------------------------------------------------
    def _ts(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).isoformat(timespec="milliseconds")

    def estimate_e8(self, est_input_tokens: int, max_output_tokens: int, pricing: Pricing | None = None) -> int:
        return (pricing or self.pricing).estimate_e8(est_input_tokens, max_output_tokens)

    def reserve(self, *, run_id: str, stage: str, attempt: int, try_index: int, est_input_tokens: int,
                max_output_tokens: int, provider: str = "", model: str = "", pricing: Pricing | None = None) -> Reservation:
        """Check caps for one billable request and record a reservation. Raises ``BudgetExceeded``."""
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id is required for a reservation")
        estimate = self.estimate_e8(est_input_tokens, max_output_tokens, pricing)
        with _locked_append_fd(self.path) as fd:
            totals = self._totals(self._state(), run_id)
            refusal: BudgetExceeded | None = None
            if totals.global_e8 + estimate > self.caps.global_stop_e8:
                refusal = BudgetExceeded("global_stop", run_id=run_id, spent_e8=totals.global_e8, estimate_e8=estimate,
                                         cap=self.caps.global_stop_e8)
            elif totals.run_calls + 1 > self.caps.run_calls:
                refusal = BudgetExceeded("run_calls", run_id=run_id, spent_e8=totals.run_e8, estimate_e8=estimate,
                                         cap=self.caps.run_calls, calls=totals.run_calls)
            elif totals.run_e8 + estimate > self.caps.run_cost_e8:
                refusal = BudgetExceeded("run_cost", run_id=run_id, spent_e8=totals.run_e8, estimate_e8=estimate,
                                         cap=self.caps.run_cost_e8)
            base = {"v": LEDGER_VERSION, "ts": self._ts(), "run_id": run_id, "stage": stage, "attempt": attempt,
                    "try_index": try_index, "provider": provider, "model": model,
                    "est_input_tokens": est_input_tokens, "max_output_tokens": max_output_tokens,
                    "estimate_e8": estimate, "estimate_usd": e8_to_usd(estimate)}
            if refusal is not None:
                _write_line(fd, _dumps({**base, "event": "refused", "reason": refusal.reason}))
                raise refusal
            rid = uuid.uuid4().hex
            _write_line(fd, _dumps({**base, "event": "reserve", "rid": rid}))
        return Reservation(rid, run_id, stage, attempt, try_index, estimate)

    def settle(self, reservation: Reservation, *, input_tokens: int, cached_input_tokens: int, output_tokens: int,
               outcome: str = "ok", pricing: Pricing | None = None) -> int:
        """Replace the reservation's estimate with the actual cost (from the response usage). Returns e8."""
        cost = (pricing or self.pricing).cost_e8(input_tokens, cached_input_tokens, output_tokens)
        self._finish(reservation, {"event": "settle", "input_tokens": input_tokens,
                                   "cached_input_tokens": cached_input_tokens, "output_tokens": output_tokens,
                                   "cost_e8": cost, "cost_usd": e8_to_usd(cost), "outcome": outcome,
                                   "over_estimate": cost > reservation.estimate_e8})
        return cost

    def settle_unknown(self, reservation: Reservation, *, reason: str) -> int:
        """Actual cost unknown (timeout, 5xx, no usage block): the estimate stays charged. Returns e8."""
        self._finish(reservation, {"event": "unknown", "reason": reason, "charged_e8": reservation.estimate_e8})
        return reservation.estimate_e8

    def _finish(self, reservation: Reservation, row: dict) -> None:
        with _locked_append_fd(self.path) as fd:
            entry = self._state().get(reservation.rid)
            if entry is None:
                raise LedgerCorrupt(f"reservation {reservation.rid} is not in {self.path.name}")
            if entry.final_e8 is not None:
                raise ValueError(f"reservation {reservation.rid} is already settled")
            _write_line(fd, _dumps({"v": LEDGER_VERSION, "ts": self._ts(), "rid": reservation.rid,
                                    "run_id": reservation.run_id, **row}))
