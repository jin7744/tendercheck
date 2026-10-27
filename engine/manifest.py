"""N2 manifest parsing (design 09 §2-3 last bullet, §4-2; interfaces §1).

Every line is masked with engine.mask before it is parsed or stored, so no field of the
returned Manifest can carry an original sensitive value. The first label line ("[A-M2] ...")
is split off: it names the test category and must never reach a model.
"""
from __future__ import annotations

import re
import unicodedata

from engine.dates import parse_datetime_text
from engine.mask import mask_text, merge_counts

LABEL_RE = re.compile(r"^\s*\[[^\]\n]+\]")
UNKNOWN_TOKENS = {"(기재 없음)", "기재 없음", "(not stated)", "not stated", "확인 안 됨", "(확인 안 됨)",
                  "미기재", "(미기재)", "unknown", "(unknown)", "?"}
NONE_TOKENS = {"-", "–", "—", "없음", "none", "n/a"}
MERGED_TOKENS = {"(병합)", "병합", "(merged)", "merged"}
YES_WORDS = {"yes", "y", "true", "o", "예", "있음", "유"}
NO_WORDS = {"no", "n", "false", "x", "아니오", "아니요", "없음", "무", "-", "–", "—", "none"}


def _key(s: str) -> str:
    return re.sub(r"[\s_()·]", "", s.casefold())


HEADER_KEYS = {
    "company": {"bidder", "company", "업체", "업체명", "입찰자", "입찰참가자"},
    "short_name": {"shortname", "파일명용업체명", "약칭", "파일명용약칭"},
    "participation": {"biddingas", "participation", "참가형태", "참가방식"},
    "seal_type": {"seal", "sealtype", "날인인감", "인감", "날인인감종류"},
    "planned_upload": {"plannedupload", "uploadplanned", "업로드예정", "업로드예정시각", "제출예정", "제출예정시각"},
}
COLUMNS = {
    "doc_label": {"서류", "서류명", "document", "doc"},
    "file_name": {"file", "filename", "제출파일명", "파일명", "파일", "서류또는파일"},
    "declared_format": {"format", "형식"},
    "size": {"sizemb", "size", "용량mb", "용량"},
    "signed": {"signed", "서명"},
    "sealed": {"seal", "sealed", "날인"},
    "sign_seal": {"서명/날인", "signed/seal", "sign/seal", "서명날인"},
    "issue_date": {"issuedate", "발급일", "issued"},
    "notes": {"notes", "note", "비고", "remarks"},
}
_COL_BY_KEY = {k: col for col, keys in COLUMNS.items() for k in keys}
_HDR_BY_KEY = {k: f for f, keys in HEADER_KEYS.items() for k in keys}

