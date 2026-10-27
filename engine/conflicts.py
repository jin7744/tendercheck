"""Conflicts: code candidates K1-K5, model adjudication intake (stage C), D18 merge (design 09 §2-8).

Code finds candidates; the model may only close one when it says `same_obligation == "no"` and both
distinguishing quotes are verified in the source. Everything else stays open. No side is ever
chosen as the correct one (08 T03).
"""
from __future__ import annotations

import re
import unicodedata

from engine import templates, textfilter
from engine.checks import METHODS, _Ctx, find_quote, norm08, project
from engine.dates import DATE_RES, TIME_RES, required_until
from engine.normalize import segment_map

RULES = ("K1", "K2", "K3", "K4", "K5")
_FAR = (10 ** 6, 0, 0)


# ---------------------------------------------------------------- helpers

def tmp_id_of(vreq: dict) -> str:
    return vreq["req"].get("tmp_id") or vreq["check"].get("tmp_id", "")


def usable(vreq: dict) -> bool:
    """Verified (Q1/Q2/Q3/Q6), grounded (Q4/Q5) and not removed or merged by Q7/Q8."""
    chk = vreq.get("check") or {}
    return chk.get("verified") is True and chk.get("grounded") is True and not chk.get("remove") \
        and not chk.get("merged_into")


def doc_position(bundle: dict | None, seg_id: str, quote: str = "") -> tuple[int, int, int]:
    """Document order key: (doc index in the bundle, line number, offset of the quote in the line)."""
    if not bundle:
        return _FAR
    seg = segment_map(bundle).get(seg_id)
    if seg is None:
        return _FAR
    order = {d["doc_id"]: i for i, d in enumerate(bundle["docs"])}
    first = (quote or "").split("\n")[0].strip()
    off = seg["text"].find(first) if first else 0
    return (order.get(seg["doc_id"], _FAR[0]), seg["line"], max(off, 0))


def cut(span: str, needle: str) -> str:
    """Exact substring of `span` that matches `needle` under exact -> casefold -> nospace, or ''."""
    if not span or not needle:
        return ""
    span = unicodedata.normalize("NFC", span)
    for method in METHODS:
        q = project(unicodedata.normalize("NFC", needle), method)[0].strip()
        if not q:
            return ""
        p, starts, ends = project(span, method)
        i = p.find(q)
        if i >= 0:
            return span[starts[i]:ends[i + len(q) - 1]]
    return ""


def _short(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _subject(req: dict) -> str:
    return " ".join((req.get("subject") or "").casefold().split())


def _exts(req: dict) -> set[str]:
    return {e.strip().lstrip(".").casefold() for e in (req.get("format") or {}).get("extensions") or [] if e.strip()}


def _deadline_key(chk: dict):
    p = chk.get("deadline") or {}
    if not p.get("ok"):
        return None
    return (p.get("date", ""), p.get("time", ""), p.get("offset", ""))


def _k1_differs(ka, kb) -> bool:
    if ka[:2] != kb[:2]:
        return True
    return bool(ka[2] and kb[2] and ka[2] != kb[2])


def _validity_key(req: dict):
    v = req.get("validity") or {}
    rule = v.get("rule_type", "none")
    if rule == "none":
        return None
    until = required_until(v) if rule == "valid_until_date" else None
    return (rule, int(v.get("days") or 0), int(v.get("months") or 0), until.isoformat() if until else v.get("raw", ""))


def _size_text(quote: str, mb: float) -> str:
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:MB|메가바이트|megabytes?)\b", quote, re.IGNORECASE):
        try:
            if abs(float(m.group(1).replace(",", ".")) - float(mb)) < 1e-9:
                return m.group(0)
        except ValueError:
            continue
    return f"{float(mb):g} MB"


def _ext_text(quote: str, exts: set[str]) -> str:
    out = []
    for e in sorted(exts):
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(e)}(?![A-Za-z0-9])", quote, re.IGNORECASE)
        out.append(m.group(0) if m else e.upper())
    return ", ".join(out)


