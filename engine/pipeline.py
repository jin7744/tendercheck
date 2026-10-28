"""End-to-end run (design 09 §2-1, §3-2, §2-13): U -> E1 -> V/E2/E3/E4 -> M -> K -> C -> D18 merge -> F -> S -> report.

The model reads, selects and drafts; code verifies every quote, checks every file and decides every status.
Model calls go through a tap that counts calls, keeps the run under the call and cost caps and logs usage.
A stop (call cap, cost cap, ledger budget, billing, ledger damage), an extraction failure or a matching failure
ends the model phase; the code phase still runs on what was verified so far and the report carries the
fail-closed banner. Nothing in this module depends on a particular tender: fixtures live in tests only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from engine import report as report_mod
from engine import rules, stages, textfilter
from engine.checks import check_requirements, contains, norm08
from engine.conflicts import (apply_adjudication, conflict_info_for, cut, deadline_anchors, find_candidates,
                              merge_conflicting, tmp_id_of, usable)
from engine.filechecks import is_general_rule, run_file_checks
from engine.injection import detect_forced_verdict
from engine.ledger import DEFAULT_PRICING, BudgetExceeded, LedgerCorrupt, e8_to_usd
from engine.manifest import parse_manifest
from engine.mask import SYNTH_RE, counts_list, mask_text
from engine.model_client import BillingNotConfigured, validate
from engine.normalize import build_source_bundle, detect_lang, segment_map

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
MAX_CALLS = 9
RUN_COST_CAP_USD = 2.00
MAX_FIX_ROUNDS = 2
MAX_COVERAGE = stages.MAX_COVERAGE_SEGMENTS
NC = rules.NC
MATCH_RULES = ("S10", "S11", "S12", "S13", "S14", "S15")

# Words that usually mark a submission obligation (E4 coverage trigger). Generic tender vocabulary only.
TRIGGER_RE = re.compile(
    r"\b(?:shall|must|required?|requires|submit(?:ted|s)?|submission|deadline|include[sd]?|valid(?:ity)?|"
    r"notari[sz]ed|signed|signature|seal(?:ed)?|attach(?:ed)?|merged?|pdf|mb|files?|format|original|copy|"
    r"certified)\b|제출|하여야|해야|필수|마감|서명|날인|인감|유효|첨부|파일|이하|이내|공증|원본|사본|병합|기한",
    re.IGNORECASE)

STOP_TEXT = {
    "call_cap": {"en": "the model call limit for one run was reached", "ko": "실행당 모델 호출 상한에 도달했습니다"},
    "budget": {"en": "the cost limit was reached", "ko": "비용 상한에 도달했습니다"},
    "billing": {"en": "live mode paused: billing is not configured", "ko": "라이브 모드 중지: 결제 설정이 없습니다"},
    "ledger": {"en": "the cost ledger could not be read", "ko": "비용 원장을 읽을 수 없습니다"},
    "extract": {"en": "requirement extraction failed, so no checklist was produced",
                "ko": "요구사항 추출에 실패해 점검표를 만들지 못했습니다"},
    "match": {"en": "file matching failed; rows that need a file match are left for confirmation",
              "ko": "파일 대조에 실패해 파일 대조가 필요한 항목은 확인 필요로 두었습니다"},
}
GAP_TEXT = {
    "unexplained": {"en": "possible requirement not reviewed (no record and no reason given)",
                    "ko": "검토되지 않은 요구사항일 수 있음(추출도 사유도 없음)"},
    "over_limit": {"en": "possible requirement not reviewed (over the coverage limit)",
                   "ko": "검토되지 않은 요구사항일 수 있음(커버리지 검토 상한 초과)"},
    "recheck_failed": {"en": "re-read was requested but did not complete", "ko": "재확인을 요청했으나 완료되지 않음"},
    "ask_failed": {"en": "the items asked for in your message could not be read",
                   "ko": "메시지에서 점검 요청 항목을 읽지 못했습니다"},
}


class CallCapReached(RuntimeError):
    """The run already made MAX_CALLS model calls."""


class RunBudgetReached(RuntimeError):
    """The run's recorded spend reached RUN_COST_CAP_USD."""


STOP_ERRORS = (BudgetExceeded, BillingNotConfigured, LedgerCorrupt, CallCapReached, RunBudgetReached)


def _stop_key(exc: BaseException) -> str:
    if isinstance(exc, CallCapReached) or (isinstance(exc, BudgetExceeded) and exc.reason == "run_calls"):
        return "call_cap"
    if isinstance(exc, BillingNotConfigured):
        return "billing"
    if isinstance(exc, LedgerCorrupt):
        return "ledger"
    return "budget"