_ISO_DATE = r"\d{4}-\d{1,2}-\d{1,2}"
NOTE_RES = {
    "notarized": re.compile(r"notari[sz]ed\s*[:：]\s*(yes|no)\b|공증\s*[:：]?\s*(예|아니오|있음|없음|완료|yes|no)", re.I),
    "certified_copy": re.compile(r"\bcertified\s+(?:true\s+)?copy\b", re.I),
    "copy": re.compile(r"\bcopy\b|사본", re.I),
    "original": re.compile(r"\boriginal\b|원본(?!대조)", re.I),
    "valid_until": re.compile(r"valid\s+(?:until|through|thru|to)\s*[:：]?\s*([^;|]+)|만료일\s*[:：]?\s*([^,;|]+)", re.I),
    "period": re.compile(rf"(?:보증기간|유효기간|validity\s+period|valid\s+from|period)\s*[:：]?\s*({_ISO_DATE})\s*"
                         rf"(?:~|∼|〜|–|—|\bto\b|-(?=\s))\s*({_ISO_DATE})", re.I),
    "amount": re.compile(r"amount\s*[:：]?\s*(?:of\s+)?([A-Z]{3})\s*([\d,]+(?:\.\d+)?)", re.I),
    "amount_krw": re.compile(r"보증금액\s*[:：]?\s*([\d,]+)\s*원"),
    "ratio": re.compile(r"(?:입찰금액|bid\s+(?:price|amount))\s*의?\s*(\d+(?:\.\d+)?)\s*%"
                        r"|(\d+(?:\.\d+)?)\s*%\s*of\s+the\s+bid|100\s*분의\s*(\d+(?:\.\d+)?)", re.I),
    "merged_contents": re.compile(r"one\s+merged\s+file\s*[:：]\s*([^;|]+)", re.I),
    "merged_file": re.compile(r"병합\s*파일|merged\s+file", re.I),
    "headcount": re.compile(r"투입\s*예정\s*인력\s*(\d+)\s*명|(\d+)\s*(?:persons|people|staff|personnel)\b", re.I),
    "signature_count": re.compile(r"서명\s*(\d+)\s*매|(\d+)\s*매|(\d+)\s*(?:signed\s+)?(?:forms|sheets|signatures)\b", re.I),
    "member": re.compile(r"\bmember\s*[:：]\s*([^;|]+)", re.I),
    "lead_member": re.compile(r"\blead\s+member\b|대표사", re.I),
    "signed_by": re.compile(r"signed\s+by\s*[:：]\s*([^;|]+)", re.I),
    "member_mark": re.compile(r"^\s*구성원\s*$|^\s*member\s*$|구성원\s*서류", re.I),
    "seal_members": re.compile(r"구성원\s*(\d+)\s*개사\s*(?:인감\s*)?날인|구성원\s*(전원)\s*(?:의\s*)?(?:인감\s*)?날인"),
    "sign_members": re.compile(r"구성원\s*(\d+)\s*개사\s*서명|구성원\s*(전원)\s*(?:의\s*)?서명"),
}
_NEG = re.compile(r"없음|안\s*함|미\s*(?:날인|서명|표기)|누락|\bno\b|\bnone\b|\bnot\b|\bwithout\b", re.I)


def _is_unknown(v: str) -> bool:
    return v.strip().casefold() in {t.casefold() for t in UNKNOWN_TOKENS}


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split on `sep` outside parentheses/brackets."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([（【":
            depth += 1
        elif ch in ")]）】" and depth:
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return [p for p in out if p]


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    return [c.strip() for c in s.split("|")]


def _table_columns(line: str) -> list[str] | None:
    if "|" not in line:
        return None
    cols = [_COL_BY_KEY.get(_key(c), "") for c in _cells(line)]
    if cols and cols[-1] == "" and line.rstrip().endswith("|"):
        cols = cols[:-1]
    known = [c for c in cols if c]
    if "file_name" in known and len(known) >= 3:
        return cols
    return None


def ext_of(file_name: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,8})\s*$", file_name)
    return m.group(1).lower() if m else ""


def _size(cell: str):
    c = cell.strip()
    if c.casefold() in {t.casefold() for t in MERGED_TOKENS}:
        return "merged"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:mb|MB|㎆)?", c)
    if m:
        return float(m.group(1))
    return "unknown"


def _yes_no(cell: str) -> str:
    c = cell.strip().casefold()
    if c in YES_WORDS:
        return "yes"
    if c in NO_WORDS:
        return "no"
    return "unknown"


def _issue_date(cell: str) -> str:
    c = cell.strip()
    if not c or _is_unknown(c) or c in NONE_TOKENS:
        return "unknown"
    p = parse_datetime_text(c)
    if p["ok"] and not p["time"]:
        return p["date"]
    return "unknown"


def _date_of(text: str) -> str:
    p = parse_datetime_text(text.strip())
    return p["date"] if p["ok"] else ""


def _number(s: str) -> float:
    return float(s.replace(",", ""))