def dt_tokens(text: str) -> list[str]:
    """Date and time as written in `text` (e.g. '2 December 2026', '14:00'; '2026. 12. 9.', '오전 10시')."""
    work = list(text or "")
    out = []
    for _, rx, _ in DATE_RES:
        m = rx.search("".join(work))
        if m:
            out.append(m.group(0).strip())
            work[m.start():m.end()] = " " * (m.end() - m.start())
            break
    for _, rx in TIME_RES:
        m = rx.search("".join(work))
        if m and m.group(0).strip():
            out.append(m.group(0).strip())
            break
    return out


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text or "")


def qv_tokens(rule: str, value: str) -> list[str]:
    """What QV3 requires an agency question to repeat from one side (09 §2-9: times and numbers)."""
    if rule == "K1":
        return dt_tokens(value) or [value]
    if rule == "K3":
        return [v.strip() for v in value.split(",") if v.strip()]
    if rule == "K5":
        return dt_tokens(value) or _numbers(value)
    if rule == "K2":
        return _numbers(value)
    return []


def _side(vreq: dict) -> dict:
    req, chk = vreq["req"], vreq["check"]
    p = chk.get("deadline") or {}
    return {"tmp_id": tmp_id_of(vreq), "doc_id": chk.get("doc_id") or (req.get("location") or {}).get("doc_id", ""),
            "seg_id": chk.get("seg_id", ""), "seg_ids": list(chk.get("seg_ids") or [chk.get("seg_id", "")]),
            "location": chk.get("location", ""), "quote": chk.get("quote", ""),
            "iso_local": p.get("iso_local", "") if p.get("ok") else "", "offset": p.get("offset", "") if p.get("ok") else ""}


def _pair_rule(va: dict, vb: dict):
    """(rule, value_a, value_b) of the first K rule the pair meets, else None."""
    ra, rb, ca, cb = va["req"], vb["req"], va["check"], vb["check"]
    qa, qb = ca.get("quote", ""), cb.get("quote", "")
    da, db = ra.get("deadline") or {}, rb.get("deadline") or {}
    kind = da.get("kind", "none")
    if kind != "none" and kind == db.get("kind", "none"):
        ka, kb = _deadline_key(ca), _deadline_key(cb)
        if ka and kb and _k1_differs(ka, kb):
            return "K1", cut(qa, da.get("raw", "")) or da.get("raw", ""), cut(qb, db.get("raw", "")) or db.get("raw", "")
    if not _subject(ra) or _subject(ra) != _subject(rb):
        return None
    sa = float((ra.get("format") or {}).get("max_size_mb") or 0)
    sb = float((rb.get("format") or {}).get("max_size_mb") or 0)
    if sa > 0 and sb > 0 and sa != sb:
        return "K2", _size_text(qa, sa), _size_text(qb, sb)
    ea, eb = _exts(ra), _exts(rb)
    if ea and eb and not (ea & eb):
        return "K3", _ext_text(qa, ea), _ext_text(qb, eb)
    for field in ("sign_seal", "copy_type"):
        a, b = ra.get(field, "not_stated"), rb.get(field, "not_stated")
        if a != "not_stated" and b != "not_stated" and a != b:
            return "K4", _short(qa), _short(qb)
    ka, kb = _validity_key(ra), _validity_key(rb)
    if ka and kb and ka != kb:
        va_, vb_ = (ra.get("validity") or {}).get("raw", ""), (rb.get("validity") or {}).get("raw", "")
        return "K5", cut(qa, va_) or _short(qa), cut(qb, vb_) or _short(qb)
    return None


# ---------------------------------------------------------------- K1-K5

def find_candidates(vreqs: list[dict]) -> list[dict]:
    """K1-K5 over pairs of usable VReqs, in input order, at most one candidate per pair.

    Candidate = {id 'K1'.., rule 'K1'..'K5', a: tmp_id, b: tmp_id, values: {a, b}, subject,
                 deadline_kind, sides: [side_a, side_b]}  (sides/subject/deadline_kind are additions
                 to interfaces §5 so that apply_adjudication can build Conflict.sides)."""
    pool = [v for v in vreqs if usable(v)]
    out = []
    for i, va in enumerate(pool):
        for vb in pool[i + 1:]:
            hit = _pair_rule(va, vb)
            if not hit:
                continue
            rule, val_a, val_b = hit
            out.append({"id": f"K{len(out) + 1}", "rule": rule, "a": tmp_id_of(va), "b": tmp_id_of(vb),
                        "values": {"a": val_a, "b": val_b}, "subject": va["req"].get("subject", ""),
                        "deadline_kind": (va["req"].get("deadline") or {}).get("kind", "none") if rule == "K1" else "",
                        "sides": [_side(va), _side(vb)]})
    return out