# ---------------------------------------------------------------------------------------------
# model-call tap
# ---------------------------------------------------------------------------------------------

class _Tap:
    """Wraps the client. Attribute reads/writes are forwarded (stages.stage_settings sets max_output_tokens /
    extra_body on the client), complete() is counted, capped and logged. Only counts of PII markers found in a
    request or reply are kept, never the text."""

    def __init__(self, client, *, max_calls: int = MAX_CALLS, cap_usd: float = RUN_COST_CAP_USD):
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_max_calls", max_calls)
        object.__setattr__(self, "_cap_usd", cap_usd)
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "pii_in_requests", 0)
        object.__setattr__(self, "pii_in_replies", 0)
        object.__setattr__(self, "attempted", "")

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_client"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_client"), name, value)

    def spent_usd(self) -> float:
        return round(sum(c["cost_usd"] for c in self.calls), 8)

    def complete(self, stage, attempt, messages, schema_name):
        object.__setattr__(self, "attempted", stage)
        if len(self.calls) >= self._max_calls:
            raise CallCapReached(f"run already made {len(self.calls)} model calls (cap {self._max_calls})")
        if self.spent_usd() >= self._cap_usd:
            raise RunBudgetReached(f"run spent ${self.spent_usd():.6f} (cap ${self._cap_usd:.2f})")
        sent = json.dumps(messages, ensure_ascii=False)
        object.__setattr__(self, "pii_in_requests", self.pii_in_requests + len(SYNTH_RE.findall(sent)))
        entry = {"stage": stage, "attempt": attempt, "schema_name": schema_name, "model_resolved": "",
                 "tokens_in": 0, "tokens_cached": 0, "tokens_out": 0, "tokens_reasoning": 0,
                 "usage_reported": False, "cost_usd": 0.0, "latency_ms": 0, "error": None, "returned": False}
        try:
            result = self._client.complete(stage, attempt, messages, schema_name)
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            self.calls.append(entry)
            raise
        u = result.usage
        pricing = getattr(self._client, "pricing", None) or DEFAULT_PRICING
        cost = pricing.cost_e8(u.input_tokens, u.cached_input_tokens, u.output_tokens)
        reply = json.dumps(result.raw, ensure_ascii=False) if result.raw is not None else ""
        object.__setattr__(self, "pii_in_replies", self.pii_in_replies + len(SYNTH_RE.findall(reply)))
        entry.update(model_resolved=result.model_resolved, tokens_in=u.input_tokens,
                     tokens_cached=u.cached_input_tokens, tokens_out=u.output_tokens,
                     tokens_reasoning=u.reasoning_tokens, usage_reported=u.reported, cost_usd=e8_to_usd(cost),
                     latency_ms=result.latency_ms, error=result.error, returned=True)
        self.calls.append(entry)
        return result


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _bundle_id(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", run_id or "")[:40] or "bundle"


def _manifest_lang(text: str) -> str:
    return "en" if detect_lang(text or "") == "en" else "ko"


def _term_in_source(term: str, texts: list[str]) -> bool:
    """Search term found in the source: whole-word for Latin/digit terms, spacing-insensitive otherwise."""
    t = norm08(term).casefold()
    if not t:
        return False
    if re.fullmatch(r"[\x00-\x7f]+", t):
        rx = re.compile(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])")
        return any(rx.search(norm08(x).casefold()) for x in texts)
    return any(contains(x, term) for x in texts)


def _block_of(bundle: dict) -> dict:
    out = {}
    for b in bundle["blocks"]:
        for s in b["seg_ids"]:
            out[s] = b["seg_ids"]
    return out


PERIOD_RULES = ("valid_for_from_issue", "issued_within_before_anchor", "valid_at_least_after_anchor")
MAX_MERGED_QUOTE = 300


def _has_period(v: dict) -> bool:
    return v.get("rule_type") in PERIOD_RULES and (int(v.get("days") or 0) > 0 or int(v.get("months") or 0) > 0)


def _anchor_only(v: dict) -> bool:
    return (v.get("anchor_event", "not_stated") != "not_stated" and not int(v.get("days") or 0)
            and not int(v.get("months") or 0) and not v.get("until_iso"))


