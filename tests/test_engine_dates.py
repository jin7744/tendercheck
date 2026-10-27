"""Q5 parsing and F7 validity arithmetic. Every expected date is a literal from spec 08
(§2.5 / §3.2 / §3.5 hand calculations) or written out by hand here."""
import unittest
from datetime import date

import test_engine_fixtures  # noqa: F401  (sys.path)

from engine.dates import (add_months, counting_from_example, evaluate_validity, parse_datetime_text,
                          period_end, same_reading, to_offset, window_start)

ANNEX1_TZ = "All times in this Bid Data Sheet are Corvessa local time (UTC+03:00)."
BODY_S6 = ("A Tax Clearance Certificate is valid for 90 days from its date of issue (for example, a certificate "
           "issued on 1 March is valid through 30 May). The certificate must be valid on the deadline for "
           "submission of Bids.")
A_DEADLINES = {"Body §5": "2026-11-30T14:00:00+03:00", "Annex 1 ITB 23.1": "2026-12-02T14:00:00+03:00"}
B_DEADLINES = {"공고문 5.가": "2026-12-09T10:00:00", "제안요청서 Ⅳ-1": "2026-12-10T10:00:00"}


class ParseDeadline(unittest.TestCase):
    def test_annex_tz_sentence(self):
        p = parse_datetime_text("30 November 2026, 14:00 hours", ANNEX1_TZ)
        self.assertTrue(p["ok"], p["error"])
        self.assertEqual(p["iso_local"], "2026-11-30T14:00:00+03:00")
        self.assertEqual(p["tz_source"], "tz_quote")

    def test_body_s5_states_its_own_zone(self):
        p = parse_datetime_text("30 November 2026, 14:00 hours Corvessa time (UTC+03:00)")
        self.assertEqual((p["iso_local"], p["tz_source"], p["info"]), ("2026-11-30T14:00:00+03:00", "raw", []))

    def test_itb_23_1(self):
        p = parse_datetime_text("Date: 2 December 2026; Time: 14:00 hours.", ANNEX1_TZ)
        self.assertEqual(p["iso_local"], "2026-12-02T14:00:00+03:00")

    def test_itb_23_1_without_zone(self):
        p = parse_datetime_text("Date: 2 December 2026; Time: 14:00 hours.")
        self.assertEqual((p["iso_local"], p["offset"], p["info"]), ("2026-12-02T14:00:00", "", ["TZ_NOT_STATED"]))

    def test_korean_dot_form(self):
        p = parse_datetime_text("2026. 12. 9.(수) 10:00 (나라장터 서버 시각 기준)")
        self.assertEqual(p["iso_local"], "2026-12-09T10:00:00")
        self.assertEqual((p["weekday_stated"], p["weekday_ok"]), ("수", True))
        self.assertEqual(p["info"], ["TZ_NOT_STATED"])

    def test_korean_word_form_am(self):
        p = parse_datetime_text("2026년 12월 10일(목) 오전 10시")
        self.assertEqual((p["iso_local"], p["weekday_ok"]), ("2026-12-10T10:00:00", True))

    def test_korean_pm_with_minutes(self):
        self.assertEqual(parse_datetime_text("2026년 12월 10일 오후 2시 30분")["iso_local"], "2026-12-10T14:30:00")

    def test_weekday_mismatch_is_info_only(self):
        p = parse_datetime_text("2026. 12. 9.(목) 10:00")
        self.assertTrue(p["ok"])
        self.assertEqual(p["date"], "2026-12-09")
        self.assertIs(p["weekday_ok"], False)
        self.assertIn("WEEKDAY_MISMATCH", p["info"])

    def test_notice_and_evaluation_weekdays(self):
        # 08 §3.2: 2026-11-20 Fri, 12-15 Tue.
        self.assertIs(parse_datetime_text("2026. 11. 20.(금)")["weekday_ok"], True)
        self.assertIs(parse_datetime_text("2026. 12. 15.(화) 예정")["weekday_ok"], True)

    def test_english_12h_and_iso(self):
        self.assertEqual(parse_datetime_text("November 30, 2026 2:00 PM")["iso_local"], "2026-11-30T14:00:00")
        self.assertEqual(parse_datetime_text("2026-11-30T14:00:00+03:00")["iso_local"], "2026-11-30T14:00:00+03:00")
        self.assertEqual(parse_datetime_text("31 March 2027")["iso_local"], "2027-03-31")

    def test_planned_uploads(self):
        self.assertEqual(parse_datetime_text("2026-11-30 20:30 KST (UTC+09:00)")["iso_local"],
                         "2026-11-30T20:30:00+09:00")
        self.assertEqual(parse_datetime_text("2026-11-27 10:00 UTC+03:00")["iso_local"],
                         "2026-11-27T10:00:00+03:00")
        self.assertEqual(parse_datetime_text("2026-11-30 20:30 Asia/Seoul")["iso_local"],
                         "2026-11-30T20:30:00+09:00")

    def test_unparseable_or_ambiguous(self):
        for raw in ("the date stated in BDS ITB 19.1", "", "2027. 1. 1. ~ 2027. 12. 31.",
                    "30 November 2026 14:00 or 15:00", "31 November 2026"):
            self.assertFalse(parse_datetime_text(raw)["ok"], raw)

    def test_conflicting_zones_fail_closed(self):
        self.assertFalse(parse_datetime_text("30 November 2026, 14:00 (UTC+03:00)", "(UTC+02:00)")["ok"])
        self.assertFalse(parse_datetime_text("30 November 2026, 14:00 KST (UTC+03:00)")["ok"])


