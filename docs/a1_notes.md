# A1 notes — N2 manifest (`engine/manifest.py`) and F1–F14 (`engine/filechecks.py`)

## Deviations / additions to `pipeline_interfaces.md` (read first)

Nothing in §1/§4 was renamed or removed. Additions only:

| # | Where | What | Why |
|---|---|---|---|
| X1 | Manifest | top-level `lang` (`en`/`ko`) | F3 needs it for the KO `(주)` ban. |
| X2 | Profile | `member_count` (int, 0 = not stated; 1 for `single`) | F12/F14 need the stated count ("2 members", "2개사"); `members` alone can be incomplete. |
| X3 | Manifest `masked_text` | table header prefixed `row_id | `, each row prefixed `M<n> | `; header lines and cells otherwise unchanged (masked) | §1 says "re-rendered … for model M"; M must answer with row ids, so they are in the text. |
| X4 | FileCheck | `general_rule` (bool) | Tells the caller F1–F3 ran on every manifest row. |
| X5 | FileCheck `readings` | keyed by check id: `readings["F1"…"F11"]` = per-row entries `{row_id, file_name, result, detail, …}`; `readings["F7"]` entries also carry `issue_date`, `valid_until`, `readings` (from `evaluate_validity`), `split_by`, `conventions`, `reason_codes`, `info` | §4 leaves `readings` open; the report needs violating file names and the per-deadline limits. |
| X6 | FileCheck, applicable check with zero matched rows | `unknown` + `ATTRIBUTE_UNKNOWN:matched_file` (not `not_applicable`) | Fail-closed: an attribute requirement whose parent exists but has no matched row must not reach S15 `met`. `matched_file` is not put in `unknown_fields` (not a bidder fact). |

## Corrections to the orchestrator brief (08 / fixtures win)

- **A-M2 is a 2-member JV**, not 3: `Bidding as: JV, 2 members: …` (08 §2.3); 08 §2.5 R15 M2 "구성원 2개사인데 BF4는 1개". Tests assert 2.
- **B-M3 seal type is `unknown`** (`날인 인감: (기재 없음)`; 08 §3.5 R07 M3 "날인 인감 종류 기재 없음"). The usage-seal case is B-M1 (`날인 인감: 사용인감`, R07 M1 triggered).

## Interpretations of 08 / 09