def merge_split_validity(vreqs: list[dict], segs: dict) -> tuple[list[dict], list[dict]]:
    """One validity obligation is sometimes extracted as two records in the same source line: the period
    ("valid for N days from issue") and the moment it must hold ("valid on the submission deadline").
    Rejoin them into one record whose quote is the contiguous source span covering both, so F7 sees the
    period and the anchor together. Returns (reqs, merges); never invents text, never crosses lines."""
    reqs = [v["req"] for v in vreqs]
    merges, used = [], set()
    for i, va in enumerate(vreqs):
        a, ca = va["req"], va["check"]
        if i in used or not (ca["verified"] and ca["grounded"]) or not _has_period(a.get("validity") or {}):
            continue
        if (a.get("validity") or {}).get("anchor_event", "not_stated") != "not_stated":
            continue
        for j, vb in enumerate(vreqs):
            b, cb = vb["req"], vb["check"]
            if j == i or j in used or not (cb["verified"] and cb["grounded"]):
                continue
            if not _anchor_only(b.get("validity") or {}) or cb["seg_id"] != ca["seg_id"]:
                continue
            if (a.get("attribute_of") or b.get("attribute_of")) and a.get("attribute_of") != b.get("attribute_of") \
                    and a.get("attribute_of") and b.get("attribute_of"):
                continue
            text = segs[ca["seg_id"]]["text"]
            sa, sb = text.find(ca["quote"]), text.find(cb["quote"])
            if sa < 0 or sb < 0:
                continue
            start, end = min(sa, sb), max(sa + len(ca["quote"]), sb + len(cb["quote"]))
            if end - start > MAX_MERGED_QUOTE:
                continue
            validity = {**a["validity"], "anchor_event": b["validity"]["anchor_event"]}
            merged = {**a, "quote": text[start:end], "validity": validity,
                      "attribute_of": a.get("attribute_of") or b.get("attribute_of", "")}
            reqs[i] = merged
            used |= {i, j}
            merges.append({"kept": a.get("tmp_id", ""), "absorbed": b.get("tmp_id", ""), "seg_id": ca["seg_id"]})
            break
    absorbed = {m["absorbed"]: m["kept"] for m in merges}
    out = []
    for k, r in enumerate(reqs):
        if r.get("tmp_id") in absorbed:
            continue
        if r.get("attribute_of") in absorbed:
            r = {**r, "attribute_of": absorbed[r["attribute_of"]]}
        out.append(r)
    return out, merges


# Words that name a consortium, most specific first: the first one found in the quote becomes the condition quote
# ("공동수급 … 대표사" quotes "공동수급"). English terms match as whole words, case-insensitive except "JV".
CONSORTIUM_TERMS = ("joint venture", "consortium", "JV", "lead member", "lead partner",
                    "공동수급", "공동이행", "공동도급", "컨소시엄", "대표사", "구성원")


def _consortium_word(quote: str) -> str:
    for term in CONSORTIUM_TERMS:
        if term.isascii():
            m = re.search(r"\b" + re.escape(term) + r"\b", quote, 0 if term == "JV" else re.IGNORECASE)
            if m:
                return m.group(0)
        elif term in quote:
            return term
    return ""


def add_member_rule_conditions(vreqs: list[dict]) -> tuple[list[dict], list[dict]]:
    """A member rule (member count, lead share) binds only a consortium. When the model gave such a record no
    condition, add consortium = yes, quoting the consortium word found in the record's own quote (source case kept),
    so a single bidder gets "applies only to a joint venture" instead of met or a missing value. Records without a
    member rule or such a word, already conditional, or not verified and grounded stay as they are.
    Returns (reqs, log)."""
    out, log = [], []
    for v in vreqs:
        r, c = v["req"], v["check"]
        mr = r.get("member_rule") or {}
        has_rule = int(mr.get("max_members") or 0) > 0 or float(mr.get("lead_share_min_pct") or 0) > 0
        word = _consortium_word(r.get("quote", "")) if has_rule else ""
        if not (c.get("verified") and c.get("grounded")) or r.get("conditions") \
                or r.get("mandatory") == "conditional" or not word:
            out.append(r)
            continue
        out.append({**r, "mandatory": "conditional",
                    "conditions": [{"condition_quote": word, "fact_key": "consortium", "applies_when": "yes"}]})
        log.append({"tmp_id": r.get("tmp_id", ""), "condition_quote": word})
    return out, log


def _seal_kind(value: str) -> str:
    v = (value or "").casefold()
    if "사용인감" in v or "usage" in v or "authorized" in v or "authorised" in v:
        return "usage"
    if "법인인감" in v or "corporate" in v or "company seal" in v:
        return "corporate"
    if "인감" in v or "seal" in v:
        return "unspecified"
    return ""


