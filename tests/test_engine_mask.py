"""D10 masking. Expected strings are written by hand from spec 08 §4.2 T05 and 09 §2-3."""
import re
import unittest

from test_engine_fixtures import B_M2_PII_HEADER, B_M2_PII_NOTE

from engine.mask import counts_list, mask_text, type_labels
from engine.normalize import build_source_bundle

SYNTH_08 = re.compile(r"SYNTH-[A-Z]+-\d{4}-TEST")


class T05Markers(unittest.TestCase):
    def test_header_line(self):
        r = mask_text(B_M2_PII_HEADER)
        self.assertEqual(r.text, "담당자: [MASKED:name] / 연락처: [MASKED:phone]")
        self.assertEqual(r.counts, {"name": 1, "phone": 1})

    def test_note_line(self):
        r = mask_text(B_M2_PII_NOTE)
        self.assertEqual(r.text, "투입 예정 인력 4명 - [MASKED:name](주민등록번호 [MASKED:rrn]), [MASKED:name], "
                                 "[MASKED:name], [MASKED:name] / 급여계좌 [MASKED:account]")
        self.assertEqual(r.counts, {"name": 4, "rrn": 1, "account": 1})

    def test_both_lines_counts_and_no_marker_left(self):
        r = mask_text(B_M2_PII_HEADER + "\n" + B_M2_PII_NOTE)
        self.assertEqual(counts_list(r.counts), [{"type": "name", "count": 5}, {"type": "rrn", "count": 1},
                                                 {"type": "phone", "count": 1}, {"type": "account", "count": 1}])
        self.assertEqual(SYNTH_08.findall(r.text), [])
        self.assertNotIn("SYNTH", r.text)
        self.assertEqual(type_labels(r.counts, "ko"), "성명·주민등록번호·연락처·계좌")

    def test_a5_marker_format(self):
        self.assertEqual(mask_text("담당자: SYNTH-NAME-TEST-ONLY").text, "담당자: [MASKED:name]")

    def test_unknown_marker_token_uses_type_word(self):
        self.assertEqual(mask_text("연락처: SYNTH-XYZ-0001-TEST").text, "연락처: [MASKED:phone]")
        self.assertEqual(mask_text("memo SYNTH-XYZ-0001-TEST").text, "memo [MASKED:synthetic]")

    def test_idempotent(self):
        once = mask_text(B_M2_PII_NOTE).text
        again = mask_text(once)
        self.assertEqual((again.text, again.counts), (once, {}))

    def test_bundle_masks_before_segmenting(self):
        b = build_source_bundle([{"doc_id": "X", "title": "T", "text": "§1 " + B_M2_PII_HEADER}], output_lang="ko")
        self.assertEqual(b["segments"][0]["text"], "§1 담당자: [MASKED:name] / 연락처: [MASKED:phone]")
        self.assertEqual(b["pii_masked"], [{"type": "name", "count": 1}, {"type": "phone", "count": 1}])
        self.assertNotIn("SYNTH", repr(b))


class ShapePatterns(unittest.TestCase):
    # Placeholder values only: all-zero digit groups and reserved example domains.
    def test_shapes(self):
        cases = {
            "주민번호 000000-0000000 기재": "주민번호 [MASKED:rrn] 기재",
            "tel 010-0000-0000.": "tel [MASKED:phone].",
            "call +82-10-0000-0000 now": "call [MASKED:phone] now",
            "mail someone@example.com today": "mail [MASKED:email] today",
            "password: zz9-qq8": "password: [MASKED:password]",
            "비밀번호=abc123 입니다": "비밀번호=[MASKED:password] 입니다",
            "급여 000-000000-00-000 입금": "급여 [MASKED:account] 입금",
        }
        self.assertEqual({k: mask_text(k).text for k in cases}, cases)


class NoFalsePositives(unittest.TestCase):
    def test_08_strings_unchanged(self):
        unchanged = [
            "QDS_BF1.pdf | PDF | 0.4 | yes | yes | 2026-11-20 |",
            "QDS_BF2.pdf | PDF | 0.5 | no | no | 2026-11-16 | bank guarantee; amount USD 12,000; valid until 2027-04-28",
            "보증금액 입찰금액의 5%, 보증기간 2026-12-02~2027-01-31",
            "공동수급협정서[서식 제2호] | 별누리코드_공동수급협정서.pdf | PDF | 0.5 | 구성원 2개사 인감 날인 | 2026-11-30 | 공동이행, 지분 60:40",
            "REQUEST FOR BIDS - RFB No. CMWA/ITM/2026/014",
            "가온시 시설관리공단 공고 제2026-0412호 (가상 기관)",
            " 가. 전자입찰서(제안서 포함) 제출 마감: 2026. 12. 9.(수) 10:00 (나라장터 서버 시각 기준)",
            "Planned upload: 2026-11-30 20:30 KST (UTC+09:00)",
            "BF4-1, BF4-2, BF4-3 JV Member Information Form, one file per member (JV only).",
            "비밀번호를 입력하지 마십시오.",
        ]
        for s in unchanged:
            r = mask_text(s)
            self.assertEqual((r.text, r.counts), (s, {}), s)


if __name__ == "__main__":
    unittest.main()