class Conversion(unittest.TestCase):
    def test_a_m3_upload_in_deadline_zone(self):
        # 08 §2.5: 20:30 KST = 14:30 UTC+03:00; §5 deadline 14:00 UTC+03:00 = 20:00 KST.
        self.assertEqual(to_offset("2026-11-30T20:30:00+09:00", "+03:00").isoformat(), "2026-11-30T14:30:00+03:00")
        self.assertEqual(to_offset("2026-11-30T14:00:00+03:00", "+09:00").isoformat(), "2026-11-30T20:00:00+09:00")

    def test_naive_cannot_convert(self):
        with self.assertRaises(ValueError):
            to_offset("2026-12-09T10:00:00", "+09:00")


class SameReading(unittest.TestCase):
    def setUp(self):
        self.p = parse_datetime_text("Date: 2 December 2026; Time: 14:00 hours.", ANNEX1_TZ)

    def test_match(self):
        self.assertEqual(same_reading(self.p, "2026-12-02T14:00:00+03:00"), (True, ""))
        self.assertTrue(same_reading(self.p, "2026-12-02T14:00:00")[0])

    def test_mismatch(self):
        self.assertFalse(same_reading(self.p, "2026-12-03T14:00:00+03:00")[0])
        self.assertFalse(same_reading(self.p, "2026-12-02T15:00:00+03:00")[0])
        self.assertFalse(same_reading(self.p, "2026-12-02T14:00:00+09:00")[0])
        self.assertFalse(same_reading(self.p, "2026-12-02")[0])
        self.assertFalse(same_reading(self.p, "Dec 2")[0])


class Arithmetic(unittest.TestCase):
    def test_add_months_clamps_month_end(self):
        self.assertEqual(add_months(date(2026, 5, 31), -3), date(2026, 2, 28))
        self.assertEqual(add_months(date(2024, 5, 31), -3), date(2024, 2, 29))
        self.assertEqual(add_months(date(2026, 12, 9), -3), date(2026, 9, 9))

    def test_08_example_1_march_to_30_may(self):
        self.assertEqual(period_end(date(2026, 3, 1), days=90, convention="anchor_not_counted"), date(2026, 5, 30))

    def test_08_windows_and_guarantee_ends(self):
        # 08 §3.5: 12/9 - 3 months = 9/9, 12/10 -> 9/10; 12/9 + 30 = 1/8, 12/10 + 30 = 1/9; day-1 counting 1/7, 1/8.
        self.assertEqual(window_start(date(2026, 12, 9), months=3, convention="anchor_not_counted"), date(2026, 9, 9))
        self.assertEqual(window_start(date(2026, 12, 10), months=3, convention="anchor_not_counted"), date(2026, 9, 10))
        self.assertEqual(period_end(date(2026, 12, 9), days=30, convention="anchor_not_counted"), date(2027, 1, 8))
        self.assertEqual(period_end(date(2026, 12, 10), days=30, convention="anchor_not_counted"), date(2027, 1, 9))
        self.assertEqual(period_end(date(2026, 12, 9), days=30, convention="anchor_counted"), date(2027, 1, 7))
        self.assertEqual(period_end(date(2026, 12, 10), days=30, convention="anchor_counted"), date(2027, 1, 8))

    def test_counting_fixed_by_example(self):
        self.assertEqual(counting_from_example(BODY_S6, days=90), ("anchor_not_counted",))
        self.assertIsNone(counting_from_example("valid for 90 days from its date of issue", days=90))