# ---------------------------------------------------------------- adjudication

_DT_WORDS = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?|hours?|hrs?|h|time|date|a\.?m\.?|p\.?m\.?|noon|midnight|"
    r"utc|gmt|kst|st|nd|rd|th|at|on)\b|오전|오후|정오|년|월|일|시|분|요일|[월화수목금토일](?=\))",
    re.IGNORECASE)


def _notation_only(text: str) -> bool:
    """True when `text` is only a date/time written some way (no word that could name an obligation)."""
    rest = _DT_WORDS.sub(" ", text)
    return not re.search(r"[^\W\d_]", rest)


def _closing_failure(ctx: _Ctx, cand: dict, adj: dict) -> str:
    """'' when the candidate may close; otherwise why it stays open."""
    quotes = [adj.get("distinguishing_quote_a", ""), adj.get("distinguishing_quote_b", "")]
    if not all(q.strip() for q in quotes):
        return "distinguishing quote missing"
    if norm08(quotes[0]).casefold() == norm08(quotes[1]).casefold():
        return "distinguishing quotes are identical"
    for k, (q, side, other) in enumerate(zip(quotes, cand["sides"], reversed(cand["sides"]))):
        tag = "ab"[k]
        hit = find_quote(ctx, q, side["doc_id"], side["seg_id"])
        if not hit.get("found"):
            return f"distinguishing quote {tag} not found in {side['doc_id']} (Q1)"
        blk = ctx.block_of.get(side["seg_id"])
        if blk is None or hit["block"]["block_id"] != blk["block_id"]:
            return f"distinguishing quote {tag} is not in the clause of {side['location']} (Q2)"
        if norm08(q).casefold() in norm08(other["quote"]).casefold():
            return f"distinguishing quote {tag} also appears in the other side"
        if cand["rule"] == "K1":
            value = cand["values"]["ab"[k]]
            if _notation_only(q) or (value and norm08(q).casefold() in norm08(value).casefold()):
                return f"distinguishing quote {tag} only restates the date/time (notation is not a difference)"
    return ""


def _model_candidate(n: int, va: dict, vb: dict) -> dict:
    return {"id": f"M{n}", "rule": "model", "a": tmp_id_of(va), "b": tmp_id_of(vb),
            "values": {"a": _short(va["check"].get("quote", "")), "b": _short(vb["check"].get("quote", ""))},
            "subject": va["req"].get("subject", ""), "deadline_kind": "", "sides": [_side(va), _side(vb)]}


def _question(cand: dict, sides: list[dict], values: dict, model_q: dict | None, lang: str, log: list) -> tuple[str, str]:
    vals = (values["a"], values["b"])
    if model_q is not None:
        draft = model_q.get("question", "")
        tokens = qv_tokens(cand["rule"], vals[0]) + qv_tokens(cand["rule"], vals[1])
        res = templates.check_question(draft, values=tokens)
        if res["ok"]:
            return draft, "model"
        log.append({"candidate": cand["id"], "action": "question_replaced", "why": ",".join(res["failures"])})
    subject = templates.conflict_subject(cand["rule"], lang, cand.get("deadline_kind") or "submission")
    q = templates.agency_conflict_question(lang, sides[0]["location"], vals[0], sides[1]["location"], vals[1], subject)
    if not textfilter.is_clean(q):
        q = templates.agency_conflict_question(lang, sides[0]["location"], "…", sides[1]["location"], "…")
    return q, "template"


