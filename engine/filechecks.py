"""F1-F14 file checks (design 09 §2-6 table; interfaces §4).

Code reads only Req fields and the parsed manifest; the model's match decides which rows are
evidence for a requirement. Fail-closed: a value the check needs but cannot read is `unknown`,
never `pass`. General file rules (format, size, filename template) are checked against every
manifest row, not only the matched ones (09 §2-6, 08 R17/R19 reasons).
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from engine.dates import evaluate_validity, to_date

CHECK_IDS = tuple(f"F{i}" for i in range(1, 15))
RULE_SIDE_FIELDS = {"anchor_date", "period", "required_until"}
NOT_BIDDER_FACTS = RULE_SIDE_FIELDS | {"matched_file"}
CORP_MARKS = re.compile(r"\(주\)|㈜|（주）|주식회사|\(유\)|유한회사|\(사\)|\(재\)")
_CORP_RULE = re.compile(r"\(주\)|㈜|법인\s*표기|corporate\s+(?:suffix|designation)", re.I)

_BRACKET = re.compile(r"\[([^\]]+)\]|\{([^}]+)\}|<([^>]+)>")
_PH_COMPANY = re.compile(r"(?:bidder\s*)?short\s*name|bidder\s*name|company\s*name|firm\s*name|bidder|company"
                         r"|파일명용\s*업체명|업체명|회사명|입찰자명|상호|약칭", re.I)
_PH_DOC = re.compile(r"doc(?:ument)?\s*(?:code|name|type)?|file\s*code|서류\s*코드|문서\s*코드|서류명|문서명|서류", re.I)
_BARE = re.compile(r"BidderShortName|ShortName|BidderName|CompanyName|DocumentCode|DocumentName|DocCode"
                   r"|파일명용\s*업체명|업체명|회사명|입찰자명|서류명|문서명")


def _req(vreq: dict) -> dict:
    return vreq.get("req", vreq) if isinstance(vreq, dict) else {}


def _norm_ext(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lstrip(".").casefold()


def _fmt(req: dict) -> dict:
    return req.get("format") or {}


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def is_general_rule(req: dict) -> bool:
    """A file rule for the whole submission ("All files shall be in PDF format", size cap,
    filename template), as opposed to a rule on one document's own file."""
    req = _req(req)
    fmt = _fmt(req)
    has_rule = bool(fmt.get("extensions")) or _num(fmt.get("max_size_mb")) > 0 or bool(fmt.get("filename_template"))
    return (has_rule and not req.get("attribute_of") and not req.get("doc_code")
            and fmt.get("merge_single_pdf", "not_stated") != "yes"
            and req.get("category") not in ("document", "merge_rule", "validity"))


def _entry(row: dict, result: str, detail: str = "", field: str = "", **extra) -> dict:
    e = {"row_id": row.get("row_id", ""), "file_name": row.get("file_name", ""), "result": result, "detail": detail}
    if field:
        e["field"] = field
    e.update(extra)
    return e


def _agg(entries: list[dict]) -> str:
    results = {e["result"] for e in entries}
    for r in ("fail", "unknown", "pass"):
        if r in results:
            return r
    return "not_applicable"


def _summary(entries: list[dict]) -> str:
    parts = []
    for res in ("fail", "unknown"):
        hit = [e for e in entries if e["result"] == res]
        if hit:
            parts.append(f"{res}: " + "; ".join(f"{e['file_name'] or e['row_id']}: {e['detail']}" if e["detail"]
                                                 else (e["file_name"] or e["row_id"]) for e in hit))
    if not parts:
        ok = [e["file_name"] or e["row_id"] for e in entries if e["result"] == "pass"]
        parts.append("pass: " + ", ".join(ok) if ok else "")
    return " | ".join(p for p in parts if p)


