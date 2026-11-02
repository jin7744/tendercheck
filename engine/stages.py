"""Model-facing stages U, E1-E4, M, C (design 09 §2-2, §2-4, §2-6, §2-8, §3; interfaces §3, §8).

Every stage renders one versioned prompt from engine/prompts/, sends ONE user message through
client.complete(stage, attempt, messages, schema_name) and post-validates the reply against its inputs.
Message layout: data blocks first, instructions last. The SOURCE block is the first bytes of the message and is
byte-identical for E1, E2, E3, E4 and C, so the provider prefix cache can hit.

Isolation (09 §2-11 L5): U gets only the (re-)masked user message; E1 never gets the manifest; M gets verified
requirements, masked rows and profile facts, never the source text.

Errors: output problems (ModelResult.error) and model-layer exceptions come back as {"parsed": None, "error": ...}.
Budget and billing errors (PROPAGATE_ERRORS) are re-raised so the pipeline stops the run.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from engine.checks import TMP_ID_RE, contains, norm08
from engine.ledger import BudgetExceeded, LedgerCorrupt
from engine.mask import mask_text
from engine.model_client import MODEL_LAYER_ERRORS, BillingNotConfigured, Usage
from engine.normalize import canonical_id, segment_map

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Per-stage tunables. max_output_tokens includes reasoning tokens and sizes the ledger reservation
# (input bytes x $1.25/1M + max_output_tokens x $4.25/1M). Starting values; adjust after the live run.
STAGE_CONFIG = {
    "U": {"reasoning_effort": "minimal", "max_output_tokens": 4096},
    "E1": {"reasoning_effort": "medium", "max_output_tokens": 32768},
    "E2": {"reasoning_effort": "low", "max_output_tokens": 8192},
    "E3": {"reasoning_effort": "low", "max_output_tokens": 16384},
    "E4": {"reasoning_effort": "low", "max_output_tokens": 16384},
    "M": {"reasoning_effort": "low", "max_output_tokens": 16384},
    "C": {"reasoning_effort": "low", "max_output_tokens": 8192},
}
# reasoning_effort is sent (via extra_body) only to providers known to accept it on Chat (smoke 2026-10-27).
REASONING_EFFORT_PROVIDERS = frozenset({"meta"})
PROPAGATE_ERRORS = (BudgetExceeded, BillingNotConfigured, LedgerCorrupt)

STAGES = {
    "U": ("u_ask.md", "x_ask"),
    "E1": ("e1_extract.md", "x_extract"),
    "E2": ("e2_fix.md", "x_fix"),
    "E3": ("e3_recheck.md", "x_extract"),
    "E4": ("e4_coverage.md", "x_coverage"),
    "M": ("m_match.md", "x_match"),
    "C": ("c_conflict.md", "x_conflict"),
}
LANG_NAMES = {"en": "English", "ko": "Korean"}

MAX_ASK_ITEMS = 10
MAX_SEARCH_TERMS = 5
MAX_USER_MESSAGE_CHARS = 4000
MAX_UNCERTAIN = 5
MAX_RECHECK_SEGMENTS = 5
MAX_COVERAGE_SEGMENTS = 30

_HEADER_RE = re.compile(r"version: ([a-z0-9_]+/\d+\.\d+\.\d+)\n---\n")
_INCLUDE_RE = re.compile(r"\{\{include:([a-z0-9_]+\.md)\}\}")
_VAR_RE = re.compile(r"\{\{([A-Z_]+)\}\}")


# ---------------------------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _read_prompt_file(name: str) -> tuple[str, str]:
    raw = (PROMPTS_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    m = _HEADER_RE.match(raw)
    if not m:
        raise ValueError(f"prompt {name}: first lines must be 'version: <name>/<x.y.z>' and '---'")
    return m.group(1), raw[m.end():].strip()


def prompt_template(stage: str) -> tuple[str, str]:
    """(version, template text with includes resolved). Version lists included fragments, e.g. 'a/1.0.0+b/1.0.0'."""
    version, body = _read_prompt_file(STAGES[stage][0])
    versions = [version]

    def include(m: re.Match) -> str:
        inc_version, text = _read_prompt_file(m.group(1))
        versions.append(inc_version)
        return text

    body = _INCLUDE_RE.sub(include, body)
    return "+".join(versions), body


def prompt_version(stage: str) -> str:
    return prompt_template(stage)[0]


def prompt_sha256(stage: str) -> str:
    version, body = prompt_template(stage)
    return hashlib.sha256(f"{version}\n{body}".encode("utf-8")).hexdigest()


def render_prompt(stage: str, **values: str) -> str:
    """Fill {{NAME}} placeholders (str.replace semantics); a placeholder without a value raises KeyError."""
    _, body = prompt_template(stage)

    def sub(m: re.Match) -> str:
        if m.group(1) not in values:
            raise KeyError(f"prompt {stage}: no value for {{{{{m.group(1)}}}}}")
        return str(values[m.group(1)])

    return _VAR_RE.sub(sub, body)


def _lang_name(lang: str) -> str:
    if lang not in LANG_NAMES:
        raise ValueError(f"unsupported output language {lang!r}")
    return LANG_NAMES[lang]


# ---------------------------------------------------------------------------------------------
# message building
# ---------------------------------------------------------------------------------------------

def _jl(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _block(name: str, lines: list[str]) -> str:
    """Every content line starts with '[', '{' or '"', so no line can equal the closing marker."""
    return f"<<<{name}\n" + ("\n".join(lines) if lines else "(none)") + f"\n{name}>>>"


def source_block(bundle: dict) -> str:
    """Whole bundle as `[seg_id] text` lines (engine.normalize.render_segments format), a blank line between
    blocks, a `# DOCUMENT` line per document. Depends on the bundle only (prefix-cache key)."""
    by_doc: dict[str, list[dict]] = {}
    for s in bundle["segments"]:
        by_doc.setdefault(s["doc_id"], []).append(s)
    lines = ["<<<SOURCE"]
    for i, doc in enumerate(bundle["docs"]):
        if i:
            lines.append("")
        lines.append(f"# DOCUMENT {doc['doc_id']} title={_jl(doc['title'])}")
        prev = None
        for s in by_doc.get(doc["doc_id"], []):
            if prev is not None and s["block"] != prev:
                lines.append("")
            lines.append(f"[{s['seg_id']}] {s['text'].strip()}")
            prev = s["block"]
    lines.append("SOURCE>>>")
    return "\n".join(lines)


def _compose(blocks: list[str], instructions: str) -> str:
    return "\n\n".join([*blocks, instructions])


def _is_empty(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return v == 0
    return v in ("", "none", "not_stated") or v == [] or v == {}


def _compact(value):
    """Drop default values ("", 0, [], "none", "not_stated") to keep task blocks short."""
    if isinstance(value, dict):
        out = {k: _compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if not _is_empty(v)}
    if isinstance(value, list):
        return [c for c in (_compact(v) for v in value) if not _is_empty(c)]
    return value


def _mask(value):
    return mask_text(value).text if isinstance(value, str) else value


# ---------------------------------------------------------------------------------------------
# calling
# ---------------------------------------------------------------------------------------------

# A package far longer than the synthetic ones (a real tender: about 31,000 characters against 3,600) needs room to
# answer; B-M2's E1 reply alone was 17,263 output tokens. Content above LONG_INPUT_CHARS gets LONG_INPUT_FACTOR x
# each stage's max_output_tokens. The request hash does not include the budget, so recorded calls replay unchanged.
LONG_INPUT_CHARS = 20000
LONG_INPUT_FACTOR = 2


@contextmanager
def stage_settings(client, stage: str, *, long_input: bool = False):
    """Apply STAGE_CONFIG to the client for one call and restore it afterwards. client.complete() has no
    per-call knobs, so this sets the public attributes max_output_tokens and extra_body["reasoning_effort"]."""
    cfg = STAGE_CONFIG[stage]
    saved = {}
    try:
        if isinstance(getattr(client, "max_output_tokens", None), int):
            saved["max_output_tokens"] = client.max_output_tokens
            client.max_output_tokens = int(cfg["max_output_tokens"]) * (LONG_INPUT_FACTOR if long_input else 1)
        if getattr(client, "provider", "") in REASONING_EFFORT_PROVIDERS \
                and isinstance(getattr(client, "extra_body", None), dict):
            saved["extra_body"] = client.extra_body
            client.extra_body = {**client.extra_body, "reasoning_effort": cfg["reasoning_effort"]}
        yield cfg
    finally:
        for key, value in saved.items():
            setattr(client, key, value)


def _envelope(stage: str, attempt: int) -> dict:
    return {"stage": stage, "attempt": attempt, "prompt_version": prompt_version(stage), "called": False,
            "parsed": None, "error": None, "model_resolved": "", "usage": Usage(reported=False).as_dict(),
            "latency_ms": 0, "dropped": [], "notes": []}


def _call(client, stage: str, attempt: int, content: str) -> dict:
    env = _envelope(stage, attempt)
    env["called"] = True
    messages = [{"role": "user", "content": content}]
    try:
        with stage_settings(client, stage, long_input=len(content) > LONG_INPUT_CHARS):
            result = client.complete(stage, attempt, messages, STAGES[stage][1])
    except PROPAGATE_ERRORS:
        raise
    except MODEL_LAYER_ERRORS as exc:
        env["error"] = f"{type(exc).__name__}: {exc}"
        return env
    env.update(model_resolved=result.model_resolved, usage=result.usage.as_dict(), latency_ms=result.latency_ms)
    if result.error is not None or result.parsed is None:
        env["error"] = result.error or "empty_content: no parsed reply"
    else:
        env["parsed"] = copy.deepcopy(result.parsed)
        fixed = _canonicalize_ids(env["parsed"])
        if fixed:
            env["notes"].append(f"ids_canonicalized: {fixed}")
    return env


_ID_KEYS = {"seg_id", "doc_id", "tmp_id", "req_id", "row_id", "attribute_of", "for_id", "req_a", "req_b",
            "candidate_id"}
_ID_LIST_KEYS = {"row_ids", "merge_scope", "unmatched_rows"}


def _canonicalize_ids(node) -> int:
    """Rewrite id fields of a parsed reply in place to the issued form; returns how many changed."""
    changed = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _ID_KEYS and isinstance(value, str):
                new = canonical_id(value)
                changed += new != value
                node[key] = new
            elif key in _ID_LIST_KEYS and isinstance(value, list):
                new_list = [canonical_id(v) for v in value]
                changed += sum(a != b for a, b in zip(new_list, value))
                node[key] = new_list
            else:
                changed += _canonicalize_ids(value)
    elif isinstance(node, list):
        for item in node:
            changed += _canonicalize_ids(item)
    return changed


# ---------------------------------------------------------------------------------------------
# post-validation helpers
# ---------------------------------------------------------------------------------------------

def _id_num(tmp_id: str) -> int:
    return int(tmp_id[1:]) if isinstance(tmp_id, str) and TMP_ID_RE.match(tmp_id) else 0


def _next_id(taken) -> str:
    return f"R{max([_id_num(t) for t in taken] + [0]) + 1}"


def _req_list(items) -> list[dict]:
    """Accepts Req dicts or VReq dicts ({"req", "check"})."""
    return [it["req"] if isinstance(it, dict) and "req" in it else it for it in items or ()]


def _assign_ids(reqs: list[dict], taken, env: dict, label: str) -> list[dict]:
    """Repeated tmp_id within the reply -> later record dropped. Malformed id or id already used by an existing
    record -> renamed to a fresh R<n>; references inside the reply (attribute_of, merge_scope) follow."""
    taken = set(taken)
    reply_ids = {r.get("tmp_id", "") for r in reqs}
    counter = max([_id_num(t) for t in taken | reply_ids] + [0])
    seen: set[str] = set()
    out, remap = [], {}
    for r in reqs:
        tid = r.get("tmp_id", "")
        if tid in seen:
            env["dropped"].append(f"{label}: requirement {tid!r} repeats a tmp_id of this reply; dropped")
            continue
        seen.add(tid)
        if not TMP_ID_RE.match(tid) or tid in taken:
            counter += 1
            new = f"R{counter}"
            remap[tid] = new
            env["notes"].append(f"{label}: tmp_id {tid!r} renamed to {new}")
            r = {**r, "tmp_id": new}
        out.append(r)
    if remap:
        for r in out:
            if r.get("attribute_of") in remap:
                r["attribute_of"] = remap[r["attribute_of"]]
            fmt = r.get("format")
            if isinstance(fmt, dict) and fmt.get("merge_scope"):
                fmt["merge_scope"] = [remap.get(x, x) for x in fmt["merge_scope"]]
    return out


def _seg_list(items: list[dict], segs: dict, env: dict, label: str, field: str, *, allowed=None, limit=None):
    out, seen = [], set()
    for it in items:
        sid = it.get("seg_id", "")
        if sid not in segs:
            env["dropped"].append(f"{label}: {field} cites unknown segment {sid!r}; dropped")
        elif allowed is not None and sid not in allowed:
            env["dropped"].append(f"{label}: {field} cites {sid!r}, outside the requested segments; dropped")
        elif sid in seen:
            env["dropped"].append(f"{label}: {field} repeats {sid!r}; dropped")
        elif limit is not None and len(out) >= limit:
            env["dropped"].append(f"{label}: {field} {sid!r} over the limit of {limit}; dropped")
        else:
            seen.add(sid)
            out.append(it)
    return out


def _clean_extract(parsed: dict, bundle: dict, env: dict, *, label: str, allowed=None, taken=()) -> None:
    segs = segment_map(bundle)
    reqs = parsed.get("requirements", [])
    if allowed is not None:
        kept = []
        for r in reqs:
            sid = (r.get("location") or {}).get("seg_id", "")
            if sid in allowed:
                kept.append(r)
            else:
                env["dropped"].append(f"{label}: requirement {r.get('tmp_id')!r} quotes {sid!r}, outside the "
                                      "requested segments; dropped")
        reqs = kept
    reqs = _assign_ids(reqs, taken, env, label)
    for r in reqs:
        sid = (r.get("location") or {}).get("seg_id", "")
        if sid not in segs:
            env["notes"].append(f"{label}: requirement {r['tmp_id']} cites unknown segment {sid!r} (left to Q6)")
    parsed["requirements"] = reqs
    parsed["uncertain_segments"] = _seg_list(parsed.get("uncertain_segments", []), segs, env, label,
                                             "uncertain_segments", allowed=allowed, limit=MAX_UNCERTAIN)
    parsed["not_requirements"] = _seg_list(parsed.get("not_requirements", []), segs, env, label,
                                           "not_requirements", allowed=allowed)
    suspects, seen = [], set()
    for s in parsed.get("injection_suspects", []):
        sid, quote = s.get("seg_id", ""), s.get("quote", "")
        if sid not in segs:
            env["dropped"].append(f"{label}: injection_suspect cites unknown segment {sid!r}; dropped")
        elif not contains(segs[sid]["text"], quote):
            env["dropped"].append(f"{label}: injection_suspect quote not found in {sid}; dropped")
        elif (sid, norm08(quote)) in seen:
            env["dropped"].append(f"{label}: injection_suspect repeats {sid}; dropped")
        else:
            seen.add((sid, norm08(quote)))
            suspects.append(s)
    parsed["injection_suspects"] = suspects


def _segment_ids(seg_ids, bundle: dict, env: dict, label: str, limit: int) -> list[dict]:
    """[{seg_id, reason, note}] from str ids or dicts; unknown and repeated ids dropped, then capped."""
    segs = segment_map(bundle)
    out, seen = [], set()
    for item in seg_ids or ():
        entry = {"seg_id": item} if isinstance(item, str) else {k: item.get(k, "") for k in ("seg_id", "reason", "note")}
        sid = entry["seg_id"]
        if sid not in segs:
            env["dropped"].append(f"{label}: requested segment {sid!r} does not exist; not sent")
        elif sid in seen:
            continue
        elif len(out) >= limit:
            env["dropped"].append(f"{label}: requested segment {sid!r} over the limit of {limit}; not sent")
        else:
            seen.add(sid)
            out.append(entry)
    return out


def _existing_block(reqs: list[dict]) -> str:
    return _block("REQUIREMENTS", [_jl(_compact({"tmp_id": r.get("tmp_id", ""), "subject": r.get("subject", ""),
                                                 "quote": r.get("quote", "")})) for r in reqs])


# ---------------------------------------------------------------------------------------------
# U: user message -> asked items (D6). Sees nothing but the masked message.
# ---------------------------------------------------------------------------------------------

def run_ask(client, user_message_masked: str, *, lang: str = "en", attempt: int = 0) -> dict:
    """U. The only input is the user message (re-masked here); no source, no manifest. Adds "asked_items"."""
    if not isinstance(user_message_masked, str):
        raise TypeError("run_ask takes the user message string only")
    env = _envelope("U", attempt)
    message = mask_text(unicodedata.normalize("NFC", user_message_masked)).text.strip()
    if len(message) > MAX_USER_MESSAGE_CHARS:
        env["notes"].append(f"U: message cut to {MAX_USER_MESSAGE_CHARS} characters")
        message = message[:MAX_USER_MESSAGE_CHARS]
    if not message:
        env.update(parsed={"asked_items": []}, asked_items=[])
        return env
    content = _compose([_block("USER_MESSAGE", [_jl(message)])], render_prompt("U", OUTPUT_LANGUAGE=_lang_name(lang)))
    pre = env
    env = _call(client, "U", attempt, content)
    env["notes"][:0] = pre["notes"]
    items = []
    if env["parsed"] is not None:
        for raw in env["parsed"].get("asked_items", []):
            item = " ".join(raw.get("item", "").split())
            if not item:
                env["dropped"].append("U: asked item without text; dropped")
                continue
            if len(items) >= MAX_ASK_ITEMS:
                env["dropped"].append(f"U: asked item {item!r} over the limit of {MAX_ASK_ITEMS}; dropped")
                continue
            terms, seen = [], set()
            for t in raw.get("search_terms", []):
                t = " ".join(t.split())
                if len(t) >= 2 and t.casefold() not in seen:
                    seen.add(t.casefold())
                    terms.append(t)
            if len(terms) > MAX_SEARCH_TERMS:
                env["notes"].append(f"U: search terms of {item!r} cut to {MAX_SEARCH_TERMS}")
                terms = terms[:MAX_SEARCH_TERMS]
            if not terms:
                env["notes"].append(f"U: item {item!r} had no usable search term; the item text is used")
                terms = [item]
            items.append({"item": item, "search_terms": terms})
        env["parsed"] = {"asked_items": items}
    env["asked_items"] = items
    return env


# ---------------------------------------------------------------------------------------------
# E1-E4: extraction loop
# ---------------------------------------------------------------------------------------------

def _empty_extract() -> dict:
    return {"requirements": [], "uncertain_segments": [], "not_requirements": [], "injection_suspects": []}


def run_extract(client, bundle: dict, *, lang: str, attempt: int = 0) -> dict:
    """E1. Input = the source bundle only (never the manifest or the user message)."""
    if not bundle.get("segments"):
        env = _envelope("E1", attempt)
        env["error"] = "source_missing: the bundle has no segments; no call made"
        return env
    content = _compose([source_block(bundle)], render_prompt("E1", OUTPUT_LANGUAGE=_lang_name(lang)))
    env = _call(client, "E1", attempt, content)
    if env["parsed"] is not None:
        _clean_extract(env["parsed"], bundle, env, label="E1")
    return env


_FIX_FIELDS = ("deadline", "validity", "doc_code", "format", "amount", "member_rule", "sign_seal", "notarized")


def run_fix(client, bundle: dict, failures: list[dict], attempt: int) -> dict:
    """E2. failures = [{tmp_id, failures: [str], req}]. Output fixes only for those tmp_ids."""
    env = _envelope("E2", attempt)
    lines, wanted = [], []
    for f in failures or ():
        tid = f.get("tmp_id", "")
        if tid in wanted:
            continue
        wanted.append(tid)
        req = f.get("req") or {}
        fields = _compact({k: req.get(k) for k in _FIX_FIELDS if k in req})
        conds = [c.get("condition_quote", "") for c in req.get("conditions") or [] if c.get("condition_quote")]
        lines.append(_jl(_compact({"tmp_id": tid, "subject": req.get("subject", ""),
                                   "requirement": req.get("requirement", ""), "quote": req.get("quote", ""),
                                   "location": req.get("location") or {}, "failures": list(f.get("failures") or []),
                                   "fields": {**fields, "condition_quotes": conds}})))
    if not lines:
        env["parsed"] = {"fixes": []}
        return env
    content = _compose([source_block(bundle), _block("FAILURES", lines)],
                       render_prompt("E2", OUTPUT_LANGUAGE=_lang_name(bundle["output_lang"])))
    env = _call(client, "E2", attempt, content)
    if env["parsed"] is not None:
        fixes, seen = [], set()
        for fx in env["parsed"].get("fixes", []):
            tid = fx.get("tmp_id", "")
            if tid not in wanted:
                env["dropped"].append(f"E2: fix for {tid!r}, which was not in the failures list; dropped")
            elif tid in seen:
                env["dropped"].append(f"E2: second fix for {tid}; dropped")
            elif fx.get("action") == "requote" and not fx.get("quote", "").strip():
                env["dropped"].append(f"E2: requote for {tid} without a quote; dropped")
            else:
                seen.add(tid)
                fixes.append(fx)
        env["notes"] += [f"E2: no fix returned for {tid}" for tid in wanted if tid not in seen]
        env["parsed"]["fixes"] = fixes
    return env


def run_recheck(client, bundle: dict, seg_ids, attempt: int = 0, *, existing_reqs=()) -> dict:
    """E3. seg_ids = ids or {seg_id, reason, note} (from E1 uncertain_segments), at most 5. New records may only
    quote those segments; their tmp_ids are kept clear of existing_reqs (Req or VReq dicts)."""
    env = _envelope("E3", attempt)
    targets = _segment_ids(seg_ids, bundle, env, "E3", MAX_RECHECK_SEGMENTS)
    if not targets:
        env["parsed"] = _empty_extract()
        return env
    existing = _req_list(existing_reqs)
    taken = [r.get("tmp_id", "") for r in existing]
    content = _compose([source_block(bundle), _block("SEGMENTS", [_jl(_compact(t)) for t in targets]),
                        _existing_block(existing)],
                       render_prompt("E3", OUTPUT_LANGUAGE=_lang_name(bundle["output_lang"]), FIRST_ID=_next_id(taken)))
    pre = env
    env = _call(client, "E3", attempt, content)
    env["dropped"][:0] = pre["dropped"]
    if env["parsed"] is not None:
        _clean_extract(env["parsed"], bundle, env, label="E3", allowed={t["seg_id"] for t in targets}, taken=taken)
    return env


def run_coverage(client, bundle: dict, uncited_seg_ids, *, existing_reqs=(), attempt: int = 0) -> dict:
    """E4. At most 30 uncited trigger segments. Adds "unexplained_seg_ids": listed segments the reply neither
    extracted from nor skipped (-> info_gaps)."""
    env = _envelope("E4", attempt)
    targets = [t["seg_id"] for t in _segment_ids(uncited_seg_ids, bundle, env, "E4", MAX_COVERAGE_SEGMENTS)]
    if not targets:
        env.update(parsed={"new_requirements": [], "skips": []}, unexplained_seg_ids=[])
        return env
    segs = segment_map(bundle)
    existing = _req_list(existing_reqs)
    taken = [r.get("tmp_id", "") for r in existing]
    content = _compose([source_block(bundle), _block("SEGMENTS", [f"[{s}] {segs[s]['text'].strip()}" for s in targets]),
                        _existing_block(existing)],
                       render_prompt("E4", OUTPUT_LANGUAGE=_lang_name(bundle["output_lang"]), FIRST_ID=_next_id(taken)))
    pre = env
    env = _call(client, "E4", attempt, content)
    env["dropped"][:0] = pre["dropped"]
    allowed = set(targets)
    if env["parsed"] is not None:
        holder = {"requirements": env["parsed"].get("new_requirements", [])}
        _clean_extract(holder, bundle, env, label="E4", allowed=allowed, taken=taken)
        skips = _seg_list(env["parsed"].get("skips", []), segs, env, "E4", "skips", allowed=allowed)
        env["parsed"] = {"new_requirements": holder["requirements"], "skips": skips}
        covered = {r["location"]["seg_id"] for r in holder["requirements"]} | {s["seg_id"] for s in skips}
        env["unexplained_seg_ids"] = [s for s in targets if s not in covered]
    else:
        env["unexplained_seg_ids"] = list(targets)
    return env


# ---------------------------------------------------------------------------------------------
# M: requirement <-> file matching (09 §2-6). Never sees the source text.
# ---------------------------------------------------------------------------------------------

_MATCH_REQ_FIELDS = ("category", "subject", "requirement", "mandatory", "attribute_of", "doc_code", "quantity_rule",
                     "sign_seal", "copy_type", "notarized")
_ROW_FIELDS = (("doc_label", "doc_label"), ("file_name", "file_name"), ("declared_format", "format"),
               ("size_mb", "size_mb"), ("signed", "signed"), ("sealed", "sealed"),
               ("sign_seal_text", "sign_seal_text"), ("issue_date", "issue_date"), ("notes", "notes"),
               ("bundle", "bundle"))


def _usable_vreqs(vreqs, env: dict, label: str) -> list[dict]:
    out, seen = [], set()
    for v in vreqs or ():
        req, chk = v.get("req") or {}, v.get("check") or {}
        tid = req.get("tmp_id", "")
        if chk.get("remove") or not chk.get("verified"):
            env["notes"].append(f"{label}: {tid or '?'} not sent (unverified or removed)")
        elif tid in seen:
            env["notes"].append(f"{label}: {tid} repeated in the input; first kept")
        else:
            seen.add(tid)
            out.append(v)
    return out


def _match_req_line(v: dict) -> str:
    req, chk = v["req"], v["check"]
    view = {"req_id": req["tmp_id"], **{k: req.get(k, "") for k in _MATCH_REQ_FIELDS},
            "quote": chk.get("quote") or req.get("quote", ""),
            "conditions": [{"fact_key": c.get("fact_key", ""), "applies_when": c.get("applies_when", "")}
                           for c in req.get("conditions") or []],
            "format": req.get("format") or {}, "validity": {"rule_type": (req.get("validity") or {}).get("rule_type", "")}}
    return _jl(_compact(view))


def _bidder_line(profile: dict) -> str:
    view = {"company": _mask(profile.get("company", "")), "short_name": _mask(profile.get("short_name", "")),
            "participation": profile.get("participation", ""),
            "members": [{"name": _mask(m.get("name", "")), "role": m.get("role", ""), "share_pct": m.get("share_pct", 0)}
                        for m in profile.get("members") or []],
            "seal_type": profile.get("seal_type", ""), "facts": profile.get("facts") or {}}
    return _jl({"bidder": _compact(view)})


def _row_view(row: dict) -> dict:
    return {"row_id": row.get("row_id", ""), **{out: _mask(row.get(key, "")) for key, out in _ROW_FIELDS}}


def _note_has(row_view: dict, quote: str) -> bool:
    q = norm08(quote)
    return bool(q) and any(q in norm08(row_view.get(f) or "") for f in ("notes", "sign_seal_text"))


def run_match(client, vreqs: list[dict], manifest: dict, *, lang: str = "en", attempt: int = 0) -> dict:
    """M. Input = verified, kept requirements + masked rows + profile facts (no source, no label line).
    "parsed" is the interfaces §3 Match: {by_req, attribute_findings, unmatched_rows, dropped}."""
    env = _envelope("M", attempt)
    used = _usable_vreqs(vreqs, env, "M")
    rows = [_row_view(r) for r in manifest.get("rows") or []]
    row_ids = [r["row_id"] for r in rows]
    if not used or not rows:
        return _with_match(env, {"by_req": {}, "attribute_findings": [], "unmatched_rows": row_ids, "dropped": []})
    req_lines = [_match_req_line(v) for v in used]
    manifest_lines = [_bidder_line(manifest.get("profile") or {})] + [_jl(_compact(r)) for r in rows]
    content = _compose([_block("REQUIREMENTS", req_lines), _block("MANIFEST", manifest_lines)],
                       render_prompt("M", OUTPUT_LANGUAGE=_lang_name(lang)))
    pre = env
    env = _call(client, "M", attempt, content)
    env["notes"][:0] = pre["notes"]
    if env["parsed"] is None:
        return env
    known_reqs = {v["req"]["tmp_id"] for v in used}
    row_by_id = {r["row_id"]: r for r in rows}
    dropped = env["dropped"]
    by_req: dict[str, dict] = {}
    for m in env["parsed"].get("matches", []):
        rid = m.get("req_id", "")
        if rid not in known_reqs:
            dropped.append(f"M: match for unknown req_id {rid!r}; dropped")
            continue
        ids = []
        for row_id in m.get("row_ids", []):
            if row_id not in row_by_id:
                dropped.append(f"M: match {rid} -> unknown row_id {row_id!r}; dropped")
            elif row_id not in ids:
                ids.append(row_id)
        if rid in by_req:
            entry = by_req[rid]
            entry["row_ids"] += [x for x in ids if x not in entry["row_ids"]]
            if m.get("status_hint") == "needs-confirmation" and entry["status_hint"] != "needs-confirmation":
                entry.update(status_hint="needs-confirmation", hint_reason=m.get("hint_reason", ""))
            env["notes"].append(f"M: second match entry for {rid} merged")
            continue
        by_req[rid] = {"row_ids": ids, "status_hint": m.get("status_hint", "no_concern"),
                       "hint_reason": m.get("hint_reason", ""), "rationale": m.get("rationale", "")}
    findings = []
    for f in env["parsed"].get("attribute_findings", []):
        row = row_by_id.get(f.get("row_id", ""))
        if row is None:
            dropped.append(f"M: attribute finding for unknown row_id {f.get('row_id')!r}; dropped")
            continue
        findings.append({"row_id": row["row_id"], "attribute": f.get("attribute", ""), "value": f.get("value", ""),
                         "note_quote": f.get("note_quote", ""), "verified": _note_has(row, f.get("note_quote", ""))})
    matched = {x for e in by_req.values() for x in e["row_ids"]}
    unmatched = [r for r in row_ids if r not in matched]
    model_unmatched = [r for r in env["parsed"].get("unmatched_rows", []) if r in row_by_id]
    if sorted(set(model_unmatched)) != sorted(unmatched):
        env["notes"].append(f"M: model unmatched_rows {model_unmatched} differ from the matches; code list used")
    return _with_match(env, {"by_req": by_req, "attribute_findings": findings, "unmatched_rows": unmatched,
                             "dropped": list(dropped)})


def _with_match(env: dict, match: dict) -> dict:
    """parsed = Match; its keys are mirrored on the envelope so both readings of interfaces §8 work."""
    env["parsed"] = match
    env.update({k: match[k] for k in ("by_req", "attribute_findings", "unmatched_rows")})
    return env


# ---------------------------------------------------------------------------------------------
# C: conflict adjudication (09 §2-8)
# ---------------------------------------------------------------------------------------------

def run_conflict(client, candidates: list[dict], vreqs: list[dict], bundle: dict, *, attempt: int = 0) -> dict:
    """C. candidates = [{id, rule, a, b, values: {a, b}}] from engine.conflicts.find_candidates. Distinguishing
    quotes are verified later by apply_adjudication; here only ids are checked."""
    env = _envelope("C", attempt)
    used = {v["req"]["tmp_id"]: v for v in _usable_vreqs(vreqs, env, "C")}
    cand_lines, cand_ids, cand_pairs = [], [], set()
    for c in candidates or ():
        cid, a, b = c.get("id", ""), c.get("a", ""), c.get("b", "")
        if a not in used or b not in used or cid in cand_ids:
            env["notes"].append(f"C: candidate {cid!r} not sent (unknown or unverified side, or repeated id)")
            continue
        cand_ids.append(cid)
        cand_pairs.add(frozenset((a, b)))
        values = c.get("values") or {}

        def side(tid, key):
            chk = used[tid]["check"]
            return {"req_id": tid, "location": chk.get("location", ""), "seg_id": chk.get("seg_id", ""),
                    "quote": chk.get("quote", ""), "value": str(values.get(key, ""))}

        cand_lines.append(_jl(_compact({"candidate_id": cid, "rule": c.get("rule", ""),
                                        "subject": used[a]["req"].get("subject", ""),
                                        "a": side(a, "a"), "b": side(b, "b")})))
    req_lines = [_jl(_compact({"req_id": tid, "subject": v["req"].get("subject", ""), "category": v["req"].get("category", ""),
                               "location": v["check"].get("location", ""), "quote": v["check"].get("quote", "")}))
                 for tid, v in used.items()]
    content = _compose([source_block(bundle), _block("CANDIDATES", cand_lines), _block("REQUIREMENTS", req_lines)],
                       render_prompt("C", OUTPUT_LANGUAGE=_lang_name(bundle["output_lang"])))
    pre = env
    env = _call(client, "C", attempt, content)
    env["notes"][:0] = pre["notes"]
    if env["parsed"] is None:
        return env
    dropped = env["dropped"]
    adjudications, seen = [], set()
    for adj in env["parsed"].get("adjudications", []):
        cid = adj.get("candidate_id", "")
        if cid not in cand_ids:
            dropped.append(f"C: adjudication for unknown candidate {cid!r}; dropped")
        elif cid in seen:
            dropped.append(f"C: second adjudication for {cid}; dropped")
        else:
            seen.add(cid)
            adjudications.append(adj)
    env["notes"] += [f"C: no adjudication for {cid} (stays a conflict)" for cid in cand_ids if cid not in seen]
    new_conflicts, pairs = [], set(cand_pairs)
    for nc in env["parsed"].get("new_conflicts", []):
        a, b = nc.get("req_a", ""), nc.get("req_b", "")
        pair = frozenset((a, b))
        if a not in used or b not in used or a == b:
            dropped.append(f"C: new conflict {a!r}/{b!r} names an unknown or unverified requirement; dropped")
        elif pair in pairs:
            dropped.append(f"C: new conflict {a}/{b} repeats a known pair; dropped")
        else:
            pairs.add(pair)
            new_conflicts.append(nc)
    question_ids = set(cand_ids) | set(used)
    questions, asked = [], set()
    for q in env["parsed"].get("questions", []):
        fid = q.get("for_id", "")
        if fid not in question_ids:
            dropped.append(f"C: question for unknown id {fid!r}; dropped")
        elif fid in asked:
            dropped.append(f"C: second question for {fid}; dropped")
        else:
            asked.add(fid)
            questions.append(q)
    env["parsed"] = {"adjudications": adjudications, "new_conflicts": new_conflicts, "questions": questions}
    return env


__all__ = [
    "LANG_NAMES", "MAX_COVERAGE_SEGMENTS", "MAX_RECHECK_SEGMENTS", "PROPAGATE_ERRORS", "REASONING_EFFORT_PROVIDERS",
    "STAGES", "STAGE_CONFIG", "prompt_sha256", "prompt_template", "prompt_version", "render_prompt", "run_ask",
    "run_conflict", "run_coverage", "run_extract", "run_fix", "run_match", "run_recheck", "source_block",
    "stage_settings",
]