def read_sign_seal(text: str) -> tuple[str, str, dict]:
    """(signed, sealed, parsed extras) from a KO 서명/날인 cell. '-' means none (09 §2-3)."""
    t = text.strip()
    if t in NONE_TOKENS:
        return "no", "no", {}
    if not t or _is_unknown(t):
        return "unknown", "unknown", {}
    signed, sealed, extra = set(), set(), {}
    kinds: list[str] = []
    for part in re.split(r"[,，;]", t):
        p = part.strip()
        if not p:
            continue
        neg = bool(_NEG.search(p))
        if re.search(r"원본대조필|certified", p, re.I):
            extra["certified_mark"] = "no" if neg else "yes"
            continue
        if re.search(r"날인|인감|직인|seal|stamp", p, re.I):
            sealed.add("no" if neg else "yes")
            if not neg:
                if "사용인감" in p:
                    kinds.append("usage")
                if "법인인감" in p:
                    kinds.append("corporate")
                if "직인" in p:
                    kinds.append("official_stamp")
                rest = p.replace("사용인감", "").replace("법인인감", "")
                if "인감" in rest and not kinds:
                    kinds.append("unspecified")
        if re.search(r"서명|sign", p, re.I):
            signed.add("no" if neg else "yes")
    if kinds:
        extra["seal_kinds"] = kinds
    one = lambda s: s.pop() if len(s) == 1 else "unknown"  # noqa: E731
    return one(signed), one(sealed), extra


def parse_notes(notes: str, sign_seal_text: str = "", doc_label: str = "") -> dict:
    """Code-read "key: value" facts (09 §2-3). Keys that cannot be read are absent."""
    out: dict = {}
    work = notes
    m = NOTE_RES["signed_by"].search(work)
    if m:
        out["signed_by"] = m.group(1).strip()
        work = work[:m.start()] + work[m.end():]
    m = NOTE_RES["notarized"].search(work)
    if m:
        v = (m.group(1) or m.group(2)).casefold()
        out["notarized"] = "yes" if v in ("yes", "예", "있음", "완료") else "no"
    if NOTE_RES["certified_copy"].search(work):
        out["copy_kind"] = "certified_copy"
    elif NOTE_RES["copy"].search(work):
        out["copy_kind"] = "copy"
    elif NOTE_RES["original"].search(work):
        out["copy_kind"] = "original"
    m = NOTE_RES["period"].search(work)
    if m:
        f, t = _date_of(m.group(1)), _date_of(m.group(2))
        if f and t:
            out["guarantee_period"] = {"from": f, "to": t}
    m = NOTE_RES["valid_until"].search(work)
    if m:
        d = _date_of(m.group(1) or m.group(2))
        if d:
            out["valid_until"] = d
    m = NOTE_RES["amount"].search(work)
    if m:
        out["amount"] = {"currency": m.group(1).upper(), "value": _number(m.group(2))}
    else:
        m = NOTE_RES["amount_krw"].search(work)
        if m:
            out["amount"] = {"currency": "KRW", "value": _number(m.group(1))}
    m = NOTE_RES["ratio"].search(work)
    if m:
        out["amount_ratio"] = float(next(g for g in m.groups() if g))
    m = NOTE_RES["merged_contents"].search(work)
    if m:
        out["merged_contents"] = [s.strip() for s in m.group(1).split(",") if s.strip()]
        out["merged_file"] = "yes"
    elif NOTE_RES["merged_file"].search(work):
        out["merged_file"] = "yes"
    m = NOTE_RES["headcount"].search(work)
    if m:
        out["headcount"] = int(m.group(1) or m.group(2))
    for src in (sign_seal_text, work):
        m = NOTE_RES["signature_count"].search(src) if src else None
        if m:
            out["signature_count"] = int(next(g for g in m.groups() if g))
            break
    m = NOTE_RES["member"].search(work)
    if m:
        out["member"] = m.group(1).strip()
    elif NOTE_RES["member_mark"].search(work):
        lm = re.search(r"\(([^()]+)\)\s*$", doc_label)
        if lm:
            out["member"] = lm.group(1).strip()
    if "member" not in out and NOTE_RES["lead_member"].search(work):
        out["member_role"] = "lead"
    for key, rx in (("seal_member_count", "seal_members"), ("sign_member_count", "sign_members")):
        m = NOTE_RES[rx].search(sign_seal_text) or NOTE_RES[rx].search(work)
        if m:
            out[key] = "all" if m.group(2) else int(m.group(1))
    return out