| # | Point | Decision |
|---|---|---|
| I1 | Tokens (09 §2-3) | `(기재 없음)`, `(not stated)`, `확인 안 됨` → unknown. `-` is context-dependent: in `서명/날인` → signed/sealed `no`; in `발급일` → `"unknown"` (09 §4-2). `(병합)` size → `"merged"`; empty size → `"unknown"`. |
| I2 | Masking | Every line is masked with `engine.mask.mask_text` before parsing, including the label line and header lines that cannot be parsed (they go to `warnings` already masked). `pii_counts` = sum over all lines; row `pii` = that row's line had a masked value. |
| I3 | KO `서명/날인` cell | Split on commas; `날인/인감/직인` → sealed, `서명` → signed, `원본대조필` → `parsed.certified_mark`; a part with `없음/미날인/누락…` negates that part (B-M2 R04 "원본대조필 없음, 날인 없음" → sealed `no`, mark `no`). A fact not mentioned stays `unknown` (e.g. "사용인감 날인" → signed `unknown`). Seal kinds: 사용인감 → `usage`, 법인인감 → `corporate`, bare 인감 → `unspecified`. |
| I4 | Profile facts | Only from the header: `consortium` from participation, `usage_seal` from `날인 인감`. Row text never sets them (08 R07 M3: "인감 날인"만 있음 → unknown). `foreign_entity`, `subcontracting`, `sme` always `unknown` (the 08 format has no field). |
| I5 | Members | Trailing parenthesis groups are annotations only when they contain lead/대표/지분/%/가상/fictional, so `(주)` prefixes survive (`(주)별누리코드`). Stated count ≠ listed names → `member_count = 0` + warning. Single firm: `members = []`, `member_count = 1`. |
| I6 | Planned upload | `engine.dates.parse_datetime_text`; kept only with date, time and zone (`2026-11-30 20:30 KST (UTC+09:00)` → `2026-11-30T20:30:00+09:00`); otherwise `""` + warning (zone never invented). |
| I7 | `parsed` keys | 09 §4-2 keys plus: `certified_mark`, `seal_kinds`, `merged_file`, `member_role` (`(lead member)`), `seal_member_count` / `sign_member_count` (`구성원 2개사 인감 날인`, or `"all"` for 전원). `copy_kind` may also be `original`. KO member rows: note `구성원` + label `서류(이름)` → `member = 이름`. `투입 예정 인력 수 기재 없음` sets no `headcount`. |
| I8 | Bundles | `MB<n>`: rows sharing a file name (≥2), or one row with a merged marker (`one merged file:`, `병합 파일`, `(병합)`). |
| I9 | General file rules | `is_general_rule`: format fields set (`extensions`/`max_size_mb`/`filename_template`), no `attribute_of`, no `doc_code`, not a merge rule, category not `document`/`merge_rule`/`validity`. These run F1–F3 on **every** manifest row (deduplicated by file name) whatever `rows` holds. Format fields on a document requirement run on its matched rows only — so 08 B-M2 R02 (.hwp) and A-M2 R10 (14.2 MB) stay clean and the violation lands on R15/R18 only. |
| I10 | F1 | Extension from the file name (lowercase, no dot); declared format used only when there is no extension; the two disagreeing across the allowed set → `unknown`. |
| I11 | F2 | `Decimal` comparison, `size <= limit` passes (10.0 / 30.0). A `(병합)` row uses the single number written on another row of the same file/bundle (B-M1 6.2, B-M3 5.0); none or contradictory → `unknown`. |
| I12 | F3 | Template placeholders (`[BidderShortName]`, `[DocumentCode]`, bare `업체명`, `서류명`): company placeholder must equal the profile short name exactly; document placeholder = any non-empty name (no code list exists to validate against; 08 reasons are prefix / extension only); extension case-insensitive. Corporate marks `(주)`, `㈜`, `주식회사` … fail when `lang == "ko"` or the rule mentions them. No short name → `unknown`. Unrecognised placeholder → `unknown`. |
| I13 | F4 | ≥2 distinct file names among matched rows → `fail` + `NOT_MERGED`. One file passes only with merge evidence (bundle); a lone row without it → `unknown`. |
| I14 | F5 | `signature`/`seal`/`signature_and_seal` read `signed`/`sealed`. `seal_corporate_or_usage` passes on usage, corporate or bare 인감 (08 R02 M3 met); a seal with no kind written → `unknown`. |
| I15 | F6 | `copy_allowed` passes any matched row; `original` needs an explicit original note (copy / `사본` label → fail); `certified_copy` or sign_seal `certified_copy_mark` need `certified_mark = yes` or `certified copy`. |
| I16 | F7 | `evaluate_validity` with all anchors; file side: `valid_until` (else guarantee period end) for `valid_until_date`, guarantee period end (else `valid_until`) for `valid_at_least_after_anchor`. Anchors are used only when `anchor_event` is `submission_deadline` or `not_stated`. Split → `unknown` with `CONFLICT_DEPENDENT` and/or `COUNTING_AMBIGUOUS` (passed through). Several rows: fail beats unknown beats pass. |
| I17 | F8 | Future issue date → result `pass` + `ISSUE_DATE_AFTER_AS_OF` (info, 09 F8). The `issued_within_before_anchor` demotion to `unknown` already done inside `dates.py` is kept. |
| I18 | F10 | Base column `no` → fail. `signed by:` with "only" → fail; all/each/both/every/전원/모두 → pass; a number N vs member count. `구성원 N개사 인감 날인` vs member count. Nothing stated → `unknown`. Single firm → the base column decides. |
| I19 | F11 | Amount ≥ required in the same currency; currency differs → `unknown`. Ratio ≥ required %. |
| I20 | F12 | JV member count = `profile.member_count` only (a stated-vs-listed mismatch stays 0 → `unknown`, no fallback to the listed names). Coverage per document label: a row with `member` counts for that member, a row without counts for the lead. For labelled (KO) rows the other rows of the manifest with the same base label (label minus `(member)`) are added, so a match that names only the member's copy still sees the lead's copy. Member count unknown → `unknown`; a member tag that matches no listed member, together with a shortfall → `unknown`. |
| I21 | F13 | `headcount` (`투입 예정 인력 N명`) vs sum of `signature_count` (`자필 서명 N매`); no headcount → `unknown` + `headcount`. |
| I22 | F14 | Member count ≤ max; lead `share_pct` ≥ minimum (single firm passes the share rule). Not stated → `unknown`. |
| I23 | Reason codes | fail → `CHECK_FAILED:Fn`, except F4 → `NOT_MERGED`, F12/F13 → `QUANTITY_SHORT` (and `quantity_short = true`). unknown → `ATTRIBUTE_UNKNOWN:<field>`. `unknown_fields` holds bidder facts only (rule-side `anchor_date`/`period`/`required_until` and `matched_file` are codes only). |

## Tests

`tests/test_manifest.py` (35) and `tests/test_filechecks.py` (57). Expected values are literals from 08 §2.3/§3.3 tables and the §2.5/§3.5 "손 계산 근거" reasons; requirement records are hand-typed from the §2.4/§3.4 quotes; matched rows are picked by literal file name. `scenarios/answers/*.json` is not read. Mutation spot-check (scratch copy): `<=`→`<` in F2, disabling the KO negation, and disabling F12 sibling expansion each made the targeted tests fail.
