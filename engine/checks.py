"""Code checks Q1-Q8 on model-extracted requirements (design 09 §2-5).

Q1 returns the exact source span (D19): the quote is matched on a normalized projection and
cut back out of the bundle text through a normalized->original index map, so the report quote
is always a substring of the source, whatever spacing/case the model used.
"""
from __future__ import annotations

import re
import unicodedata

from engine.dates import parse_datetime_text, required_until, same_reading
from engine.injection import match_injection
from engine.normalize import segment_map

QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
METHODS = ("exact", "casefold", "nospace")
TMP_ID_RE = re.compile(r"^R[0-9]{1,3}$")
MAX_QUOTE_LINES = 3
MAX_QUOTE_CHARS = 300
CHECK_IDS = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8")
VERIFY_CHECKS = ("Q1", "Q2", "Q3", "Q6")

_SIGN = r"sign|서명"
_SEAL = r"seal|인감|날인|직인"
_ALL = r"\ball\b|\beach\b|전원|각자|모든|각각"
SIGN_SEAL_KEYWORDS = {
    "signature": (_SIGN,),
    "seal": (_SEAL,),
    "signature_and_seal": (_SIGN, _SEAL),
    "seal_corporate_or_usage": (r"corporate|법인인감", r"usage|authori[sz]ed[- ]use|사용인감"),
    "all_members_seal": (_ALL, _SEAL),
    "all_members_sign": (_ALL, _SIGN),
    "each_person_handwritten": (_ALL, r"자필|handwrit|by hand"),
    "certified_copy_mark": (r"원본대조필|certified\s+(?:true\s+)?copy",),
}
NOTARIZED_KEYWORDS = r"notari[sz]|공증"
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def norm08(text: str) -> str:
    """08 §4.1-2: NFC, whitespace runs -> one space, curly quotes -> straight, en/em dash -> '-'."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).translate(QUOTE_MAP)).strip()


def project(text: str, method: str) -> tuple[str, list[int], list[int]]:
    """Normalized projection of NFC `text` with index maps: char i of the result comes from
    text[starts[i]:ends[i]]. exact = norm08 rules; casefold adds str.casefold; nospace also
    drops all whitespace (Korean spacing differences)."""
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            if method != "nospace":
                out.append(" ")
                starts.append(i)
                ends.append(j)
            i = j
            continue
        ch = text[i].translate(QUOTE_MAP)
        if method != "exact":
            ch = ch.casefold()
        for c in ch:
            out.append(c)
            starts.append(i)
            ends.append(i + 1)
        i += 1
    return "".join(out), starts, ends


def _pq(text: str, method: str) -> str:
    return project(unicodedata.normalize("NFC", text), method)[0].strip()


def contains(hay: str, needle: str) -> str:
    """First method under which `needle` occurs in `hay`, or ''."""
    for method in METHODS:
        q = _pq(needle, method)
        if q and q in project(hay, method)[0]:
            return method
    return ""


def _numbers(text: str) -> list[float]:
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    nums += [float(v) for w, v in _NUM_WORDS.items() if re.search(rf"\b{w}\b", text, re.I)]
    return nums


def has_number(text: str, value: float) -> bool:
    return any(abs(n - float(value)) < 1e-9 for n in _numbers(text))


class _Ctx:
    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.segs = segment_map(bundle)
        self.docs = {d["doc_id"] for d in bundle["docs"]}
        self.blocks: dict[str, list[dict]] = {}
        for b in bundle["blocks"]:
            lines = [self.segs[s]["text"] for s in b["seg_ids"]]
            offs, pos = [], 0
            for ln in lines:
                offs.append(pos)
                pos += len(ln) + 1
            self.blocks.setdefault(b["doc_id"], []).append({
                "block_id": b["block_id"], "seg_ids": b["seg_ids"], "text": "\n".join(lines),
                "offs": offs, "lens": [len(ln) for ln in lines]})
        self.block_of = {s: blk for blks in self.blocks.values() for blk in blks for s in blk["seg_ids"]}
        self.suspects: dict[str, list[dict]] = {}
        for s in bundle.get("injection_suspects", []):
            self.suspects.setdefault(s["seg_id"], []).append(s)
        self._proj: dict[tuple[str, str], tuple[str, list[int], list[int]]] = {}

    def proj(self, blk: dict, method: str):
        key = (blk["block_id"], method)
        if key not in self._proj:
            self._proj[key] = project(blk["text"], method)
        return self._proj[key]

    def doc_text(self, doc_id: str) -> str:
        return "\n".join(blk["text"] for blk in self.blocks.get(doc_id, []))

    def bundle_text(self) -> str:
        return "\n".join(self.doc_text(d) for d in sorted(self.docs))


def find_quote(ctx: _Ctx, quote: str, doc_id: str, claimed_seg: str = "") -> dict:
    """Locate `quote` in one document, one block, at most 3 consecutive lines. Ladder
    exact -> casefold -> nospace; the first method with any occurrence decides."""
    for method in METHODS:
        q = _pq(quote, method)
        if not q:
            return {"found": False, "detail": "empty quote"}
        occ = []
        for blk in ctx.blocks.get(doc_id, []):
            p, st, en = ctx.proj(blk, method)
            i = p.find(q)
            while i != -1:
                os_, oe = st[i], en[i + len(q) - 1]
                seg_ids = [blk["seg_ids"][k] for k, off in enumerate(blk["offs"])
                           if off < oe and off + blk["lens"][k] > os_]
                occ.append({"block": blk, "start": os_, "end": oe, "seg_ids": seg_ids,
                            "quote": blk["text"][os_:oe]})
                i = p.find(q, i + 1)
        if occ:
            ok = [o for o in occ if len(o["seg_ids"]) <= MAX_QUOTE_LINES]
            if not ok:
                return {"found": False, "method": method,
                        "detail": f"quote spans {len(occ[0]['seg_ids'])} lines (max {MAX_QUOTE_LINES})"}
            pick = next((o for o in ok if claimed_seg in o["seg_ids"]), ok[0])
            return {"found": True, "method": method, **pick}
    return {"found": False, "detail": f"quote not found in {doc_id}"}


def _check(checks: dict, cid: str, result: str, detail: str = "", code: str = "") -> None:
    checks[cid] = {"id": cid, "result": result, "detail": detail}
    if code:
        checks[cid]["code"] = code


def _q4(ctx: _Ctx, req: dict, span: str, blk: dict, doc_id: str) -> tuple[list[str], bool]:
    """Field grounding. Returns (failure details, tz_quote_grounded)."""
    fails = []
    dl = req.get("deadline") or {}
    val = req.get("validity") or {}
    fmt = req.get("format") or {}
    amt = req.get("amount") or {}
    mem = req.get("member_rule") or {}
    if dl.get("raw") and not contains(span, dl["raw"]):
        fails.append("deadline.raw not in quote")
    if val.get("raw") and not contains(span, val["raw"]):
        fails.append("validity.raw not in quote")
    for f in ("days", "months"):
        if int(val.get(f) or 0) > 0 and not has_number(span, val[f]):
            fails.append(f"validity.{f}={val[f]} not in quote")
    for cond in req.get("conditions") or []:
        cq = cond.get("condition_quote", "")
        if not cq or not contains(blk["text"], cq):
            fails.append(f"condition_quote {cq!r} not in the quote's block")
    low = span.casefold()
    for ext in fmt.get("extensions") or []:
        tok = ext.strip().lstrip(".").casefold()
        if not tok or not re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", low):
            fails.append(f"extension {ext!r} not in quote")
    if float(fmt.get("max_size_mb") or 0) > 0 and not has_number(span, fmt["max_size_mb"]):
        fails.append(f"max_size_mb={fmt['max_size_mb']} not in quote")
    if fmt.get("filename_template") and not contains(span, fmt["filename_template"]):
        fails.append("filename_template not in quote")
    if float(amt.get("value") or 0) > 0 and not has_number(span, amt["value"]):
        fails.append(f"amount.value={amt['value']} not in quote")
    if amt.get("currency") and amt["currency"].casefold() not in low:
        fails.append(f"amount.currency {amt['currency']!r} not in quote")
    if float(amt.get("ratio_pct") or 0) > 0 and not has_number(span, amt["ratio_pct"]):
        fails.append(f"amount.ratio_pct={amt['ratio_pct']} not in quote")
    if int(mem.get("max_members") or 0) > 0 and not has_number(span, mem["max_members"]):
        fails.append(f"member_rule.max_members={mem['max_members']} not in quote")
    if float(mem.get("lead_share_min_pct") or 0) > 0 and not has_number(span, mem["lead_share_min_pct"]):
        fails.append(f"member_rule.lead_share_min_pct={mem['lead_share_min_pct']} not in quote")
    # Checklist codes usually live in one annex while the obligation is quoted from the body,
    # so a code is grounded if it appears verbatim anywhere in the bundle.
    code = req.get("doc_code", "")
    if code and not re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", ctx.bundle_text()):
        fails.append(f"doc_code {code!r} not in any source document")
    ss = req.get("sign_seal", "not_stated")
    for kw in SIGN_SEAL_KEYWORDS.get(ss, ()):
        if not re.search(kw, span, re.I):
            fails.append(f"sign_seal={ss} has no keyword /{kw}/ in quote")
    if req.get("notarized") == "required" and not re.search(NOTARIZED_KEYWORDS, span, re.I):
        fails.append("notarized=required has no keyword in quote")
    tz_ok = False
    if dl.get("tz_quote"):
        tz_ok = any(contains(s["text"], dl["tz_quote"]) for s in ctx.segs.values() if s["doc_id"] == doc_id)
        if not tz_ok:
            fails.append("deadline.tz_quote not in any segment of the same document")
    return fails, tz_ok


def _q5(req: dict, tz_ok: bool) -> tuple[str, str, str, dict | None, list[str]]:
    """(result, detail, reason code, parsed deadline, info codes)."""
    dl = req.get("deadline") or {}
    val = req.get("validity") or {}
    parsed, info, problems = None, [], []
    code = ""
    if dl.get("raw") or dl.get("kind", "none") != "none":
        parsed = parse_datetime_text(dl.get("raw", ""), dl.get("tz_quote", "") if tz_ok else "")
        if not parsed["ok"]:
            code = "DATE_UNPARSED"
            problems.append(f"deadline.raw unparsed: {parsed['error']}")
        else:
            info += parsed["info"]
            same, why = same_reading(parsed, dl.get("iso_local", ""))
            if not same:
                code = "DATE_MISMATCH"
                problems.append(f"deadline.iso_local differs ({why})")
    if val.get("rule_type") == "valid_until_date":
        until = parse_datetime_text(val.get("raw", ""))
        if not until["ok"]:
            code = code or "DATE_UNPARSED"
            problems.append(f"validity.raw unparsed: {until['error']}")
        elif val.get("until_iso") and required_until(val) is None:
            code = code or "DATE_MISMATCH"
            problems.append(f"validity.until_iso {val['until_iso']!r} differs from code reading {until['date']}")
    if problems:
        return "fail", "; ".join(problems), code, parsed, info
    applicable = parsed is not None or val.get("rule_type") == "valid_until_date"
    return "pass", "" if applicable else "not applicable", "", parsed, info


def _finalize(res: dict) -> dict:
    checks = res["checks"]
    res["verified"] = all(checks[c]["result"] == "pass" for c in VERIFY_CHECKS)
    res["grounded"] = all(checks[c]["result"] == "pass" for c in ("Q4", "Q5"))
    codes = []
    if not res["verified"]:
        codes.append("UNVERIFIED_QUOTE")
    if checks["Q4"]["result"] != "pass":
        codes.append("FIELD_NOT_GROUNDED")
    if checks["Q5"]["result"] == "fail":
        codes.append(checks["Q5"]["code"])
    res["reason_codes"] = codes
    res["failures"] = [f"{c['id']}: {c['detail']}" for c in (checks[i] for i in CHECK_IDS) if c["result"] == "fail"]
    return res


def check_requirement(req: dict, bundle: dict, ctx: _Ctx | None = None) -> dict:
    """Q1-Q6 and Q8 on one Req (model_x_extract shape). Q7 needs the list: see check_requirements."""
    ctx = ctx or _Ctx(bundle)
    checks: dict[str, dict] = {}
    loc = req.get("location") or {}
    doc_id, claimed = loc.get("doc_id", ""), loc.get("seg_id", "")
    quote = req.get("quote", "")
    info: list[str] = []
    res = {"tmp_id": req.get("tmp_id", ""), "doc_id": doc_id, "seg_id": "", "seg_ids": [],
           "location": "", "page": 0, "quote": "", "method": "", "deadline": None,
           "remove": False, "merged_into": "", "info_codes": info, "flags": [], "checks": checks}

    # Q6 existence
    q6 = []
    if not TMP_ID_RE.match(res["tmp_id"]):
        q6.append(f"tmp_id {res['tmp_id']!r} does not match R<n>")
    if doc_id not in ctx.docs:
        q6.append(f"doc_id {doc_id!r} not in bundle")
    seg = ctx.segs.get(claimed)
    if seg is None:
        q6.append(f"seg_id {claimed!r} not in bundle")
    elif seg["doc_id"] != doc_id:
        q6.append(f"seg_id {claimed!r} belongs to {seg['doc_id']}")

    # Q1 existence in the cited document, Q2 location
    hit = find_quote(ctx, quote, doc_id, claimed) if doc_id in ctx.docs else {"found": False, "detail": f"doc_id {doc_id!r} not in bundle"}
    if hit["found"]:
        _check(checks, "Q1", "pass", hit["method"])
        first = ctx.segs[hit["seg_ids"][0]]
        res.update(quote=hit["quote"], method=hit["method"], seg_id=first["seg_id"], seg_ids=hit["seg_ids"],
                   location=first["location"], page=first["page"])
        if claimed in hit["seg_ids"]:
            _check(checks, "Q2", "pass")
        else:
            _check(checks, "Q2", "pass", f"claimed {claimed!r}, found at {first['seg_id']}")
            info.append("LOCATION_CORRECTED")
    else:
        _check(checks, "Q1", "fail", hit["detail"])
        elsewhere = [d for d in sorted(ctx.docs) if d != doc_id and find_quote(ctx, quote, d)["found"]]
        if elsewhere:
            _check(checks, "Q2", "fail", f"quote found only in {', '.join(elsewhere)}")
        else:
            _check(checks, "Q2", "unknown", "quote not found in any document")
        if seg is not None:
            res.update(location=seg["location"], page=seg["page"])

    # Q3 length/shape (on the verified span when there is one)
    text = norm08(res["quote"] or quote)
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    nonspace = len(re.sub(r"\s", "", text))
    if not (words >= 4 or nonspace >= 8):
        _check(checks, "Q3", "fail", f"too short ({words} words, {nonspace} non-space chars)")
    elif len(text) > MAX_QUOTE_CHARS:
        _check(checks, "Q3", "fail", f"too long ({len(text)} > {MAX_QUOTE_CHARS} chars)")
    else:
        _check(checks, "Q3", "pass")

    # Q4 field grounding, Q5 date recompute (need the verified span)
    if hit["found"]:
        fails, tz_ok = _q4(ctx, req, hit["quote"], hit["block"], doc_id)
        _check(checks, "Q4", "fail" if fails else "pass", "; ".join(fails))
        result, detail, code, parsed, q5_info = _q5(req, tz_ok)
        _check(checks, "Q5", result, detail, code)
        res["deadline"] = parsed
        info += q5_info
    else:
        _check(checks, "Q4", "unknown", "quote not verified")
        _check(checks, "Q5", "unknown", "quote not verified")

    _check(checks, "Q6", "fail" if q6 else "pass", "; ".join(q6))
    _check(checks, "Q7", "pass")

    # Q8 instruction-to-AI passed off as a requirement
    pats = sorted({p for t in (quote, res["quote"], req.get("requirement", "")) for p in match_injection(t)})
    overlap = False
    if hit["found"]:
        blk = hit["block"]
        for sid in hit["seg_ids"]:
            off = blk["offs"][blk["seg_ids"].index(sid)]
            for s in ctx.suspects.get(sid, []):
                if max(hit["start"], off + s["start"]) < min(hit["end"], off + s["end"]):
                    overlap = True
    if pats or overlap:
        _check(checks, "Q8", "fail", "instruction-like text: " + ", ".join(pats or ["overlaps injection suspect"]))
        res["remove"] = True
        res["flags"].append("INJECTION_AS_REQUIREMENT")
    else:
        _check(checks, "Q8", "pass")
    res["checks"] = {c: checks[c] for c in CHECK_IDS}
    return _finalize(res)


def check_requirements(reqs: list[dict], bundle: dict) -> dict:
    """All per-requirement checks plus list-level ones: duplicate tmp_id and dangling
    attribute_of (Q6), duplicates by (segment, normalized quote) merged into the first (Q7)."""
    ctx = _Ctx(bundle)
    results = [check_requirement(r, bundle, ctx) for r in reqs]
    ids = [r.get("tmp_id", "") for r in reqs]
    seen: set[str] = set()
    for req, res in zip(reqs, results):
        extra = []
        tid = res["tmp_id"]
        if tid in seen:
            extra.append(f"duplicate tmp_id {tid!r}")
        seen.add(tid)
        parent = req.get("attribute_of", "")
        if parent and (parent == tid or parent not in ids):
            extra.append(f"attribute_of {parent!r} not found")
        if extra:
            q6 = res["checks"]["Q6"]
            detail = "; ".join(filter(None, [q6["detail"], *extra]))
            _check(res["checks"], "Q6", "fail", detail)
            _finalize(res)
    kept: dict[tuple[str, str], str] = {}
    merged = {}
    for res in results:
        if res["remove"] or not res["verified"]:
            continue
        key = (res["seg_id"], norm08(res["quote"]))
        if key in kept:
            res["remove"] = True
            res["merged_into"] = kept[key]
            merged[res["tmp_id"]] = kept[key]
            _check(res["checks"], "Q7", "fail", f"duplicate of {kept[key]}; merged")
            _finalize(res)
        else:
            kept[key] = res["tmp_id"]
    return {"results": results, "merged": merged}


def checks_list(res: dict) -> list[dict]:
    """Checks in Q1..Q8 order (x_meta.rows[].checks shape)."""
    return [res["checks"][c] for c in CHECK_IDS]
