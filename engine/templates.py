"""Question templates (design 09 §2-9) and the QV1-QV5 checks on agency questions.

Agency questions: the model may draft one (stage C); a draft that fails QV1-QV5 is replaced by
the template here. Bidder questions are templates only, keyed `fact:<fact_key>` / `attr:<field>`.
"""
from __future__ import annotations

import re

from engine import textfilter
from engine.checks import norm08
from engine.mask import mask_text

YES_NO = {"en": "(Yes/No)", "ko": "(예/아니오)"}
MAX_QUESTION_CHARS = 300

# ---------------------------------------------------------------- Korean particles

_DIGIT_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True,
                "8": True, "9": False}
_DIGIT_RIEUL = set("178")
_ROMAN = {"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5", "Ⅵ": "6", "Ⅶ": "7", "Ⅷ": "8", "Ⅸ": "9", "Ⅹ": "10"}
_TRAILING = " '\"‘’“”"


def particle(word: str, pair: str) -> str:
    """Korean particle for `word`: pair is 'with-final/without-final', e.g. '은/는', '이/가',
    '을/를', '과/와', '으로/로'. Unknown endings (Latin letters, brackets) give e.g. '은(는)'."""
    with_final, without = pair.split("/")
    w = (word or "").rstrip(_TRAILING)
    ch = w[-1:] if w else ""
    if ch in _ROMAN:
        ch = _ROMAN[ch][-1] if ch != "Ⅹ" else "X"
    if "가" <= ch <= "힣":
        final = (ord(ch) - 0xAC00) % 28
        has, rieul = final != 0, final == 8
    elif ch.isdigit():
        has, rieul = _DIGIT_FINAL[ch], ch in _DIGIT_RIEUL
    elif ch == "X":
        has, rieul = True, False
    else:
        return f"{with_final}({without})"
    if pair == "으로/로" and rieul:
        return without
    return with_final if has else without


def josa(word: str, pair: str) -> str:
    return f"{word}{particle(word, pair)}"


# ---------------------------------------------------------------- agency questions

CONFLICT_TOPICS = {
    "K1": {"en": "deadline", "ko": "마감"},
    "K2": {"en": "file size", "ko": "파일 용량"},
    "K3": {"en": "file format", "ko": "파일 형식"},
    "K4": {"en": "signature or seal", "ko": "서명·날인"},
    "K5": {"en": "validity", "ko": "유효기간"},
    "model": {"en": "requirement", "ko": "요구사항"},
}
DEADLINE_SUBJECTS = {
    "submission": {"en": "deadline for submission", "ko": "제출 마감"},
    "questions": {"en": "deadline for questions", "ko": "질의 마감"},
    "clarification": {"en": "deadline for clarification requests", "ko": "설명 요청 마감"},
    "opening": {"en": "bid opening time", "ko": "개찰 일시"},
    "other": {"en": "deadline", "ko": "마감"},
}
CONFLICT_SUBJECTS = {
    "K2": {"en": "maximum file size", "ko": "파일 용량 상한"},
    "K3": {"en": "required file format", "ko": "제출 파일 형식"},
}


def conflict_topic(rule: str, lang: str) -> str:
    return CONFLICT_TOPICS.get(rule, CONFLICT_TOPICS["model"])[lang]


def conflict_subject(rule: str, lang: str, deadline_kind: str = "submission") -> str:
    if rule == "K1":
        return DEADLINE_SUBJECTS.get(deadline_kind, DEADLINE_SUBJECTS["other"])[lang]
    return CONFLICT_SUBJECTS.get(rule, {"en": "", "ko": ""})[lang]


def agency_conflict_question(lang: str, loc_a: str, val_a: str, loc_b: str, val_b: str, subject: str = "") -> str:
    """09 §2-9 conflict templates (08 Q-A1 / Q-B1 shape). Without a subject the question asks
    whether the statement at loc_a applies (used for clause-valued conflicts)."""
    if lang == "ko":
        head = f"{josa(loc_a, '은/는')} '{val_a}', {josa(loc_b, '은/는')} '{val_b}'{particle(val_b, '으로/로')} 적혀 있습니다."
        if subject:
            return f"{head} {josa(subject, '은/는')} {val_a}입니까? {YES_NO['ko']}"
        return f"{head} {loc_a}의 내용이 적용됩니까? {YES_NO['ko']}"
    if subject:
        return f"{loc_a} states {val_a}, but {loc_b} states {val_b}. Is the {subject} {val_a}? {YES_NO['en']}"
    return f"{loc_a} states '{val_a}', but {loc_b} states '{val_b}'. Does the statement in {loc_a} apply? {YES_NO['en']}"


def agency_counting_question(lang: str, phrase: str, anchor: str, n: int, *, unit: str = "days",
                             document: str = "") -> str:
    """09 §2-9 counting template (08 Q-B2 shape): does the period start the day after the anchor?"""
    if lang == "ko":
        span = f"{n}개월" if unit == "months" else f"{n}일"
        head = f"{document}의 '{phrase}'" if document else f"'{phrase}'"
        return f"{head}{particle(phrase, '은/는')} {anchor} 다음 날부터 {span}{particle(span, '을/를')} 센다는 뜻입니까? {YES_NO['ko']}"
    span = f"{n} months" if unit == "months" else f"{n} days"
    where = f" in the {document}" if document else ""
    return f"Does '{phrase}'{where} mean counting {span} starting the day after {anchor}? {YES_NO['en']}"


# ---------------------------------------------------------------- bidder questions

