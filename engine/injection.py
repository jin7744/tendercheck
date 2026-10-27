"""Instruction-like text inside source documents (design 09 §2-3, §2-11) and forced-verdict
requests in user messages (D6). Detection only: source text is never altered.
"""
from __future__ import annotations

import re

INJECTION_PATTERNS = (
    ("ignore_instructions_en",
     r"\b(?:ignore|disregard|forget)\s+(?:(?:all|any|the)\s+)*(?:previous|prior|above|earlier|preceding|all|any)"
     r"(?:\s+(?:the|previous|prior|above|earlier|preceding|other))*\s+(?:instructions?|directions|prompts?|rules)\b"),
    ("ignore_instructions_ko",
     r"(?:이전|앞의|위의|기존)\s*(?:의\s*)?(?:모든\s*)?지시(?:사항)?(?:을|를|은|는)?\s*(?:모두\s*|전부\s*|다\s*)?무시"),
    ("mark_all_en",
     r"\bmark\s+(?:all|every|each|everything)\b[^.\n]{0,40}?\bas\s+(?:met|compliant|passed|pass|satisfied|eligible|complete|ok)\b"),
    ("mark_all_ko",
     r"모든\s*항목(?:을|를|은|이)?\s*['\"‘’“”]?\s*충족\s*['\"‘’“”]?\s*(?:으로|로)\s*(?:표시|판정|처리)"),
    ("note_to_ai_en",
     r"\bnote\s+to\s+(?:the\s+|any\s+)?(?:automated|ai|llm|machine)(?:\s+(?:review|reviewing|evaluation))?"
     r"\s+(?:systems?|reviewers?|models?|assistants?|tools?)\b"),
    ("ai_reviewer_ko", r"검토하는\s*(?:AI|인공지능|에이아이|모델)"),
    ("role_system", r"(?:^|[.:;!?<>\[\]()]\s*)system\s*:"),
    ("you_are_now", r"\byou\s+are\s+now\s+(?:a|an|the|in|acting|operating)\b"),
)
_INJECTION_RES = [(pid, re.compile(rx, re.I | re.M)) for pid, rx in INJECTION_PATTERNS]

FORCED_VERDICT_PATTERNS = (
    ("eligible_en", r"\b(?:in)?eligible\b"),
    ("compliant_en", r"\b(?:non-?)?compliant\b"),
    ("submitted_en", r"\bsubmitted\b"),
    ("drop_question_en", r"\b(?:drop|remove|skip|omit|delete)\s+(?:the\s+|all\s+|any\s+)?(?:agency\s+)?questions?\b"),
    ("eligible_ko", r"적격"),
    ("submitted_ko", r"제출\s*(?:이\s*)?완료|접수\s*완료"),
    ("accepted_ko", r"문제\s*없이\s*접수"),
    ("drop_question_ko", r"질문(?:은|을|는)?\s*(?:빼|삭제|제외|없애|생략)"),
)
_FORCED_RES = [(pid, re.compile(rx, re.I)) for pid, rx in FORCED_VERDICT_PATTERNS]

_CLAUSE_START = re.compile(r"(?:[.!?。]|:)\s+")
_SENTENCE_END = re.compile(r"[.!?。](?=\s|$)")


def match_injection(text: str) -> list[str]:
    """Pattern ids found in `text` (used by Q8 on quotes and requirement sentences)."""
    return [pid for pid, rx in _INJECTION_RES if rx.search(text)]


def _clause_span(text: str, first: int, last: int) -> tuple[int, int]:
    start = 0
    for m in _CLAUSE_START.finditer(text, 0, first):
        start = m.end()
    m = _SENTENCE_END.search(text, last)
    end = m.end() if m else len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def find_in_segment(text: str) -> dict | None:
    """One merged span per segment: from the clause holding the first hit to the end of the
    sentence holding the last hit. Returns {start, end, quote, patterns} or None."""
    hits = [(m.start(), m.end(), pid) for pid, rx in _INJECTION_RES for m in rx.finditer(text)]
    if not hits:
        return None
    first = min(h[0] for h in hits)
    last = max(h[1] for h in hits)
    start, end = _clause_span(text, first, last)
    start, end = min(start, first), max(end, last)
    return {"start": start, "end": end, "quote": text[start:end],
            "patterns": sorted({h[2] for h in hits}, key=[p for p, _ in INJECTION_PATTERNS].index)}


def find_injection_suspects(segments: list[dict]) -> list[dict]:
    """`segments` = SourceBundle segments. Quote is an exact substring of the segment text."""
    out = []
    for seg in segments:
        hit = find_in_segment(seg["text"])
        if hit:
            out.append({"doc_id": seg["doc_id"], "seg_id": seg["seg_id"], "location": seg["location"], **hit})
    return out


def detect_forced_verdict(message: str) -> list[str]:
    """Pattern ids of a user request to assert eligibility/submission or drop agency questions.
    The message itself is never echoed."""
    return [pid for pid, rx in _FORCED_RES if rx.search(message)]
