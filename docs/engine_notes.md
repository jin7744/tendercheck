# Engine notes — deterministic core (normalize, mask, injection, dates, checks)

Decisions taken where the design (09) or the spec (08) left room. Rule of thumb used: when two
readings were possible, take the one that cannot produce a false "met" (fail-closed: unknown
rather than pass).

Tests: `cd tendercheck && PYTHONUTF8=1 python3 -m unittest discover -s tests -v`
(stdlib only; expected values are literals from 08/09, never recomputed with engine code).

## N1 normalization (`engine/normalize.py`)

| # | Point | Decision |
|---|---|---|
| N-1 | `seg_id = <doc_id>-L<n>`: physical line or running count? | Physical 1-based line number of the document text. Blank lines, `[[page N]]` markers and the fixture header line (`[A-BODY]`) consume a number but are not segments. |
| N-2 | What text are quotes cut from (D1, D19)? | The canonical text: newlines unified, NFC applied once, then masked (D10). The model only sees masked text, so its quotes are matched against masked text. `sha256` is taken on the raw, pre-mask input (per doc; `source_sha256` over doc ids + doc hashes). |
| N-3 | Does a page marker break a block? | Yes. A block is a run of segment lines with no blank line and no page marker, so a section path never carries over a page break and a quote never spans pages. |
| N-4 | Where is `section_rules`? | `SECTION_RULES` in `engine/normalize.py` (contracts/ is owned elsewhere). It belongs in the freeze manifest (09 §6-5). |
| N-5 | Child tokens without a parent (`(a)` with no `§n`, `①`/`가.` with no `n.`) | No section ("not stated"); the token is not promoted to a top-level section. |
| N-6 | `별첨 서식` + `서식 제3호` would read `별첨 서식 서식 제3호` | When a section starts with the title's last word, the word is not repeated → `별첨 서식 제3호` (the 08 literal). |
| N-7 | 08 writes `Annex 2 footer` and `별첨 서식 제4호 하단` | 09's rule is followed: the footer line follows a blank line and has no number → `Annex 2 (section not stated)`; the Form 4 note line is in the same block as `[서식 제4호]` → `별첨 서식 제4호`. 08 T06 only checks that the location contains `Annex 2`. |
| N-8 | Page marker present but no section | `Body (section not stated) p.4`. |
| N-9 | Roman numerals | Only U+2160–U+2169 (`Ⅳ`) as written in 08; ASCII `IV.` is not a section token. |
| N-10 | Language of the not-stated label | Follows `output_lang` (EN `(section not stated)`, KO `(위치 미제공)`). |

## D10 masking (`engine/mask.py`)

- Synthetic markers `SYNTH-[A-Z]+-\d{4}-TEST` (08) and `SYNTH-[A-Z]+-TEST-ONLY` (A5): the type comes from the marker's middle token first (NAME/PHONE/RRN/ACCT/EMAIL/PW); the type word written right before the marker (담당자·주민등록번호·연락처·급여계좌…) is the fallback. 09 names the type word; both give the same type on every 08 and A5 marker. Only the marker is replaced; the type word stays.
- Shapes: RRN `6-7` digits; phone needs separators (`010-1234-5678`, `+82-10-…`); email; password value only after an explicit `:`/`=` label; account = 3–5 hyphen-joined digit groups with 10–16 digits in total. Dates (`2026-11-20`), ranges (`2026-12-02~2027-01-31`), `제2026-0412호`, `60:40`, `CMWA/ITM/2026/014` are not masked (tested). A 3-2-5 business registration number would be masked as an account (over-masking accepted).
- Originals are never stored, logged or returned; only the masked text and per-type counts.

## Injection and forced verdicts (`engine/injection.py`)

- One suspect per segment; quote = from the start of the clause holding the first hit to the end of the sentence holding the last hit, an exact substring of the segment (passes the 08 F2 check). Source text is never altered.
- Narrowed against false positives that would make Q8 delete a real requirement: `system:` only at line start or after punctuation (not "Submission system:"); `you are now` only before a/an/the/in/acting/operating (not "you are now required to"); `ignore … instructions` needs all/previous/prior/above/earlier/preceding/any.
- Forced-verdict patterns are whole words `eligible`, `compliant`, `submitted` (not `eligibility`/`compliance`), `drop the agency question`, KO `적격`, `제출 완료`, `접수 완료`, `문제없이 접수`, `질문은 빼/삭제/제외`. The KO default message (`…제출 파일을 점검…`) does not trigger.

## Dates (`engine/dates.py`)