FACT_QUESTIONS = {
    "consortium": {"en": "Are you bidding as a joint venture (JV)? (Yes/No)", "ko": "공동수급으로 참가합니까? (예/아니오)"},
    "usage_seal": {"en": "Did you seal the documents with an authorized-use seal instead of the registered company seal? (Yes/No)",
                   "ko": "제출서류에 사용인감을 날인했습니까? (예/아니오)"},
    "foreign_entity": {"en": "Are you bidding as a foreign entity? (Yes/No)", "ko": "외국 업체로 참가합니까? (예/아니오)"},
    "subcontracting": {"en": "Will you subcontract part of the work? (Yes/No)", "ko": "일부 업무를 하도급할 예정입니까? (예/아니오)"},
    "sme": {"en": "Is your company a small or medium-sized enterprise (SME)? (Yes/No)", "ko": "중소기업에 해당합니까? (예/아니오)"},
    "other": {"en": "Does the condition stated in {context} apply to your bid? (Yes/No)",
              "ko": "{context}에 적힌 조건이 이번 입찰에 해당합니까? (예/아니오)"},
}
ATTR_QUESTIONS = {
    "headcount": {"en": "How many proposed staff are there?", "ko": "투입 예정 인력은 몇 명입니까?"},
    "issue_date": {"en": "What is the issue date of {document}?", "ko": "{document}의 발급일은 언제입니까?"},
    "valid_until": {"en": "Until what date is {document} valid?", "ko": "{document}의 유효기간은 언제까지입니까?"},
    "signature_count": {"en": "How many signed pages does {document} have?", "ko": "{document}의 자필 서명은 몇 매입니까?"},
    "notarized": {"en": "Is {document} notarized? (Yes/No)", "ko": "{document}{은/는} 공증을 받았습니까? (예/아니오)"},
    "signed": {"en": "Is {document} signed? (Yes/No)", "ko": "{document}에 서명이 있습니까? (예/아니오)"},
    "sealed": {"en": "Does {document} bear a seal? (Yes/No)", "ko": "{document}에 인감이 날인되어 있습니까? (예/아니오)"},
    "seal_kind": {"en": "Which seal is on {document}: the registered company seal or an authorized-use seal?",
                  "ko": "{document}에 법인인감과 사용인감 중 어느 인감을 날인했습니까?"},
    "signed_by": {"en": "Who signed {document}?", "ko": "{document}에는 누가 서명했습니까?"},
    "copy_kind": {"en": "Is {document} an original, a copy or a certified copy?",
                  "ko": "{document}{은/는} 원본, 사본, 원본대조필 사본 중 무엇입니까?"},
    "amount": {"en": "What amount does {document} state?", "ko": "{document}에 적힌 금액은 얼마입니까?"},
    "members": {"en": "Which members are in your bid, and what are their shares?", "ko": "구성원과 지분율은 어떻게 됩니까?"},
    "size_mb": {"en": "What is the file size of {document} in MB?", "ko": "{document}의 파일 용량은 몇 MB입니까?"},
}
ATTR_ALIASES = {"member": "members", "member_count": "members", "share": "members", "share_pct": "members",
                "expiry": "valid_until", "guarantee_period": "valid_until", "signers": "signed_by",
                "seal": "sealed", "signature": "signed", "size": "size_mb"}
GENERIC_ATTR = {"en": "What is the {field} of {document}?", "ko": "{document}의 {field}{은/는} 무엇입니까?"}
DEFAULT_DOCUMENT = {"en": "this document", "ko": "해당 서류"}
DEFAULT_CONTEXT = {"en": "the source", "ko": "원문"}


def _fill(template: str, **kw) -> str:
    out = template
    for key, value in kw.items():
        for pair in ("은/는", "이/가", "을/를"):
            out = out.replace("{" + key + "}{" + pair + "}", josa(value, pair))
        out = out.replace("{" + key + "}", value)
    return out


def bidder_question(key: str, lang: str, *, document: str = "", context: str = "") -> str:
    """Bidder question for 'fact:<fact_key>' or 'attr:<field>' (09 §2-9)."""
    kind, _, name = key.partition(":")
    doc = document or DEFAULT_DOCUMENT[lang]
    if kind == "fact":
        tpl = FACT_QUESTIONS.get(name, FACT_QUESTIONS["other"])[lang]
        return _fill(tpl, context=context or DEFAULT_CONTEXT[lang])
    name = ATTR_ALIASES.get(name, name)
    if name in ATTR_QUESTIONS:
        return _fill(ATTR_QUESTIONS[name][lang], document=doc)
    return _fill(GENERIC_ATTR[lang], document=doc, field=name.replace("_", " "))


# ---------------------------------------------------------------- QV1-QV5

_OPTIONS_TAIL = re.compile(r"\s*\((?:yes\s*/\s*no|예\s*/\s*아니(?:오|요))\)\s*$", re.IGNORECASE)


def check_question(question: str, *, values=()) -> dict:
    """QV1 ends with a question mark (an answer-options tail such as '(Yes/No)' is allowed after it),
    QV2 <= 300 chars, QV3 contains every conflict value token (dates, times, numbers cut from the two
    quotes by code) as written, case aside, QV4 no A-grade phrase, QV5 no unmasked sensitive value.
    Returns {ok, failures}."""
    failures = []
    text = norm08(question or "")
    core = _OPTIONS_TAIL.sub("", text)
    if not core.endswith(("?", "？")):
        failures.append("QV1")
    if len(text) > MAX_QUESTION_CHARS:
        failures.append("QV2")
    if any(not norm08(v) or norm08(v).casefold() not in text.casefold() for v in values):
        failures.append("QV3")
    if textfilter.blocked(question or ""):
        failures.append("QV4")
    if mask_text(question or "").counts:
        failures.append("QV5")
    return {"ok": not failures, "failures": failures}
