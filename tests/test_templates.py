"""Question templates and QV1-QV5 (design 09 §2-9). Expected strings are the 08 §2.5/§3.5 question
texts and the 09 §2-9 template texts, typed here as literals."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine import templates, textfilter  # noqa: E402

Q_A1 = ("Section 5 of the RFB states 30 November 2026, 14:00 hours (UTC+03:00), but Annex 1, BDS ITB 23.1 states "
        "2 December 2026, 14:00 hours. Is the deadline for submission of Bids 30 November 2026, 14:00 hours Corvessa "
        "time (UTC+03:00)? (Yes/No)")
Q_B1 = ("공고문 5.가는 '2026. 12. 9.(수) 10:00', 제안요청서 Ⅳ-1은 '2026년 12월 10일(목) 오전 10시'로 적혀 있습니다. "
        "전자입찰서(제안서 포함) 제출 마감은 2026. 12. 9.(수) 10:00입니까? (예/아니오)")
Q_B2 = "입찰보증보험증권의 '입찰서 제출 마감일부터 30일 이상'은 마감일 다음 날부터 30일을 센다는 뜻입니까? (예/아니오)"


class ReferenceQuestionsPassQV(unittest.TestCase):
    def test_q_a1(self):
        self.assertEqual(templates.check_question(Q_A1, values=("30 November 2026, 14:00 hours", "2 December 2026, 14:00 hours")),
                         {"ok": True, "failures": []})

    def test_q_b1(self):
        self.assertEqual(templates.check_question(Q_B1, values=("2026. 12. 9.(수) 10:00", "2026년 12월 10일(목) 오전 10시")),
                         {"ok": True, "failures": []})

    def test_q_b2_and_bidder_questions(self):
        for q in (Q_B2, "공동수급으로 참가합니까? (예/아니오)", "투입 예정 인력은 몇 명입니까?",
                  "Are you bidding as a joint venture (JV)? (Yes/No)"):
            with self.subTest(q=q):
                self.assertTrue(templates.check_question(q)["ok"])


class QVFailures(unittest.TestCase):
    def test_qv1_no_question_mark(self):
        self.assertEqual(templates.check_question("Please confirm the deadline (Yes/No)")["failures"], ["QV1"])

    def test_qv2_too_long(self):
        self.assertEqual(templates.check_question("a" * 300 + "?")["failures"], ["QV2"])

    def test_qv3_missing_value(self):
        q = "Is the deadline for submission 30 November 2026, 14:00 hours? (Yes/No)"
        self.assertEqual(templates.check_question(q, values=("30 November 2026", "2 December 2026"))["failures"], ["QV3"])

    def test_qv4_forbidden_phrase(self):
        q = "The official deadline is 30 November 2026, right? (Yes/No)"
        self.assertEqual(templates.check_question(q)["failures"], ["QV4"])

    def test_qv5_unmasked_values(self):
        self.assertEqual(templates.check_question("Is SYNTH-NAME-0001-TEST the contact person? (Yes/No)")["failures"], ["QV5"])
        self.assertEqual(templates.check_question("Should we call 010-1234-5678?")["failures"], ["QV5"])


class ConflictTemplates(unittest.TestCase):
    def test_en_template_09(self):
        q = templates.agency_conflict_question(
            "en", "Body §5", "30 November 2026, 14:00 hours Corvessa time (UTC+03:00)",
            "Annex 1 ITB 23.1", "Date: 2 December 2026; Time: 14:00 hours", "deadline for submission")
        self.assertEqual(q, "Body §5 states 30 November 2026, 14:00 hours Corvessa time (UTC+03:00), but Annex 1 ITB 23.1 "
                            "states Date: 2 December 2026; Time: 14:00 hours. Is the deadline for submission 30 November "
                            "2026, 14:00 hours Corvessa time (UTC+03:00)? (Yes/No)")
        self.assertTrue(templates.check_question(q, values=("30 November 2026", "2 December 2026"))["ok"])

    def test_ko_template_reproduces_q_b1(self):
        q = templates.agency_conflict_question("ko", "공고문 5.가", "2026. 12. 9.(수) 10:00", "제안요청서 Ⅳ-1",
                                               "2026년 12월 10일(목) 오전 10시", "전자입찰서(제안서 포함) 제출 마감")
        self.assertEqual(q, Q_B1)

    def test_ko_counting_template_reproduces_q_b2(self):
        q = templates.agency_counting_question("ko", "입찰서 제출 마감일부터 30일 이상", "마감일", 30, document="입찰보증보험증권")
        self.assertEqual(q, Q_B2)

    def test_en_counting_template(self):
        q = templates.agency_counting_question("en", "valid for 30 days from the deadline", "the deadline", 30)
        self.assertEqual(q, "Does 'valid for 30 days from the deadline' mean counting 30 days starting the day after "
                            "the deadline? (Yes/No)")

    def test_topics(self):
        self.assertEqual((templates.conflict_topic("K1", "en"), templates.conflict_topic("K1", "ko")), ("deadline", "마감"))


class BidderTemplates(unittest.TestCase):
    def test_09_literals(self):
        cases = [
            ("fact:consortium", "en", "", "Are you bidding as a joint venture (JV)? (Yes/No)"),
            ("fact:consortium", "ko", "", "공동수급으로 참가합니까? (예/아니오)"),
            ("fact:usage_seal", "ko", "", "제출서류에 사용인감을 날인했습니까? (예/아니오)"),
            ("fact:usage_seal", "en", "", "Did you seal the documents with an authorized-use seal instead of the "
                                          "registered company seal? (Yes/No)"),
            ("attr:headcount", "ko", "", "투입 예정 인력은 몇 명입니까?"),
            ("attr:headcount", "en", "", "How many proposed staff are there?"),
            ("attr:issue_date", "ko", "법인등기부등본", "법인등기부등본의 발급일은 언제입니까?"),
            ("attr:issue_date", "en", "QDS_TAX.pdf", "What is the issue date of QDS_TAX.pdf?"),
        ]
        for key, lang, doc, want in cases:
            with self.subTest(key=key, lang=lang):
                self.assertEqual(templates.bidder_question(key, lang, document=doc), want)

    def test_other_keys_have_text(self):
        self.assertEqual(templates.bidder_question("fact:other", "en", context="Body §4"),
                         "Does the condition stated in Body §4 apply to your bid? (Yes/No)")
        self.assertEqual(templates.bidder_question("attr:notarized", "ko", document="위임장"),
                         "위임장은 공증을 받았습니까? (예/아니오)")
        self.assertEqual(templates.bidder_question("attr:colour", "en", document="the form"),
                         "What is the colour of the form?")

    def test_every_template_is_clean_and_ends_as_a_question(self):
        keys = [f"fact:{k}" for k in templates.FACT_QUESTIONS] + [f"attr:{k}" for k in templates.ATTR_QUESTIONS]
        for key in keys:
            for lang in ("en", "ko"):
                q = templates.bidder_question(key, lang, document="X", context="Y")
                with self.subTest(key=key, lang=lang):
                    self.assertEqual(textfilter.blocked(q), [])
                    self.assertNotIn("QV1", templates.check_question(q)["failures"])


class KoreanParticles(unittest.TestCase):
    def test_particles(self):
        cases = [("공고문 5.가", "은/는", "는"), ("제안요청서 Ⅳ-1", "은/는", "은"), ("제출 마감", "은/는", "은"),
                 ("오전 10시", "으로/로", "로"), ("30일", "을/를", "을"), ("3개월", "을/를", "을"),
                 ("서울", "으로/로", "로"), ("PDF", "은/는", "은(는)")]
        for word, pair, want in cases:
            with self.subTest(word=word):
                self.assertEqual(templates.particle(word, pair), want)

    def test_circled_number_locations(self):
        # 08 B locations end in circled numbers (공고문 3.⑥); ⑥ is read 육, ② 이, ⑫ 십이, ⑳ 이십.
        cases = [("공고문 3.⑥", "은/는", "은"), ("공고문 3.②", "은/는", "는"), ("공고문 3.⑫", "이/가", "가"),
                 ("공고문 3.⑦", "으로/로", "로"), ("공고문 3.⑳", "을/를", "을")]
        for word, pair, want in cases:
            with self.subTest(word=word):
                self.assertEqual(templates.particle(word, pair), want)
        self.assertEqual(templates.bidder_question("fact:other", "ko", context="공고문 3.⑥"),
                         "공고문 3.⑥에 적힌 조건이 이번 입찰에 해당합니까? (예/아니오)")


if __name__ == "__main__":
    unittest.main()
