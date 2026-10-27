"""N1 input normalization: raw text documents -> SourceBundle (design 09 §2-3, §4-2).

Segment = one non-empty line, seg_id `<doc_id>-L<n>` with n = physical 1-based line number.
Block = run of segment lines not interrupted by a blank line or a `[[page N]]` marker.
Section paths and location labels are computed here from `SECTION_RULES`; the model never
writes a location (D2).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from engine.injection import find_injection_suspects
from engine.mask import counts_list, mask_text, merge_counts

DOC_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,15}$")
BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
DOC_TYPES = ("notice", "rfp", "annex", "forms", "qa", "other")
OUTPUT_LANGS = ("en", "ko")
PAGE_MARKER_RE = re.compile(r"^\s*\[\[page\s+(\d+)\]\]\s*$", re.I)
NOT_STATED = {"en": "(section not stated)", "ko": "(위치 미제공)"}

# Line-head section tokens, matched after leading whitespace is stripped (first match wins).
# `top`: label when the token starts a new path. `under`: {parent rule id: child label}; the
# child is appended to the nearest such parent in the current block. A token with `under` but
# no `top` and no parent in the block gives no section (not stated).
SECTION_RULES = (
    {"id": "sec", "pattern": r"§\s*(\d+)(?!\d)", "top": "§{0}"},
    {"id": "itb", "pattern": r"ITB\s+(\d+(?:\.\d+)*)\b", "top": "ITB {0}"},
    {"id": "form", "pattern": r"Form\s+([A-Z]{1,5}-\d+)\b", "top": "Form {0}"},
    {"id": "checklist", "pattern": r"Submission\s+Checklist\b", "top": "Checklist"},
    {"id": "form_ko", "pattern": r"\[서식\s*제\s*(\d+)\s*호\]", "top": "서식 제{0}호"},
    {"id": "roman", "pattern": r"([Ⅰ-Ⅹ])\.(?=\s|$)", "top": "{0}"},
    {"id": "arabic", "pattern": r"(\d{1,2})\.(?=\s|$)", "top": "{0}.", "under": {"roman": "-{0}"}},
    {"id": "paren", "pattern": r"\(([a-z]|[ivx]+)\)(?=\s|$)", "under": {"sec": "({0})"}},
    {"id": "circled", "pattern": r"([①-⑳])", "under": {"arabic": "{0}"}},
    {"id": "hangul", "pattern": r"([가나다라마바사아자차카타파하])\.(?=\s|$)", "under": {"arabic": "{0}"}},
)
_RULES = [(r, re.compile(r["pattern"])) for r in SECTION_RULES]


def _next_path(path: list[tuple[str, str]], line: str) -> list[tuple[str, str]] | None:
    """New section path if `line` starts with a section token, else None (inherit)."""
    head = line.lstrip()
    for rule, rx in _RULES:
        m = rx.match(head)
        if not m:
            continue
        for i in range(len(path) - 1, -1, -1):
            parent = path[i][0]
            if parent in rule.get("under", {}):
                return path[:i + 1] + [(rule["id"], rule["under"][parent].format(*m.groups()))]
        if "top" in rule:
            return [(rule["id"], rule["top"].format(*m.groups()))]
        return []
    return None


def location_label(title: str, section: str, page: int, lang: str) -> str:
    """`{title} {section}` (+ ` p.N`); no section -> `{title} (section not stated)` / `(위치 미제공)`.
    A section starting with the title's last word does not repeat it (`별첨 서식` + `서식 제3호`)."""
    if section:
        t_last = title.split()[-1] if title.split() else ""
        s_first, _, s_rest = section.partition(" ")
        label = f"{title} {s_rest}" if s_rest and s_first == t_last else f"{title} {section}"
    else:
        label = f"{title} {NOT_STATED[lang]}"
    return f"{label} p.{page}" if page else label


def detect_lang(text: str) -> str:
    hangul = sum("가" <= c <= "힣" for c in text)
    latin = sum(c.isascii() and c.isalpha() for c in text)
    if hangul == 0:
        return "en"
    if latin == 0:
        return "ko"
    ratio = hangul / (hangul + latin)
    return "ko" if ratio >= 0.8 else "en" if ratio <= 0.2 else "mixed"