def apply_attribute_findings(manifest: dict, match: dict | None) -> dict:
    """Copy of the manifest with verified model findings added to row["parsed"] where code read nothing
    (09 §2-6: a note_quote must be in that row's notes; code-read "key: value" facts win)."""
    out = copy.deepcopy(manifest)
    if not match:
        return out
    rows = {r["row_id"]: r for r in out.get("rows") or []}
    for f in match.get("attribute_findings") or []:
        row = rows.get(f.get("row_id", ""))
        if row is None or not f.get("verified"):
            continue
        parsed, attr, value = row.setdefault("parsed", {}), f.get("attribute", ""), " ".join(str(f.get("value", "")).split())
        if not value:
            continue
        if attr in ("signed_by", "member") and attr not in parsed:
            parsed[attr] = value
        elif attr in ("headcount", "signature_count") and attr not in parsed:
            m = re.fullmatch(r"\D*(\d{1,4})\D*", value)
            if m:
                parsed[attr] = int(m.group(1))
        elif attr == "seal_kind" and not parsed.get("seal_kinds"):
            kind = _seal_kind(value)
            if kind:
                parsed["seal_kinds"] = [kind]
    return out


# ---------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------

class _Run:
    def __init__(self, bundle, manifest, *, lang, as_of, tap, user_message):
        self.bundle, self.manifest, self.lang, self.as_of, self.tap = bundle, manifest, lang, as_of, tap
        self.user_message = user_message
        self.segs = segment_map(bundle)
        self.reqs: list[dict] = []
        self.vreqs: list[dict] = []
        self.uncertain: list[dict] = []
        self.not_requirements: list[dict] = []
        self.model_suspects: list[dict] = []
        self.match: dict | None = None
        self.match_error: str | None = None
        self.c_parsed: dict | None = None
        self.c_called = False
        self.stop: dict | None = None
        self.extract_failed: str | None = None
        self.not_in_source: list[str] = []
        self.asked: list[dict] = []
        self.gaps: list[str] = []
        self.stage_log: list[dict] = []
        self.removed: list[dict] = []
        self.stats = {"requoted": 0, "retracted": 0, "fix_rounds": 0, "recheck_requested": 0, "recheck_new": 0,
                      "coverage_sent": 0, "coverage_unexplained": 0, "trigger_segments": 0, "explained": 0,
                      "cited_triggers": 0, "ever_extracted": set()}

    # ----------------------------------------------------------------- bookkeeping
    def _log(self, env: dict) -> None:
        self.stage_log.append({"stage": env["stage"], "attempt": env["attempt"], "called": env["called"],
                               "prompt_version": env["prompt_version"], "error": env["error"],
                               "dropped": list(env["dropped"]), "notes": list(env["notes"])})

    def _gap(self, seg_id: str, key: str) -> None:
        seg = self.segs.get(seg_id)
        where = seg["location"] if seg else seg_id
        text = f"{where} [{seg_id}]: {GAP_TEXT[key][self.lang]}"
        if text not in self.gaps:
            self.gaps.append(text)

    def _add_reqs(self, reqs: list[dict]) -> None:
        for r in reqs:
            self.reqs.append(copy.deepcopy(r))
            self.stats["ever_extracted"].add(r.get("tmp_id", ""))

    def _take_lists(self, parsed: dict) -> None:
        self.model_suspects += parsed.get("injection_suspects") or []
        self.not_requirements += parsed.get("not_requirements") or []

    def recheck(self) -> None:
        res = check_requirements(self.reqs, self.bundle)
        self.vreqs = [{"req": r, "check": c} for r, c in zip(self.reqs, res["results"])]

    def kept(self) -> list[dict]:
        return [v for v in self.vreqs if not v["check"]["remove"]]

    def failing(self) -> list[dict]:
        return [{"tmp_id": v["req"].get("tmp_id", ""), "failures": list(v["check"]["failures"]), "req": v["req"]}
                for v in self.kept() if not (v["check"]["verified"] and v["check"]["grounded"])]

    # ----------------------------------------------------------------- model stages
    def ask(self) -> None:
        masked = mask_text(unicodedata.normalize("NFC", self.user_message)).text
        if not masked.strip():
            return
        env = stages.run_ask(self.tap, masked, lang=self.lang)
        self._log(env)
        if env["error"]:
            self.gaps.append(GAP_TEXT["ask_failed"][self.lang])
            return
        texts = [d["text"] for d in self.bundle["docs"]]
        for item in env.get("asked_items") or []:
            name = mask_text(item["item"]).text
            hits = [t for t in item["search_terms"] if _term_in_source(t, texts)]
            segs = [s["seg_id"] for s in self.bundle["segments"] if any(_term_in_source(t, [s["text"]]) for t in hits)]
            self.asked.append({"item": name, "found": bool(hits), "seg_ids": segs[:10]})
            if not hits and name not in self.not_in_source:
                self.not_in_source.append(name)

    def extract(self) -> None:
        env = stages.run_extract(self.tap, self.bundle, lang=self.lang, attempt=0)
        self._log(env)
        if env["error"] or env["parsed"] is None:
            self.extract_failed = env["error"] or "no reply"
            return
        self._add_reqs(env["parsed"]["requirements"])
        self.uncertain = list(env["parsed"].get("uncertain_segments") or [])
        self._take_lists(env["parsed"])
        self.recheck()

    def _apply_fixes(self, fixes: list[dict]) -> None:
        by_id = {r.get("tmp_id"): i for i, r in enumerate(self.reqs)}
        drop = set()
        for fx in fixes:
            i = by_id.get(fx.get("tmp_id"))
            if i is None:
                continue
            if fx["action"] == "retract":
                drop.add(i)
                self.stats["retracted"] += 1
                self.removed.append({"tmp_id": fx["tmp_id"], "why": "retracted by the model (E2)"})
            else:
                self.reqs[i] = {**self.reqs[i], "quote": fx["quote"], "location": dict(fx["location"])}
                self.stats["requoted"] += 1
        self.reqs = [r for i, r in enumerate(self.reqs) if i not in drop]

    def _cited(self) -> set[str]:
        blocks = _block_of(self.bundle)
        cited = set()
        for v in self.vreqs:
            chk, req = v["check"], v["req"]
            ids = chk.get("seg_ids") or [(req.get("location") or {}).get("seg_id", "")]
            cited |= set(ids)
            for c in req.get("conditions") or []:
                for sid in blocks.get(chk.get("seg_id", ""), []):
                    if c.get("condition_quote") and contains(self.segs[sid]["text"], c["condition_quote"]):
                        cited.add(sid)
                        break
        return cited

    def coverage_targets(self) -> list[str]:
        trig = [s["seg_id"] for s in self.bundle["segments"] if TRIGGER_RE.search(s["text"])]
        cited = self._cited()
        explained = {s["seg_id"] for s in self.not_requirements} | {s["seg_id"] for s in self.uncertain} \
            | {s["seg_id"] for s in self.bundle.get("injection_suspects") or []}
        self.stats["trigger_segments"] = len(trig)
        self.stats["cited_triggers"] = len([s for s in trig if s in cited])
        self.stats["explained"] = len([s for s in trig if s not in cited and s in explained])
        return [s for s in trig if s not in cited and s not in explained]

    def verify_loop(self) -> None:
        e3_done = e4_done = False
        while True:
            failing = self.failing()
            if failing and self.stats["fix_rounds"] < MAX_FIX_ROUNDS:
                env = stages.run_fix(self.tap, self.bundle, failing, self.stats["fix_rounds"])
                self.stats["fix_rounds"] += 1
                self._log(env)
                if env["parsed"] is not None:
                    self._apply_fixes(env["parsed"]["fixes"])
                    self.recheck()
                continue
            if not e3_done:
                e3_done = True
                if self.uncertain:
                    env = stages.run_recheck(self.tap, self.bundle, self.uncertain, 0, existing_reqs=self.reqs)
                    self._log(env)
                    self.stats["recheck_requested"] = len(self.uncertain)
                    if env["parsed"] is None:
                        for u in self.uncertain:
                            self._gap(u["seg_id"], "recheck_failed")
                    else:
                        self._add_reqs(env["parsed"]["requirements"])
                        self.stats["recheck_new"] = len(env["parsed"]["requirements"])
                        self._take_lists(env["parsed"])
                        self.recheck()
                    continue
            if not e4_done:
                e4_done = True
                targets = self.coverage_targets()
                for sid in targets[MAX_COVERAGE:]:
                    self._gap(sid, "over_limit")
                targets = targets[:MAX_COVERAGE]
                if targets:
                    self.stats["coverage_sent"] = len(targets)
                    env = stages.run_coverage(self.tap, self.bundle, targets, existing_reqs=self.reqs)
                    self._log(env)
                    if env["parsed"] is not None:
                        self._add_reqs(env["parsed"]["new_requirements"])
                        self.recheck()
                    unexplained = env.get("unexplained_seg_ids") or []
                    self.stats["coverage_unexplained"] = len(unexplained)
                    for sid in unexplained:
                        self._gap(sid, "unexplained")
                    continue
            break

    def match_files(self) -> None:
        env = stages.run_match(self.tap, self.kept(), self.manifest, lang=self.lang)
        self._log(env)
        if env["error"] or env["parsed"] is None:
            self.match_error = env["error"] or "no reply"
            self.match = None
        else:
            self.match = env["parsed"]

    def adjudicate(self) -> None:
        kept = self.kept()
        candidates = find_candidates(kept)
        if not candidates and sum(1 for v in kept if usable(v)) < 2:
            return
        self.c_called = True
        env = stages.run_conflict(self.tap, candidates, kept, self.bundle, attempt=0)
        self._log(env)
        self.c_parsed = env["parsed"]

    def model_phase(self) -> None:
        try:
            if self.user_message.strip():
                self.ask()
            self.extract()
            if self.extract_failed:
                return
            self.verify_loop()
            self.reqs, merges = merge_split_validity(self.vreqs, self.segs)
            if merges:
                self.stats["validity_merges"] = merges
                for m in merges:
                    self.removed.append({"tmp_id": m["absorbed"], "why": f"joined into {m['kept']} (split validity)"})
                self.recheck()
            self.match_files()
            self.adjudicate()
            # After the last model stage: the condition only feeds S5/S6, and M/C requests stay as recorded.
            self.reqs, added = add_member_rule_conditions(self.vreqs)
            if added:
                self.stats["member_rule_conditions"] = added
                self.recheck()
        except STOP_ERRORS as exc:
            self.stop = {"key": _stop_key(exc), "class": type(exc).__name__, "stage": self.tap.attempted}

    # ----------------------------------------------------------------- code phase
    def untrusted(self) -> list[dict]:
        out = [{"quote": s["quote"], "location": s["location"], "seg_id": s["seg_id"]}
               for s in self.bundle.get("injection_suspects") or []]
        coded = {s["seg_id"] for s in out}
        for s in self.model_suspects:
            seg = self.segs.get(s.get("seg_id", ""))
            if seg is None or seg["seg_id"] in coded:
                continue
            span = cut(seg["text"], s.get("quote", ""))
            if span.strip():
                out.append({"quote": span.strip(), "location": seg["location"], "seg_id": seg["seg_id"]})
                coded.add(seg["seg_id"])
        return out

    def decide_all(self):
        kept = self.kept()
        for v in self.vreqs:
            if v["check"]["remove"]:
                why = "merged duplicate (Q7)" if v["check"].get("merged_into") else "instruction-like text (Q8)"
                self.removed.append({"tmp_id": v["req"].get("tmp_id", ""), "why": why,
                                     "flags": list(v["check"].get("flags") or [])})
        candidates = find_candidates(kept)
        adj = apply_adjudication(candidates, self.c_parsed if self.c_called else None, self.bundle, vreqs=kept)
        conflicts = adj["conflicts"]
        merged = merge_conflicting(kept, conflicts)
        anchors, _ = deadline_anchors(merged, conflicts)
        manifest = apply_attribute_findings(self.manifest, self.match)
        rows_by_id = {r["row_id"]: r for r in manifest.get("rows") or []}
        by_tmp = {tmp_id_of(v): v for v in merged}
        by_req = (self.match or {}).get("by_req") or {}
        profile = manifest.get("profile") or {}
        done: dict[str, dict] = {}
        visiting: set[str] = set()

        def rows_for(v):
            tmp = tmp_id_of(v)
            ids = list((by_req.get(tmp) or {}).get("row_ids") or [])
            parent = v["req"].get("attribute_of")
            if not ids and parent:
                ids = list((by_req.get(parent) or {}).get("row_ids") or [])
            return [rows_by_id[i] for i in ids if i in rows_by_id]

        def decide_one(v):
            tmp = tmp_id_of(v)
            if tmp in done:
                return done[tmp]
            visiting.add(tmp)
            parent_id = v["req"].get("attribute_of") or ""
            parent = None
            if parent_id in by_tmp and parent_id != tmp and parent_id not in visiting:
                parent = decide_one(by_tmp[parent_id])["decision"]
            common = {"match": self.match, "conflict_info": conflict_info_for(v, conflicts), "profile": profile,
                      "as_of": self.as_of, "parent": parent}
            pre = rules.decide(v, file_check=None, **common)
            fc = None
            if pre["rule"] in ("S5", "S6"):
                dec = pre
            else:
                fc = run_file_checks(v, rows_for(v), manifest, as_of=self.as_of, anchors=anchors)
                dec = rules.decide(v, file_check=fc, **common)
            if self.match is None and dec["rule"] in MATCH_RULES and not is_general_rule(v["req"]):
                dec = {**dec, "status": NC, "reason_codes": ["ATTRIBUTE_UNKNOWN:evidence"], "rule": "S14",
                       "info_codes": list(dec.get("info_codes") or []) + [f"DEMOTED_NO_MATCH:{dec['rule']}"],
                       "bidder_question_keys": [], "evidence_row_ids": [], "depends_on_conflict": None}
            item = {"vreq": v, "decision": dec, "file_check": fc, "match": by_req.get(tmp)}
            visiting.discard(tmp)
            done[tmp] = item
            return item

        items = [decide_one(v) for v in merged]
        return items, conflicts, adj

    def x_meta(self, adj: dict, items: list[dict]) -> dict:
        kept = self.kept()
        ever = self.stats["ever_extracted"]
        resolved = sorted({c["model_resolved"] for c in self.tap.calls if c["model_resolved"]})
        downgrades = sum(1 for it in items if "MODEL_FLAGGED" in it["decision"]["reason_codes"])
        return {
            "as_of": self.as_of,
            "inputs_sha256": {"source": self.bundle.get("source_sha256", ""), "manifest": self.manifest_sha,
                              "user_message": _sha(self.user_message) if self.user_message else ""},
            "model": {"resolved": resolved},
            "verification_log": {
                "calls": [{k: c[k] for k in ("stage", "attempt", "model_resolved", "tokens_in", "tokens_cached",
                                             "tokens_out", "tokens_reasoning", "cost_usd", "latency_ms", "error")}
                          for c in self.tap.calls],
                "quote_checks": {"total": len(ever), "passed": sum(1 for v in kept if v["check"]["verified"]),
                                 "requoted": self.stats["requoted"], "retracted": self.stats["retracted"],
                                 "unverified": sum(1 for v in kept if not v["check"]["verified"]),
                                 "ungrounded": sum(1 for v in kept if v["check"]["verified"]
                                                   and not v["check"]["grounded"]),
                                 "fix_rounds": self.stats["fix_rounds"]},
                "rechecks": {"requested": self.stats["recheck_requested"], "resolved": self.stats["recheck_new"]},
                "coverage": {"trigger_segments": self.stats["trigger_segments"],
                             "cited": self.stats["cited_triggers"], "explained": self.stats["explained"],
                             "sent": self.stats["coverage_sent"], "unexplained": self.stats["coverage_unexplained"]},
                "status_hint_downgrades": downgrades,
                "validity_merges": self.stats.get("validity_merges", []),
                "member_rule_conditions": self.stats.get("member_rule_conditions", []),
                "stages": self.stage_log,
            },
            "cost_total_usd": self.tap.spent_usd(),
            "info_gaps": list(self.gaps),
            "asked_items": self.asked,
            "removed_records": self.removed,
            "adjudication_log": adj.get("log", []),
            "stop": self.stop,
            "match_error": self.match_error,
            "extract_error": self.extract_failed,
        }


