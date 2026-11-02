# Package R: a real public tender, pre-registered (2026-11-02)

Everything in this file was written before the first model call on package R. The key, the bidder list and the
rules below are committed first; the run log is added afterwards in a separate section and commit.

## Source (real)

| | |
|---|---|
| Notice | 평택시 공고 제2026-1272호, 「AI를 활용한 건축설계 인재양성 용역」 (Pyeongtaek City, Korea), notice date 2026-04-09 |
| Where | 나라장터 (g2b.go.kr), bid notice `R26BK01455093`, order `000`; attachments fileSeq 1 (공고문, HWPX), 2 (공고문, PDF), 3 (제안요청서, HWP) via `https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do?bidPbancNo=R26BK01455093&bidPbancOrd=000&fileType=&fileSeq=<n>&prcmBsneSeCd=03` |
| Downloaded | 2026-11-02 with curl, no login |
| Text | `packages/R/R-공고문.txt`: the HWPX paragraphs (table cells included, one per line). Dates, amounts, rule numbers and the 원본대조필 / 우편 lines were cross-checked against the PDF text (PyMuPDF): the same. `packages/R/R-제안요청서.txt`: the HWP through pyhwp `hwp5html`, table rows flattened to `\| cell \| cell \|`. Rows with no text in any cell are dropped and runs of blank lines collapsed; no other edit |
| Masking | One official's name (2 places in the 공고문) is replaced with `○○○`. Office phone numbers are kept as published |
| Use | Notices of national and local governments are not protected works under the Korean Copyright Act, Art. 7; the RFP is an attachment of the notice |

Size: about 30,900 characters (67,600 UTF-8 bytes), against about 3,600 for synthetic package B.

## What is synthetic

The bidder and its list are invented: `manifests/R-M1.txt`, (주)가온디지털아카데미(가상). `as_of` is
2026-04-17 09:00 KST, inside the notice window (the real deadline has passed). Five defects were planted, each tied to
a quoted rule:

| Key row | Rule (quoted in the key) | The list says | Expected |
|---|---|---|---|
| R01 | 가격입찰서(전자입찰) 제출: … 2026. 4. 20.(월) 10:00 | planned submission 2026-04-20 13:00 KST | error |
| R11, R14 | 사업자등록증 사본(원본대조필); 사본일 경우에는 “원본대조필” 후 인감날인 | 원본대조필 없음, 인감 날인 없음 | error, error |
| R15 | 용역이행 실적증명서 발급기간: 제출일 기준 6개월 이내 | issued 2025-09-30 (six months before 2026-04-20 is 2025-10-20) | error |
| R08 | ⑥ 보안서약서 1부. | not on the list | missing |
| R16 | ① 제안서 10부(업체명 표기 1부, 미표기 9부) | 출력본 9부 | missing |

The other 15 key rows are on the list as required and are expected `met` (R02 the visit-submission window
09:00 ~ 17:00, which 13:00 is inside; R06 is conditional on an agent submitting, and one does).

## Key and pass rule

- Key: `scenarios/answers/R.json`, 21 rows written by hand from the committed text, before any model call on
  package R. Expected counts over the key rows: met 15, missing 2, error 4. The engine never reads it.
- The key covers the obligations the bidder list touches. The engine will extract more records from a 31,000-character
  package; those are listed by the grader as extras and not scored.
- **Pass** = `python3 -m eval.grade regression R-M1` gives PASS on r1: every key row matched with the expected status,
  every quote found in the source, every evidence file on the list. Also reported whatever the verdict: rows equal
  (N/21), key rows expected non-met that came back `met` (the safety number), and the extras count.

## Run rules

- One live run, `runs/live_20261102_real_r1/R-M1`. **r1 is the run shown**, whatever it says. No rerun to improve it.
- If r1 stops (budget refusal, truncated reply, error), that is the result and it is reported.
- Caps: the ledger's per-run $2.00 / 9 billable requests, global stop $140.
- Output budgets: inputs longer than 20,000 characters get twice each stage's `max_output_tokens` (E1 65,536). This was
  set before the run, from B-M2's E1 reply (17,263 output tokens for 3,600 characters). Recorded calls are unaffected:
  the request hash does not include the budget.

## Limits known before the run

- Quotes are capped at 300 characters; some real clauses are longer, so the engine has to quote part of a line.
- Code has no rule for a number of printed copies (only per member and per person), so R16 can at best be flagged.
- Conflicts are not in the pass rule.

## Observations a person made reading the package, written before the run

