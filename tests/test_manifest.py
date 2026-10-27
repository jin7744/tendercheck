"""N2 manifest parsing. Expected values are literals read by hand from spec 08 §2.3, §3.3, §2.5/§3.5
reasons and T05; none is computed with engine code."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.manifest import parse_manifest  # noqa: E402

MANIFESTS = ROOT / "scenarios" / "manifests"
LANG = {"A-M1": "en", "A-M2": "en", "A-M3": "en", "B-M1": "ko", "B-M2": "ko", "B-M2-PII": "ko", "B-M3": "ko"}
SYNTH_08 = re.compile(r"SYNTH-[A-Z]+-\d{4}-TEST")


def raw(name: str) -> str:
    return (MANIFESTS / f"{name}.txt").read_text(encoding="utf-8")


def load(name: str) -> dict:
    return parse_manifest(raw(name), lang=LANG[name])


def row(m: dict, file_name: str, doc_label: str = "") -> dict:
    hits = [r for r in m["rows"] if r["file_name"] == file_name and (not doc_label or r["doc_label"] == doc_label)]
    assert len(hits) == 1, (file_name, doc_label, len(hits))
    return hits[0]


class RowCounts(unittest.TestCase):
    def test_every_manifest_row_count(self):
        # 08 §2.3 / §3.3 tables, counted by hand.
        expected = {"A-M1": 8, "A-M2": 11, "A-M3": 8, "B-M1": 13, "B-M2": 7, "B-M2-PII": 7, "B-M3": 9}
        for name, n in expected.items():
            with self.subTest(name=name):
                m = load(name)
                self.assertEqual(len(m["rows"]), n)
                self.assertEqual([r["row_id"] for r in m["rows"]], [f"M{i}" for i in range(1, n + 1)])

    def test_first_and_last_file_names(self):
        self.assertEqual([load("A-M2")["rows"][i]["file_name"] for i in (0, -1)], ["QMJV_BF1.pdf", "QMJV_BF4-1.pdf"])
        self.assertEqual([load("B-M1")["rows"][i]["file_name"] for i in (0, -1)],
                         ["별누리코드_입찰참가신청서.pdf", "별누리코드_공동수급협정서.pdf"])


class LabelLine(unittest.TestCase):
    def test_label_line_stripped_from_masked_text(self):
        cases = {"A-M1": "[A-M1] Normal", "A-M2": "[A-M2] Missing and errors mixed", "A-M3": "[A-M3] Boundary cases",
                 "B-M1": "[B-M1] 정상", "B-M2": "[B-M2] 누락·오류 섞임", "B-M2-PII": "[B-M2] 누락·오류 섞임",
                 "B-M3": "[B-M3] 경계 사례"}
        for name, label in cases.items():
            with self.subTest(name=name):
                m = load(name)
                self.assertEqual(m["label_line"], label)
                self.assertNotIn(label, m["masked_text"])
                self.assertNotIn(label.split("] ")[0] + "]", m["masked_text"])

    def test_no_label_line(self):
        m = parse_manifest("Bidder: X Ltd. | Short name: XL\nfile | format | size_MB | signed | seal | issue_date | notes\n"
                           "XL_BF1.pdf | PDF | 0.4 | yes | yes | 2026-11-20 |\n", lang="en")
        self.assertEqual((m["label_line"], len(m["rows"]), m["profile"]["short_name"]), ("", 1, "XL"))

    def test_masked_text_is_the_file_minus_label_for_non_pii_manifests(self):
        # Over-masking guard: dates, ranges, 60:40, file names must come through unchanged.
        for name in ("A-M1", "A-M2", "A-M3", "B-M1", "B-M2", "B-M3"):
            with self.subTest(name=name):
                m = load(name)
                lines = m["masked_text"].split("\n")
                stripped = [re.sub(r"^(?:row_id|M\d+) \| ", "", ln) for ln in lines]
                original = [ln.strip() for ln in raw(name).split("\n")[1:] if ln.strip()]
                self.assertEqual(stripped, original)
                self.assertEqual(m["pii_counts"], {})

    def test_masked_text_carries_row_ids(self):
        m = load("A-M1")
        self.assertIn("row_id | file | format | size_MB | signed | seal | issue_date | notes", m["masked_text"])
        self.assertIn("M2 | QDS_POA.pdf | PDF | 0.3 | yes | no | 2026-11-18 | notarized: yes", m["masked_text"])


class Profiles(unittest.TestCase):
    def test_a_m1_single_firm(self):
        p = load("A-M1")["profile"]
        self.assertEqual(p["company"], "Quillfield Data Services Ltd. (fictional)")
        self.assertEqual(p["short_name"], "QDS")
        self.assertEqual((p["participation"], p["members"], p["member_count"]), ("single", [], 1))
        self.assertEqual(p["planned_upload"], "2026-11-27T10:00:00+03:00")
        self.assertEqual(p["facts"]["consortium"], "no")

    def test_a_m2_two_member_jv(self):
        # 08 §2.3 "JV, 2 members"; §2.5 R15 M2 "구성원 2개사인데 BF4는 1개".
        p = load("A-M2")["profile"]
        self.assertEqual(p["short_name"], "QMJV")
        self.assertEqual(p["participation"], "jv")
        self.assertEqual(p["member_count"], 2)
        self.assertEqual(p["members"], [
            {"name": "Quillfield Data Services Ltd.", "role": "lead", "share_pct": 0.0},
            {"name": "Merrowind Cloud Ltd.", "role": "member", "share_pct": 0.0}])
        self.assertEqual(p["planned_upload"], "2026-11-26T16:00:00+03:00")
        self.assertEqual(p["facts"]["consortium"], "yes")

    def test_a_m3_not_stated_and_kst_upload(self):
        # 08 §2.5 R14~R16 M3 "참가 형태 기재 없음"; R01 M3 "2026-11-30 20:30 KST(UTC+09:00)".
        p = load("A-M3")["profile"]
        self.assertEqual((p["participation"], p["member_count"]), ("unknown", 0))
        self.assertEqual(p["facts"]["consortium"], "unknown")
        self.assertEqual(p["planned_upload"], "2026-11-30T20:30:00+09:00")

    def test_b_m1_jv_with_shares_and_usage_seal(self):
        p = load("B-M1")["profile"]
        self.assertEqual(p["short_name"], "별누리코드")
        self.assertEqual(p["participation"], "jv")
        self.assertEqual(p["member_count"], 2)
        self.assertEqual(p["members"], [{"name": "(주)별누리코드", "role": "lead", "share_pct": 60.0},
                                        {"name": "(주)새솔링크", "role": "member", "share_pct": 40.0}])
        self.assertEqual(p["seal_type"], "usage")
        self.assertEqual(p["facts"]["usage_seal"], "yes")
        self.assertEqual(p["facts"]["consortium"], "yes")
        self.assertEqual(p["planned_upload"], "")

    def test_b_m2_single_corporate_seal(self):
        # 08 §3.5 R07 M2 "법인인감 선언 → cond(nt)", R12~R14 M2 "단독 선언".
        p = load("B-M2")["profile"]
        self.assertEqual((p["participation"], p["seal_type"]), ("single", "corporate"))
        self.assertEqual((p["facts"]["consortium"], p["facts"]["usage_seal"]), ("no", "no"))

    def test_b_m3_seal_and_participation_not_stated(self):
        # 08 §3.5 R07 M3: 날인 인감 종류 기재 없음("인감 날인"만 있음) -> unknown, not usage.
        p = load("B-M3")["profile"]
        self.assertEqual((p["participation"], p["seal_type"]), ("unknown", "unknown"))
        self.assertEqual((p["facts"]["consortium"], p["facts"]["usage_seal"]), ("unknown", "unknown"))

    def test_unstated_facts_stay_unknown(self):
        f = load("B-M1")["profile"]["facts"]
        self.assertEqual((f["foreign_entity"], f["subcontracting"], f["sme"]), ("unknown", "unknown", "unknown"))

    def test_member_count_mismatch_is_not_guessed(self):
        m = parse_manifest("Bidder: X JV | Short name: XJ\nBidding as: JV, 3 members: A Ltd. (lead), B Ltd.\n"
                           "file | format | size_MB | signed | seal | issue_date | notes\n", lang="en")
        self.assertEqual((m["profile"]["participation"], m["profile"]["member_count"]), ("jv", 0))
        self.assertTrue(any("member count" in w for w in m["warnings"]))

    def test_planned_upload_without_zone_is_not_invented(self):
        m = parse_manifest("Bidder: X Ltd. | Short name: XL\nPlanned upload: 2026-11-27 10:00\n"
                           "file | format | size_MB | signed | seal | issue_date | notes\n", lang="en")
        self.assertEqual(m["profile"]["planned_upload"], "")
        self.assertTrue(any("Planned upload" in w or "planned upload" in w for w in m["warnings"]))


class RowFields(unittest.TestCase):
    def test_en_columns(self):
        r = row(load("A-M1"), "QDS_BF2.pdf")
        self.assertEqual((r["doc_label"], r["ext"], r["declared_format"], r["size_mb"], r["signed"], r["sealed"],
                          r["issue_date"], r["sign_seal_text"]),
                         ("", "pdf", "PDF", 0.5, "no", "no", "2026-11-16", ""))
        self.assertEqual(r["notes"], "bank guarantee; amount USD 12,000; valid until 2027-04-28")
        self.assertEqual(r["parsed"]["valid_until"], "2027-04-28")
        self.assertEqual(r["parsed"]["amount"], {"currency": "USD", "value": 12000.0})
        self.assertEqual((r["bundle"], r["scan_ref"], r["pii"]), ("", "", False))

    def test_a_m3_bid_security_one_day_short_value(self):
        self.assertEqual(row(load("A-M3"), "OSL_BF2.pdf")["parsed"]["valid_until"], "2027-04-27")

    def test_notarized_and_copy_notes(self):
        self.assertEqual(row(load("A-M1"), "QDS_POA.pdf")["parsed"], {"notarized": "yes"})
        self.assertEqual(row(load("A-M2"), "QMJV_POA.pdf")["parsed"]["notarized"], "no")
        self.assertEqual(row(load("A-M1"), "QDS_INC.pdf")["parsed"]["copy_kind"], "copy")
        self.assertEqual(row(load("A-M3"), "OSL_INC.pdf")["parsed"]["copy_kind"], "certified_copy")

    def test_signed_by_and_member_notes(self):
        m = load("A-M2")
        self.assertEqual(row(m, "QMJV_JVA.pdf")["parsed"]["signed_by"], "lead member only")
        self.assertNotIn("member_role", row(m, "QMJV_JVA.pdf")["parsed"])
        self.assertEqual(row(m, "QMJV_BF4-1.pdf")["parsed"]["member"], "Quillfield Data Services Ltd.")
        self.assertEqual(row(m, "QMJV_INC.pdf")["parsed"]["member_role"], "lead")
        self.assertEqual(row(m, "QMJV_BF3.xlsx")["ext"], "xlsx")
        self.assertEqual(row(m, "QMJV_TEC.pdf")["size_mb"], 14.2)

    def test_en_merged_file_note(self):
        r = row(load("A-M1"), "QDS_FIN.pdf")
        self.assertEqual(r["parsed"]["merged_contents"], ["audited 2023", "2024", "2025"])
        self.assertNotEqual(r["bundle"], "")
        for f in ("FIN_2023.pdf", "FIN_2024.pdf", "FIN_2025.pdf"):
            self.assertEqual(row(load("A-M2"), f)["bundle"], "")

    def test_ko_merged_bundle(self):
        m = load("B-M1")
        merged = [r for r in m["rows"] if r["file_name"] == "별누리코드_자격서류.pdf"]
        self.assertEqual([r["row_id"] for r in merged], ["M2", "M3", "M4", "M5", "M6", "M7"])
        self.assertEqual(len({r["bundle"] for r in merged}), 1)
        self.assertNotEqual(merged[0]["bundle"], "")
        self.assertEqual([r["size_mb"] for r in merged], [6.2, "merged", "merged", "merged", "merged", "merged"])
        self.assertEqual(row(m, "별누리코드_제안서.pdf")["bundle"], "")

    def test_ko_issue_date_dash_and_not_confirmed_are_unknown(self):
        self.assertEqual(row(load("B-M1"), "별누리코드_자격서류.pdf", "사업자등록증 사본")["issue_date"], "unknown")
        self.assertEqual(row(load("B-M3"), "다올빛소프트_자격서류.pdf", "법인등기부등본")["issue_date"], "unknown")
        self.assertEqual(row(load("B-M3"), "다올빛소프트_자격서류.pdf", "법인인감증명서")["issue_date"], "2026-09-15")

    def test_ko_sign_seal_cell(self):
        m = load("B-M1")
        r = row(m, "별누리코드_자격서류.pdf", "사업자등록증 사본")
        self.assertEqual(r["sign_seal_text"], "원본대조필 표기, 사용인감 날인")
        self.assertEqual((r["sealed"], r["parsed"]["certified_mark"], r["parsed"]["seal_kinds"]), ("yes", "yes", ["usage"]))
        self.assertEqual(row(m, "별누리코드_사용인감계.pdf")["parsed"]["seal_kinds"], ["usage", "corporate"])
        self.assertEqual(row(m, "별누리코드_공동수급협정서.pdf")["parsed"]["seal_member_count"], 2)
        self.assertEqual(row(load("B-M3"), "다올빛소프트_입찰참가신청서.pdf")["parsed"]["seal_kinds"], ["unspecified"])

    def test_ko_negation_trap_b_m2_r04(self):
        # 08 §3.5 R04 M2: "원본대조필 없음, 날인 없음".
        r = row(load("B-M2"), "물안개시스템_사업자등록증.pdf")
        self.assertEqual(r["sign_seal_text"], "원본대조필 없음, 날인 없음")
        self.assertEqual(r["sealed"], "no")
        self.assertEqual(r["parsed"]["certified_mark"], "no")
        self.assertNotIn("seal_kinds", r["parsed"])

    def test_ko_dash_sign_seal_means_none(self):
        r = row(load("B-M1"), "별누리코드_제안서.pdf")
        self.assertEqual((r["sign_seal_text"], r["signed"], r["sealed"]), ("-", "no", "no"))

    def test_ko_member_rows(self):
        m = load("B-M1")
        self.assertEqual(row(m, "별누리코드_자격서류.pdf", "사업자등록증 사본(새솔링크)")["parsed"]["member"], "새솔링크")
        self.assertEqual(row(m, "별누리코드_자격서류.pdf", "법인등기부등본(새솔링크)")["parsed"]["member"], "새솔링크")
        self.assertNotIn("member", row(m, "별누리코드_자격서류.pdf", "법인등기부등본")["parsed"])

    def test_headcount_and_signature_count(self):
        r1 = row(load("B-M1"), "별누리코드_개인정보동의서.pdf")
        self.assertEqual((r1["parsed"]["headcount"], r1["parsed"]["signature_count"], r1["signed"]), (5, 5, "yes"))
        r2 = row(load("B-M2"), "물안개시스템_개인정보동의서.pdf")
        self.assertEqual((r2["parsed"]["headcount"], r2["parsed"]["signature_count"]), (4, 3))
        r3 = row(load("B-M3"), "다올빛소프트_개인정보동의서.pdf")
        self.assertEqual(r3["notes"], "투입 예정 인력 수 기재 없음")
        self.assertNotIn("headcount", r3["parsed"])
        self.assertEqual(r3["parsed"]["signature_count"], 3)

    def test_guarantee_period_and_ratio(self):
        r = row(load("B-M1"), "별누리코드_입찰보증보험증권.pdf")
        self.assertEqual(r["parsed"]["guarantee_period"], {"from": "2026-12-02", "to": "2027-01-31"})
        self.assertEqual(r["parsed"]["amount_ratio"], 5.0)
        self.assertEqual(row(load("B-M3"), "다올빛소프트_입찰보증보험증권.pdf")["parsed"]["guarantee_period"]["to"],
                         "2027-01-08")
        self.assertEqual(row(load("B-M2"), "물안개시스템_입찰보증보험증권.pdf")["parsed"]["guarantee_period"]["to"],
                         "2026-12-31")

    def test_boundary_sizes_read_as_numbers(self):
        self.assertEqual(row(load("A-M3"), "OSL_TEC.pdf")["size_mb"], 10.0)
        self.assertEqual(row(load("B-M3"), "다올빛소프트_제안서.pdf")["size_mb"], 30.0)
        self.assertEqual(row(load("B-M2"), "제안서최종(2).pdf")["size_mb"], 38.4)

    def test_lang_must_be_en_or_ko(self):
        with self.assertRaises(ValueError):
            parse_manifest("x", lang="fr")


class PII(unittest.TestCase):
    def test_no_synthetic_token_in_any_field(self):
        m = load("B-M2-PII")
        dumped = json.dumps(m, ensure_ascii=False)
        self.assertEqual(SYNTH_08.findall(dumped), [])
        self.assertNotIn("SYNTH", dumped)

    def test_counts_by_type(self):
        # Header: 담당자 (name), 연락처 (phone). 동의서 note: 4 names, 주민등록번호 (rrn), 급여계좌 (account).
        self.assertEqual(load("B-M2-PII")["pii_counts"], {"name": 5, "phone": 1, "rrn": 1, "account": 1})

    def test_only_the_consent_row_is_flagged(self):
        m = load("B-M2-PII")
        self.assertEqual([r["row_id"] for r in m["rows"] if r["pii"]], ["M5"])
        r = m["rows"][4]
        self.assertEqual(r["file_name"], "물안개시스템_개인정보동의서.pdf")
        self.assertIn("[MASKED:rrn]", r["notes"])
        self.assertEqual(r["parsed"]["headcount"], 4)

    def test_header_line_kept_masked_in_warnings_and_text(self):
        m = load("B-M2-PII")
        self.assertIn("담당자: [MASKED:name] / 연락처: [MASKED:phone]", m["masked_text"])
        self.assertTrue(any("[MASKED:name]" in w for w in m["warnings"]))

    def test_same_statuses_input_as_b_m2(self):
        # 08 T05: B-M2-PII equals B-M2 except the two marked places.
        a, b = load("B-M2"), load("B-M2-PII")
        self.assertEqual(a["profile"], b["profile"])
        for ra, rb in zip(a["rows"], b["rows"]):
            self.assertEqual({k: v for k, v in ra.items() if k not in ("notes", "pii")},
                             {k: v for k, v in rb.items() if k not in ("notes", "pii")})


if __name__ == "__main__":
    unittest.main()
