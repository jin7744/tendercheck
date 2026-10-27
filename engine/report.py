"""Report builder (design 09 §2-10, §4-6, §4-7; interfaces §7).

report.json = 08 §1.3 fields + `x_meta`; `grading_view(report)` = the 08 fields only (grading.json);
`render_text(report)` = report.txt. Every free-text field is code-templated and passes
engine.textfilter; status values come only from engine.rules decisions.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from engine import rules, templates, textfilter
from engine.conflicts import cut, doc_position
from engine.mask import merge_counts, type_labels
from engine.normalize import segment_map

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
FIXED = json.loads((CONTRACTS / "fixed_text.json").read_text(encoding="utf-8"))["fixed_text"]
SCHEMA_VERSION = "1.0.0"
STATUSES = ("met", "missing", "error", "conditional", "needs-confirmation")
GRADING_KEYS = ("run_id", "basis", "requirements", "conflicts", "bidder_questions", "untrusted_instructions",
                "not_in_source", "status_counts", "disclaimer")
CONFLICT_KEYS = ("id", "topic", "sides", "agency_question")
MAX_TOP_ACTIONS = 3
MAX_CHECKLIST = 10

# ---------------------------------------------------------------- fixed wording (code templates)

T = {
    "en": {
        "UNVERIFIED_QUOTE": ("The quoted text for this requirement was not found in the source, so it was not compared with the file list.",
                             "Check this clause in the source documents yourself."),
        "FIELD_NOT_GROUNDED": ("A date, number or rule in this requirement could not be confirmed from the quoted text.",
                               "Check the dates and values in the source before upload."),
        "DATE_MISMATCH": ("The date read from the quoted text differs from the extracted date.",
                          "Check the dates and values in the source before upload."),
        "DATE_UNPARSED": ("The date in the quoted text could not be read by the checker.",
                          "Check the dates and values in the source before upload."),
        "CONFLICT": "{loc_a} states {val_a}; {loc_b} states {val_b}. The documents differ ({cid}).",
        "CONFLICT_PLAIN": "The documents state different values ({cid}); both quotes are listed under the conflict.",
        "CONFLICT_ACTION": "Wait for the agency's answer to {cid}.",
        "CONFLICT_SAFE": " To be on time under every reading, upload before {safe}.",
        "CONFLICT_ACTION_OTHER": "Wait for the agency's answer to {cid}, or prepare files that satisfy both statements.",
        "UPLOAD_CONVERTED": " Planned upload {up} = {up_conv}: {parts}.",
        "UPLOAD_PART": "{rel} the {loc} deadline ({dl})",
        "UPLOAD_NOT_CONVERTED": " Planned upload {up}; at least one deadline states no time zone, so they were not compared.",
        "REL": {"before": "before", "after": "after", "same": "at"},
        "CONFLICT_DEPENDENT": ("The result changes with the answer to {cid}: it differs between the conflicting readings.",
                               "Wait for the agency's answer to {cid}, or prepare a document that works under every reading."),
        "COUNTING_SUFFIX": " It also depends on whether the first day is counted.",
        "SAME_CONFLICT": " The result is the same under every reading of {cid}.",
        "SAME_COUNTING": " The result is the same under every counting reading.",
        "READINGS": " Readings: {parts}.",
        "READING": {"pass": "{anchor}: satisfied", "fail": "{anchor}: not satisfied", "split": "{anchor}: depends on day counting"},
        "CONDITION_UNKNOWN": ("Required only if {conds}; the file list does not say whether this applies.",
                              "Answer the bidder question; if the condition applies, this item becomes required."),
        "NOT_TRIGGERED": ("Required only if {conds}; the file list says this does not apply.",
                          "If that changes, this item becomes required."),
        "TRIGGERED": "The condition applies ({conds}). ",
        "MANDATORY_UNCLEAR": ("The source does not make clear whether this item is required.",
                              "Ask the issuing authority whether it is required, or include it."),
        "OPTIONAL_ITEM": ("The source marks this item as optional, and no listed file matches it.", "Decide whether to include it."),
        "UPLOAD_BEFORE_DEADLINE": ("Planned upload {up} = {up_conv}, before the {loc} deadline ({dl}).", "Keep the planned upload time."),
        "UPLOAD_AFTER_DEADLINE": ("Planned upload {up} = {up_conv}, after the {loc} deadline ({dl}).", "Upload before {dl_up}."),
        "ACTION_PENDING_DEADLINE": ("The source sets this deadline: {raw}. No planned upload time is listed.",
                                    "Plan the upload before {raw}."),
        "ACTION_PENDING_TZ": ("Planned upload {up}; the deadline ({raw}) states no time zone or time, so the two were not compared.",
                              "Confirm the time zone with the issuing authority and upload before {raw}."),
        "ACTION_PENDING": ("This is a submission step that the file list cannot show.", "Follow this step when you submit."),
        "DEADLINE_PASSED": ("The scenario date ({as_of}) is after this deadline ({raw}).", "Ask the issuing authority about this deadline."),
        "NO_MATCHING_FILE": ("No listed file matches this requirement.", "Add this document to the bid."),
        "PARENT_MISSING": ("Depends on {parent}, which has no file in the list.",
                           "When adding that document, make sure it also meets this requirement."),
        "QUANTITY_SHORT": ("Fewer files are listed than required (one per member or per person).", "Add the missing copies."),
        "CHECK_FAILED": "{labels} check failed for {files}.",
        "ATTRIBUTE_UNKNOWN": ("A value needed for this check is not in the file list: {fields}.", "Answer the bidder question below."),
        "ATTRIBUTE_UNKNOWN_RULE": ("The source does not give a value needed for this check: {fields}.",
                                   "Check this clause with the issuing authority."),
        "ATTRIBUTE_UNKNOWN_EVIDENCE": ("No listed file or check shows this requirement.", "Confirm it yourself before upload."),
        "COUNTING_AMBIGUOUS": ("The result depends on how the days are counted.", "Ask the issuing authority how the period is counted."),
        "ALL_CHECKS_PASSED": ("Matched to {files}; the checks for this rule found no problem.", "No action needed."),
        "ALL_CHECKS_PASSED_NOFILE": ("The checks for this rule found no problem.", "No action needed."),
        "MODEL_FLAGGED": ("The checks found no problem, but the file match was marked for a second look.",
                          "Open the file and confirm that it matches this requirement."),
        "REVIEW_FLAG": ("The checks found no problem, but a second review raised a question about this row.",
                        "Open the file and confirm that it matches this requirement."),
        "GENERIC": ("See the quoted clause and the file list.", "Check this item before upload."),
        "MORE": "{shown} and {n} more",
        "AND": ", ",
        "SUMMARY": "{n} requirements compared with {m} listed files: {met} met, {missing} missing, {error} error, "
                   "{conditional} conditional, {nc} needing confirmation.",
        "BANNER": "Incomplete analysis — {reason}",
        "BANNER_DEFAULT": "the run stopped before every step finished",
        "CHECK_DEADLINE": "Confirm the upload time against {loc}.",
    },
    "ko": {
        "UNVERIFIED_QUOTE": ("이 요구사항의 인용문을 원문에서 찾지 못해 파일 목록과 대조하지 않았습니다.", "원문의 해당 조항을 직접 확인하세요."),
        "FIELD_NOT_GROUNDED": ("이 요구사항의 날짜·숫자·규칙을 인용문에서 확인하지 못했습니다.", "업로드 전에 원문의 날짜와 값을 직접 확인하세요."),
        "DATE_MISMATCH": ("인용문에서 읽은 날짜가 추출된 날짜와 다릅니다.", "업로드 전에 원문의 날짜와 값을 직접 확인하세요."),
        "DATE_UNPARSED": ("인용문의 날짜를 읽지 못했습니다.", "업로드 전에 원문의 날짜와 값을 직접 확인하세요."),
        "CONFLICT": "{loc_a_j} '{val_a}', {loc_b_j} '{val_b}'{ro} 적혀 있어 문서가 서로 다릅니다({cid}).",
        "CONFLICT_PLAIN": "문서마다 값이 다르게 적혀 있습니다({cid}). 양쪽 인용은 충돌 항목에 있습니다.",
        "CONFLICT_ACTION": "{cid}에 대한 기관 답변을 기다리세요.",
        "CONFLICT_SAFE": " 모든 해석에 맞추려면 {safe} 전에 올리세요.",
        "CONFLICT_ACTION_OTHER": "{cid}에 대한 기관 답변을 기다리거나, 두 문장을 모두 만족하도록 준비하세요.",
        "UPLOAD_CONVERTED": " 업로드 예정 {up} = {up_conv}: {parts}입니다.",
        "UPLOAD_PART": "{loc} 마감({dl}) {rel}",
        "UPLOAD_NOT_CONVERTED": " 업로드 예정 {up}; 시간대가 적히지 않은 마감이 있어 비교하지 않았습니다.",
        "REL": {"before": "전", "after": "후", "same": "정각"},
        "CONFLICT_DEPENDENT": ("결과가 {cid}의 답에 따라 달라집니다: 충돌하는 마감 해석에 따라 판정이 갈립니다.",
                               "{cid}에 대한 기관 답변을 기다리거나, 모든 해석에 맞는 서류를 준비하세요."),
        "COUNTING_SUFFIX": " 첫날을 기간에 넣는지에 따라서도 달라집니다.",
        "SAME_CONFLICT": " {cid}의 모든 해석에서 결과가 같습니다.",
        "SAME_COUNTING": " 모든 기산 방식에서 결과가 같습니다.",
        "READINGS": " 해석별: {parts}.",
        "READING": {"pass": "{anchor} 기준 충족", "fail": "{anchor} 기준 미충족", "split": "{anchor} 기준 기산 방식에 따라 다름"},
        "CONDITION_UNKNOWN": ("{conds}에만 필요한데, 목록에서 해당 여부를 알 수 없습니다.",
                              "아래 입찰자 질문에 답해 주세요. 조건에 해당하면 이 항목이 필요합니다."),
        "NOT_TRIGGERED": ("{conds}에만 필요하며, 목록에 해당하지 않는다고 적혀 있습니다.", "기재 내용이 바뀌면 이 항목이 필요해집니다."),
        "TRIGGERED": "{conds}에 해당합니다. ",
        "MANDATORY_UNCLEAR": ("원문만으로는 이 항목이 필수인지 분명하지 않습니다.", "필수 여부를 기관에 확인하거나 함께 제출하세요."),
        "OPTIONAL_ITEM": ("원문에서 선택 항목이며, 목록에 대응하는 파일이 없습니다.", "제출 여부를 정하세요."),
        "UPLOAD_BEFORE_DEADLINE": ("업로드 예정 {up} = {up_conv}로 {loc} 마감({dl}) 전입니다.", "예정대로 올리세요."),
        "UPLOAD_AFTER_DEADLINE": ("업로드 예정 {up} = {up_conv}로 {loc} 마감({dl}) 후입니다.", "{dl_up} 전에 올리세요."),
        "ACTION_PENDING_DEADLINE": ("원문 마감: {raw}. 목록에 업로드 예정 시각이 없습니다.", "{raw} 전에 올리도록 계획하세요."),
        "ACTION_PENDING_TZ": ("업로드 예정 {up}; 마감({raw})에 시간대나 시각이 적혀 있지 않아 비교하지 않았습니다.",
                              "시간대를 기관에 확인하고 {raw} 전에 올리세요."),
        "ACTION_PENDING": ("파일 목록으로는 확인할 수 없는 제출 절차입니다.", "제출할 때 이 절차를 따르세요."),
        "DEADLINE_PASSED": ("기준 시각({as_of})이 이 마감({raw}) 뒤입니다.", "이 마감에 대해 발주기관에 확인하세요."),
        "NO_MATCHING_FILE": ("목록에 이 요구사항에 대응하는 파일이 없습니다.", "이 서류를 추가하세요."),
        "PARENT_MISSING": ("{parent}의 파일이 목록에 없어 이 속성도 확인할 수 없습니다.", "해당 서류를 추가할 때 이 요구사항도 맞추세요."),
        "QUANTITY_SHORT": ("구성원별·인원별로 필요한 수보다 적게 기재되어 있습니다.", "모자란 서류를 추가하세요."),
        "CHECK_FAILED": "{labels} 검사에서 문제가 나왔습니다: {files}.",
        "ATTRIBUTE_UNKNOWN": ("판정에 필요한 값이 목록에 없습니다: {fields}.", "아래 입찰자 질문에 답해 주세요."),
        "ATTRIBUTE_UNKNOWN_RULE": ("판정에 필요한 값이 원문에 없습니다: {fields}.", "이 조항을 발주기관에 확인하세요."),
        "ATTRIBUTE_UNKNOWN_EVIDENCE": ("이 요구사항을 보여 주는 파일이나 검사 결과가 목록에 없습니다.", "업로드 전에 직접 확인하세요."),
        "COUNTING_AMBIGUOUS": ("기간을 세는 방식에 따라 결과가 달라집니다.", "기간 계산 방식을 기관에 확인하세요."),
        "ALL_CHECKS_PASSED": ("{files}와 대조했고 검사에서 문제가 나오지 않았습니다.", "추가 조치 없음."),
        "ALL_CHECKS_PASSED_NOFILE": ("검사에서 문제가 나오지 않았습니다.", "추가 조치 없음."),
        "MODEL_FLAGGED": ("검사에서 문제는 없었지만 파일 대조 결과에 재확인 표시가 붙었습니다.", "파일을 열어 이 요구사항과 맞는지 확인하세요."),
        "REVIEW_FLAG": ("검사에서 문제는 없었지만 2차 검토에서 이 행에 대한 문제가 제기되었습니다.", "파일을 열어 이 요구사항과 맞는지 확인하세요."),
        "GENERIC": ("인용 조항과 파일 목록을 확인하세요.", "업로드 전에 이 항목을 확인하세요."),
        "MORE": "{shown} 외 {n}개",
        "AND": ", ",
        "SUMMARY": "요구사항 {n}개를 목록의 파일 {m}개와 대조했습니다: 충족 {met} · 누락 {missing} · 오류 {error} · "
                   "조건부 {conditional} · 확인 필요 {nc}.",
        "BANNER": "분석 미완료 — {reason}",
        "BANNER_DEFAULT": "실행이 모든 단계를 마치기 전에 멈췄습니다",
        "CHECK_DEADLINE": "{loc} 기준으로 업로드 시각을 확인하세요.",
    },
}
F_LABELS = {
    "en": {"F1": "File format", "F2": "File size", "F3": "File name", "F4": "Merge", "F5": "Signature/seal",
           "F6": "Original/copy", "F7": "Validity", "F9": "Notarization", "F10": "All-member signature",
           "F11": "Amount", "F12": "Per-member quantity", "F13": "Per-person quantity", "F14": "Member composition"},
    "ko": {"F1": "파일 형식", "F2": "파일 용량", "F3": "파일명", "F4": "병합", "F5": "서명·날인", "F6": "원본·사본",
           "F7": "유효기간", "F9": "공증", "F10": "구성원 전원 서명·날인", "F11": "금액", "F12": "구성원별 수량",
           "F13": "인원별 수량", "F14": "구성원 조건"},
}
F_ACTIONS = {
    "en": {"F1": "Convert the file to the required format.", "F2": "Reduce the file to the size limit.",
           "F3": "Rename the file to the required pattern.", "F4": "Merge the documents into one file as required.",
           "F5": "Add the required signature or seal.", "F6": "Provide the required original or certified copy.",
           "F7": "Obtain a document that is valid for the required period.", "F9": "Have the document notarized.",
           "F10": "Have every member sign or seal it.", "F11": "Correct the amount.", "F12": "Add one per member.",
           "F13": "Add one per person.", "F14": "Check the member composition against this rule."},
    "ko": {"F1": "요구 형식으로 변환하세요.", "F2": "용량 상한 이하로 줄이세요.", "F3": "요구 형식에 맞게 파일명을 바꾸세요.",
           "F4": "요구대로 한 파일로 병합하세요.", "F5": "요구된 서명·날인을 하세요.", "F6": "요구된 원본 또는 원본대조필 사본을 내세요.",
           "F7": "요구 기간에 유효한 서류를 새로 발급받으세요.", "F9": "공증을 받으세요.", "F10": "구성원 전원의 서명·날인을 받으세요.",
           "F11": "금액을 고치세요.", "F12": "구성원마다 한 부씩 갖추세요.", "F13": "인원마다 한 부씩 갖추세요.",
           "F14": "구성원 구성을 이 규칙과 대조하세요."},
}
FIELD_LABELS = {
    "en": {"issue_date": "issue date", "valid_until": "expiry date", "headcount": "number of staff",
           "signature_count": "number of signed pages", "notarized": "notarization", "signed": "signature",
           "sealed": "seal", "amount": "amount", "members": "members", "seal_kind": "seal type", "signed_by": "signers",
           "copy_kind": "original or copy", "size_mb": "file size", "extension": "file extension",
           "short_name": "short name", "anchor_date": "deadline date", "period": "period",
           "required_until": "required date", "validity": "validity", "sign_seal": "signature or seal",
           "merged_contents": "merged contents"},
    "ko": {"issue_date": "발급일", "valid_until": "만료일", "headcount": "인원수", "signature_count": "서명 매수",
           "notarized": "공증 여부", "signed": "서명 여부", "sealed": "날인 여부", "amount": "금액", "members": "구성원",
           "seal_kind": "인감 종류", "signed_by": "서명자", "copy_kind": "원본·사본 여부", "size_mb": "파일 용량",
           "extension": "확장자", "short_name": "파일명용 업체명", "anchor_date": "기준 마감일", "period": "기간",
           "required_until": "요구 일자", "validity": "유효기간", "sign_seal": "서명·날인", "merged_contents": "병합 내용"},
}
CONDITIONS = {
    "en": {"yes": {"consortium": "you bid as a joint venture", "usage_seal": "you use an authorized-use seal",
                   "foreign_entity": "you bid as a foreign entity", "subcontracting": "you subcontract part of the work",
                   "sme": "your company is an SME", "other": "the condition stated in the source applies"},
           "no": {"consortium": "you do not bid as a joint venture", "usage_seal": "you do not use an authorized-use seal",
                  "foreign_entity": "you do not bid as a foreign entity", "subcontracting": "you do not subcontract",
                  "sme": "your company is not an SME", "other": "the condition stated in the source does not apply"},
           "join": " and "},
    "ko": {"yes": {"consortium": "공동수급으로 참가하는 경우", "usage_seal": "사용인감을 날인하는 경우",
                   "foreign_entity": "외국 업체로 참가하는 경우", "subcontracting": "하도급하는 경우",
                   "sme": "중소기업인 경우", "other": "원문에 적힌 조건에 해당하는 경우"},
           "no": {"consortium": "공동수급으로 참가하지 않는 경우", "usage_seal": "사용인감을 날인하지 않는 경우",
                  "foreign_entity": "외국 업체로 참가하지 않는 경우", "subcontracting": "하도급하지 않는 경우",
                  "sme": "중소기업이 아닌 경우", "other": "원문에 적힌 조건에 해당하지 않는 경우"},
           "join": "이면서 "},
}
ANCHOR_WORDS = {"en": "the deadline", "ko": "마감일"}
LABELS = {
    "en": {"status": {"met": "Met", "missing": "Missing", "error": "Error", "conditional": "Conditional",
                      "needs-confirmation": "Needs confirmation"},
           "cond": {"unknown": "not known", "not_triggered": "does not apply", "triggered": "applies"},
           "kind": {"deadline": "deadline", "document": "document", "attribute": "attribute", "validity": "validity",
                    "file_rule": "file rule", "conditional": "conditional"},
           "counts": "Status", "top": "Top actions", "checklist": "Checklist", "requirement": "Requirement",
           "applies": "Applies", "evidence": "Evidence (files)", "reason": "Reason", "action": "Action",
           "quote": "Source quote", "depends": "Depends on", "conflicts": "Conflicts between documents",
           "gaps": "Information gaps", "bidder_q": "Questions for you", "nis": "Not in the provided documents",
           "untrusted": "Instruction-like text in the documents", "pre": "Before you upload", "none": "-",
           "notices": "Notices"},
    "ko": {"status": {"met": "충족", "missing": "누락", "error": "오류", "conditional": "조건부",
                      "needs-confirmation": "확인 필요"},
           "cond": {"unknown": "미확인", "not_triggered": "해당 없음", "triggered": "해당"},
           "kind": {"deadline": "마감", "document": "서류", "attribute": "속성", "validity": "유효기간",
                    "file_rule": "파일 규칙", "conditional": "조건부"},
           "counts": "상태", "top": "우선 행동", "checklist": "점검표", "requirement": "요구사항", "applies": "적용 여부",
           "evidence": "현재 근거(파일)", "reason": "사유", "action": "수정 행동", "quote": "원문 인용",
           "depends": "종속", "conflicts": "문서 간 충돌", "gaps": "정보 부족", "bidder_q": "입찰자 확인 질문",
           "nis": "원문에 없는 항목", "untrusted": "문서 속 지시문", "pre": "제출 직전 체크", "none": "-",
           "notices": "알림"},
}


# ---------------------------------------------------------------- small helpers

def fixed(key: str, lang: str, **kw) -> str:
    text = FIXED[key][lang]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", v)
    return text


def status_counts(requirements: list[dict]) -> dict:
    counts = {s: 0 for s in STATUSES}
    for r in requirements:
        counts[r["status"]] += 1
    return counts


def grading_view(report: dict) -> dict:
    """grading.json: the 08 §1.3 fields only (conflicts reduced to id/topic/sides/agency_question)."""
    out = {k: copy.deepcopy(report[k]) for k in GRADING_KEYS}
    out["conflicts"] = [{k: c[k] for k in CONFLICT_KEYS} for c in out["conflicts"]]
    return out


def _fmt_dt(iso: str) -> str:
    """'2026-11-30T14:30:00+03:00' -> '2026-11-30 14:30 UTC+03:00' (naive: no zone)."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    text = dt.strftime("%Y-%m-%d %H:%M")
    if dt.tzinfo is not None:
        off = dt.utcoffset()
        total = int(off.total_seconds() // 60)
        sign = "-" if total < 0 else "+"
        h, m = divmod(abs(total), 60)
        text += f" UTC{sign}{h:02d}:{m:02d}"
    return text


def _files_text(files: list[str], lang: str, limit: int = 5) -> str:
    t = T[lang]
    shown = t["AND"].join(files[:limit])
    return shown if len(files) <= limit else t["MORE"].format(shown=shown, n=len(files) - limit)


def _conds_text(req: dict, lang: str) -> str:
    table = CONDITIONS[lang]
    parts = []
    for c in req.get("conditions") or []:
        key = c.get("fact_key", "other")
        side = "no" if c.get("applies_when") == "no" else "yes"
        parts.append(table[side].get(key, table[side]["other"]))
    if not parts:
        parts = [table["yes"]["other"]]
    seen = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return table["join"].join(seen)


def _readings(file_check: dict | None):
    """Validity readings list (engine.dates.evaluate_validity shape) found anywhere in a FileCheck."""
    def walk(obj):
        if isinstance(obj, list) and obj and all(isinstance(x, dict) and "anchor" in x and "result" in x for x in obj):
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                got = walk(v)
                if got:
                    return got
        if isinstance(obj, list):
            for v in obj:
                got = walk(v)
                if got:
                    return got
        return None
    return walk((file_check or {}).get("readings")) or []


def _readings_text(file_check: dict | None, lang: str) -> str:
    rows = _readings(file_check)
    if not rows:
        return ""
    by = {}
    for r in rows:
        by.setdefault(r.get("anchor") or "", set()).add(r.get("result"))
    parts = []
    for anchor, results in by.items():
        if not anchor:
            continue
        key = results.pop() if len(results) == 1 else "split"
        if key not in ("pass", "fail", "split"):
            continue
        parts.append(T[lang]["READING"][key].format(anchor=anchor))
    return T[lang]["READINGS"].format(parts="; ".join(parts)) if parts else ""


def _check_details(file_check: dict | None, ids: list[str]) -> str:
    out = []
    for c in (file_check or {}).get("checks") or []:
        if c.get("id") in ids and c.get("detail"):
            out.append(str(c["detail"]))
    text = "; ".join(out)
    return text if len(text) <= 200 else text[:199] + "…"


# ---------------------------------------------------------------- reason / action per row

class _Ctx:
    def __init__(self, lang, conflicts, ids, rows_by_id, profile, as_of, log):
        self.lang, self.conflicts, self.ids = lang, {c["id"]: c for c in conflicts}, ids
        self.rows_by_id, self.profile, self.as_of, self.log = rows_by_id, profile or {}, as_of, log


def _upload_sentence(conflict: dict, upload: str, lang: str) -> str:
    t = T[lang]
    sides = conflict.get("side_info") or []
    if not upload or len(sides) < 2:
        return ""
    up = rules._aware(upload)
    dls = [rules._aware(s.get("iso_local", "")) for s in sides]
    if up is None:
        return ""
    if any(d is None for d in dls):
        return t["UPLOAD_NOT_CONVERTED"].format(up=_fmt_dt(upload))
    tz = dls[0].tzinfo
    parts = []
    for s, d in zip(sides, dls):
        rel = "before" if up < d else "after" if up > d else "same"
        parts.append(t["UPLOAD_PART"].format(rel=t["REL"][rel], loc=s["location"], dl=_fmt_dt(d.isoformat())))
    joined = "; ".join(parts) if lang == "en" else ", ".join(parts)
    return t["UPLOAD_CONVERTED"].format(up=_fmt_dt(upload), up_conv=_fmt_dt(up.astimezone(tz).isoformat()), parts=joined)


def _safe_time(conflict: dict, upload: str) -> str:
    """Earliest side of a deadline conflict, for the 'on time under every reading' action (09 §2-8).
    Only when both sides carry a time and are both zoned or both unzoned; never a choice of side."""
    sides = conflict.get("side_info") or []
    if len(sides) != 2:
        return ""
    parsed = []
    for s in sides:
        iso = s.get("iso_local", "")
        if "T" not in iso:
            return ""
        try:
            parsed.append(datetime.fromisoformat(iso))
        except ValueError:
            return ""
    zoned = [p.tzinfo is not None for p in parsed]
    k = 0 if (all(zoned) or not any(zoned)) and parsed[0] <= parsed[1] else 1
    if all(zoned):
        up = rules._aware(upload)
        return _fmt_dt((parsed[k].astimezone(up.tzinfo) if up is not None else parsed[k]).isoformat())
    if not any(zoned):
        return (conflict.get("values") or {}).get("ab"[k], "")
    return ""


def _conflict_texts(ctx: _Ctx, cid: str):
    lang, t = ctx.lang, T[ctx.lang]
    c = ctx.conflicts.get(cid)
    if c is None:
        return [t["CONFLICT_PLAIN"].format(cid=cid or "-")], [t["CONFLICT_ACTION"].format(cid=cid or "-")]
    sides, values = c["sides"], c.get("values") or {}
    loc_a, loc_b = sides[0]["location"], sides[1]["location"]
    val_a, val_b = values.get("a", ""), values.get("b", "")
    upload = ctx.profile.get("planned_upload", "")
    timing = _upload_sentence(c, upload, lang) if c.get("rule") == "K1" else ""
    if lang == "ko":
        full = t["CONFLICT"].format(loc_a_j=templates.josa(loc_a, "은/는"), val_a=val_a,
                                    loc_b_j=templates.josa(loc_b, "은/는"), val_b=val_b,
                                    ro=templates.particle(val_b, "으로/로"), cid=cid)
    else:
        full = t["CONFLICT"].format(loc_a=loc_a, val_a=val_a, loc_b=loc_b, val_b=val_b, cid=cid)
    plain = t["CONFLICT_PLAIN"].format(cid=cid)
    reasons = [full + timing, plain + timing, plain]
    if c.get("rule") == "K1":
        base = t["CONFLICT_ACTION"].format(cid=cid)
        safe = _safe_time(c, upload)
        actions = [base + t["CONFLICT_SAFE"].format(safe=safe), base] if safe else [base]
    else:
        actions = [t["CONFLICT_ACTION_OTHER"].format(cid=cid)]
    return reasons, actions


def _texts(ctx: _Ctx, item: dict, dec: dict, files: list[str]):
    """(reason candidates, action candidates): first clean one wins in textfilter.pick."""
    lang, t = ctx.lang, T[ctx.lang]
    req, chk = item["vreq"]["req"], item["vreq"]["check"]
    fc = item.get("file_check")
    codes = dec.get("reason_codes") or []
    first = codes[0] if codes else ""
    rule = dec.get("rule", "")
    files_txt = _files_text(files, lang) if files else ""
    suffix = ""
    if "SAME_UNDER_ALL_READINGS" in codes:
        suffix = t["SAME_CONFLICT"].format(cid=dec["depends_on_conflict"]) if dec.get("depends_on_conflict") \
            else t["SAME_COUNTING"]
    timing = dec.get("timing") or {}
    raw = (req.get("deadline") or {}).get("raw", "")
    raw_src = cut(chk.get("quote", ""), raw) or raw

    if rule == "S3":
        return _conflict_texts(ctx, dec.get("depends_on_conflict") or "")
    if rule == "S4":
        cid = dec.get("depends_on_conflict") or "-"
        r, a = t["CONFLICT_DEPENDENT"]
        reason = r.format(cid=cid) + _readings_text(fc, lang)
        if "COUNTING_AMBIGUOUS" in codes:
            reason += t["COUNTING_SUFFIX"]
        return [reason, r.format(cid=cid)], [a.format(cid=cid)]
    if first in ("UNVERIFIED_QUOTE", "FIELD_NOT_GROUNDED", "DATE_MISMATCH", "DATE_UNPARSED",
                 "MANDATORY_UNCLEAR", "OPTIONAL_ITEM", "NO_MATCHING_FILE", "QUANTITY_SHORT"):
        r, a = t[first]
        detail = _check_details(fc, ["F12", "F13"]) if first == "QUANTITY_SHORT" else ""
        return [r + (f" ({detail})" if detail else "") + suffix, r], [a]
    if first in ("CONDITION_UNKNOWN", "NOT_TRIGGERED"):
        r, a = t[first]
        return [r.format(conds=_conds_text(req, lang)), r.format(conds=CONDITIONS[lang]["yes"]["other"])], [a]
    if first in ("UPLOAD_BEFORE_DEADLINE", "UPLOAD_AFTER_DEADLINE"):
        r, a = t[first]
        kw = {"up": _fmt_dt(timing.get("upload", "")), "up_conv": _fmt_dt(timing.get("upload_in_deadline_tz", "")),
              "loc": chk.get("location", ""), "dl": _fmt_dt(timing.get("deadline", "")),
              "dl_up": _fmt_dt(timing.get("deadline_in_upload_tz", ""))}
        return [r.format(**kw)], [a.format(**kw)]
    if first == "ACTION_PENDING":
        upload = timing.get("upload") if timing else ""
        if rule == "S8" and upload and raw_src:
            r, a = t["ACTION_PENDING_TZ"]
            return [r.format(up=_fmt_dt(upload), raw=raw_src), t["ACTION_PENDING"][0]], [a.format(raw=raw_src), t["ACTION_PENDING"][1]]
        if raw_src:
            r, a = t["ACTION_PENDING_DEADLINE"]
            return [r.format(raw=raw_src), t["ACTION_PENDING"][0]], [a.format(raw=raw_src), t["ACTION_PENDING"][1]]
        return [t["ACTION_PENDING"][0]], [t["ACTION_PENDING"][1]]
    if first == "DEADLINE_PASSED":
        r, a = t[first]
        return [r.format(as_of=_fmt_dt(ctx.as_of), raw=raw_src or _fmt_dt(timing.get("deadline", ""))), r.format(
            as_of=_fmt_dt(ctx.as_of), raw=_fmt_dt(timing.get("deadline", "")))], [a]
    if first == "PARENT_MISSING":
        r, a = t[first]
        parent = ctx.ids.get(dec.get("depends_on") or "", dec.get("depends_on") or "-")
        return [r.format(parent=parent)], [a]
    if first == "NOT_MERGED" or first.startswith("CHECK_FAILED:"):
        fids = ["F4" if c == "NOT_MERGED" else c.split(":", 1)[1] for c in codes
                if c == "NOT_MERGED" or c.startswith("CHECK_FAILED:")]
        labels = t["AND"].join(F_LABELS[lang].get(f, f) for f in fids)
        detail = _check_details(fc, fids)
        base = t["CHECK_FAILED"].format(labels=labels, files=files_txt or "-")
        actions = " ".join(F_ACTIONS[lang].get(f, "") for f in fids).strip() or t["GENERIC"][1]
        return [base + (f" ({detail})" if detail else "") + suffix, base + suffix, base], [actions, t["GENERIC"][1]]
    if first.startswith("ATTRIBUTE_UNKNOWN:") or first == "COUNTING_AMBIGUOUS":
        fields = [c.split(":", 1)[1] for c in codes if c.startswith("ATTRIBUTE_UNKNOWN:")]
        bidder = [f for f in fields if f not in rules.RULE_SIDE_FIELDS]
        side = [f for f in fields if f in rules.RULE_SIDE_FIELDS and f != "evidence"]
        reasons, actions = [], []
        if "evidence" in fields:
            reasons.append(t["ATTRIBUTE_UNKNOWN_EVIDENCE"][0])
            actions.append(t["ATTRIBUTE_UNKNOWN_EVIDENCE"][1])
        if bidder:
            reasons.append(t["ATTRIBUTE_UNKNOWN"][0].format(
                fields=t["AND"].join(FIELD_LABELS[lang].get(f, f.replace("_", " ")) for f in bidder)))
            actions.append(t["ATTRIBUTE_UNKNOWN"][1])
        if side:
            reasons.append(t["ATTRIBUTE_UNKNOWN_RULE"][0].format(
                fields=t["AND"].join(FIELD_LABELS[lang].get(f, f.replace("_", " ")) for f in side)))
            actions.append(t["ATTRIBUTE_UNKNOWN_RULE"][1])
        if "COUNTING_AMBIGUOUS" in codes:
            reasons.append(t["COUNTING_AMBIGUOUS"][0])
            actions.append(t["COUNTING_AMBIGUOUS"][1])
        reason = " ".join(reasons) + suffix
        return [reason, " ".join(reasons)], [" ".join(_unique(actions))]
    if first in ("MODEL_FLAGGED", "REVIEW_FLAG"):
        r, a = t[first]
        return [r + suffix, r], [a]
    if first == "ALL_CHECKS_PASSED":
        lead = t["TRIGGERED"].format(conds=_conds_text(req, lang)) if dec.get("condition_state") == "triggered" else ""
        if files_txt:
            r, a = t["ALL_CHECKS_PASSED"]
            return [lead + r.format(files=files_txt) + suffix, r.format(files=files_txt)], [a]
        r, a = t["ALL_CHECKS_PASSED_NOFILE"]
        return [lead + r + suffix, r], [a]
    return [t["GENERIC"][0]], [t["GENERIC"][1]]


def _unique(items):
    out = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out


# ---------------------------------------------------------------- display order, actions

def _display_order(rows: list[dict], meta_rows: dict) -> list[str]:
    """D16: deadline rows first (deadline ascending), then missing/error, then the rest; ID order inside."""
    def key(r):
        m = meta_rows.get(r["id"], {})
        if r["kind"] == "deadline":
            return (0, m.get("deadline_sort", "9999"), r["id"])
        if r["status"] in ("missing", "error"):
            return (1, "", r["id"])
        return (2, "", r["id"])
    return [r["id"] for r in sorted(rows, key=key)]


def _actions(rows: list[dict], order: list[str], lang: str):
    by = {r["id"]: r for r in rows}
    pending = [by[i] for i in order if by[i]["status"] in ("missing", "error", "needs-confirmation")
               or (by[i]["status"] == "conditional" and by[i]["condition_state"] == "unknown")]
    top = [f"{r['id']}: {r['bidder_action']}" for r in pending if r["status"] != "conditional"][:MAX_TOP_ACTIONS]
    checklist = [f"[ ] {r['id']} {r['location']}: {r['bidder_action']}" for r in pending][:MAX_CHECKLIST]
    for r in (by[i] for i in order):
        if len(checklist) >= MAX_CHECKLIST:
            break
        if r["kind"] == "deadline" and r not in pending:
            checklist.append("[ ] " + T[lang]["CHECK_DEADLINE"].format(loc=r["location"]))
    return top, checklist


# ---------------------------------------------------------------- build

def _citation(item: dict, bundle: dict | None) -> dict | None:
    """The row's quote is always cut from the source by code (D19): the verified span, or for an
    unverified quote (S1) the whole claimed source line. None when neither exists (row kept out
    of `requirements`, listed in x_meta.unverified_rows) - an empty or model-written quote would
    be a hallucinated citation (08 F2)."""
    req, chk = item["vreq"]["req"], item["vreq"]["check"]
    if chk.get("quote"):
        seg_id = chk.get("seg_id", "")
        return {"quote": chk["quote"], "location": chk.get("location", ""), "seg_id": seg_id,
                "position": doc_position(bundle, seg_id, chk["quote"])}
    seg = segment_map(bundle).get((req.get("location") or {}).get("seg_id", "")) if bundle else None
    if seg is None or not seg["text"].strip():
        return None
    pos = doc_position(bundle, seg["seg_id"])
    return {"quote": seg["text"].strip(), "location": seg["location"], "seg_id": seg["seg_id"],
            "position": (pos[0], pos[1], 10 ** 6)}


def _pii_counts(manifest: dict | None, bundle: dict | None, flags: dict) -> dict:
    counts = dict((manifest or {}).get("pii_counts") or {})
    counts = merge_counts(counts, {e["type"]: e["count"] for e in (bundle or {}).get("pii_masked") or []})
    extra = {e["type"]: e["count"] for e in flags.get("pii_masked") or [] if e.get("type") not in counts}
    return merge_counts(counts, extra)


def build_report(run_id: str, decisions_in_doc_order: list[dict], conflicts: list[dict], *, lang: str,
                 manifest: dict | None = None, bundle: dict | None = None, untrusted: list[dict] | None = None,
                 not_in_source: list[str] | None = None, flags: dict | None = None, x_meta: dict | None = None) -> dict:
    """Items: [{'vreq': VReq, 'decision': Decision, 'file_check': FileCheck|None,
                'match': this requirement's Match entry|None, 'action_draft': str (optional)}].
    Items are (re)sorted by document position of the quote; IDs R01.. follow that order.
    `not_in_source`: item names the user asked for that the source does not contain."""
    lang = lang if lang in T else "en"
    t = T[lang]
    flags = dict(flags or {})
    log: dict = {"filtered": list(flags.get("filtered") or []), "warned": []}
    meta = copy.deepcopy(x_meta or {})
    manifest = manifest or {}
    profile = manifest.get("profile") or {}
    mrows = {r["row_id"]: r for r in manifest.get("rows") or [] if "row_id" in r}
    as_of = (bundle or {}).get("as_of", "")
    missing_source = bundle is not None and rules.source_missing(bundle)

    conflicts = [] if missing_source else list(conflicts or [])
    cited, unverified = [], []
    for it in [] if missing_source else list(decisions_in_doc_order or []):
        c = _citation(it, bundle)
        (cited if c else unverified).append((c, it))
    cited.sort(key=lambda pair: pair[0]["position"])
    items = [dict(it, _cite=c) for c, it in cited]
    width = max(2, len(str(len(items))))
    ids = {}
    for n, it in enumerate(items, 1):
        ids[it["vreq"]["req"].get("tmp_id") or it["vreq"]["check"].get("tmp_id", f"_{n}")] = f"R{n:0{width}d}"
    ctx = _Ctx(lang, conflicts, ids, mrows, profile, as_of, log)

    counts = _pii_counts(manifest, bundle, flags)
    types = type_labels(counts, lang) if counts else ""
    pii_text = fixed("pii", lang, types=types) if types else ""
    pii_attached = False

    requirements, meta_rows, bidder_keys, agency_extra = [], {}, [], []
    for n, it in enumerate(items):
        req, chk, dec = it["vreq"]["req"], it["vreq"]["check"], it["decision"]
        tmp = req.get("tmp_id") or chk.get("tmp_id", "")
        rid = ids[tmp]
        row_ids = list(dec.get("evidence_row_ids") if dec.get("evidence_row_ids") is not None
                       else (it.get("match") or {}).get("row_ids") or [])
        files = []
        for row_id in row_ids:
            name = (mrows.get(row_id) or {}).get("file_name")
            if name and name not in files:
                files.append(name)
        reasons, actions = _texts(ctx, it, dec, files)
        reason = textfilter.pick(f"requirements[{n}].reason", reasons + [t["GENERIC"][0]], log)
        if it.get("action_draft"):
            actions = [it["action_draft"]] + actions
        action = textfilter.pick(f"requirements[{n}].bidder_action", actions + [t["GENERIC"][1]], log)
        if pii_text and any((mrows.get(r) or {}).get("pii") for r in row_ids):
            action = textfilter.pick(f"requirements[{n}].bidder_action", [f"{action} {pii_text}", action], log)
            pii_attached = True
        depends_on = ids.get(dec.get("depends_on") or "") if dec.get("depends_on") else None
        cite = it["_cite"]
        requirements.append({
            "id": rid, "kind": rules.kind_of(req), "quote": cite["quote"], "location": cite["location"],
            "status": dec["status"], "condition_state": dec.get("condition_state"), "evidence_files": files,
            "depends_on": depends_on, "depends_on_conflict": dec.get("depends_on_conflict"),
            "reason": reason, "bidder_action": action})
        for key in dec.get("bidder_question_keys") or []:
            doc = ""
            for row_id in row_ids:
                r = mrows.get(row_id) or {}
                doc = r.get("doc_label") or r.get("file_name") or ""
                if doc:
                    break
            bidder_keys.append((key, doc or req.get("subject", ""), chk.get("location", "")))
        if "counting" in (dec.get("agency_question_keys") or []):
            v = req.get("validity") or {}
            unit, nval = ("months", int(v.get("months") or 0)) if int(v.get("months") or 0) else ("days", int(v.get("days") or 0))
            phrase = v.get("raw", "") or chk.get("quote", "")
            doc = next(((mrows.get(r) or {}).get("doc_label") or "" for r in row_ids if (mrows.get(r) or {}).get("doc_label")), "")
            q = templates.agency_counting_question(lang, phrase, ANCHOR_WORDS[lang], nval, unit=unit, document=doc)
            q = textfilter.pick(f"x_meta.agency_questions[{rid}]", [q], log)
            if q:
                agency_extra.append({"for": rid, "question": q})
        p = chk.get("deadline") or {}
        meta_rows[rid] = {
            "id": rid, "tmp_id": tmp, "subject": req.get("subject", ""),
            "requirement": textfilter.pick(f"x_meta.rows[{rid}].requirement", [req.get("requirement", "")], log),
            "category": req.get("category", ""), "seg_id": cite["seg_id"], "page": chk.get("page", 0),
            "verify_method": chk.get("method", "") or "claimed_segment", "rule": dec.get("rule", ""),
            "reason_codes": list(dec.get("reason_codes") or []), "info_codes": list(dec.get("info_codes") or []),
            "checks": list((chk.get("checks") or {}).values()) + list((it.get("file_check") or {}).get("checks") or []),
            "evidence_row_ids": row_ids, "condition_state": dec.get("condition_state"),
            "deadline_sort": _deadline_sort(p, dec, ctx),
            "provenance": {"extracted_by": "model", "verified_by": "code",
                           "matched_by": "model" if it.get("match") else "none", "status_by": "code"}}

    out_conflicts = []
    for i, c in enumerate(conflicts):
        topic = textfilter.pick(f"conflicts[{i}].topic", [c.get("topic", ""), templates.conflict_topic("model", lang)], log)
        fallback = templates.agency_conflict_question(lang, c["sides"][0]["location"], "…", c["sides"][1]["location"], "…")
        question = textfilter.pick(f"conflicts[{i}].agency_question", [c.get("agency_question", ""), fallback], log)
        out_conflicts.append({**{k: c[k] for k in ("id", "sides")}, "topic": topic, "agency_question": question,
                              "members": [ids.get(m, m) for m in c.get("members") or []],
                              "rule": c.get("rule", ""), "values": c.get("values", {}),
                              "question_source": c.get("question_source", "")})

    bidder_questions = []
    for key, doc, loc in bidder_keys:
        q = templates.bidder_question(key, lang, document=doc, context=loc)
        q = textfilter.pick(f"bidder_questions[{len(bidder_questions)}]", [q], log)
        if q and q not in bidder_questions:
            bidder_questions.append(q)
    if pii_text and not pii_attached:
        bidder_questions.append(pii_text)

    entries = untrusted if untrusted is not None else (bundle or {}).get("injection_suspects") or []
    untrusted_out = []
    for e in [] if missing_source else entries:
        u = {"quote": e.get("quote", ""), "location": e.get("location", ""), "action": "ignored"}
        if u["quote"] and u not in untrusted_out:
            untrusted_out.append(u)

    nis = []
    for item in not_in_source or []:
        text = textfilter.pick(f"not_in_source[{len(nis)}]",
                               [fixed("not_in_source", lang, item=item),
                                fixed("not_in_source", lang, item="…")], log)
        if text and text not in nis:
            nis.append(text)

    report = {
        "run_id": run_id, "basis": fixed("basis", lang), "requirements": requirements,
        "conflicts": [{k: c[k] for k in CONFLICT_KEYS} for c in out_conflicts], "bidder_questions": bidder_questions,
        "untrusted_instructions": untrusted_out, "not_in_source": nis, "status_counts": status_counts(requirements),
        "disclaimer": fixed("disclaimer", lang)}

    order = _display_order(requirements, meta_rows)
    top, checklist = _actions(requirements, order, lang)
    state = "source_missing" if missing_source else (
        "partial" if meta.get("analysis_state") == "partial" or flags.get("incomplete_reason") else "complete")
    notices = list(flags.get("notices") or [])
    if missing_source:
        notices.append("source_missing")
    if untrusted_out:
        notices.append("injection_badge")
    if flags.get("forced_verdict_request_ignored"):
        notices.append("forced_verdict")
    if pii_text:
        notices.append("pii")
    banner = ""
    if state == "partial":
        reason = textfilter.pick("x_meta.banner", [flags.get("incomplete_reason", ""), t["BANNER_DEFAULT"]], log)
        banner = t["BANNER"].format(reason=reason)
    n_files = len({r.get("file_name") for r in mrows.values() if r.get("file_name")})
    sc = report["status_counts"]
    summary = fixed("source_missing", lang) if missing_source else t["SUMMARY"].format(
        n=len(requirements), m=n_files, met=sc["met"], missing=sc["missing"], error=sc["error"],
        conditional=sc["conditional"], nc=sc["needs-confirmation"])
    referenced = {r for m in meta_rows.values() for r in m["evidence_row_ids"]}
    computed = {
        "schema_version": SCHEMA_VERSION, "output_lang": lang, "as_of": as_of, "analysis_state": state,
        "banner": banner, "summary_line": summary, "rows": [meta_rows[r["id"]] for r in requirements],
        "display_order": order, "top_actions": top, "pre_submission_checklist": checklist,
        "agency_questions": [{"for": c["id"], "question": c["agency_question"]} for c in out_conflicts] + agency_extra,
        "conflicts_detail": [{k: c[k] for k in ("id", "members", "rule", "values", "question_source")} for c in out_conflicts],
    }
    meta.update({k: v for k, v in computed.items() if k not in meta or k in ("analysis_state", "rows", "display_order")})
    meta["unverified_rows"] = [
        {"tmp_id": it["vreq"]["req"].get("tmp_id", ""), "status": it["decision"]["status"],
         "reason_codes": list(it["decision"].get("reason_codes") or []),
         "requirement": textfilter.pick("x_meta.unverified_rows", [it["vreq"]["req"].get("requirement", "")], log)}
        for _, it in unverified]
    meta.setdefault("info_gaps", [])
    meta.setdefault("unmatched_files", _unique([r.get("file_name") for rid, r in mrows.items() if rid not in referenced]))
    mflags = dict(meta.get("flags") or {})
    mflags.update({
        "pii_masked": [{"type": k, "count": v} for k, v in counts.items() if v],
        "filtered": log["filtered"], "warned": log["warned"],
        "forced_verdict_request_ignored": bool(flags.get("forced_verdict_request_ignored")),
        "notices": _unique(notices)})
    meta["flags"] = mflags
    report["x_meta"] = meta
    return report


def _deadline_sort(parsed: dict, dec: dict, ctx: _Ctx) -> str:
    cid = dec.get("depends_on_conflict") if dec.get("rule") == "S3" else None
    if cid and cid in ctx.conflicts:
        isos = [s.get("iso_local", "") for s in ctx.conflicts[cid].get("side_info") or [] if s.get("iso_local")]
        if isos:
            return min(isos)
    return parsed.get("iso_local", "") if parsed and parsed.get("ok") else "9999"


# ---------------------------------------------------------------- report.txt

def render_text(report: dict, lang: str | None = None) -> str:
    """Plain-text report in the 09 §2-10 order: banner, summary, counts, notices, top actions,
    checklist (D16 order), conflicts and gaps, questions, not-in-source, ignored instructions,
    pre-submission checklist, disclaimer."""
    meta = report.get("x_meta") or {}
    lang = lang or meta.get("output_lang") or "en"
    lang = lang if lang in LABELS else "en"
    L, out = LABELS[lang], []
    rows = {r["id"]: r for r in report["requirements"]}
    meta_rows = {m["id"]: m for m in meta.get("rows") or []}
    order = [i for i in meta.get("display_order") or [] if i in rows] or list(rows)
    if meta.get("banner"):
        out += [meta["banner"], ""]
    out.append(f"TenderCheck — {report['run_id']}")
    out.append(report["basis"])
    if meta.get("summary_line"):
        out.append(meta["summary_line"])
    sc = report["status_counts"]
    out.append(f"{L['counts']}: " + " · ".join(f"{L['status'][s]} {sc[s]}" for s in STATUSES))
    notices = meta.get("flags", {}).get("notices") or []
    types = type_labels({e["type"]: e["count"] for e in meta.get("flags", {}).get("pii_masked") or []}, lang)
    texts = []
    for key in notices:
        if key == "pii":
            if types:
                texts.append(fixed("pii", lang, types=types))
        elif key in FIXED and key not in ("basis", "disclaimer", "not_in_source", "questions_header", "injection_badge"):
            texts.append(fixed(key, lang))
    if texts:
        out += ["", f"{L['notices']}:"] + [f"- {x}" for x in texts]
    if meta.get("top_actions"):
        out += ["", f"{L['top']}:"] + [f"{k}. {a}" for k, a in enumerate(meta["top_actions"], 1)]
    if order:
        out += ["", f"{L['checklist']}:"]
    for rid in order:
        r, m = rows[rid], meta_rows.get(rid, {})
        state = f" ({L['cond'][r['condition_state']]})" if r["condition_state"] else ""
        out.append(f"{rid} [{L['kind'][r['kind']]}] {L['status'][r['status']]}{state}")
        if m.get("requirement"):
            out.append(f"  {L['requirement']}: {m['requirement']}")
        out.append(f"  {L['applies']}: {L['cond'][r['condition_state']] if r['condition_state'] else L['none']}")
        out.append(f"  {L['evidence']}: {', '.join(r['evidence_files']) or L['none']}")
        out.append(f"  {L['reason']}: {r['reason']}")
        out.append(f"  {L['action']}: {r['bidder_action']}")
        out.append(f"  {L['quote']}: \"{' '.join(r['quote'].split())}\" ({r['location']})")
        deps = [d for d in (r["depends_on"], r["depends_on_conflict"]) if d]
        if deps:
            out.append(f"  {L['depends']}: {', '.join(deps)}")
    if report["conflicts"] or meta.get("info_gaps"):
        out += ["", f"{L['conflicts']}:"]
        for c in report["conflicts"]:
            out.append(f"{c['id']} {c['topic']}")
            out += [f"  - {s['location']}: \"{' '.join(s['quote'].split())}\"" for s in c["sides"]]
        for g in meta.get("info_gaps") or []:
            out.append(f"- {L['gaps']}: {g if isinstance(g, str) else json.dumps(g, ensure_ascii=False)}")
    questions = meta.get("agency_questions") or [{"for": c["id"], "question": c["agency_question"]} for c in report["conflicts"]]
    if questions:
        out += ["", f"{fixed('questions_header', lang)}:"] + [f"- {q['for']}: {q['question']}" for q in questions]
    if report["bidder_questions"]:
        out += ["", f"{L['bidder_q']}:"] + [f"- {q}" for q in report["bidder_questions"]]
    if report["not_in_source"]:
        out += ["", f"{L['nis']}:"] + [f"- {x}" for x in report["not_in_source"]]
    if report["untrusted_instructions"]:
        out += ["", f"{L['untrusted']}: {fixed('injection_badge', lang)}"]
        out += [f"- \"{u['quote']}\" ({u['location']})" for u in report["untrusted_instructions"]]
    if meta.get("pre_submission_checklist"):
        out += ["", f"{L['pre']}:"] + list(meta["pre_submission_checklist"])
    out += ["", report["disclaimer"]]
    return "\n".join(out) + "\n"