def apply_adjudication(candidates: list[dict], x_conflict: dict | None, bundle: dict, *,
                       vreqs: list[dict] | None = None) -> dict:
    """Close a candidate only on same_obligation == 'no' with both distinguishing quotes verified
    (found in that side's document and clause, absent from the other side; for K1 not a mere
    restatement of the date/time). Model failure (None) keeps every candidate. `new_conflicts`
    are accepted only between usable VReqs, and only when `vreqs` is passed.

    Returns {"conflicts": [Conflict], "closed": [candidate + distinguishing_quotes], "log": [...]}.
    Conflict = {id 'C1'.., topic, members [tmp_a, tmp_b] (document order), sides [{quote, location}] x2,
                agency_question, values {a, b}, rule, candidate_id, subject, deadline_kind,
                anchors {location: iso_local} (K1 with parsed times), side_info [...], question_source}."""
    lang = bundle.get("output_lang", "en")
    ctx = _Ctx(bundle)
    log: list[dict] = []
    model_ok = isinstance(x_conflict, dict)
    ids = {c["id"] for c in candidates}
    adj: dict[str, dict] = {}
    questions: dict[str, dict] = {}
    if model_ok:
        for a in x_conflict.get("adjudications") or []:
            cid = a.get("candidate_id", "")
            if cid not in ids or cid in adj:
                log.append({"candidate": cid, "action": "ignored", "why": "unknown or duplicate candidate id"})
                continue
            adj[cid] = a
        for q in x_conflict.get("questions") or []:
            questions.setdefault(q.get("for_id", ""), q)

    open_, closed = [], []
    for cand in candidates:
        a = adj.get(cand["id"])
        if not model_ok:
            why = "model output unavailable"
        elif a is None:
            why = "no adjudication"
        elif a.get("same_obligation") != "no":
            why = f"same_obligation={a.get('same_obligation')}"
        else:
            why = _closing_failure(ctx, cand, a)
            if not why:
                note = textfilter.pick("explanation", [a.get("explanation", "")])
                closed.append({**cand, "distinguishing_quotes": [a["distinguishing_quote_a"], a["distinguishing_quote_b"]],
                               "explanation": note})
                log.append({"candidate": cand["id"], "action": "closed", "why": "reviewed: different obligations",
                            "quotes": [a["distinguishing_quote_a"], a["distinguishing_quote_b"]]})
                continue
        log.append({"candidate": cand["id"], "action": "kept", "why": why})
        open_.append(cand)

    if model_ok and x_conflict.get("new_conflicts"):
        pool = {tmp_id_of(v): v for v in (vreqs or []) if usable(v)}
        pairs = {frozenset((c["a"], c["b"])) for c in candidates}
        for nc in x_conflict["new_conflicts"]:
            a, b = nc.get("req_a", ""), nc.get("req_b", "")
            if vreqs is None:
                log.append({"candidate": f"{a}/{b}", "action": "ignored", "why": "new conflict: vreqs not passed"})
            elif a == b or a not in pool or b not in pool:
                log.append({"candidate": f"{a}/{b}", "action": "ignored", "why": "new conflict: requirement not verified"})
            elif frozenset((a, b)) in pairs:
                log.append({"candidate": f"{a}/{b}", "action": "ignored", "why": "new conflict: duplicate pair"})
            else:
                pairs.add(frozenset((a, b)))
                cand = _model_candidate(sum(1 for c in open_ if c["rule"] == "model") + 1, pool[a], pool[b])
                open_.append(cand)
                log.append({"candidate": cand["id"], "action": "added", "why": "new conflict from the model"})

    def pos(side):
        return doc_position(bundle, side["seg_id"], side["quote"])

    conflicts = []
    for cand in sorted(open_, key=lambda c: min(pos(s) for s in c["sides"])):
        order = sorted(range(2), key=lambda k: pos(cand["sides"][k]))
        sides = [dict(cand["sides"][k], position=list(pos(cand["sides"][k]))) for k in order]
        values = {"a": cand["values"]["ab"[order[0]]], "b": cand["values"]["ab"[order[1]]]}
        cid = f"C{len(conflicts) + 1}"
        question, source = _question(cand, sides, values, questions.get(cand["id"]), lang, log)
        conflict = {"id": cid, "topic": templates.conflict_topic(cand["rule"], lang),
                    "members": [s["tmp_id"] for s in sides],
                    "sides": [{"quote": s["quote"], "location": s["location"]} for s in sides],
                    "agency_question": question, "values": values, "rule": cand["rule"],
                    "candidate_id": cand["id"], "subject": cand.get("subject", ""),
                    "deadline_kind": cand.get("deadline_kind", ""), "side_info": sides, "question_source": source}
        if cand["rule"] == "K1" and all(s["iso_local"] for s in sides):
            labels = [s["location"] for s in sides]
            if labels[0] == labels[1]:
                labels = [f"{s['location']} ({s['tmp_id']})" for s in sides]
            conflict["anchors"] = {lab: s["iso_local"] for lab, s in zip(labels, sides)}
        conflicts.append(conflict)
    return {"conflicts": conflicts, "closed": closed, "log": log}