These are in the tender itself, not planted. After the run we record which of them TenderCheck surfaced.

| # | Where | What |
|---|---|---|
| O1 | 공고문 5.라.2) vs 제안요청서 Ⅵ.1 | 질의응답 10분 (총 20분) vs 질의응답(15분) |
| O2 | 공고문 2.다, 5.마 vs 6.가 | 행정안전부 예규 제344호 vs 제325호 for the same 낙찰자 결정기준 |
| O3 | 공고문 7.나 vs 제안요청서 Ⅱ.6 | 입찰보증금: 낙찰금액의 100분의 5 vs 제안금액의 5/100 이상 |
| O4 | 제안요청서 Ⅵ 정량평가 table | past-performance criterion names "UX/UI 디자이너 양성 과정" in an architecture-design training tender |
| O5 | 공고문 3.마 | registration "입찰서 제출마감일 전일까지" = Sunday 2026-04-19, while the desk works weekdays only |
| O6 | 공고문 9 vs 제안요청서 Ⅴ.3.가⑤ | 청렴계약 이행서약서 "제출한 것으로 간주" vs 청렴이행서약서 1부 listed to submit |

---

## r1 result (added after the run, 2026-11-02)

`runs/live_20261102_real_r1/R-M1` (replies in `replays/R-M1/calls.jsonl`). Engine 127c4fd, key 5d057a5.

| | |
|---|---|
| Run | complete, 8 calls (U, E1, E2, E3, E4, E2 again, M, C), all `muse-spark-1.3`, 226 s of model time, **$0.4025** |
| E1 | 22,677 input / 29,989 output tokens (14,115 reasoning) |
| Records | 52 requirements: met 25, missing 1, error 4, conditional 5, needs-confirmation 17; conflicts 0, agency questions 0 |
| **Verdict** | **FAIL** (pre-registered rule): status match **11/21**; F2 (one quote differs from the source); one over-merged record |
| Safety | key rows expected non-met that came back `met`: **0 of 6** |

### The five planted defects

| Key row | Expected | Got | Why (from the report) |
|---|---|---|---|
| R08 보안서약서 | missing | **missing** | no file on the list |
| R11 사업자등록증 사본(원본대조필) | error | **error** | original/copy check failed |
| R15 실적증명서 6개월 | error | **error** | issued 2025-09-30, outside the window "in every counting convention" |
| R01 e-bid 10:00, planned 13:00 | error | needs-confirmation | the notice gives no time zone, so code does not compare; the action line still says to submit before 2026. 4. 20.(월) 10:00 |
| R16 제안서 10부, list 9부 | missing | needs-confirmation | no copy-count rule in code (known before the run); flagged for review |
| R14 사본 → 원본대조필 후 인감날인 | error | conditional (unknown) | the condition "사본일 경우" was not resolved from the list's 비고 "사본" |

### Rows expected met that came back otherwise

| Key row | Got | Cause |
|---|---|---|
| R10 사용인감계 / 법인인감증명서 | **error** | false alarm: a seal check was applied to the 법인인감증명서 row, a registry certificate that carries no bidder seal |
| R17 USB (제안서 + 발표 ppt) | **error** | false alarm: the file `.pptx` failed a format check against "ppt" |
| R02 visit window 09:00 ~ 17:00 | needs-confirmation | the date parser did not read a time range |
| R06 위임장·재직증명서 (대리인 제출 시) | conditional (unknown) | the list says an agent submits (비고), the condition was not resolved |
| R18 소기업·소상공인 확인서 | needs-confirmation | "유효기간 내" has no period in the tender text, so code could not decide |
| R03 입찰참가신청서 | unmatched | the engine's record R30 quotes the Ⅶ table row and is `met`; the key's anchor "입찰참가신청서 1부" does not cover that row. Scored as a miss as registered; noted here after the run |
| R12 법인 등기부등본 | unmatched | over-merged with 사업자등록증 in one record (R38, error) |

### Other findings

- F2: the record quoting "평택시청 청년정책과 … ☎031-8024-3571" carries `☎[MASKED:phone]`; office numbers in the tender are masked before the model, so the quote is checked against the masked text, not the published one.
- Three extra records were marked `met` on content rules a file list cannot show (A4 binding, page numbering, no company marks on evaluation copies): met because a proposal file exists. That is over-trust.
- None of the six observations O1–O6 was surfaced: C returned no conflicts and no questions.
- The doubled output budget was not needed this time: E1's 29,989 output tokens would have fit in 32,768.