def _distinct_files(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        key = r.get("file_name") or r.get("row_id")
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _row_ids_for(file_name: str, manifest: dict) -> list[str]:
    return [r["row_id"] for r in manifest.get("rows", []) if r.get("file_name") == file_name]


# ---------------- F1-F3: format, size, filename ----------------

# A clause that refuses files ("…은 접수하지 않는다", "will not be accepted") makes the extensions named in it
# refused, unless it names them as the exception ("PDF 이외의 파일", "other than PDF").
_REFUSE = re.compile(r"접수하지\s*않|제출할\s*수\s*없|제출하지\s*않|허용하지\s*않|인정하지\s*않|받지\s*않|불가|금지"
                     r"|\bnot\s+(?:be\s+)?(?:accepted|allowed|permitted)\b|\b(?:shall|must|may)\s+not\b"
                     r"|\brejected\b|\bprohibited\b", re.IGNORECASE)
_EXCEPT = re.compile(r"이외|외의|외에는|제외한|아닌|\bother\s+than\b|\bexcept\b", re.IGNORECASE)


def split_extensions(req: dict) -> tuple[set[str], set[str]]:
    """(allowed, refused) extensions of a format record. An extension is refused when every clause of the quote
    that names it refuses files without naming it as the exception; otherwise it is allowed (the old reading)."""
    exts = {_norm_ext(e) for e in _fmt(req).get("extensions") or [] if _norm_ext(e)}
    clauses = re.split(r"(?<=[.;。!?])\s+", req.get("quote", ""))
    refused = set()
    for e in exts:
        token = re.compile(r"(?<![A-Za-z])" + re.escape(e) + r"(?![A-Za-z])", re.IGNORECASE)
        named = [c for c in clauses if token.search(c)]
        if named and all(_REFUSE.search(c) and not _EXCEPT.search(c) for c in named):
            refused.add(e)
    return exts - refused, refused


def _f1(req: dict, files: list[dict]) -> list[dict]:
    allowed, refused = split_extensions(req)

    def ok(e):
        return e in allowed if allowed else e not in refused

    out = []
    for r in files:
        ext, decl = r.get("ext", ""), _norm_ext(r.get("declared_format", ""))
        if not ext and not decl:
            out.append(_entry(r, "unknown", "no extension or format stated", "extension"))
        elif ext and decl and ext != decl and ok(ext) != ok(decl):
            out.append(_entry(r, "unknown", f"file extension .{ext} vs declared format {r['declared_format']}",
                              "extension"))
        else:
            e = ext or decl
            good = ok(e)
            why = f".{e} not in allowed {sorted(allowed)}" if allowed else f".{e} is refused {sorted(refused)}"
            out.append(_entry(r, "pass" if good else "fail", "" if good else why))
    return out


def file_size(row: dict, manifest: dict):
    """Declared size of the file holding `row`: its own number, or the number written on another
    row of the same file/bundle for "(병합)" rows. None if unreadable or contradictory."""
    peers = [r for r in manifest.get("rows", []) if r.get("file_name") == row.get("file_name")
             or (row.get("bundle") and r.get("bundle") == row.get("bundle"))] or [row]
    sizes = {Decimal(str(r["size_mb"])) for r in peers if isinstance(r.get("size_mb"), (int, float))}
    return sizes.pop() if len(sizes) == 1 else None


def _f2(req: dict, files: list[dict], manifest: dict) -> list[dict]:
    try:
        limit = Decimal(str(_fmt(req).get("max_size_mb")))
    except InvalidOperation:
        return [_entry(r, "unknown", "size limit unreadable") for r in files]
    out = []
    for r in files:
        size = file_size(r, manifest)
        if size is None:
            out.append(_entry(r, "unknown", "size not stated", "size_mb"))
        else:
            ok = size <= limit
            out.append(_entry(r, "pass" if ok else "fail",
                              f"{size} MB {'<=' if ok else '>'} {limit} MB", size_mb=float(size)))
    return out


def _bare_tokens(text: str) -> list[tuple]:
    toks, pos = [], 0
    for m in _BARE.finditer(text):
        if m.start() > pos:
            toks.append(("lit", text[pos:m.start()]))
        toks.append(("company",) if _PH_COMPANY.fullmatch(m.group(0)) else ("doc",))
        pos = m.end()
    if pos < len(text):
        toks.append(("lit", text[pos:]))
    return toks


def template_regex(template: str, short_name: str) -> tuple[re.Pattern | None, str, str]:
    """(compiled pattern, problem, unknown field). Company placeholders must equal the short
    name; document placeholders accept any non-empty name; the extension is case-insensitive."""
    t = (template or "").strip().strip("'\"‘’“”`")
    toks, pos = [], 0
    for m in _BRACKET.finditer(t):
        toks += _bare_tokens(t[pos:m.start()])
        inner = next(g for g in m.groups() if g).strip()
        if _PH_COMPANY.fullmatch(inner):
            toks.append(("company",))
        elif _PH_DOC.fullmatch(inner):
            toks.append(("doc",))
        else:
            return None, f"placeholder [{inner}] not understood", ""
        pos = m.end()
    toks += _bare_tokens(t[pos:])
    kinds = [k[0] for k in toks]
    if "company" not in kinds and "doc" not in kinds:
        return None, f"template {t!r} has no placeholder", ""
    if "company" in kinds and not short_name:
        return None, "short name not stated", "short_name"
    parts = []
    for i, tok in enumerate(toks):
        if tok[0] == "company":
            parts.append(re.escape(short_name))
        elif tok[0] == "doc":
            parts.append(r"[^/\\]+?")
        else:
            lit = tok[1]
            em = re.search(r"\.([A-Za-z0-9]{1,8})$", lit) if i == len(toks) - 1 else None
            if em:
                parts.append(re.escape(lit[:em.start()]) + r"\.(?i:" + re.escape(em.group(1)) + ")")
            else:
                parts.append(re.escape(lit))
    return re.compile("".join(parts)), "", ""


def _f3(req: dict, files: list[dict], manifest: dict, quote: str) -> list[dict]:
    template = _fmt(req).get("filename_template", "")
    short = (manifest.get("profile") or {}).get("short_name", "")
    rx, problem, field = template_regex(template, short)
    if rx is None:
        return [_entry(r, "unknown", problem, field) for r in files]
    ban = manifest.get("lang") == "ko" or bool(_CORP_RULE.search(" ".join([template, quote,
                                                                          _req(req).get("requirement", "")])))
    out = []
    for r in files:
        name = r.get("file_name", "")
        if not name:
            out.append(_entry(r, "unknown", "file name not stated", "file_name"))
        elif ban and CORP_MARKS.search(name):
            out.append(_entry(r, "fail", "corporate designation such as (주) in the file name"))
        elif rx.fullmatch(name):
            out.append(_entry(r, "pass"))
        else:
            out.append(_entry(r, "fail", f"does not match {template.strip()} (name part = {short})"))
    return out


# ---------------- F4-F6: merge, sign/seal, original/copy ----------------

def _f4(rows: list[dict]) -> tuple[str, str]:
    names = []
    for r in rows:
        if r.get("file_name") not in names:
            names.append(r.get("file_name"))
    if len(names) > 1:
        return "fail", f"{len(names)} separate files: " + ", ".join(names)
    evidence = len(rows) > 1 or any(r.get("bundle") for r in rows)
    if evidence:
        return "pass", f"one file: {names[0]}"
    return "unknown", f"one file {names[0]} but the list does not say it holds the merged documents"


def _yn(row: dict, key: str) -> tuple[str, str]:
    v = row.get(key, "unknown")
    return ("pass", "") if v == "yes" else ("fail", f"{key} = no") if v == "no" else ("unknown", f"{key} not stated")


def _f5_row(req: dict, row: dict) -> dict:
    ss = req.get("sign_seal", "not_stated")
    parts = []
    if ss in ("signature", "signature_and_seal"):
        parts.append((*_yn(row, "signed"), "signed"))
    if ss in ("seal", "signature_and_seal", "seal_corporate_or_usage"):
        res, why = _yn(row, "sealed")
        field = "sealed"
        if ss == "seal_corporate_or_usage" and res == "pass":
            kinds = set(row.get("parsed", {}).get("seal_kinds", []))
            if not kinds & {"usage", "corporate", "unspecified"}:
                res, why, field = "unknown", "seal kind not stated (corporate or authorized-use seal)", "seal_kind"
        parts.append((res, why, field))
    result = _agg([{"result": p[0]} for p in parts])
    hit = [p for p in parts if p[0] == result]
    return _entry(row, result, "; ".join(p[1] for p in hit if p[1]),
                  next((p[2] for p in hit), "") if result == "unknown" else "")


def _f6_row(req: dict, row: dict) -> dict:
    ct, ss = req.get("copy_type", "not_stated"), req.get("sign_seal", "not_stated")
    parsed = row.get("parsed", {})
    kind, mark = parsed.get("copy_kind", ""), parsed.get("certified_mark", "")
    checks = []
    if ct == "original":
        if kind in ("copy", "certified_copy") or "사본" in row.get("doc_label", ""):
            checks.append(("fail", "original required, file is a copy", ""))
        elif kind == "original":
            checks.append(("pass", "", ""))
        else:
            checks.append(("unknown", "original or copy not stated", "copy_kind"))
    if ct == "certified_copy" or ss == "certified_copy_mark":
        if mark == "yes" or kind == "certified_copy":
            checks.append(("pass", "", ""))
        elif mark == "no" or kind == "copy":
            checks.append(("fail", "certified-copy mark (원본대조필) required, not present", ""))
        else:
            checks.append(("unknown", "certified-copy mark not stated", "certified_mark"))
    if ct == "copy_allowed" and not checks:
        checks.append(("pass", "copy allowed" + (f" ({kind.replace('_', ' ')})" if kind else ""), ""))
    result = _agg([{"result": c[0]} for c in checks])
    hit = [c for c in checks if c[0] == result]
    return _entry(row, result, "; ".join(c[1] for c in hit if c[1]), next((c[2] for c in hit if c[2]), ""))


# ---------------- F7-F9: validity, issue date, notarization ----------------

def _f7_row(validity: dict, row: dict, anchors: dict, as_of: str) -> dict:
    rule = validity.get("rule_type", "none")
    parsed = row.get("parsed", {})
    period_to = (parsed.get("guarantee_period") or {}).get("to", "")
    if rule == "valid_until_date":
        until = parsed.get("valid_until") or period_to
    else:
        until = period_to or parsed.get("valid_until", "")
    use_anchors = anchors or {}
    if rule != "valid_until_date" and validity.get("anchor_event", "not_stated") not in ("not_stated",
                                                                                         "submission_deadline"):
        use_anchors = {}
    try:
        ev = evaluate_validity(validity, issue_date=row.get("issue_date"), file_until=until or None,
                               anchors=use_anchors, as_of=as_of)
    except (ValueError, TypeError) as exc:
        return _entry(row, "unknown", f"validity not computable: {exc}", reason_codes=[], info=[], readings=[],
                      split_by=[], issue_date=row.get("issue_date", "unknown"), valid_until=until or "unknown")
    detail = _f7_detail(ev, row, until)
    fields = [c.split(":", 1)[1] for c in ev["reason_codes"] if c.startswith("ATTRIBUTE_UNKNOWN:")]
    bidder = [f for f in fields if f not in RULE_SIDE_FIELDS]
    return _entry(row, ev["result"], detail, bidder[0] if bidder else "", fields=bidder,
                  reason_codes=list(ev["reason_codes"]), info=list(ev["info"]), readings=ev["readings"],
                  split_by=ev["split_by"], conventions=ev["conventions"],
                  issue_date=row.get("issue_date", "unknown"), valid_until=until or "unknown")


def _f7_detail(ev: dict, row: dict, until: str) -> str:
    if ev["result"] == "not_applicable":
        return "no validity rule"
    head = []
    if row.get("issue_date") and row["issue_date"] != "unknown":
        head.append(f"issued {row['issue_date']}")
    if until:
        head.append(f"valid until {until}")
    if not ev["readings"]:
        return ", ".join(head + [", ".join(ev["reason_codes"])])
    reads = "; ".join(f"{r['anchor'] or 'required'} {r['anchor_date']} [{r['convention']}] limit {r['limit']}: "
                      f"{r['result']}" for r in ev["readings"])
    return ", ".join(head) + (" | " if head else "") + reads


def _f8(rows: list[dict], as_of: str) -> tuple[str, str, list[str]]:
    asof = to_date(as_of) if as_of else None
    dated = [r for r in rows if r.get("issue_date") not in (None, "", "unknown")]
    if not dated or asof is None:
        return "not_applicable", "no issue date or no as_of", []
    future = [r for r in dated if to_date(r["issue_date"]) > asof]
    if future:
        names = ", ".join(f"{r['file_name']} ({r['issue_date']})" for r in future)
        return "pass", f"info only: issue date after as_of {asof.isoformat()}: {names}", ["ISSUE_DATE_AFTER_AS_OF"]
    return "pass", f"all issue dates on or before {asof.isoformat()}", []


def _f9_row(row: dict) -> dict:
    v = row.get("parsed", {}).get("notarized", "")
    if v == "yes":
        return _entry(row, "pass", "notarized: yes")
    if v == "no":
        return _entry(row, "fail", "notarized: no")
    return _entry(row, "unknown", "notarization not stated", "notarized")


# ---------------- F10-F14: members, amount, quantities ----------------

def _norm_name(name: str) -> str:
    s = re.sub(r"\((?:주|유|사|재|가상|fictional)\)|㈜|주식회사|유한회사", "", name or "", flags=re.I)
    s = re.sub(r"\b(?:ltd|limited|inc|co|corp|llc|plc)\b\.?", "", s, flags=re.I)
    return re.sub(r"[\W_]+", "", s.casefold())


def match_member(tag: str, members: list[dict]) -> int | None:
    t = _norm_name(tag)
    if not t:
        return None
    hits = [i for i, m in enumerate(members)
            if (n := _norm_name(m.get("name", ""))) and (n == t or (len(t) >= 2 and (t in n or n in t)))]
    return hits[0] if len(hits) == 1 else None


def required_members(profile: dict) -> int:
    """Number of firms that must each be covered: 1 for a single firm, the JV member count from the
    parser (0 when not stated or when the stated count and the listed names disagree)."""
    part = profile.get("participation", "unknown")
    if part == "single":
        return 1
    if part == "jv":
        return int(profile.get("member_count") or 0)
    return 0


def _f10_row(req: dict, row: dict, profile: dict) -> dict:
    ss = req.get("sign_seal")
    need = required_members(profile)
    base_key = "sealed" if ss == "all_members_seal" else "signed"
    if row.get(base_key) == "no":
        return _entry(row, "fail", f"{base_key} = no")
    if profile.get("participation") == "single":
        res, why = _yn(row, base_key)
        return _entry(row, res, why or "single firm", base_key if res == "unknown" else "")
    parsed = row.get("parsed", {})
    signed_by = parsed.get("signed_by", "")
    count = parsed.get("seal_member_count" if ss == "all_members_seal" else "sign_member_count")
    if signed_by:
        sb = signed_by.casefold()
        if re.search(r"\bonly\b|만\b|단독", sb):
            return _entry(row, "fail", f"signed by: {signed_by}")
        if re.search(r"\ball\b|\beach\b|\bevery\b|\bboth\b|전원|모두", sb):
            return _entry(row, "pass", f"signed by: {signed_by}")
        m = re.search(r"(\d+)", sb)
        if m:
            count = int(m.group(1))
    if count == "all":
        return _entry(row, "pass", "all members")
    if isinstance(count, int):
        if not need:
            return _entry(row, "unknown", f"{count} members signed/sealed; member count not stated", "member_count")
        ok = count >= need
        return _entry(row, "pass" if ok else "fail", f"{count} of {need} members")
    return _entry(row, "unknown", "which members signed/sealed is not stated", "signed_by")


def _f11_row(req: dict, row: dict) -> dict:
    amt = req.get("amount") or {}
    parsed = row.get("parsed", {})
    parts = []
    if _num(amt.get("value")) > 0:
        got = parsed.get("amount")
        cur = (amt.get("currency") or "").casefold()
        if not got:
            parts.append(("unknown", "amount not stated", "amount"))
        elif cur and got["currency"].casefold() != cur:
            parts.append(("unknown", f"currency {got['currency']} vs required {amt['currency']}", "amount"))
        else:
            ok = Decimal(str(got["value"])) >= Decimal(str(amt["value"]))
            parts.append(("pass" if ok else "fail", f"{got['currency']} {got['value']:,.0f} "
                          f"{'>=' if ok else '<'} {amt.get('currency', '')} {_num(amt['value']):,.0f}", ""))
    if _num(amt.get("ratio_pct")) > 0:
        got = parsed.get("amount_ratio")
        if got is None:
            parts.append(("unknown", "amount ratio not stated", "amount_ratio"))
        else:
            ok = Decimal(str(got)) >= Decimal(str(amt["ratio_pct"]))
            parts.append(("pass" if ok else "fail", f"{got:g}% {'>=' if ok else '<'} {_num(amt['ratio_pct']):g}%", ""))
    result = _agg([{"result": p[0]} for p in parts])
    hit = [p for p in parts if p[0] == result]
    return _entry(row, result, "; ".join(p[1] for p in hit), next((p[2] for p in hit if p[2]), ""))


def _base_label(row: dict) -> str:
    label = row.get("doc_label", "")
    member = row.get("parsed", {}).get("member", "")
    if member and label.rstrip().endswith(f"({member})"):
        return label.rstrip()[: -len(member) - 2].rstrip()
    return label.strip()


def _f12(rows: list[dict], manifest: dict) -> tuple[str, str, str]:
    profile = manifest.get("profile") or {}
    need = required_members(profile)
    if not need:
        return "unknown", "number of JV members not stated", "member_count"
    members = profile.get("members") or []
    lead = next((i for i, m in enumerate(members) if m.get("role") == "lead"), None)
    pool = list(rows)
    labels = {_base_label(r) for r in rows if _base_label(r)}
    for r in manifest.get("rows", []):
        if r not in pool and _base_label(r) in labels:
            pool.append(r)
    groups: dict[str, list[dict]] = {}
    for r in pool:
        groups.setdefault(_base_label(r), []).append(r)
    short, unresolved = [], []
    for label, group in groups.items():
        covered = set()
        for r in group:
            tag = r.get("parsed", {}).get("member", "")
            if tag:
                idx = match_member(tag, members)
                if idx is None:
                    unresolved.append(tag)
                else:
                    covered.add("lead" if idx == lead else idx)
            else:
                covered.add("lead")
        if len(covered) < need:
            names = [members[i]["name"] for i in range(len(members))
                     if ("lead" if i == lead else i) not in covered]
            short.append(f"{label or 'files'}: {len(covered)} of {need} members"
                         + (f" (missing {', '.join(names)})" if names else ""))
    if short and unresolved:
        return "unknown", "; ".join(short) + f"; member names not matched: {', '.join(unresolved)}", "member"
    if short:
        return "fail", "; ".join(short), ""
    return "pass", f"{need} of {need} members covered", ""


def _f13(rows: list[dict]) -> tuple[str, str, str]:
    heads = {r["parsed"]["headcount"] for r in rows if "headcount" in r.get("parsed", {})}
    if len(heads) != 1:
        return "unknown", ("number of persons not stated" if not heads else
                           f"different numbers of persons: {sorted(heads)}"), "headcount"
    need = heads.pop()
    sigs = [r["parsed"]["signature_count"] for r in rows if "signature_count" in r.get("parsed", {})]
    if not sigs:
        return "unknown", f"{need} persons; number of signed forms not stated", "signature_count"
    got = sum(sigs)
    if got < need:
        return "fail", f"{got} signed forms for {need} persons", ""
    return "pass", f"{got} signed forms for {need} persons", ""


def _f14(req: dict, profile: dict) -> tuple[str, str, str]:
    rule = req.get("member_rule") or {}
    max_m, lead_min = int(_num(rule.get("max_members"))), _num(rule.get("lead_share_min_pct"))
    part = profile.get("participation", "unknown")
    parts = []
    if max_m > 0:
        n = required_members(profile)
        if not n:
            parts.append(("unknown", "number of members not stated", "member_count"))
        else:
            parts.append(("pass" if n <= max_m else "fail", f"{n} member(s), limit {max_m}", ""))
    if lead_min > 0:
        if part == "single":
            parts.append(("pass", "single firm", ""))
        else:
            lead = next((m for m in profile.get("members") or [] if m.get("role") == "lead"), None)
            share = _num(lead.get("share_pct")) if lead else 0
            if part != "jv" or not share:
                parts.append(("unknown", "lead member share not stated", "lead_share_pct"))
            else:
                parts.append(("pass" if share >= lead_min else "fail", f"lead share {share:g}%, minimum {lead_min:g}%", ""))
    result = _agg([{"result": p[0]} for p in parts])
    hit = [p for p in parts if p[0] == result]
    return result, "; ".join(p[1] for p in hit), next((p[2] for p in hit if p[2]), "")


# ---------------- driver ----------------

def run_file_checks(vreq: dict, rows: list[dict], manifest: dict, *, as_of: str, anchors: dict) -> dict:
    """F1-F14 for one verified requirement against its matched rows (interfaces §4)."""
    req = _req(vreq)
    quote = ((vreq.get("check") or {}).get("quote", "") if isinstance(vreq, dict) else "") or req.get("quote", "")
    profile = manifest.get("profile") or {}
    rows = list(rows or [])
    fmt = _fmt(req)
    general = is_general_rule(req)
    files = _distinct_files(manifest.get("rows", []) if general else rows)
    checks, readings, unknown_fields, codes = [], {}, [], []

    def put(cid: str, result: str, detail: str, entries: list[dict] | None = None, field: str = "",
            extra_codes: list[str] | None = None) -> None:
        checks.append({"id": cid, "result": result, "detail": detail})
        if entries is not None:
            readings[cid] = entries
        if result == "fail":
            codes.append({"F4": "NOT_MERGED", "F12": "QUANTITY_SHORT", "F13": "QUANTITY_SHORT"}.get(cid, f"CHECK_FAILED:{cid}"))
        if result == "unknown":
            fields = [field] if field else []
            for e in entries or []:
                if e["result"] == "unknown":
                    fields += e.get("fields") or ([e["field"]] if e.get("field") else [])
            for f in dict.fromkeys(fields):
                if f not in NOT_BIDDER_FACTS and f not in unknown_fields:
                    unknown_fields.append(f)
                codes.append(f"ATTRIBUTE_UNKNOWN:{f}")
        codes.extend(extra_codes or [])

    def no_rows(cid: str) -> None:
        put(cid, "unknown", "no matched file for this requirement", None, "matched_file")

    def per_row(cid: str, applicable: bool, fn, pool: list[dict]) -> None:
        if not applicable:
            put(cid, "not_applicable", "")
        elif not pool:
            no_rows(cid)
        else:
            entries = [fn(r) for r in pool]
            put(cid, _agg(entries), _summary(entries), entries)

    if fmt.get("extensions"):
        if files:
            entries = _f1(req, files)
            put("F1", _agg(entries), _summary(entries), entries)
        else:
            no_rows("F1")
    else:
        put("F1", "not_applicable", "")
    if _num(fmt.get("max_size_mb")) > 0:
        if files:
            entries = _f2(req, files, manifest)
            put("F2", _agg(entries), _summary(entries), entries)
        else:
            no_rows("F2")
    else:
        put("F2", "not_applicable", "")
    if fmt.get("filename_template"):
        if files:
            entries = _f3(req, files, manifest, quote)
            put("F3", _agg(entries), _summary(entries), entries)
        else:
            no_rows("F3")
    else:
        put("F3", "not_applicable", "")

    if fmt.get("merge_single_pdf") == "yes":
        if rows:
            res, detail = _f4(rows)
            put("F4", res, detail, None, "merged_contents" if res == "unknown" else "")
        else:
            no_rows("F4")
    else:
        put("F4", "not_applicable", "")

    ss = req.get("sign_seal", "not_stated")
    per_row("F5", ss in ("signature", "seal", "signature_and_seal", "seal_corporate_or_usage"),
            lambda r: _f5_row(req, r), rows)
    per_row("F6", req.get("copy_type", "not_stated") != "not_stated" or ss == "certified_copy_mark",
            lambda r: _f6_row(req, r), rows)

    validity = req.get("validity") or {}
    if validity.get("rule_type", "none") != "none":
        if rows:
            entries = [_f7_row(validity, r, anchors or {}, as_of) for r in rows]
            res = _agg(entries)
            extra = []
            for e in entries:
                if e["result"] == res:
                    extra += [c for c in e["reason_codes"] if not c.startswith("ATTRIBUTE_UNKNOWN:")]
                extra += e["info"]
            put("F7", res, _summary(entries), entries, "", extra)
            if res == "unknown":
                for e in entries:
                    if e["result"] == "unknown":
                        codes.extend(c for c in e["reason_codes"] if c.startswith("ATTRIBUTE_UNKNOWN:")
                                     and c.split(":", 1)[1] in RULE_SIDE_FIELDS)
        else:
            no_rows("F7")
    else:
        put("F7", "not_applicable", "")

    res8, det8, info8 = _f8(rows, as_of)
    put("F8", res8, det8, None, "", info8)

    per_row("F9", req.get("notarized") == "required", _f9_row, rows)
    per_row("F10", ss in ("all_members_seal", "all_members_sign"), lambda r: _f10_row(req, r, profile), rows)
    amt = req.get("amount") or {}
    per_row("F11", _num(amt.get("value")) > 0 or _num(amt.get("ratio_pct")) > 0, lambda r: _f11_row(req, r), rows)

    qrule = req.get("quantity_rule", "none")
    if qrule == "per_member":
        if rows:
            res, detail, field = _f12(rows, manifest)
            put("F12", res, detail, None, field)
        else:
            no_rows("F12")
    else:
        put("F12", "not_applicable", "")
    if qrule == "per_person" or ss == "each_person_handwritten":
        if rows:
            res, detail, field = _f13(rows)
            put("F13", res, detail, None, field)
        else:
            no_rows("F13")
    else:
        put("F13", "not_applicable", "")

    rule = req.get("member_rule") or {}
    if _num(rule.get("max_members")) > 0 or _num(rule.get("lead_share_min_pct")) > 0:
        res, detail, field = _f14(req, profile)
        put("F14", res, detail, None, field)
    else:
        put("F14", "not_applicable", "")

    return {"checks": checks, "quantity_short": any(c["id"] in ("F12", "F13") and c["result"] == "fail" for c in checks),
            "unknown_fields": unknown_fields, "reason_codes": list(dict.fromkeys(codes)), "readings": readings,
            "general_rule": general}


def general_rule_findings(vreqs: list[dict], manifest: dict) -> dict:
    """Per-row view of every general file rule (F1-F3 on all rows): {row_id: [{tmp_id, id,
    result, detail}]} for rows that fail or cannot be checked. Rows of one file share the result."""
    out: dict[str, list[dict]] = {}
    files = _distinct_files(manifest.get("rows", []))
    for v in vreqs:
        req = _req(v)
        if not is_general_rule(req):
            continue
        quote = ((v.get("check") or {}).get("quote", "")) or req.get("quote", "")
        fmt = _fmt(req)
        runs = []
        if fmt.get("extensions"):
            runs.append(("F1", _f1(req, files)))
        if _num(fmt.get("max_size_mb")) > 0:
            runs.append(("F2", _f2(req, files, manifest)))
        if fmt.get("filename_template"):
            runs.append(("F3", _f3(req, files, manifest, quote)))
        for cid, entries in runs:
            for e in entries:
                if e["result"] in ("fail", "unknown"):
                    for rid in _row_ids_for(e["file_name"], manifest) or [e["row_id"]]:
                        out.setdefault(rid, []).append({"tmp_id": req.get("tmp_id", ""), "id": cid,
                                                        "result": e["result"], "detail": e["detail"]})
    return out