class ValidityPackageA(unittest.TestCase):
    """R07 (Body §6, 90 days from issue, valid on the deadline) and R13 (ITB 20.2)."""
    R07 = {"rule_type": "valid_for_from_issue", "raw": BODY_S6, "months": 0, "days": 90, "until_iso": "",
           "anchor_event": "submission_deadline"}
    R13 = {"rule_type": "valid_until_date", "raw": "The Bid Security shall remain valid until 28 April 2027.",
           "months": 0, "days": 0, "until_iso": "2027-04-28", "anchor_event": "not_stated"}

    def test_m1_met_under_both_deadlines(self):
        r = evaluate_validity(self.R07, issue_date="2026-10-15", anchors=A_DEADLINES)
        self.assertEqual((r["result"], r["reason_codes"]), ("pass", ["SAME_UNDER_ALL_READINGS"]))
        self.assertEqual({x["limit"] for x in r["readings"]}, {"2027-01-13"})

    def test_m2_expired_under_both(self):
        r = evaluate_validity(self.R07, issue_date="2026-08-20", anchors=A_DEADLINES)
        self.assertEqual(r["result"], "fail")
        self.assertEqual({x["limit"] for x in r["readings"]}, {"2026-11-18"})

    def test_m3_depends_on_conflict(self):
        r = evaluate_validity(self.R07, issue_date="2026-09-01", anchors=A_DEADLINES)
        self.assertEqual((r["result"], r["reason_codes"], r["split_by"]), ("unknown", ["CONFLICT_DEPENDENT"], ["anchor"]))
        self.assertEqual({x["anchor"]: x["result"] for x in r["readings"]}, {"Body §5": "pass", "Annex 1 ITB 23.1": "fail"})
        self.assertEqual({x["limit"] for x in r["readings"]}, {"2026-11-30"})

    def test_missing_issue_date(self):
        r = evaluate_validity(self.R07, issue_date="unknown", anchors=A_DEADLINES)
        self.assertEqual((r["result"], r["reason_codes"]), ("unknown", ["ATTRIBUTE_UNKNOWN:issue_date"]))

    def test_r13_bid_security(self):
        self.assertEqual(evaluate_validity(self.R13, file_until="2027-04-28")["result"], "pass")
        self.assertEqual(evaluate_validity(self.R13, file_until="2027-04-27")["result"], "fail")
        self.assertEqual(evaluate_validity(self.R13, file_until=None)["reason_codes"], ["ATTRIBUTE_UNKNOWN:valid_until"])

    def test_r13_model_date_disagrees(self):
        r = evaluate_validity({**self.R13, "until_iso": "2027-04-27"}, file_until="2027-04-28")
        self.assertEqual((r["result"], r["reason_codes"]), ("unknown", ["ATTRIBUTE_UNKNOWN:required_until"]))


class ValidityPackageB(unittest.TestCase):
    """R05/R06 (3 months before the deadline) and R11 (30 days from the deadline)."""
    R05 = {"rule_type": "issued_within_before_anchor", "raw": "입찰서 제출 마감일 기준 최근 3개월 이내 발급분",
           "months": 3, "days": 0, "until_iso": "", "anchor_event": "submission_deadline"}
    R11 = {"rule_type": "valid_at_least_after_anchor", "raw": "보증기간은 입찰서 제출 마감일부터 30일 이상으로 한다",
           "months": 0, "days": 30, "until_iso": "", "anchor_event": "submission_deadline"}
    AS_OF = "2026-12-05T09:00:00+09:00"

    def test_r05_r06(self):
        cases = {"2026-11-23": "pass", "2026-08-03": "fail", "2026-09-15": "pass"}
        got = {d: evaluate_validity(self.R05, issue_date=d, anchors=B_DEADLINES, as_of=self.AS_OF)["result"]
               for d in cases}
        self.assertEqual(got, cases)

    def test_r05_m3_issue_date_unknown(self):
        r = evaluate_validity(self.R05, issue_date="unknown", anchors=B_DEADLINES, as_of=self.AS_OF)
        self.assertEqual((r["result"], r["reason_codes"]), ("unknown", ["ATTRIBUTE_UNKNOWN:issue_date"]))

    def test_window_boundary_counting_ambiguous(self):
        # Issued 9/9 with a single 12/9 anchor: window starts 9/9 (not counted) or 9/10 (counted).
        r = evaluate_validity(self.R05, issue_date="2026-09-09", anchors={"공고문 5.가": "2026-12-09"})
        self.assertEqual((r["result"], r["reason_codes"]), ("unknown", ["COUNTING_AMBIGUOUS"]))

    def test_issue_after_as_of_is_not_a_pass(self):
        r = evaluate_validity(self.R05, issue_date="2026-12-06", anchors=B_DEADLINES, as_of=self.AS_OF)
        self.assertEqual((r["result"], r["info"]), ("unknown", ["ISSUE_DATE_AFTER_AS_OF"]))

    def test_r11(self):
        m1 = evaluate_validity(self.R11, file_until="2027-01-31", anchors=B_DEADLINES)
        m2 = evaluate_validity(self.R11, file_until="2026-12-31", anchors=B_DEADLINES)
        self.assertEqual((m1["result"], m2["result"]), ("pass", "fail"))

    def test_r11_m3_conflict_and_counting(self):
        r = evaluate_validity(self.R11, file_until="2027-01-08", anchors=B_DEADLINES)
        self.assertEqual(r["result"], "unknown")
        self.assertEqual(r["reason_codes"], ["CONFLICT_DEPENDENT", "COUNTING_AMBIGUOUS"])
        got = {(x["anchor"], x["convention"]): (x["limit"], x["result"]) for x in r["readings"]}
        self.assertEqual(got, {
            ("공고문 5.가", "anchor_not_counted"): ("2027-01-08", "pass"),
            ("공고문 5.가", "anchor_counted"): ("2027-01-07", "pass"),
            ("제안요청서 Ⅳ-1", "anchor_not_counted"): ("2027-01-09", "fail"),
            ("제안요청서 Ⅳ-1", "anchor_counted"): ("2027-01-08", "pass"),
        })

    def test_none_rule(self):
        self.assertEqual(evaluate_validity({"rule_type": "none"})["result"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
