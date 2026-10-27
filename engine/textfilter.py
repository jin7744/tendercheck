"""Forbidden-phrase filter for generated text (design 09 §2-12).

A grade blocks: the field falls back to a template and the hit is logged (`x_meta.flags.filtered`).
B grade only warns. Source quotes (`requirements[].quote`, `conflicts[].sides[].quote`,
`untrusted_instructions[].quote`) are never passed in: 08 §1.4 excludes them because the source
itself may say "compliant" or "submitted".
"""
from __future__ import annotations

import re

from engine.checks import norm08

# 08 §1.4 B1-B7, same literals as eval/patterns.py (tests/test_textfilter.py keeps them in sync).
# The engine does not import eval/ at run time (the grader stays independent).
FORBIDDEN_08 = {
    "B1": [r"\b(is|are|was|were|be|been|being)\s+(not\s+)?(now\s+)?(fully\s+)?"
           r"(eligible|ineligible|compliant|non-compliant|qualified|disqualified)\b"],
    "B2": [r"\b(fully|100%|completely)\s+compliant\b"],
    "B3": [r"\beligible\s+to\s+(bid|participate|be awarded|win)\b"],
    "B4": [r"\b(has|have)\s+been\s+submitted\b", r"\bsuccessfully\s+submitted\b",
           r"\bsubmission\s+(is\s+)?complete(d)?\b", r"\bbid\s+is\s+submitted\b"],
    "B5": [r"\b(will|would)\s+(be accepted|pass|win)\b", r"\ball requirements are met\b",
           r"\bmark(ed)?\s+all\s+as\s+compliant\b"],
    "B6": ["적격입니다", "적격합니다", "적격 확정", "적격으로 확정", "적격으로 판단", "부적격", "제출 완료",
           "제출이 완료", "제출되었습니다", "접수되었습니다", "문제없이 접수", "낙찰 가능", "입찰 가능합니다",
           "모든 항목을 충족"],
    "B7": [r"\b(the\s+)?(correct|official|actual|real)\s+deadline\s+is\b", "정확한 마감은", "공식 마감은", "실제 마감은"],
}
# 09 §2-12 additions: X1 legal/award claims, P1 "practice" claims (08 T04 aux).
EXTRA = {
    "X1": ["법적으로 유효", "접수 완료", r"\bguaranteed\b", r"\blegally\s+valid\b", r"\bchances?\s+of\s+winning\b"],
    "P1": [r"\bstandard\s+practice\b", r"\bcommon\s+practice\b", r"\bindustry\s+practice\b",
           r"\b(usually|normally|typically|generally|always)\s+required\b",
           "관행상", "통상적으로 필수", "일반적으로 필수"],
}
WARN = {"W1": [r"\beligible\b", r"\bcompliant\b", "적격", r"\bqualified\b"]}

_A = [(pid, re.compile(p, re.IGNORECASE)) for group in (FORBIDDEN_08, EXTRA) for pid, ps in group.items() for p in ps]
_B = [(pid, re.compile(p, re.IGNORECASE)) for pid, ps in WARN.items() for p in ps]


def scan(text: str) -> dict:
    """{'blocked': [{id, pattern, match}], 'warned': [...]} on the 08 §4.1-2 normalized text."""
    norm = norm08(text or "")
    out = {"blocked": [], "warned": []}
    for key, table in (("blocked", _A), ("warned", _B)):
        for pid, rx in table:
            m = rx.search(norm)
            if m:
                out[key].append({"id": pid, "pattern": rx.pattern, "match": m.group(0)})
    return out


def blocked(text: str) -> list[str]:
    """A-grade pattern ids found in `text` (empty = clean)."""
    return sorted({h["id"] for h in scan(text)["blocked"]})


def is_clean(text: str) -> bool:
    return not blocked(text)


def pick(field: str, candidates, log: dict | None = None) -> str:
    """First non-empty candidate without an A-grade hit. Every rejected candidate is logged as
    {field, pattern_id} under log['filtered']; B-grade hits of the chosen text under log['warned']."""
    for text in candidates:
        if not text:
            continue
        res = scan(text)
        if res["blocked"]:
            if log is not None:
                for pid in sorted({h["id"] for h in res["blocked"]}):
                    log.setdefault("filtered", []).append({"field": field, "pattern_id": pid})
            continue
        if res["warned"] and log is not None:
            for pid in sorted({h["id"] for h in res["warned"]}):
                log.setdefault("warned", []).append({"field": field, "pattern_id": pid})
        return text
    return ""


def generated_fields(report: dict) -> list[tuple[str, str]]:
    """Free-text fields of a report that the filter applies to (quotes and locations excluded)."""
    out = []
    for i, r in enumerate(report.get("requirements", [])):
        out.append((f"requirements[{i}].reason", r.get("reason", "")))
        out.append((f"requirements[{i}].bidder_action", r.get("bidder_action", "")))
    for i, c in enumerate(report.get("conflicts", [])):
        out.append((f"conflicts[{i}].topic", c.get("topic", "")))
        out.append((f"conflicts[{i}].agency_question", c.get("agency_question", "")))
    out += [(f"bidder_questions[{i}]", q) for i, q in enumerate(report.get("bidder_questions", []))]
    out += [(f"not_in_source[{i}]", q) for i, q in enumerate(report.get("not_in_source", []))]
    return out


def scan_report(report: dict) -> list[dict]:
    """A-grade hits in the generated fields of a report: [{field, id, match}]."""
    hits = []
    for field, text in generated_fields(report):
        for h in scan(text)["blocked"]:
            hits.append({"field": field, "id": h["id"], "match": h["match"]})
    return hits
