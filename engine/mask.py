"""D10 masking: sensitive values become [MASKED:type] before any model call, log or report.

Only the masked text and per-type counts leave this module. Original values are never
stored, logged or returned.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 08 format SYNTH-NAME-0001-TEST and A5 format SYNTH-NAME-TEST-ONLY.
SYNTH_RE = re.compile(r"SYNTH-([A-Z]+)-(?:\d{4}-TEST|TEST-ONLY)")
SYNTH_TOKEN_TYPES = {
    "NAME": "name", "PHONE": "phone", "TEL": "phone", "RRN": "rrn",
    "ACCT": "account", "ACCOUNT": "account", "EMAIL": "email", "PW": "password", "PASSWORD": "password",
}
# Type word written immediately before a marker (fallback when the marker token is unknown).
TYPE_WORDS = (
    ("rrn", r"주민등록번호|주민번호"),
    ("account", r"계좌(?:번호)?|account(?:\s+(?:no\.?|number))?"),
    ("phone", r"연락처|전화(?:번호)?|휴대폰|phone|tel\.?"),
    ("email", r"이메일|e-?mail"),
    ("password", r"비밀번호|password"),
    ("name", r"담당자|성명|이름|name"),
)
_TYPE_WORD_RES = [(t, re.compile(rf"(?:{w})\s*[:：]?\s*\(?\s*$", re.I)) for t, w in TYPE_WORDS]

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
RRN_RE = re.compile(r"(?<![\d-])\d{6} ?- ?\d{7}(?![\d-])")
PHONE_RE = re.compile(r"(?<![\w-])(?:\+\d{1,3}[ -]?)?(?:\(0?\d{1,3}\)|0?\d{1,3})[ .-]\d{3,4}[ .-]\d{4}(?![\w-])")
PASSWORD_RE = re.compile(r"(\b(?:password|passwd|pwd)|비밀번호|암호)(\s*[:=：]\s*)(?!\[MASKED:)(\S+)", re.I)
ACCOUNT_RE = re.compile(r"(?<![\w-])\d{2,6}(?:-\d{2,7}){2,4}(?![\w-])")
ACCOUNT_DIGITS = (10, 16)

PII_TYPE_LABELS = {
    "en": {"name": "name", "rrn": "resident registration number", "phone": "phone number",
           "account": "bank account", "email": "email", "password": "password", "synthetic": "synthetic marker"},
    "ko": {"name": "성명", "rrn": "주민등록번호", "phone": "연락처", "account": "계좌",
           "email": "이메일", "password": "비밀번호", "synthetic": "합성 표식"},
}
TYPE_ORDER = ("name", "rrn", "phone", "account", "email", "password", "synthetic")


@dataclass
class MaskResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def _token(kind: str) -> str:
    return f"[MASKED:{kind}]"


def _synth_type(text: str, start: int, token: str) -> str:
    if token in SYNTH_TOKEN_TYPES:
        return SYNTH_TOKEN_TYPES[token]
    before = text[max(0, start - 24):start]
    for kind, rx in _TYPE_WORD_RES:
        if rx.search(before):
            return kind
    return "synthetic"


def mask_text(text: str) -> MaskResult:
    counts: dict[str, int] = {}

    def bump(kind: str) -> str:
        counts[kind] = counts.get(kind, 0) + 1
        return _token(kind)

    text = SYNTH_RE.sub(lambda m: bump(_synth_type(m.string, m.start(), m.group(1))), text)
    text = EMAIL_RE.sub(lambda m: bump("email"), text)
    text = RRN_RE.sub(lambda m: bump("rrn"), text)
    text = PHONE_RE.sub(lambda m: bump("phone"), text)
    text = PASSWORD_RE.sub(lambda m: m.group(1) + m.group(2) + bump("password"), text)

    def account(m: re.Match) -> str:
        digits = sum(c.isdigit() for c in m.group(0))
        if ACCOUNT_DIGITS[0] <= digits <= ACCOUNT_DIGITS[1]:
            return bump("account")
        return m.group(0)

    text = ACCOUNT_RE.sub(account, text)
    return MaskResult(text, counts)


def merge_counts(*counts: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in counts:
        for k, v in c.items():
            out[k] = out.get(k, 0) + v
    return out


def counts_list(counts: dict[str, int]) -> list[dict]:
    """x_meta.flags.pii_masked shape: [{type, count}] in a fixed type order."""
    order = {t: i for i, t in enumerate(TYPE_ORDER)}
    return [{"type": t, "count": counts[t]} for t in sorted(counts, key=lambda t: (order.get(t, 99), t)) if counts[t]]


def type_labels(counts: dict[str, int], lang: str) -> str:
    """Display list for the fixed `pii` notice, e.g. '성명·주민등록번호·연락처·계좌'."""
    sep = "·" if lang == "ko" else ", "
    return sep.join(PII_TYPE_LABELS[lang].get(e["type"], e["type"]) for e in counts_list(counts))