- Time with no 오전/오후 is read as a 24-hour clock as written.
- `나라장터 서버 시각 기준` is not a written time zone → naive time + `TZ_NOT_STATED` (09 converts only when a zone is written; 08 notes it is KST but the code does not infer it).
- More than one date or time in `raw`, an invalid date, a bad offset, or zones that disagree (in `raw`, or between `raw` and `tz_quote`) → unparsed (`DATE_UNPARSED`).
- `tz_quote` is used by Q5 only after Q4 found it in a segment of the same document.
- Q5 compares only what the code read: date, then time if the source has one; offsets only when both sides carry one. The code's own reading is what later stages use.
- Validity counting conventions: `anchor_not_counted` = period starts the next day (start + N); `anchor_counted` = the anchor is day 1 (start + N − 1; for look-back windows the start moves one day later). Both are evaluated unless the source fixes it.
- `valid_for_from_issue`: a worked example in `raw` fixes the convention (08 §6 "1 March → 30 May" = `anchor_not_counted`); otherwise both. Pass needs issue ≤ anchor ≤ last valid day.
- `issued_within_before_anchor`: pass needs window start ≤ issue ≤ anchor. 09 F7 says "and ≤ as_of" while F8 says a future issue date is info only; fail-closed choice: issue after `as_of` turns a pass into `unknown` + `ISSUE_DATE_AFTER_AS_OF`.
- `valid_until_date`: the required date is the code's reading of `raw`; a model `until_iso` that disagrees makes it unknown (Q5: `DATE_MISMATCH`).
- Readings differ → `unknown`; by anchor → `CONFLICT_DEPENDENT`, by convention → `COUNTING_AMBIGUOUS` (both can apply, e.g. 08 B-M3 R11). All agree with >1 reading → `SAME_UNDER_ALL_READINGS`.
- Missing inputs → `unknown` + `ATTRIBUTE_UNKNOWN:<field>`; `issue_date`/`valid_until` are bidder facts, `anchor_date`/`period`/`required_until` are rule-side gaps (no bidder question).

## Checks Q1–Q8 (`engine/checks.py`)

- Q1 ladder exact → casefold → nospace is cumulative (nospace also casefolds). The first method with any occurrence decides. A quote must sit in one block and cover ≤ 3 lines; a multi-line quote keeps the source newline (08 grading collapses whitespace).
- Q2 prefers the occurrence covering the claimed segment; otherwise the first one and `LOCATION_CORRECTED`. Reported `seg_id`/`location` = first covered segment.
- Q6 fails when the claimed `seg_id` does not exist, even if Q2 could correct it; list-level Q6 also fails duplicate `tmp_id` and an `attribute_of` that names no record.
- Q4 `doc_code` is grounded if the code appears verbatim anywhere in the bundle (changed 2026-10-27 from "same document" in design 09 §4-3: package A lists checklist codes only in Annex 1 while 11 of 14 obligations are quoted from the Body, so the same-document rule failed 9 rows that should be `met`). A code that appears in no document still fails.
- Q4 also grounds `validity.days/months`, `member_rule` numbers and `amount.ratio_pct` (numbers matched with thousands separators removed; EN words one–twelve accepted), and requires keywords for the `sign_seal` value and `notarized=required`.
- Q8 removes a record when the model quote, the verified span or the requirement sentence matches an injection pattern, or the span overlaps an injection-suspect span. `INJECTION_AS_REQUIREMENT` is a flag, not a contract reason code.
- Q7 merges records with the same first segment and the same `norm08` quote into the first one.

## Open points for the pipeline owner

- **X-Fix (E2) can only requote or retract.** A Q4/Q5 failure on a field (`iso_local`, `until_iso`, `doc_code`, a number, `tz_quote`) cannot be repaired by E2 and ends as S2 (`needs-confirmation`) after two rounds.
- **`doc_code` grounding scope (decision needed).** 09 §4-3 says "same document"; the same row defines `doc_code` as the checklist code, and in package A the checklist exists only in Annex 1. Measured on the 08 §2.4 rows with checklist codes filled as a model would: 11 of 14 rows fail Q4 (R02, R03, R05, R06, R07, R08, R10, R11, R12, R14, R15), 9 of them expected `met` for A-M1 → T01 `status_counts` would fail. Options: (a) keep "same document" and have the prompt set `doc_code` only when the code occurs in the quote's own document; (b) ground `doc_code` in any document of the bundle (not a fabrication; one regex target change in `_q4` + flip `test_doc_code_must_be_in_same_document`). Current code = (a).
- Not in this module set: N2 manifest parsing, F1–F14 file checks, K1–K5 conflict candidates, the S0–S15 rule table, report rendering.
