"""Single source for the machine contracts in contracts/*.json.

Run: python -X utf8 contracts/build_contracts.py   (rewrites the JSON files; commit both)

Model-output schemas follow strict json_schema rules: every object has
additionalProperties=false and lists every key in `required`; no recursion; no null
(absence is "" / 0 / "not_stated" / "none" / []). `pattern`/`maxLength` are not used in the
model schemas because provider support is unverified - those limits are enforced in code.
The model never outputs a status: only a two-value `status_hint` in X-Match.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent


def obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


S = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}

LOCATION = obj({"doc_id": S, "seg_id": S})

NOT_REQ_REASONS = ("background", "definition", "duplicate", "not_submission_related", "instruction_to_ai")

REQ = obj({
    "tmp_id": S,
    "category": enum("document", "deadline", "submission_method", "file_format", "file_naming",
                     "merge_rule", "signature_seal", "original_copy", "validity", "other"),
    "subject": S,
    "requirement": S,
    "quote": S,
    "location": LOCATION,
    "mandatory": enum("mandatory", "conditional", "optional", "unclear"),
    "conditions": arr(obj({
        "condition_quote": S,
        "fact_key": enum("consortium", "usage_seal", "foreign_entity", "subcontracting", "sme", "other"),
        "applies_when": enum("yes", "no"),
    })),
    "attribute_of": S,
    "doc_code": S,
    "quantity_rule": enum("none", "per_member", "per_person"),
    "member_rule": obj({"max_members": INT, "lead_share_min_pct": NUM}),
    "amount": obj({"currency": S, "value": NUM, "ratio_pct": NUM}),
    "deadline": obj({
        "kind": enum("none", "submission", "questions", "clarification", "opening", "other"),
        "raw": S, "iso_local": S, "tz_quote": S,
    }),
    "format": obj({
        "extensions": arr(S),
        "max_size_mb": NUM,
        "filename_template": S,
        "merge_single_pdf": enum("not_stated", "yes"),
        "merge_scope": arr(S),
    }),
    "sign_seal": enum("not_stated", "signature", "seal", "signature_and_seal", "seal_corporate_or_usage",
                      "all_members_seal", "all_members_sign", "each_person_handwritten", "certified_copy_mark"),
    "copy_type": enum("not_stated", "original", "copy_allowed", "certified_copy"),
    "notarized": enum("not_stated", "required"),
    "validity": obj({
        "rule_type": enum("none", "valid_for_from_issue", "issued_within_before_anchor",
                          "valid_until_date", "valid_at_least_after_anchor"),
        "raw": S, "months": INT, "days": INT, "until_iso": S,
        "anchor_event": enum("not_stated", "submission_deadline", "notice_date", "other"),
    }),
})

MODEL_SCHEMAS = {
    "x_extract": obj({
        "requirements": arr(REQ),
        "uncertain_segments": arr(obj({
            "seg_id": S,
            "reason": enum("garbled_text", "table_layout", "ambiguous_reference", "cross_reference"),
            "note": S,
        })),
        "not_requirements": arr(obj({"seg_id": S, "reason": enum(*NOT_REQ_REASONS)})),
        "injection_suspects": arr(obj({"seg_id": S, "quote": S})),
    }),
    "x_fix": obj({
        "fixes": arr(obj({
            "tmp_id": S, "action": enum("requote", "retract"), "quote": S, "location": LOCATION, "note": S,
        })),
    }),
    "x_coverage": obj({
        "new_requirements": arr(REQ),
        "skips": arr(obj({"seg_id": S, "reason": enum(*NOT_REQ_REASONS)})),
    }),
    "x_ask": obj({"asked_items": arr(obj({"item": S, "search_terms": arr(S)}))}),
    "x_match": obj({
        "matches": arr(obj({
            "req_id": S, "row_ids": arr(S), "rationale": S,
            "status_hint": enum("no_concern", "needs-confirmation"), "hint_reason": S,
        })),
        "attribute_findings": arr(obj({
            "row_id": S,
            "attribute": enum("signed_by", "seal_kind", "member", "merged_contents", "headcount",
                              "signature_count", "other"),
            "value": S, "note_quote": S,
        })),
        "unmatched_rows": arr(S),
    }),
    "x_conflict": obj({
        "adjudications": arr(obj({
            "candidate_id": S, "same_obligation": enum("yes", "no", "unclear"),
            "distinguishing_quote_a": S, "distinguishing_quote_b": S, "explanation": S,
        })),
        "new_conflicts": arr(obj({"req_a": S, "req_b": S, "explanation": S})),
        "questions": arr(obj({
            "for_id": S, "question": S, "answer_type": enum("yes_no", "single_value"), "options": arr(S),
        })),
    }),
    "x_review": obj({"flags": arr(obj({"req_id": S, "seg_id": S, "issue": S, "quote": S}))}),
}

# --- grading contract: 08 spec §1.3 (field names and enums fixed; code-built, so null is allowed) ---
NULL_OR = lambda *t: {"type": [*t, "null"]}  # noqa: E731

STATUSES = ("met", "missing", "error", "conditional", "needs-confirmation")
GRADING = obj({
    "run_id": S,
    "basis": S,
    "requirements": arr(obj({
        "id": S,
        "kind": enum("deadline", "document", "attribute", "validity", "file_rule", "conditional"),
        "quote": S,
        "location": S,
        "status": enum(*STATUSES),
        "condition_state": {"type": ["string", "null"], "enum": ["unknown", "not_triggered", "triggered", None]},
        "evidence_files": arr(S),
        "depends_on": NULL_OR("string"),
        "depends_on_conflict": NULL_OR("string"),
        "reason": S,
        "bidder_action": S,
    })),
    "conflicts": arr(obj({
        "id": S,
        "topic": S,
        "sides": {**arr(obj({"quote": S, "location": S})), "minItems": 2, "maxItems": 2},
        "agency_question": S,
    })),
    "bidder_questions": arr(S),
    "untrusted_instructions": arr(obj({"quote": S, "location": S, "action": enum("ignored")})),
    "not_in_source": arr(S),
    "status_counts": obj({k: INT for k in STATUSES}),
    "disclaimer": S,
})

REPORT = {
    **GRADING,
    "properties": {**GRADING["properties"], "x_meta": {"type": "object"}},
    "required": GRADING["required"] + ["x_meta"],
}

FIXED_TEXT = {
    "basis": {"en": "Based only on the documents provided.", "ko": "제공된 자료 기준입니다."},
    "disclaimer": {
        "en": "This is a preparation aid, not an eligibility or compliance determination. "
              "The issuing authority's official documents prevail. A person must make the final decision.",
        "ko": "이 결과는 제공된 텍스트를 대조한 준비 보조자료이며 적격·준수 여부 판정이 아닙니다. "
              "공식 공고 원문과 발주기관 안내가 우선하며, 최종 판단은 사람이 합니다.",
    },
    "not_in_source": {
        "en": "'{item}' is not found in the provided documents. You may ask the issuing authority whether it is required.",
        "ko": "'{item}'은(는) 제공된 원문에 없습니다. 필요 여부는 발주기관에 확인할 수 있습니다.",
    },
    "source_missing": {
        "en": "Checklist withheld: no source text was provided. Please add the notice, RFP and annex text.",
        "ko": "공고 원문이 없어 판정 보류 — 공고 원문 요청: 공고문·제안요청서·별첨 텍스트를 넣어 주세요.",
    },
    "pii": {
        "en": "Sensitive data removal recommended: {types} found and masked before analysis. Delete it from the source files.",
        "ko": "민감정보 삭제 권고: {types} 유형이 입력에 있어 마스킹했습니다. 원본에서 삭제하세요.",
    },
    "forced_verdict": {
        "en": "I can't confirm eligibility or submission; a person must decide. The agency questions stay in the checklist.",
        "ko": "자격이나 제출 결과는 이 도구가 확인하지 않습니다. 사람이 판단해야 하며, 기관 확인 질문은 그대로 둡니다.",
    },
    "injection_badge": {
        "en": "Ignored: instruction-like text inside the documents was treated as data.",
        "ko": "무시함: 문서 안의 지시처럼 보이는 문장은 데이터로만 처리했습니다.",
    },
    "questions_header": {"en": "Questions for the issuing authority", "ko": "기관 확인 질문"},
}

REASON_CODES = [
    "SOURCE_MISSING", "UNVERIFIED_QUOTE", "FIELD_NOT_GROUNDED", "DATE_MISMATCH", "DATE_UNPARSED",
    "CONFLICT", "CONFLICT_DEPENDENT", "COUNTING_AMBIGUOUS", "CONDITION_UNKNOWN", "NOT_TRIGGERED",
    "OPTIONAL_ITEM", "MANDATORY_UNCLEAR", "UPLOAD_BEFORE_DEADLINE", "UPLOAD_AFTER_DEADLINE",
    "ACTION_PENDING", "DEADLINE_PASSED", "NO_MATCHING_FILE", "PARENT_MISSING", "QUANTITY_SHORT",
    "NOT_MERGED", "CHECK_FAILED:F{n}", "ATTRIBUTE_UNKNOWN:{field}", "ALL_CHECKS_PASSED",
    "SAME_UNDER_ALL_READINGS", "MODEL_FLAGGED", "REVIEW_FLAG",
    "LOCATION_CORRECTED", "TZ_NOT_STATED", "WEEKDAY_MISMATCH", "ISSUE_DATE_AFTER_AS_OF",
]
INFO_ONLY_CODES = ["LOCATION_CORRECTED", "TZ_NOT_STATED", "WEEKDAY_MISMATCH", "ISSUE_DATE_AFTER_AS_OF"]


def write(name: str, data: dict) -> None:
    payload = {"schema_version": SCHEMA_VERSION, **data} if "schema_version" not in data else data
    (HERE / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    for key, schema in MODEL_SCHEMAS.items():
        write(f"model_{key}.schema.json", {"name": key, "schema": schema})
    write("report_grading.schema.json", {"name": "grading", "schema": GRADING})
    write("report.schema.json", {"name": "report", "schema": REPORT})
    write("fixed_text.json", {"fixed_text": FIXED_TEXT})
    write("reason_codes.json", {"reason_codes": REASON_CODES, "info_only": INFO_ONLY_CODES})
    print("wrote", sorted(p.name for p in HERE.glob("*.json")))


if __name__ == "__main__":
    main()