def _parse_member(item: str) -> dict:
    s, role, share = item.strip(), "member", 0.0
    while True:
        m = re.search(r"\(([^()]*)\)\s*$", s)
        if not m or not re.search(r"lead|대표|지분|share|%|가상|fictional", m.group(1), re.I):
            break
        ann = m.group(1)
        if re.search(r"\blead\b|대표사|대표", ann, re.I):
            role = "lead"
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%", ann)
        if pm:
            share = float(pm.group(1))
        s = s[:m.start()].rstrip()
    return {"name": s, "role": role, "share_pct": share}


def parse_participation(value: str) -> tuple[str, int, list[dict], list[str]]:
    """(participation, member_count (0 = not stated), members, warnings)."""
    v = value.strip()
    if not v or _is_unknown(v):
        return "unknown", 0, [], []
    low = v.casefold()
    if re.search(r"\bjv\b|joint\s+venture|consortium|공동수급|공동이행|분담이행|컨소시엄", low):
        kind = "jv"
    elif re.search(r"\bsingle\b|\bsole\b|단독", low):
        return "single", 1, [], []
    else:
        return "unknown", 0, [], [f"participation not understood: {v}"]
    warnings: list[str] = []
    stated = 0
    m = re.search(r"(\d+)\s*(?:members?|member\s+firms|firms|개사|社)", v, re.I)
    if m:
        stated = int(m.group(1))
    members: list[dict] = []
    head, sep, tail = v.partition(":") if ":" in v else v.partition("：")
    if sep and tail.strip():
        members = [_parse_member(x) for x in split_top(tail)]
    count = stated or len(members)
    if stated and members and stated != len(members):
        warnings.append(f"member count {stated} differs from {len(members)} listed members")
        count = 0
    return kind, count, members, warnings


def parse_seal_type(value: str) -> str:
    v = value.strip()
    if not v or _is_unknown(v):
        return "unknown"
    if re.search(r"사용인감|usage|authori[sz]ed[- ]use", v, re.I):
        return "usage"
    if re.search(r"법인인감|corporate|registered", v, re.I):
        return "corporate"
    return "unknown"


def _fact(v: str, yes: str, no: str) -> str:
    return "yes" if v == yes else "no" if v == no else "unknown"


