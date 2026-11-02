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
