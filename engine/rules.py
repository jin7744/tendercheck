"""Status rules S0-S15 (design 09 §5; 08 §1.2): the first rule that matches decides.

The model never sets a status (D3). Its match hint can only move met -> needs-confirmation (S15).
"""
from __future__ import annotations

from datetime import datetime

NC = "needs-confirmation"
RULE_ORDER = ("S0", "S1", "S2", "S3", "S4", "S4'", "S5", "S6", "S7", "S7b", "S8", "S9",
              "S10", "S11", "S12", "S13", "S14", "S15")
INFO_ONLY = ("LOCATION_CORRECTED", "TZ_NOT_STATED", "WEEKDAY_MISMATCH", "ISSUE_DATE_AFTER_AS_OF")
FILE_CATEGORIES = ("document", "signature_seal", "original_copy", "validity")
DEADLINE_CATEGORIES = ("deadline", "submission_method")
FILE_RULE_CATEGORIES = ("file_format", "file_naming", "merge_rule")
FACT_KEYS = ("consortium", "usage_seal", "foreign_entity", "subcontracting", "sme")
# ATTRIBUTE_UNKNOWN fields that are gaps in the source rule, not bidder facts (no bidder question).
RULE_SIDE_FIELDS = ("anchor_date", "period", "required_until", "evidence")
# Field named in ATTRIBUTE_UNKNOWN when an F check is unknown and FileCheck names no field.
CHECK_FIELDS = {"F1": "extension", "F2": "size_mb", "F3": "short_name", "F4": "merged_contents", "F5": "sign_seal",
                "F6": "copy_kind", "F7": "validity", "F9": "notarized", "F10": "signed_by", "F11": "amount",
                "F12": "members", "F13": "headcount", "F14": "members"}
INFO_CHECKS = ("F8",)


def source_missing(bundle: dict | None) -> bool:
    """S0: no source text -> zero requirement rows (the report withholds the checklist)."""
    return not bundle or bool(bundle.get("source_missing")) or not bundle.get("segments")


def kind_of(req: dict, mandatory: str | None = None) -> str:
    """08 §1.3 `kind` (design 09 §4-6): conditions -> conditional, attribute_of -> attribute,
    validity, file_format/file_naming/merge_rule -> file_rule, deadline/submission_method -> deadline,
    else document."""
    mand = mandatory or req.get("mandatory", "mandatory")
    cat = req.get("category", "other")
    if req.get("conditions") or mand == "conditional":
        return "conditional"
    if req.get("attribute_of"):
        return "attribute"
    if cat == "validity":
        return "validity"
    if cat in FILE_RULE_CATEGORIES:
        return "file_rule"
    if cat in DEADLINE_CATEGORIES:
        return "deadline"
    return "document"


def _dedupe(items):
    out = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def _condition(req: dict, profile: dict | None, mand: str, parent: dict | None):
    """(state, unknown fact keys, inherited) — state None when the row has no condition."""
    conds = req.get("conditions") or []
    facts = (profile or {}).get("facts") or {}
    if conds or mand == "conditional":
        unknown, false = [], []
        for c in conds:
            key = c.get("fact_key", "other")
            val = facts.get(key, "unknown") if key in FACT_KEYS else "unknown"
            if val not in ("yes", "no"):
                unknown.append(key if key in FACT_KEYS else "other")
            elif val != c.get("applies_when", "yes"):
                false.append(key)
        if not conds:
            unknown = ["other"]
        if unknown:
            return "unknown", _dedupe(unknown), False
        if false:
            return "not_triggered", [], False
        return "triggered", [], False
    if parent and parent.get("status") == "conditional" and parent.get("condition_state") in ("unknown", "not_triggered"):
        keys = [k.split(":", 1)[1] for k in parent.get("bidder_question_keys", []) if k.startswith("fact:")]
        return parent["condition_state"], keys, True
    return None, [], False


def _match_entry(match: dict | None, tmp_id: str) -> dict:
    if not match:
        return {}
    if "by_req" in match:
        return match["by_req"].get(tmp_id) or {}
    return match


def _parent_rows(match: dict | None, parent_id: str):
    if match and "by_req" in match:
        entry = match["by_req"].get(parent_id)
        return None if entry is None else list(entry.get("row_ids") or [])
    return None


def _parent_absent(parent: dict | None, match: dict | None, parent_id: str, own_rows: list) -> bool:
    if parent is not None:
        return parent.get("status") == "missing" and bool(
            {"NO_MATCHING_FILE", "PARENT_MISSING"} & set(parent.get("reason_codes", [])))
    rows = _parent_rows(match, parent_id)
    if rows is not None:
        return not rows
    return not own_rows