# ---------------------------------------------------------------- D18 merge and wiring helpers

def merge_conflicting(vreqs: list[dict], conflicts: list[dict]) -> list[dict]:
    """D18: every group of VReqs linked by conflicts becomes one row. The member earliest in
    document order keeps the row (copied, with `conflict_id`, `conflict_ids`, `merged_members`);
    the other members are dropped. Other VReqs pass through unchanged, in input order."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    position: dict[str, tuple] = {}
    for c in conflicts:
        a, b = c["members"][:2]
        parent[find(a)] = find(b)
        for s in c.get("side_info") or []:
            position[s["tmp_id"]] = tuple(s.get("position") or _FAR)
        for k, t in enumerate(c["members"]):
            position.setdefault(t, (0, 0, k))
    groups: dict[str, list[str]] = {}
    for t in list(parent):
        groups.setdefault(find(t), []).append(t)
    keep, ids_of, others = {}, {}, {}
    for members in groups.values():
        k = min(members, key=lambda t: (position.get(t, _FAR), t))
        cids = [c["id"] for c in conflicts if set(c["members"]) & set(members)]
        cids.sort(key=lambda s: int(s[1:]) if s[1:].isdigit() else 0)
        for t in members:
            keep[t] = k
        ids_of[k] = cids
        others[k] = [t for t in members if t != k]
    out = []
    for v in vreqs:
        t = tmp_id_of(v)
        if t not in keep:
            out.append(v)
        elif keep[t] == t:
            out.append({**v, "conflict_id": ids_of[t][0], "conflict_ids": ids_of[t], "merged_members": others[t]})
    return out


def deadline_anchors(vreqs: list[dict], conflicts: list[dict], kind: str = "submission") -> tuple[dict, str | None]:
    """Anchors for validity rules tied to the deadline (interfaces §4 `anchors`) and the conflict
    they come from: both sides of an open K1 conflict of that kind, else every usable deadline."""
    for c in conflicts:
        if c.get("rule") == "K1" and c.get("deadline_kind") == kind and c.get("anchors"):
            return dict(c["anchors"]), c["id"]
    anchors = {}
    for v in vreqs:
        p = v["check"].get("deadline") or {}
        if usable(v) and (v["req"].get("deadline") or {}).get("kind") == kind and p.get("ok"):
            label = v["check"].get("location", "") or tmp_id_of(v)
            anchors[label if label not in anchors else f"{label} ({tmp_id_of(v)})"] = p["iso_local"]
    return anchors, None


ANCHORED_RULES = ("valid_for_from_issue", "issued_within_before_anchor", "valid_at_least_after_anchor")


def conflict_info_for(vreq: dict, conflicts: list[dict], kind: str = "submission") -> dict:
    """`conflict_info` for rules.decide: the merged conflict row itself (S3), or the conflict a
    deadline-anchored validity rule depends on (S4/S4')."""
    info = {"conflict_id": vreq.get("conflict_id"), "depends_on_conflict": None}
    v = vreq["req"].get("validity") or {}
    if not info["conflict_id"] and v.get("rule_type") in ANCHORED_RULES and v.get("anchor_event") == "submission_deadline":
        for c in conflicts:
            if c.get("rule") == "K1" and c.get("deadline_kind") == kind:
                info["depends_on_conflict"] = c["id"]
                break
    return info