def canonical_text(raw: str) -> str:
    """Newlines unified and NFC applied once; this is the text quotes are cut from (D1, D19)."""
    return unicodedata.normalize("NFC", raw.replace("\r\n", "\n").replace("\r", "\n"))


def _segment_doc(doc_id: str, title: str, text: str, header: str, lang: str):
    segments, blocks = [], []
    path: list[tuple[str, str]] = []
    page = 0
    block: list[str] = []
    header_pending = bool(header)

    def close_block():
        nonlocal block, path
        if block:
            blocks.append({"block_id": f"{doc_id}-B{len(blocks) + 1}", "doc_id": doc_id, "seg_ids": block})
        block, path = [], []

    for n, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            close_block()
            continue
        m = PAGE_MARKER_RE.match(line)
        if m:
            close_block()
            page = int(m.group(1))
            continue
        if header_pending:
            header_pending = False
            if line.strip() == header:
                close_block()
                continue
        new = _next_path(path, line)
        if new is not None:
            path = new
        section = "".join(p for _, p in path)
        seg_id = f"{doc_id}-L{n}"
        block.append(seg_id)
        segments.append({
            "seg_id": seg_id, "doc_id": doc_id, "line": n, "block": f"{doc_id}-B{len(blocks) + 1}",
            "page": page, "text": line, "section": section,
            "location": location_label(title, section, page, lang),
        })
    close_block()
    return segments, blocks


def build_source_bundle(docs: list[dict], *, bundle_id: str = "bundle", output_lang: str = "en",
                        as_of: str = "", source_lang: str | None = None) -> dict:
    """docs: [{doc_id, title, text, doc_type?, header?, input_kind?, pdf_ref?}].
    `header` (e.g. "[A-BODY]") is skipped when it is the first non-empty line of `text`."""
    if not BUNDLE_ID_RE.match(bundle_id):
        raise ValueError(f"bad bundle_id {bundle_id!r}")
    if output_lang not in OUTPUT_LANGS:
        raise ValueError(f"bad output_lang {output_lang!r}")
    seen: set[str] = set()
    out_docs, segments, blocks, counts = [], [], [], {}
    src_hash = hashlib.sha256()
    for d in docs:
        doc_id, title = d["doc_id"], d["title"].strip()
        if not DOC_ID_RE.match(doc_id) or doc_id in seen:
            raise ValueError(f"bad or duplicate doc_id {doc_id!r}")
        if not title:
            raise ValueError(f"empty title for {doc_id}")
        doc_type = d.get("doc_type", "other")
        if doc_type not in DOC_TYPES:
            raise ValueError(f"bad doc_type {doc_type!r}")
        seen.add(doc_id)
        raw = d.get("text", "")
        raw_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        src_hash.update(f"{doc_id}\0{raw_sha}\0".encode("utf-8"))
        masked = mask_text(canonical_text(raw))
        counts = merge_counts(counts, masked.counts)
        segs, blks = _segment_doc(doc_id, title, masked.text, d.get("header", ""), output_lang)
        segments += segs
        blocks += blks
        out_docs.append({
            "doc_id": doc_id, "doc_type": doc_type, "title": title,
            "input_kind": d.get("input_kind", "text"), "text": masked.text,
            "pdf_ref": d.get("pdf_ref", ""), "sha256": raw_sha,
        })
    all_text = "\n".join(s["text"] for s in segments)
    return {
        "bundle_id": bundle_id,
        "source_lang": source_lang or detect_lang(all_text),
        "output_lang": output_lang,
        "as_of": as_of,
        "docs": out_docs,
        "segments": segments,
        "blocks": blocks,
        "source_sha256": src_hash.hexdigest(),
        "source_missing": not segments,
        "pii_masked": counts_list(counts),
        "injection_suspects": find_injection_suspects(segments),
    }


def segment_map(bundle: dict) -> dict[str, dict]:
    return {s["seg_id"]: s for s in bundle["segments"]}


def render_segments(bundle: dict, doc_id: str | None = None) -> str:
    """Model input lines `[A_BODY-L12] text` (D1)."""
    return "\n".join(f"[{s['seg_id']}] {s['text'].strip()}" for s in bundle["segments"]
                     if doc_id is None or s["doc_id"] == doc_id)
