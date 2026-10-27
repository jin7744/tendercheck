"""Text rules transcribed from spec 08: §4.1-2 normalization, §1.4 forbidden phrases B1-B7, §4.1-8 PII token."""
import re
import unicodedata

_QUOTES_DASHES = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
})
_WS = re.compile(r"\s+")


def normalize(text):
    text = unicodedata.normalize("NFC", text).translate(_QUOTES_DASHES)
    return _WS.sub(" ", text).strip()


# 08 §1.4, case-insensitive. Korean entries are plain strings (no regex metacharacters).
FORBIDDEN_SOURCE = {
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
FORBIDDEN = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in FORBIDDEN_SOURCE.items()}
COMMON_SETS = ("B1", "B2", "B3", "B4", "B5", "B6")

PII_TOKEN = re.compile(r"SYNTH-[A-Z]+-\d{4}-TEST")


def forbidden_hits(fields, sets=COMMON_SETS):
    hits = []
    for path, text in fields:
        norm = normalize(text)
        for set_id in sets:
            for rx in FORBIDDEN[set_id]:
                m = rx.search(norm)
                if m:
                    hits.append({"set": set_id, "pattern": rx.pattern, "field": path, "match": m.group(0)})
    return hits