def _aware(iso: str):
    if not iso or "T" not in iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def timing(parsed: dict | None, planned_upload: str = "", as_of: str = "") -> dict:
    """Deadline vs planned upload / as_of, all converted by code (09 §2-5 time-zone rule)."""
    p = parsed or {}
    dl = _aware(p.get("iso_local", "")) if p.get("ok") and p.get("time") else None
    up = _aware(planned_upload)
    now = _aware(as_of)
    out = {"deadline": p.get("iso_local", "") if p.get("ok") else "", "comparable": dl is not None,
           "upload": planned_upload or "", "relation": "", "upload_in_deadline_tz": "",
           "deadline_in_upload_tz": "", "as_of_after_deadline": bool(dl and now and now > dl)}
    if dl is not None and up is not None:
        out["relation"] = "before" if up < dl else "after" if up > dl else "same"
        out["upload_in_deadline_tz"] = up.astimezone(dl.tzinfo).isoformat()
        out["deadline_in_upload_tz"] = dl.astimezone(up.tzinfo).isoformat()
    return out


def _f_codes(file_check: dict) -> set[str]:
    codes = set(file_check.get("reason_codes") or [])
    for c in file_check.get("checks") or []:
        codes |= set(c.get("reason_codes") or [])
    return codes


def decide(vreq: dict, *, file_check: dict | None = None, match: dict | None = None,
           conflict_info: dict | None = None, profile: dict | None = None, as_of: str = "",
           mandatory: str | None = None, parent: dict | None = None, review_flag: bool = False) -> dict:
    """S1-S15 for one verified requirement (S0 is `source_missing`, applied by the report).

    match: the full Match ({'by_req': ...}) or this requirement's entry ({'row_ids', 'status_hint'}).
    conflict_info: {'conflict_id': 'Cn' when this row is a merged conflict row (S3),
                    'depends_on_conflict': 'Cn' when its validity anchors came from a conflict (S4/S4')}.
    parent: the Decision of the `attribute_of` requirement when already decided (S11; optional).
    Decision = interfaces §6 keys + rule, agency_question_keys, info_codes, evidence_row_ids, timing.
    `depends_on` is the parent's tmp_id; report.build_report maps it to R01.."""
    req, chk = vreq["req"], vreq["check"]
    tmp_id = req.get("tmp_id") or chk.get("tmp_id", "")
    mand = mandatory or req.get("mandatory") or "mandatory"
    ci = conflict_info or {}
    fc = file_check or {}
    entry = _match_entry(match, tmp_id)
    rows = list(entry.get("row_ids") or [])
    attr_parent = req.get("attribute_of") or None
    evidence = rows
    if attr_parent and not rows:
        evidence = _parent_rows(match, attr_parent) or []
    fcodes = _f_codes(fc)
    info = _dedupe([c for c in list(chk.get("info_codes") or []) + sorted(fcodes) if c in INFO_ONLY])
    cond_state, unknown_facts, inherited = _condition(req, profile, mand, parent)
    dl_timing = None

    def done(rule, status, codes, *, cond=cond_state, depends_on=None, dep_conflict=None, bidder=(), agency=(),
             ev=None):
        return {"status": status, "condition_state": cond, "reason_codes": _dedupe(codes),
                "depends_on": depends_on, "depends_on_conflict": dep_conflict,
                "bidder_question_keys": _dedupe(bidder), "rule": rule, "agency_question_keys": _dedupe(agency),
                "info_codes": info, "evidence_row_ids": list(evidence if ev is None else ev), "timing": dl_timing}

    if not chk.get("verified"):
        return done("S1", NC, ["UNVERIFIED_QUOTE"])
    if chk.get("grounded") is not True:
        codes = [c for c in chk.get("reason_codes") or [] if c in ("FIELD_NOT_GROUNDED", "DATE_MISMATCH", "DATE_UNPARSED")]
        return done("S2", NC, codes or ["FIELD_NOT_GROUNDED"])
    if ci.get("conflict_id"):
        return done("S3", NC, ["CONFLICT"], dep_conflict=ci["conflict_id"])
    dep = ci.get("depends_on_conflict")
    if "CONFLICT_DEPENDENT" in fcodes:
        codes = ["CONFLICT_DEPENDENT"] + (["COUNTING_AMBIGUOUS"] if "COUNTING_AMBIGUOUS" in fcodes else [])
        return done("S4", NC, codes, dep_conflict=dep,
                    agency=["counting"] if "COUNTING_AMBIGUOUS" in fcodes else [])
    extra, dep_c = [], None
    if dep and "SAME_UNDER_ALL_READINGS" in fcodes:
        extra, dep_c = ["SAME_UNDER_ALL_READINGS"], dep
    elif "SAME_UNDER_ALL_READINGS" in fcodes:
        extra = ["SAME_UNDER_ALL_READINGS"]

    if cond_state == "unknown":
        return done("S5", "conditional", ["CONDITION_UNKNOWN"], bidder=[f"fact:{k}" for k in unknown_facts],
                    depends_on=attr_parent if inherited else None)
    if cond_state == "not_triggered":
        return done("S6", "conditional", ["NOT_TRIGGERED"], depends_on=attr_parent if inherited else None)
    if mand == "unclear":
        return done("S7", NC, ["MANDATORY_UNCLEAR"])
    if mand == "optional" and not rows:
        return done("S7b", "conditional", ["OPTIONAL_ITEM"], cond="unknown")

    dl = req.get("deadline") or {}
    if req.get("category") in DEADLINE_CATEGORIES or dl.get("kind", "none") != "none":
        upload = (profile or {}).get("planned_upload", "")
        dl_timing = timing(chk.get("deadline"), upload, as_of)
        if dl.get("kind") == "submission" and upload and (chk.get("deadline") or {}).get("ok"):
            rel = dl_timing["relation"]
            if rel == "before":
                return done("S8", "met", ["UPLOAD_BEFORE_DEADLINE"], ev=[])
            if rel == "after":
                return done("S8", "error", ["UPLOAD_AFTER_DEADLINE"], ev=[])
            return done("S8", NC, ["ACTION_PENDING"], ev=[])
        if dl_timing["as_of_after_deadline"]:
            return done("S9", "error", ["DEADLINE_PASSED"], ev=[])
        return done("S9", NC, ["ACTION_PENDING"], ev=[])

    if req.get("category") in FILE_CATEGORIES and not attr_parent and not rows:
        return done("S10", "missing", ["NO_MATCHING_FILE"], ev=[])
    if attr_parent and _parent_absent(parent, match, attr_parent, rows):
        return done("S11", "missing", ["PARENT_MISSING"], depends_on=attr_parent, ev=[])

    checks = [c for c in fc.get("checks") or [] if c.get("id") not in INFO_CHECKS]
    if fc.get("quantity_short") or any(c.get("id") in ("F12", "F13") and c.get("result") == "fail" for c in checks):
        return done("S12", "missing", ["QUANTITY_SHORT"] + extra, dep_conflict=dep_c)
    fails = [c["id"] for c in checks if c.get("result") == "fail"]
    if fails:
        codes = ["NOT_MERGED" if f == "F4" else f"CHECK_FAILED:{f}" for f in fails]
        return done("S13", "error", codes + extra, dep_conflict=dep_c)

    fields = list(fc.get("unknown_fields") or [])
    fields += [c.split(":", 1)[1] for c in sorted(fcodes) if c.startswith("ATTRIBUTE_UNKNOWN:")]
    unknown_checks = [c["id"] for c in checks if c.get("result") == "unknown"]
    counting = "COUNTING_AMBIGUOUS" in fcodes
    if unknown_checks and not fields:
        fields = [CHECK_FIELDS.get(cid, cid.lower()) for cid in unknown_checks if not (counting and cid == "F7")]
    if fields or unknown_checks or counting:
        fields = _dedupe(fields)
        codes = [f"ATTRIBUTE_UNKNOWN:{f}" for f in fields] + (["COUNTING_AMBIGUOUS"] if counting else [])
        return done("S14", NC, codes + extra, dep_conflict=dep_c,
                    bidder=[f"attr:{f}" for f in fields if f not in RULE_SIDE_FIELDS],
                    agency=["counting"] if counting else [])
    if not evidence and not any(c.get("result") == "pass" for c in checks):
        return done("S14", NC, ["ATTRIBUTE_UNKNOWN:evidence"] + extra, dep_conflict=dep_c)

    if entry.get("status_hint") == NC:
        return done("S15", NC, ["MODEL_FLAGGED"] + extra, dep_conflict=dep_c)
    if review_flag:
        return done("S15", NC, ["REVIEW_FLAG"] + extra, dep_conflict=dep_c)
    return done("S15", "met", ["ALL_CHECKS_PASSED"] + extra, dep_conflict=dep_c)