# ---------------------------------------------------------------------------------------------
# invariants I1-I10 (design 09 §6-4)
# ---------------------------------------------------------------------------------------------

def _load_contract(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _walk_status_fields(schema, path="$"):
    bad = []
    if isinstance(schema, dict):
        for name, sub in (schema.get("properties") or {}).items():
            if name == "status":
                bad.append(f"{path}.{name}")
            if name == "status_hint" and set(sub.get("enum") or []) - {"no_concern", "needs-confirmation"}:
                bad.append(f"{path}.{name} enum")
            bad += _walk_status_fields(sub, f"{path}.{name}")
        if isinstance(schema.get("items"), dict):
            bad += _walk_status_fields(schema["items"], f"{path}[]")
    return bad


def check_invariants(report: dict, grading: dict, bundle: dict, tap_calls: list[dict],
                     pii_in_requests: int = 0, pii_in_replies: int = 0) -> dict:
    lang = (report.get("x_meta") or {}).get("output_lang", "en")
    out = []

    def put(iid, ok, detail=""):
        out.append({"id": iid, "ok": bool(ok), "detail": detail})

    errors = validate(grading, _load_contract("report_grading.schema.json")["schema"])
    put("I1", not errors, "; ".join(errors[:5]))

    docs = [norm08(d["text"]) for d in bundle.get("docs") or []]
    bad = [r["id"] for r in report["requirements"] if r["status"] == "met"
           and not any(norm08(r["quote"]) and norm08(r["quote"]) in d for d in docs)]
    put("I2", not bad, ", ".join(bad))

    segs = segment_map(bundle) if bundle.get("segments") else {}
    meta_rows = {m["id"]: m for m in (report.get("x_meta") or {}).get("rows") or []}
    bad = [r["id"] for r in report["requirements"]
           if (segs.get(meta_rows.get(r["id"], {}).get("seg_id", "")) or {}).get("location") != r["location"]]
    put("I3", not bad, ", ".join(bad))

    put("I4", report["basis"] == report_mod.fixed("basis", lang)
        and report["disclaimer"] == report_mod.fixed("disclaimer", lang))

    fields = [("basis", report["basis"]), ("disclaimer", report["disclaimer"])] + textfilter.generated_fields(report)
    banner = (report.get("x_meta") or {}).get("banner", "")
    if banner:
        fields.append(("x_meta.banner", banner))
    hits = [f"{f}:{h['id']}" for f, t in fields for h in textfilter.scan(t)["blocked"] if h["id"].startswith("B")]
    put("I5", not hits, ", ".join(hits))

    bad = [c["id"] for c in report["conflicts"] if len(c["sides"]) != 2 or not c["agency_question"].strip()]
    put("I6", not bad, ", ".join(bad))

    counts = {s: 0 for s in report_mod.STATUSES}
    for r in report["requirements"]:
        counts[r["status"]] += 1
    put("I7", counts == report["status_counts"])

    bad = []
    for p in sorted(CONTRACTS.glob("model_*.schema.json")):
        bad += [f"{p.name}:{b}" for b in _walk_status_fields(json.loads(p.read_text(encoding="utf-8"))["schema"])]
    put("I8", not bad, ", ".join(bad))

    found = len(SYNTH_RE.findall(json.dumps(report, ensure_ascii=False))) \
        + len(SYNTH_RE.findall(json.dumps(grading, ensure_ascii=False)))
    put("I9", found + pii_in_requests + pii_in_replies == 0,
        f"report/grading {found}, requests {pii_in_requests}, replies {pii_in_replies}")

    bad = [f"{c['stage']}#{c['attempt']}" for c in tap_calls if c.get("returned")
           and not (isinstance(c.get("cost_usd"), float) and isinstance(c.get("tokens_in"), int))]
    put("I10", not bad, ", ".join(bad))
    return {"ok": all(c["ok"] for c in out), "checks": out}


# ---------------------------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------------------------

def run(bundle_docs: list[dict], manifest_text: str, *, lang: str, as_of: str, run_id: str, client,
        user_message: str = "", source_lang: str | None = None) -> dict:
    """One analysis run. bundle_docs = [{doc_id, title, text, header?, doc_type?}] (normalize.build_source_bundle).
    Returns {"report", "grading", "text", "calls", "invariants"}; never raises on a stop condition."""
    bundle = build_source_bundle([dict(d) for d in bundle_docs], bundle_id=_bundle_id(run_id), output_lang=lang,
                                 as_of=as_of, source_lang=source_lang)
    manifest = parse_manifest(manifest_text or "", lang=_manifest_lang(manifest_text))
    tap = _Tap(client)
    user_message = unicodedata.normalize("NFC", user_message or "")
    masked_msg = mask_text(user_message)
    flags = {"forced_verdict_request_ignored": bool(detect_forced_verdict(masked_msg.text)),
             "pii_masked": counts_list(masked_msg.counts)}

    r = _Run(bundle, manifest, lang=lang, as_of=as_of, tap=tap, user_message=user_message)
    r.manifest_sha = _sha(manifest_text or "")
    if not rules.source_missing(bundle):
        r.model_phase()

    items, conflicts, adj = r.decide_all() if r.vreqs else ([], [], {"log": []})
    reason_key = (r.stop or {}).get("key") or ("extract" if r.extract_failed else "match" if r.match_error else "")
    if reason_key:
        flags["incomplete_reason"] = STOP_TEXT[reason_key][lang]
    x_meta = r.x_meta(adj, items)
    report = report_mod.build_report(run_id, items, conflicts, lang=lang, manifest=manifest, bundle=bundle,
                                     untrusted=r.untrusted(), not_in_source=r.not_in_source, flags=flags,
                                     x_meta=x_meta)
    grading = report_mod.grading_view(report)
    invariants = check_invariants(report, grading, bundle, tap.calls, tap.pii_in_requests, tap.pii_in_replies)
    report["x_meta"]["invariants"] = invariants
    return {"report": report, "grading": grading, "text": report_mod.render_text(report, lang),
            "calls": list(tap.calls), "invariants": invariants}


__all__ = ["CallCapReached", "MAX_CALLS", "RUN_COST_CAP_USD", "RunBudgetReached", "STOP_ERRORS", "TRIGGER_RE",
           "apply_attribute_findings", "check_invariants", "run"]
