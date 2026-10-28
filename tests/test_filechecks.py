"""F1-F14 file checks. Requirements are typed by hand from spec 08 §2.4/§3.4 quotes (as a model would
extract them); matched rows are chosen by file name the way model M would match them. Every expected
result is a literal from the 08 §2.5/§3.5 hand calculations ("손 계산 근거"), never recomputed."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.filechecks import CHECK_IDS, general_rule_findings, is_general_rule, run_file_checks  # noqa: E402
from engine.manifest import parse_manifest  # noqa: E402

MANIFESTS = ROOT / "scenarios" / "manifests"
A_AS_OF = "2026-11-25T09:00:00+09:00"
B_AS_OF = "2026-12-05T09:00:00+09:00"
A_ANCHORS = {"Body §5": "2026-11-30T14:00:00+03:00", "Annex 1 ITB 23.1": "2026-12-02T14:00:00+03:00"}
B_ANCHORS = {"공고문 5.가": "2026-12-09T10:00:00", "제안요청서 Ⅳ-1": "2026-12-10T10:00:00"}
BODY_S6 = ("A Tax Clearance Certificate is valid for 90 days from its date of issue (for example, a certificate "
           "issued on 1 March is valid through 30 May). The certificate must be valid on the deadline for "
           "submission of Bids.")


def req(tmp_id: str, **kw) -> dict:
    base = {"tmp_id": tmp_id, "category": "document", "subject": "", "requirement": "", "quote": "",
            "location": {"doc_id": "", "seg_id": ""}, "mandatory": "mandatory", "conditions": [],
            "attribute_of": "", "doc_code": "", "quantity_rule": "none",
            "member_rule": {"max_members": 0, "lead_share_min_pct": 0},
            "amount": {"currency": "", "value": 0, "ratio_pct": 0},
            "deadline": {"kind": "none", "raw": "", "iso_local": "", "tz_quote": ""},
            "format": {"extensions": [], "max_size_mb": 0, "filename_template": "", "merge_single_pdf": "not_stated",
                       "merge_scope": []},
            "sign_seal": "not_stated", "copy_type": "not_stated", "notarized": "not_stated",
            "validity": {"rule_type": "none", "raw": "", "months": 0, "days": 0, "until_iso": "",
                         "anchor_event": "not_stated"}}
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return {"req": base, "check": {"tmp_id": tmp_id, "verified": True, "grounded": True, "remove": False,
                                   "quote": base["quote"]}}


A = {
    "R02": req("R02", quote="Form of Bid (Form BF-1), signed by the authorized signatory and bearing the Bidder's "
                            "company seal", doc_code="BF1", sign_seal="signature_and_seal"),
    "R04": req("R04", category="other", quote="The Power of Attorney shall be notarized.", attribute_of="R03",
               notarized="required"),
    "R05": req("R05", quote="Certificate of Incorporation (a copy is acceptable)", doc_code="INC",
               copy_type="copy_allowed"),
    "R07": req("R07", category="validity", quote=BODY_S6, attribute_of="R06",
               validity={"rule_type": "valid_for_from_issue", "raw": BODY_S6, "days": 90,
                         "anchor_event": "submission_deadline"}),
    "R09": req("R09", category="merge_rule", quote="The audited financial statements for 2023, 2024 and 2025 shall "
               "be merged into a single PDF file (document code FIN).", doc_code="FIN",
               format={"extensions": ["PDF"], "merge_single_pdf": "yes"}),
    "R10": req("R10", quote="Technical Proposal", doc_code="TEC"),
    "R11": req("R11", quote="Price Schedule (Form BF-3), signed by the authorized signatory", doc_code="BF3",
               sign_seal="signature"),
    "R12": req("R12", quote="Bid Security (Form BF-2) in the amount of USD 12,000.", doc_code="BF2",
               amount={"currency": "USD", "value": 12000}),
    "R13": req("R13", category="validity", attribute_of="R12",
               quote="The Bid Security shall remain valid until 28 April 2027.",
               validity={"rule_type": "valid_until_date", "raw": "The Bid Security shall remain valid until "
                         "28 April 2027.", "until_iso": "2027-04-28"}),
    "R14": req("R14", mandatory="conditional", doc_code="JVA", sign_seal="all_members_sign",
               quote="a JV Agreement or a Letter of Intent to form a JV, in either case signed by all members"),
    "R15": req("R15", mandatory="conditional", quantity_rule="per_member",
               quote="a JV Member Information Form (Form BF-4) for each member, with that member's Certificate of "
                     "Incorporation attached."),
    "R16": req("R16", category="other", mandatory="conditional", member_rule={"max_members": 3},
               quote="Bids may be submitted by a single firm or by a joint venture (JV) of not more than three (3) "
                     "firms."),
    "R17": req("R17", category="file_format", quote="All files shall be in PDF format.",
               format={"extensions": ["PDF"]}),
    "R18": req("R18", category="file_format", quote="No single file may exceed 10 MB.", format={"max_size_mb": 10}),
    "R19": req("R19", category="file_naming", quote="Each file shall be named [BidderShortName]_[DocumentCode].pdf, "
               "using the document codes in the Submission Checklist below (example: ABC_BF1.pdf).",
               format={"filename_template": "[BidderShortName]_[DocumentCode].pdf"}),
}

B = {
    "R02": req("R02", quote="입찰참가신청서[서식 제1호]: 법인인감 또는 사용인감을 날인한다.",
               sign_seal="seal_corporate_or_usage"),
    "R04": req("R04", category="original_copy", quote="사업자등록증 사본: 사본에는 '원본대조필'을 표기하고 인감을 날인한다.",
               sign_seal="seal", copy_type="certified_copy"),
    "R05": req("R05", category="validity", quote="법인등기부등본(법인등기사항전부증명서): 입찰서 제출 마감일 기준 최근 3개월 이내 발급분",
               validity={"rule_type": "issued_within_before_anchor", "raw": "입찰서 제출 마감일 기준 최근 3개월 이내 발급분",
                         "months": 3, "anchor_event": "submission_deadline"}),
    "R06": req("R06", category="validity", quote="법인인감증명서: 입찰서 제출 마감일 기준 최근 3개월 이내 발급분",
               validity={"rule_type": "issued_within_before_anchor", "raw": "입찰서 제출 마감일 기준 최근 3개월 이내 발급분",
                         "months": 3, "anchor_event": "submission_deadline"}),
    "R07": req("R07", mandatory="conditional", quote="사용인감계[서식 제3호]: 사용인감을 날인하는 경우에 한하여 제출한다.",
               sign_seal="seal"),
    "R08": req("R08", quote="청렴계약이행서약서[서식 제4호]: 인감을 날인한다.", sign_seal="seal"),
    "R09": req("R09", quote="개인정보 수집·이용 동의서[서식 제5호]: 투입 예정 인력 전원이 각자 자필 서명한다.",
               sign_seal="each_person_handwritten", quantity_rule="per_person"),
    "R11": req("R11", category="validity", quote="입찰보증보험증권: 보증금액은 입찰금액의 100분의 5 이상, 보증기간은 입찰서 제출 "
               "마감일부터 30일 이상으로 한다.", amount={"ratio_pct": 5},
               validity={"rule_type": "valid_at_least_after_anchor", "raw": "보증기간은 입찰서 제출 마감일부터 30일 이상",
                         "days": 30, "anchor_event": "submission_deadline"}),
    "R12": req("R12", mandatory="conditional", sign_seal="all_members_seal",
               quote="공동수급협정서[서식 제2호]: 공동수급으로 참가하는 경우에 한하여 제출하며, 구성원 전원의 인감을 날인한다."),
    "R13": req("R13", mandatory="conditional", quantity_rule="per_member",
               quote="공동수급으로 참가하는 경우 구성원 각각의 ③ 사업자등록증 사본과 ④ 법인등기부등본"),
    "R14": req("R14", category="other", mandatory="conditional",
               member_rule={"max_members": 2, "lead_share_min_pct": 51},
               quote="공동수급(공동이행방식)을 허용하며, 구성원은 대표사를 포함하여 2개사 이하로 한다. 대표사의 지분율은 51% 이상이어야 한다."),
    "R15": req("R15", category="file_format", quote="모든 제출서류는 PDF 형식으로 제출한다.", format={"extensions": ["PDF"]}),
    "R16": req("R16", category="file_format", quote="파일 1개의 용량은 30MB 이하로 한다.", format={"max_size_mb": 30}),
    "R17": req("R17", category="merge_rule", quote="입찰참가자격 확인서류(공고문 3.의 ②~⑤, 공동수급인 경우 ⑫ 포함)는 하나의 PDF "
               "파일로 병합하여 제출한다.", format={"merge_single_pdf": "yes"}),
    "R18": req("R18", category="file_naming", quote="파일명은 '업체명_서류명.pdf'로 하며, 업체명에는 '(주)' 등 법인 표기를 넣지 않는다.",
               format={"filename_template": "업체명_서류명.pdf"}),
}


def manifest(name: str, *, replace: tuple[str, str] | None = None, drop: str = "") -> dict:
    text = (MANIFESTS / f"{name}.txt").read_text(encoding="utf-8")
    if replace:
        text = text.replace(*replace)
    if drop:
        text = "\n".join(ln for ln in text.split("\n") if not ln.startswith(drop))
    return parse_manifest(text, lang="ko" if name.startswith("B") else "en")


def pick(m: dict, *keys: str) -> list[dict]:
    """Rows by file name, or by "file name|doc label" for rows inside a merged KO file."""
    out = []
    for k in keys:
        name, _, label = k.partition("|")
        hits = [r for r in m["rows"] if r["file_name"] == name and (not label or r["doc_label"] == label)]
        assert len(hits) == 1, (k, len(hits))
        out.append(hits[0])
    return out


def run(vreq: dict, m: dict, *keys: str, pkg: str = "A", anchors: dict | None = None, as_of: str = "") -> dict:
    return run_file_checks(vreq, pick(m, *keys), m, as_of=as_of or (A_AS_OF if pkg == "A" else B_AS_OF),
                           anchors=A_ANCHORS if anchors is None and pkg == "A" else
                           B_ANCHORS if anchors is None else anchors)


def res(fc: dict, cid: str) -> str:
    return next(c["result"] for c in fc["checks"] if c["id"] == cid)


def failing(fc: dict) -> list[str]:
    return [c["id"] for c in fc["checks"] if c["result"] == "fail"]


def failed_files(fc: dict, cid: str) -> list[str]:
    return [e["file_name"] for e in fc["readings"][cid] if e["result"] == "fail"]


class Shape(unittest.TestCase):
    def test_all_fourteen_checks_in_order(self):
        fc = run(A["R10"], manifest("A-M1"), "QDS_TEC.pdf")
        self.assertEqual([c["id"] for c in fc["checks"]], list(CHECK_IDS))
        self.assertEqual(list(CHECK_IDS), [f"F{i}" for i in range(1, 15)])
        for key in ("checks", "quantity_short", "unknown_fields", "reason_codes", "readings"):
            self.assertIn(key, fc)
        for c in fc["checks"]:
            self.assertEqual(set(c), {"id", "result", "detail"})
            self.assertIn(c["result"], ("pass", "fail", "unknown", "not_applicable"))

    def test_general_rule_classification(self):
        for k in ("R17", "R18", "R19"):
            self.assertTrue(is_general_rule(A[k]["req"]), k)
        for k in ("R15", "R16", "R18"):
            self.assertTrue(is_general_rule(B[k]["req"]), k)
        for k in ("R02", "R09", "R10", "R13"):
            self.assertFalse(is_general_rule(A[k]["req"]), k)
        self.assertFalse(is_general_rule(B["R17"]["req"]))


class PackageA_M1(unittest.TestCase):
    """08 §2.5 A-M1: every non-conditional row met."""

    @classmethod
    def setUpClass(cls):
        cls.m = manifest("A-M1")

    def test_no_failures_on_met_rows(self):
        cases = {"R02": ["QDS_BF1.pdf"], "R04": ["QDS_POA.pdf"], "R05": ["QDS_INC.pdf"], "R07": ["QDS_TAX.pdf"],
                 "R09": ["QDS_FIN.pdf"], "R10": ["QDS_TEC.pdf"], "R11": ["QDS_BF3.pdf"], "R12": ["QDS_BF2.pdf"],
                 "R13": ["QDS_BF2.pdf"], "R17": [], "R18": [], "R19": []}
        for rid, files in cases.items():
            with self.subTest(rid=rid):
                fc = run(A[rid], self.m, *files)
                self.assertEqual(failing(fc), [])
                self.assertNotIn("unknown", [c["result"] for c in fc["checks"]])
                self.assertFalse(fc["quantity_short"])

    def test_r07_valid_through_2027_01_13_under_both_deadlines(self):
        # 08 R07 M1: 10/15 발급 -> 2027-01-13까지 유효 -> 두 해석에서 같음 -> met.
        fc = run(A["R07"], self.m, "QDS_TAX.pdf")
        self.assertEqual(res(fc, "F7"), "pass")
        self.assertIn("SAME_UNDER_ALL_READINGS", fc["reason_codes"])
        limits = {r["limit"] for r in fc["readings"]["F7"][0]["readings"]}
        self.assertEqual(limits, {"2027-01-13"})

    def test_r09_one_merged_file(self):
        fc = run(A["R09"], self.m, "QDS_FIN.pdf")
        self.assertEqual((res(fc, "F4"), res(fc, "F1")), ("pass", "pass"))


class PackageA_M2(unittest.TestCase):
    """08 §2.5 A-M2 error cells and the reason each one gives."""

    @classmethod
    def setUpClass(cls):
        cls.m = manifest("A-M2")

    def test_r02_company_seal_missing(self):
        # "QMJV_BF1.pdf seal = no. §3(a)가 company seal을 요구."
        fc = run(A["R02"], self.m, "QMJV_BF1.pdf")
        self.assertEqual(failing(fc), ["F5"])
        self.assertIn("CHECK_FAILED:F5", fc["reason_codes"])

    def test_r04_not_notarized(self):
        fc = run(A["R04"], self.m, "QMJV_POA.pdf")
        self.assertEqual(failing(fc), ["F9"])

    def test_r07_expired_under_both_deadlines(self):
        # "8/20 발급 -> 2026-11-18까지 유효. 두 후보 마감 모두에서 이미 만료 -> error."
        fc = run(A["R07"], self.m, "QMJV_TAX.pdf")
        self.assertEqual(failing(fc), ["F7"])
        reads = fc["readings"]["F7"][0]["readings"]
        self.assertEqual({r["limit"] for r in reads}, {"2026-11-18"})
        self.assertEqual({r["result"] for r in reads}, {"fail"})
        self.assertNotIn("CONFLICT_DEPENDENT", fc["reason_codes"])

    def test_r09_three_separate_files(self):
        # "FIN이 3개 파일(FIN_2023/2024/2025.pdf). ITB 22.3은 한 파일 병합을 요구."
        fc = run(A["R09"], self.m, "FIN_2023.pdf", "FIN_2024.pdf", "FIN_2025.pdf")
        self.assertEqual(failing(fc), ["F4"])
        self.assertIn("NOT_MERGED", fc["reason_codes"])
        self.assertEqual(res(fc, "F1"), "pass")

    def test_r10_size_is_not_judged_on_the_document_row(self):
        # 08 R10 M2 = met: the 14.2 MB violation belongs to R18 only.
        fc = run(A["R10"], self.m, "QMJV_TEC.pdf")
        self.assertEqual(failing(fc), [])
        self.assertEqual(res(fc, "F2"), "not_applicable")

    def test_r11_price_schedule_not_signed(self):
        fc = run(A["R11"], self.m, "QMJV_BF3.xlsx")
        self.assertEqual(failing(fc), ["F5"])

    def test_r12_no_bid_security_row_is_never_pass(self):
        # R12 M2 is missing (no BF2 file); with no matched row F11 cannot pass.
        fc = run(A["R12"], self.m)
        self.assertEqual(res(fc, "F11"), "unknown")
        self.assertEqual(fc["unknown_fields"], [])
        self.assertNotIn("pass", [c["result"] for c in fc["checks"]])

    def test_r14_signed_by_lead_member_only(self):
        # "JVA는 있으나 'signed by: lead member only'. §4(i)는 'signed by all members' -> error."
        fc = run(A["R14"], self.m, "QMJV_JVA.pdf")
        self.assertEqual(failing(fc), ["F10"])

    def test_r15_one_bf4_for_two_members(self):
        # "구성원 2개사인데 BF4는 1개(Quillfield)뿐 -> Merrowind 분 없음 -> missing."
        fc = run(A["R15"], self.m, "QMJV_BF4-1.pdf")
        self.assertEqual(res(fc, "F12"), "fail")
        self.assertTrue(fc["quantity_short"])
        self.assertIn("QUANTITY_SHORT", fc["reason_codes"])
        self.assertIn("Merrowind Cloud Ltd.", next(c["detail"] for c in fc["checks"] if c["id"] == "F12"))

    def test_r15_lead_documents_do_not_count_twice(self):
        fc = run(A["R15"], self.m, "QMJV_BF4-1.pdf", "QMJV_INC.pdf")
        self.assertEqual(res(fc, "F12"), "fail")

    def test_r16_two_members_within_three(self):
        # "구성원 2 <= 3 -> met."
        fc = run(A["R16"], self.m)
        self.assertEqual(res(fc, "F14"), "pass")

    def test_r17_only_the_xlsx_file(self):
        fc = run(A["R17"], self.m)
        self.assertEqual(failing(fc), ["F1"])
        self.assertEqual(failed_files(fc, "F1"), ["QMJV_BF3.xlsx"])

    def test_r18_only_the_14_2_mb_file(self):
        fc = run(A["R18"], self.m)
        self.assertEqual(failing(fc), ["F2"])
        self.assertEqual(failed_files(fc, "F2"), ["QMJV_TEC.pdf"])

    def test_r19_exact_violating_file_names(self):
        # "파일명 규칙 위반 파일: FIN_2023.pdf, FIN_2024.pdf, FIN_2025.pdf(약칭 QMJV_ 없음), QMJV_BF3.xlsx(.pdf 아님)."
        fc = run(A["R19"], self.m)
        self.assertEqual(failing(fc), ["F3"])
        self.assertEqual(failed_files(fc, "F3"), ["FIN_2023.pdf", "FIN_2024.pdf", "FIN_2025.pdf", "QMJV_BF3.xlsx"])
        detail = next(c["detail"] for c in fc["checks"] if c["id"] == "F3")
        for name in ("FIN_2023.pdf", "FIN_2024.pdf", "FIN_2025.pdf", "QMJV_BF3.xlsx"):
            self.assertIn(name, detail)

    def test_general_rules_ignore_the_matched_rows(self):
        fc = run(A["R17"], self.m, "QMJV_BF1.pdf")
        self.assertEqual(failed_files(fc, "F1"), ["QMJV_BF3.xlsx"])
        self.assertTrue(fc["general_rule"])

    def test_general_rule_findings_per_row(self):
        found = general_rule_findings([A["R17"], A["R18"], A["R19"], A["R02"]], self.m)
        self.assertEqual(sorted(found), ["M5", "M6", "M7", "M8", "M9"])
        self.assertEqual(sorted(f["id"] for f in found["M9"]), ["F1", "F3"])
        self.assertEqual([f["id"] for f in found["M8"]], ["F2"])
        self.assertEqual({f["tmp_id"] for f in found["M5"]}, {"R19"})


class PackageA_M3(unittest.TestCase):
    """08 §2.5 A-M3 boundary cells."""

    @classmethod
    def setUpClass(cls):
        cls.m = manifest("A-M3")

    def test_r07_depends_on_the_deadline_conflict(self):
        # "9/1 발급 -> 2026-11-30까지 유효(그날 포함). 11/30 마감이면 유효, 12/2 마감이면 만료 -> NC, C1."
        fc = run(A["R07"], self.m, "OSL_TAX.pdf")
        self.assertEqual(res(fc, "F7"), "unknown")
        self.assertIn("CONFLICT_DEPENDENT", fc["reason_codes"])
        self.assertNotIn("COUNTING_AMBIGUOUS", fc["reason_codes"])
        self.assertEqual(failing(fc), [])
        reads = {r["anchor"]: (r["limit"], r["result"]) for r in fc["readings"]["F7"][0]["readings"]}
        self.assertEqual(reads, {"Body §5": ("2026-11-30", "pass"), "Annex 1 ITB 23.1": ("2026-11-30", "fail")})

    def test_r07_single_deadline_counts_the_last_day(self):
        fc = run(A["R07"], self.m, "OSL_TAX.pdf", anchors={"Body §5": "2026-11-30T14:00:00+03:00"})
        self.assertEqual(res(fc, "F7"), "pass")
        fc = run(A["R07"], self.m, "OSL_TAX.pdf", anchors={"Annex 1 ITB 23.1": "2026-12-02T14:00:00+03:00"})
        self.assertEqual(res(fc, "F7"), "fail")

    def test_r13_one_day_short(self):
        # "목록의 유효일 2027-04-27 < 요구 2027-04-28 -> 하루 모자람 -> error."
        fc = run(A["R13"], self.m, "OSL_BF2.pdf")
        self.assertEqual(failing(fc), ["F7"])

    def test_r18_ten_point_zero_is_not_over(self):
        # "OSL_TEC.pdf 10.0 MB ... 10.0은 초과가 아님 -> met."
        fc = run(A["R18"], self.m)
        self.assertEqual(res(fc, "F2"), "pass")
        tec = next(e for e in fc["readings"]["F2"] if e["file_name"] == "OSL_TEC.pdf")
        self.assertEqual((tec["result"], tec["size_mb"]), ("pass", 10.0))

    def test_r05_certified_copy_accepted(self):
        # "'certified copy' - §3(c)는 사본을 허용 -> met."
        fc = run(A["R05"], self.m, "OSL_INC.pdf")
        self.assertEqual(res(fc, "F6"), "pass")

    def test_jv_rules_unknown_when_participation_not_stated(self):
        fc = run(A["R16"], self.m)
        self.assertEqual(res(fc, "F14"), "unknown")
        self.assertIn("member_count", fc["unknown_fields"])


class PackageB_M1(unittest.TestCase):
    """08 §3.5 B-M1: every row met (R07, R12-R14 triggered)."""

    @classmethod
    def setUpClass(cls):
        cls.m = manifest("B-M1")

    def test_met_rows_have_no_failure_and_no_unknown(self):
        q = "별누리코드_자격서류.pdf|"
        cases = {"R02": ["별누리코드_입찰참가신청서.pdf"],
                 "R04": [q + "사업자등록증 사본", q + "사업자등록증 사본(새솔링크)"],
                 "R05": [q + "법인등기부등본"], "R06": [q + "법인인감증명서"],
                 "R07": ["별누리코드_사용인감계.pdf"], "R08": ["별누리코드_청렴계약이행서약서.pdf"],
                 "R09": ["별누리코드_개인정보동의서.pdf"], "R11": ["별누리코드_입찰보증보험증권.pdf"],
                 "R12": ["별누리코드_공동수급협정서.pdf"],
                 "R13": [q + "사업자등록증 사본(새솔링크)", q + "법인등기부등본(새솔링크)"],
                 "R14": [], "R15": [], "R16": [], "R18": [],
                 "R17": [q + "경쟁입찰참가자격등록증", q + "사업자등록증 사본", q + "법인등기부등본", q + "법인인감증명서"]}
        for rid, files in cases.items():
            with self.subTest(rid=rid):
                fc = run(B[rid], self.m, *files, pkg="B")
                self.assertEqual(failing(fc), [])
                self.assertNotIn("unknown", [c["result"] for c in fc["checks"]])

    def test_r12_two_member_seals(self):
        fc = run(B["R12"], self.m, "별누리코드_공동수급협정서.pdf", pkg="B")
        self.assertEqual(res(fc, "F10"), "pass")

    def test_r14_two_firms_lead_sixty_percent(self):
        fc = run(B["R14"], self.m, pkg="B")
        self.assertEqual(res(fc, "F14"), "pass")

    def test_r13_shortfall_when_a_member_document_is_absent(self):
        m = manifest("B-M1", drop="법인등기부등본(새솔링크)")
        fc = run(B["R13"], m, "별누리코드_자격서류.pdf|사업자등록증 사본(새솔링크)", "별누리코드_자격서류.pdf|법인등기부등본",
                 pkg="B")
        self.assertEqual(res(fc, "F12"), "fail")
        self.assertTrue(fc["quantity_short"])
        self.assertIn("법인등기부등본: 1 of 2", next(c["detail"] for c in fc["checks"] if c["id"] == "F12"))

    def test_r16_merged_rows_use_the_bundle_size(self):
        fc = run(B["R16"], self.m, pkg="B")
        entry = next(e for e in fc["readings"]["F2"] if e["file_name"] == "별누리코드_자격서류.pdf")
        self.assertEqual((entry["result"], entry["size_mb"]), ("pass", 6.2))

    def test_corporate_mark_in_file_name_fails(self):
        # 제안요청서 Ⅳ-5: 업체명에는 '(주)' 등 법인 표기를 넣지 않는다.
        m = manifest("B-M1", replace=("별누리코드_제안서.pdf", "(주)별누리코드_제안서.pdf"))
        fc = run(B["R18"], m, pkg="B")
        self.assertEqual(failed_files(fc, "F3"), ["(주)별누리코드_제안서.pdf"])
        m2 = manifest("B-M1", replace=("파일명용 업체명: 별누리코드", "파일명용 업체명: (주)별누리코드"))
        m2 = copy.deepcopy(m2)
        for r in m2["rows"]:
            r["file_name"] = "(주)" + r["file_name"]
        fc2 = run(B["R18"], m2, pkg="B")
        self.assertEqual(res(fc2, "F3"), "fail")

    def test_template_quoted_inside_sentence_words(self):
        # Card T01 K=3 third pass (2026-10-28): the model gave filename_template "'업체명_서류명.pdf'로 하며" (quote marks
        # and the following words kept), so all eight correctly named B-M1 files failed F3. The quoted span is the
        # template.
        r18 = copy.deepcopy(B["R18"])
        r18["req"]["format"]["filename_template"] = "'업체명_서류명.pdf'로 하며"
        fc = run(r18, self.m, pkg="B")
        self.assertEqual((res(fc, "F3"), failed_files(fc, "F3")), ("pass", []))
        bad = manifest("B-M1", replace=("별누리코드_제안서.pdf", "별누리_제안서.pdf"))
        self.assertEqual(failed_files(run(r18, bad, pkg="B"), "F3"), ["별누리_제안서.pdf"])

    def test_future_issue_date_is_info_only(self):
        # 09 F8: issue date after as_of -> information flag only.
        fc = run(B["R02"], self.m, "별누리코드_입찰참가신청서.pdf", pkg="B", as_of="2026-11-25T09:00:00+09:00")
        self.assertEqual(res(fc, "F8"), "pass")
        self.assertIn("ISSUE_DATE_AFTER_AS_OF", fc["reason_codes"])
        self.assertEqual(failing(fc), [])
        self.assertFalse(any(c.startswith("CHECK_FAILED") for c in fc["reason_codes"]))


class PackageB_M2(unittest.TestCase):
    """08 §3.5 B-M2 error / missing cells."""

    @classmethod
    def setUpClass(cls):
        cls.m = manifest("B-M2")

    def test_r02_hwp_application_is_met_on_its_own_row(self):
        # "R02 M2: 신청서는 있고 법인인감으로 날인했다 -> met. 형식(HWP) 위반은 R15에서 따로 error."
        fc = run(B["R02"], self.m, "물안개시스템_입찰참가신청서.hwp", pkg="B")
        self.assertEqual(failing(fc), [])
        self.assertEqual(res(fc, "F5"), "pass")

    def test_r04_no_certified_mark_no_seal(self):
        # "R04 M2: '원본대조필 없음, 날인 없음' -> error."
        fc = run(B["R04"], self.m, "물안개시스템_사업자등록증.pdf", pkg="B")
        self.assertEqual(failing(fc), ["F5", "F6"])

    def test_r05_issued_before_the_three_month_window(self):
        # "R05 M2: 8/03 발급 -> 창 시작(9/9~9/10)보다 37일 이상 이르다 -> 충돌·기산 방식과 무관하게 error."
        fc = run(B["R05"], self.m, "scan_0012.pdf", pkg="B")
        self.assertEqual(failing(fc), ["F7"])
        self.assertEqual({r["result"] for r in fc["readings"]["F7"][0]["readings"]}, {"fail"})

    def test_r09_three_signatures_for_four_people(self):
        # "R09 M2: 인력 4명인데 동의서 3매 -> 1명분 없음 -> missing."
        fc = run(B["R09"], self.m, "물안개시스템_개인정보동의서.pdf", pkg="B")
        self.assertEqual(res(fc, "F13"), "fail")
        self.assertTrue(fc["quantity_short"])
        self.assertIn("QUANTITY_SHORT", fc["reason_codes"])

    def test_r11_guarantee_too_short_under_every_reading(self):
        # "R11 M2: 보증 종료 2026-12-31 -> 가장 느슨한 해석(1/7)보다도 짧다 -> error."
        fc = run(B["R11"], self.m, "물안개시스템_입찰보증보험증권.pdf", pkg="B")
        self.assertEqual(failing(fc), ["F7"])
        self.assertEqual(res(fc, "F11"), "pass")

    def test_r15_hwp_file(self):
        fc = run(B["R15"], self.m, pkg="B")
        self.assertEqual(failed_files(fc, "F1"), ["물안개시스템_입찰참가신청서.hwp"])

    def test_r16_38_4_mb_proposal(self):
        fc = run(B["R16"], self.m, pkg="B")
        self.assertEqual(failed_files(fc, "F2"), ["제안서최종(2).pdf"])

    def test_r17_three_separate_files(self):
        # "등록증·사업자등록증·등기부가 서로 다른 3개 파일 -> 하나의 PDF로 병합되지 않음 -> error."
        fc = run(B["R17"], self.m, "물안개시스템_등록증.pdf", "물안개시스템_사업자등록증.pdf", "scan_0012.pdf", pkg="B")
        self.assertEqual(failing(fc), ["F4"])
        self.assertIn("NOT_MERGED", fc["reason_codes"])

    def test_r18_exact_violating_file_names(self):
        # "scan_0012.pdf, 제안서최종(2).pdf는 '업체명_서류명' 형식이 아니고, 물안개시스템_입찰참가신청서.hwp는 .pdf가 아니다."
        fc = run(B["R18"], self.m, pkg="B")
        self.assertEqual(failing(fc), ["F3"])
        self.assertEqual(failed_files(fc, "F3"), ["물안개시스템_입찰참가신청서.hwp", "scan_0012.pdf", "제안서최종(2).pdf"])

    def test_pii_variant_gives_the_same_checks(self):
        pii = manifest("B-M2-PII")
        fc = run(B["R09"], pii, "물안개시스템_개인정보동의서.pdf", pkg="B")
        self.assertEqual(res(fc, "F13"), "fail")
        self.assertNotIn("SYNTH", repr(fc))


class PackageB_M3(unittest.TestCase):
    """08 §3.5 B-M3 boundary cells."""

    @classmethod
    def setUpClass(cls):
        cls.m = manifest("B-M3")

    def test_r05_issue_date_not_confirmed(self):
        # "R05 M3: 발급일 '확인 안 됨' -> 판정할 값이 없음 -> NC, 입찰자에게 발급일 확인 요청."
        fc = run(B["R05"], self.m, "다올빛소프트_자격서류.pdf|법인등기부등본", pkg="B")
        self.assertEqual(res(fc, "F7"), "unknown")
        self.assertEqual(fc["unknown_fields"], ["issue_date"])
        self.assertIn("ATTRIBUTE_UNKNOWN:issue_date", fc["reason_codes"])
        self.assertEqual(failing(fc), [])

    def test_r06_issued_five_days_inside_the_window(self):
        # "R06 M3: 9/15 발급 -> 창 시작보다 5~6일 뒤 -> 어느 해석으로도 창 안 -> met."
        fc = run(B["R06"], self.m, "다올빛소프트_자격서류.pdf|법인인감증명서", pkg="B")
        self.assertEqual(res(fc, "F7"), "pass")
        self.assertIn("SAME_UNDER_ALL_READINGS", fc["reason_codes"])

    def test_r09_headcount_not_stated(self):
        # "R09 M3: 인원수 기재 없음 -> '전원'을 확인할 수 없음 -> NC + QB-B3."
        fc = run(B["R09"], self.m, "다올빛소프트_개인정보동의서.pdf", pkg="B")
        self.assertEqual(res(fc, "F13"), "unknown")
        self.assertEqual(fc["unknown_fields"], ["headcount"])
        self.assertFalse(fc["quantity_short"])

    def test_r11_depends_on_conflict_and_counting(self):
        # "12/9 마감이면 어느 셈법으로도 충분. 12/10 마감이면 마감일을 1일째로 셀 때만 충분 -> C1과 기산 방식에 종속 -> NC."
        fc = run(B["R11"], self.m, "다올빛소프트_입찰보증보험증권.pdf", pkg="B")
        self.assertEqual(res(fc, "F7"), "unknown")
        self.assertIn("CONFLICT_DEPENDENT", fc["reason_codes"])
        self.assertIn("COUNTING_AMBIGUOUS", fc["reason_codes"])
        reads = {(r["anchor"], r["convention"]): (r["limit"], r["result"])
                 for r in fc["readings"]["F7"][0]["readings"]}
        # "12/9 + 30일 = 1/8, 12/10 + 30일 = 1/9. 마감일을 1일째로 세면 각각 1/7, 1/8."
        self.assertEqual(reads, {
            ("공고문 5.가", "anchor_not_counted"): ("2027-01-08", "pass"),
            ("공고문 5.가", "anchor_counted"): ("2027-01-07", "pass"),
            ("제안요청서 Ⅳ-1", "anchor_not_counted"): ("2027-01-09", "fail"),
            ("제안요청서 Ⅳ-1", "anchor_counted"): ("2027-01-08", "pass")})

    def test_r16_thirty_point_zero_is_within(self):
        # "M3의 30.0MB는 '30MB 이하' -> met."
        fc = run(B["R16"], self.m, pkg="B")
        self.assertEqual(res(fc, "F2"), "pass")

    def test_r02_unspecified_seal_satisfies_corporate_or_usage(self):
        # "신청서 날인 자체(R02)는 '법인인감 또는 사용인감'이므로 met."
        fc = run(B["R02"], self.m, "다올빛소프트_입찰참가신청서.pdf", pkg="B")
        self.assertEqual(res(fc, "F5"), "pass")

    def test_r17_merged_bundle(self):
        q = "다올빛소프트_자격서류.pdf|"
        fc = run(B["R17"], self.m, q + "경쟁입찰참가자격등록증", q + "사업자등록증 사본", q + "법인등기부등본",
                 q + "법인인감증명서", pkg="B")
        self.assertEqual(res(fc, "F4"), "pass")

    def test_member_rules_unknown_when_not_stated(self):
        fc = run(B["R14"], self.m, pkg="B")
        self.assertEqual(res(fc, "F14"), "unknown")


class FailClosed(unittest.TestCase):
    def test_notarization_not_stated(self):
        m = parse_manifest("Bidder: X Ltd. | Short name: XL\nBidding as: single firm\n"
                           "file | format | size_MB | signed | seal | issue_date | notes\n"
                           "XL_POA.pdf | PDF | 0.3 | yes | no | 2026-11-18 |\n", lang="en")
        fc = run_file_checks(A["R04"], m["rows"], m, as_of=A_AS_OF, anchors=A_ANCHORS)
        self.assertEqual(res(fc, "F9"), "unknown")
        self.assertEqual(fc["unknown_fields"], ["notarized"])

    def test_missing_size_and_short_name(self):
        m = parse_manifest("Bidder: X Ltd.\nfile | format | size_MB | signed | seal | issue_date | notes\n"
                           "X_BF1.pdf | PDF |  | yes | yes | 2026-11-20 |\n", lang="en")
        self.assertEqual(res(run_file_checks(A["R18"], [], m, as_of=A_AS_OF, anchors=A_ANCHORS), "F2"), "unknown")
        fc = run_file_checks(A["R19"], [], m, as_of=A_AS_OF, anchors=A_ANCHORS)
        self.assertEqual(res(fc, "F3"), "unknown")
        self.assertIn("short_name", fc["unknown_fields"])

    def test_single_merged_looking_row_without_evidence(self):
        m = parse_manifest("Bidder: X Ltd. | Short name: XL\nfile | format | size_MB | signed | seal | issue_date | notes\n"
                           "XL_FIN.pdf | PDF | 2.0 | no | no | 2026-06-30 | audited 2023\n", lang="en")
        fc = run_file_checks(A["R09"], m["rows"], m, as_of=A_AS_OF, anchors=A_ANCHORS)
        self.assertEqual(res(fc, "F4"), "unknown")

    def test_member_count_mismatch_stays_unknown(self):
        m = parse_manifest("Bidder: X JV | Short name: XJ\nBidding as: JV, 3 members: A Ltd. (lead), B Ltd.\n"
                           "file | format | size_MB | signed | seal | issue_date | notes\n"
                           "XJ_BF4-1.pdf | PDF | 1.0 | yes | no | 2026-11-12 | member: A Ltd.\n"
                           "XJ_BF4-2.pdf | PDF | 1.0 | yes | no | 2026-11-12 | member: B Ltd.\n", lang="en")
        fc = run_file_checks(A["R16"], [], m, as_of=A_AS_OF, anchors=A_ANCHORS)
        self.assertEqual(res(fc, "F14"), "unknown")
        self.assertIn("member_count", fc["unknown_fields"])
        fc = run_file_checks(A["R15"], m["rows"], m, as_of=A_AS_OF, anchors=A_ANCHORS)
        self.assertEqual(res(fc, "F12"), "unknown")

    def test_no_anchor_means_no_validity_verdict(self):
        m = manifest("A-M1")
        fc = run_file_checks(A["R07"], pick(m, "QDS_TAX.pdf"), m, as_of=A_AS_OF, anchors={})
        self.assertEqual(res(fc, "F7"), "unknown")
        self.assertIn("ATTRIBUTE_UNKNOWN:anchor_date", fc["reason_codes"])
        self.assertEqual(fc["unknown_fields"], [])


if __name__ == "__main__":
    unittest.main()