def parse_manifest(text: str, *, lang: str) -> dict:
    """N2: bidder manifest text (08 §2.3/§3.3 format) -> Manifest (interfaces §1)."""
    if lang not in ("en", "ko"):
        raise ValueError(f"lang must be 'en' or 'ko', got {lang!r}")
    text = unicodedata.normalize("NFC", (text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")
    counts: list[dict] = []
    masked: list[tuple[str, bool]] = []
    for ln in lines:
        r = mask_text(ln)
        counts.append(r.counts)
        masked.append((r.text, bool(r.counts)))

    label_line, start = "", 0
    for i, (ln, _) in enumerate(masked):
        if ln.strip():
            if LABEL_RE.match(ln):
                label_line, start = ln.strip(), i + 1
            break

    profile = {"company": "", "short_name": "", "participation": "unknown", "members": [], "member_count": 0,
               "seal_type": "unknown", "planned_upload": "",
               "facts": {"consortium": "unknown", "usage_seal": "unknown", "foreign_entity": "unknown",
                         "subcontracting": "unknown", "sme": "unknown"}}
    rows: list[dict] = []
    warnings: list[str] = []
    rendered: list[str] = []
    columns: list[str] | None = None

    for idx in range(start, len(masked)):
        ln, had_pii = masked[idx]
        if not ln.strip():
            continue
        if columns is None:
            cols = _table_columns(ln)
            if cols is not None:
                columns = cols
                rendered.append("row_id | " + ln.strip())
                continue
            rendered.append(ln.strip())
            _header_line(ln, profile, warnings)
            continue
        if "|" not in ln or re.fullmatch(r"[\s|:\-]+", ln):
            warnings.append(f"line not parsed: {ln.strip()}")
            rendered.append(ln.strip())
            continue
        row = _row(ln, columns, len(rows) + 1, had_pii, lang, warnings)
        rows.append(row)
        rendered.append(f"{row['row_id']} | " + ln.strip())

    if columns is None:
        warnings.append("no table header found")
    _assign_bundles(rows)
    profile["facts"]["consortium"] = _fact(profile["participation"], "jv", "single")
    profile["facts"]["usage_seal"] = _fact(profile["seal_type"], "usage", "corporate")
    return {"label_line": label_line, "lang": lang, "profile": profile, "rows": rows,
            "pii_counts": merge_counts(*counts), "warnings": warnings, "masked_text": "\n".join(rendered)}


def _header_line(ln: str, profile: dict, warnings: list[str]) -> None:
    for seg in _cells(ln) if "|" in ln else [ln]:
        key, sep, value = seg.partition(":")
        if not sep:
            key, sep, value = seg.partition("：")
        field = _HDR_BY_KEY.get(_key(key)) if sep else None
        if field is None:
            warnings.append(f"header not parsed: {seg.strip()}")
            continue
        value = value.strip()
        if field == "company":
            parts = split_top(value)
            profile["company"] = parts[0] if parts else ""
        elif field == "short_name":
            profile["short_name"] = "" if _is_unknown(value) else value
        elif field == "participation":
            kind, count, members, warns = parse_participation(value)
            profile.update(participation=kind, member_count=count, members=members)
            warnings.extend(warns)
        elif field == "seal_type":
            profile["seal_type"] = parse_seal_type(value)
        elif field == "planned_upload":
            p = parse_datetime_text(value) if value and not _is_unknown(value) else None
            if p and p["ok"] and p["time"] and p["offset"]:
                profile["planned_upload"] = p["iso_local"]
            elif value and not _is_unknown(value):
                warnings.append(f"planned upload has no readable date, time and zone: {value}")


def _row(ln: str, columns: list[str], n: int, had_pii: bool, lang: str, warnings: list[str]) -> dict:
    cells = _cells(ln)
    if len(cells) > len(columns):
        if all(not c for c in cells[len(columns):]):
            cells = cells[:len(columns)]
        else:
            warnings.append(f"M{n}: {len(cells)} cells for {len(columns)} columns; extra cells joined into the last")
            cells = cells[:len(columns) - 1] + [" | ".join(cells[len(columns) - 1:])]
    cells += [""] * (len(columns) - len(cells))
    val = {c: cells[i] for i, c in enumerate(columns) if c}
    file_name = val.get("file_name", "")
    sign_seal_text = val.get("sign_seal", "")
    if "sign_seal" in val:
        signed, sealed, extra = read_sign_seal(sign_seal_text)
    else:
        signed, sealed, extra = _yes_no(val.get("signed", "")), _yes_no(val.get("sealed", "")), {}
    notes = val.get("notes", "")
    doc_label = val.get("doc_label", "")
    parsed = parse_notes(notes, sign_seal_text, doc_label)
    for k, v in extra.items():
        parsed.setdefault(k, v)
    return {"row_id": f"M{n}", "doc_label": doc_label, "file_name": file_name, "ext": ext_of(file_name),
            "declared_format": val.get("declared_format", ""), "size_mb": _size(val.get("size", "")),
            "signed": signed, "sealed": sealed, "sign_seal_text": sign_seal_text,
            "issue_date": _issue_date(val.get("issue_date", "")), "notes": notes, "parsed": parsed,
            "bundle": "", "scan_ref": "", "pii": had_pii}


def _assign_bundles(rows: list[dict]) -> None:
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        if r["file_name"]:
            by_file.setdefault(r["file_name"], []).append(r)
    n = 0
    for name, group in by_file.items():
        merged = len(group) > 1 or any(r["size_mb"] == "merged" or r["parsed"].get("merged_file") == "yes"
                                       for r in group)
        if merged:
            n += 1
            for r in group:
                r["bundle"] = f"MB{n}"


def bundle_rows(row: dict, manifest: dict) -> list[dict]:
    """All rows of the merged file holding `row` (itself only when not merged)."""
    if not row.get("bundle"):
        return [row]
    return [r for r in manifest["rows"] if r.get("bundle") == row["bundle"]]
