"""K1-K5 candidates, adjudication intake and the D18 merge (design 09 §2-8, 08 T03).
Inputs are hand-written Req dicts verified against the 08 packages; expected quotes, values and
locations are literals copied from the 08 source lines."""
import copy
import unittest

from test_engine_checks import req
from test_engine_fixtures import bundle_a, bundle_b

from engine import conflicts as K
from engine import textfilter
from engine.checks import check_requirement
from engine.normalize import build_source_bundle

S5 = "The deadline for submission of Bids is 30 November 2026, 14:00 hours Corvessa time (UTC+03:00)."
ITB231 = "The deadline for submission of Bids is: Date: 2 December 2026; Time: 14:00 hours."
TZ = "All times in this Bid Data Sheet are Corvessa local time (UTC+03:00)."
B5GA = "전자입찰서(제안서 포함) 제출 마감: 2026. 12. 9.(수) 10:00 (나라장터 서버 시각 기준)"
BIV1 = "제안서 제출 마감 일시: 2026년 12월 10일(목) 오전 10시"


def vreq(r, bundle):
    return {"req": r, "check": check_requirement(r, bundle)}


def a_deadlines(order=("body", "annex")):
    A = bundle_a()
    body = req(S5, "A_BODY", "A_BODY-L25", tmp_id="R1", category="deadline", subject="bid submission deadline",
               deadline={"kind": "submission", "raw": "30 November 2026, 14:00 hours Corvessa time (UTC+03:00)",
                         "iso_local": "2026-11-30T14:00:00+03:00", "tz_quote": ""})
    annex = req(ITB231, "A_ANNEX1", "A_ANNEX1-L10", tmp_id="R2", category="deadline", subject="bid submission deadline",
                deadline={"kind": "submission", "raw": "Date: 2 December 2026; Time: 14:00 hours",
                          "iso_local": "2026-12-02T14:00:00+03:00", "tz_quote": TZ})
    pick = {"body": body, "annex": annex}
    return A, [vreq(pick[k], A) for k in order]


def b_deadlines():
    B = bundle_b()
    notice = req(B5GA, "B_NOTICE", "B_NOTICE-L32", tmp_id="R1", category="deadline", subject="proposal submission deadline",
                 deadline={"kind": "submission", "raw": "2026. 12. 9.(수) 10:00", "iso_local": "2026-12-09T10:00:00",
                           "tz_quote": ""})
    rfp = req(BIV1, "B_RFP", "B_RFP-L4", tmp_id="R2", category="deadline", subject="proposal submission deadline",
              deadline={"kind": "submission", "raw": "2026년 12월 10일(목) 오전 10시", "iso_local": "2026-12-10T10:00:00",
                        "tz_quote": ""})
    return B, [vreq(notice, B), vreq(rfp, B)]


def two_doc_bundle(text_x, text_y, lang="en"):
    return build_source_bundle([{"doc_id": "X_NOTICE", "title": "Notice", "text": text_x},
                                {"doc_id": "Y_RFP", "title": "RFP", "text": text_y}], bundle_id="T", output_lang=lang)


def hours_case(hour_x="17:00", hour_y="18:00", kind_y="submission", quote_y=None):
    bundle = two_doc_bundle(f"1. Bids are due by 1 December 2026, {hour_x} (UTC+09:00).",
                            f"1. Bids are due by 1 December 2026, {hour_y} (UTC+09:00).")
    vs = []
    for tmp, doc, hour, kind in (("R1", "X_NOTICE", hour_x, "submission"), ("R2", "Y_RFP", hour_y, kind_y)):
        q = f"Bids are due by 1 December 2026, {hour} (UTC+09:00)."
        vs.append(vreq(req(q, doc, f"{doc}-L1", tmp_id=tmp, category="deadline", subject="bid deadline",
                           deadline={"kind": kind, "raw": f"1 December 2026, {hour} (UTC+09:00)",
                                     "iso_local": f"2026-12-01T{hour}:00+09:00", "tz_quote": ""}), bundle))
    return bundle, vs


class Inputs(unittest.TestCase):
    def test_hand_built_requirements_verify(self):
        for _, vs in (a_deadlines(), b_deadlines(), hours_case()):
            for v in vs:
                self.assertTrue(v["check"]["verified"] and v["check"]["grounded"], v["check"]["failures"])


class K1Deadlines(unittest.TestCase):
    def test_package_a_candidate(self):
        _, vs = a_deadlines()
        cands = K.find_candidates(vs)
        self.assertEqual([(c["id"], c["rule"], c["a"], c["b"]) for c in cands], [("K1", "K1", "R1", "R2")])
        self.assertEqual(cands[0]["values"], {"a": "30 November 2026, 14:00 hours Corvessa time (UTC+03:00)",
                                              "b": "Date: 2 December 2026; Time: 14:00 hours"})

    def test_model_failure_keeps_the_conflict_open_and_picks_no_side(self):
        A, vs = a_deadlines(order=("annex", "body"))
        res = K.apply_adjudication(K.find_candidates(vs), None, A)
        self.assertEqual(len(res["conflicts"]), 1)
        c = res["conflicts"][0]
        self.assertEqual(c["id"], "C1")
        self.assertEqual(c["topic"], "deadline")
        self.assertEqual(c["sides"], [{"quote": S5, "location": "Body §5"}, {"quote": ITB231, "location": "Annex 1 ITB 23.1"}])
        self.assertEqual(c["members"], ["R1", "R2"])
        self.assertEqual(c["anchors"], {"Body §5": "2026-11-30T14:00:00+03:00", "Annex 1 ITB 23.1": "2026-12-02T14:00:00+03:00"})
        self.assertIn("30 November 2026", c["agency_question"])
        self.assertIn("2 December 2026", c["agency_question"])
        self.assertTrue(c["agency_question"].endswith("(Yes/No)"))
        self.assertEqual(textfilter.blocked(c["agency_question"]), [])
        self.assertEqual(res["log"], [{"candidate": "K1", "action": "kept", "why": "model output unavailable"}])

    def test_17_vs_18_stays_open_when_the_model_says_same_obligation(self):
        bundle, vs = hours_case()
        cands = K.find_candidates(vs)
        self.assertEqual(cands[0]["values"], {"a": "1 December 2026, 17:00 (UTC+09:00)", "b": "1 December 2026, 18:00 (UTC+09:00)"})
        for verdict in ("yes", "unclear"):
            x = {"adjudications": [{"candidate_id": "K1", "same_obligation": verdict, "distinguishing_quote_a": "",
                                    "distinguishing_quote_b": "", "explanation": "Same deadline."}],
                 "new_conflicts": [], "questions": []}
            res = K.apply_adjudication(cands, x, bundle)
            with self.subTest(verdict=verdict):
                self.assertEqual([c["id"] for c in res["conflicts"]], ["C1"])
                self.assertEqual(res["closed"], [])
                q = res["conflicts"][0]["agency_question"]
                self.assertIn("17:00", q)
                self.assertIn("18:00", q)

    def test_same_written_time_is_no_candidate(self):
        _, vs = hours_case("17:00", "17:00")
        self.assertEqual(K.find_candidates(vs), [])

    def test_different_kinds_are_no_candidate(self):
        _, vs = hours_case(kind_y="questions")
        self.assertEqual(K.find_candidates(vs), [])

    def test_korean_notation_difference_cannot_close(self):
        B, vs = b_deadlines()
        cands = K.find_candidates(vs)
        self.assertEqual(cands[0]["values"], {"a": "2026. 12. 9.(수) 10:00", "b": "2026년 12월 10일(목) 오전 10시"})
        x = {"adjudications": [{"candidate_id": "K1", "same_obligation": "no", "distinguishing_quote_a": "10:00",
                                "distinguishing_quote_b": "오전 10시", "explanation": "Different notation."}],
             "new_conflicts": [], "questions": []}
        res = K.apply_adjudication(cands, x, B)
        self.assertEqual([c["id"] for c in res["conflicts"]], ["C1"])
        self.assertIn("notation", res["log"][0]["why"])
        c = res["conflicts"][0]
        self.assertEqual(c["topic"], "마감")
        self.assertEqual([s["location"] for s in c["sides"]], ["공고문 5.가", "제안요청서 Ⅳ-1"])
        self.assertIn("12. 9.", c["agency_question"])
        self.assertTrue(c["agency_question"].endswith("(예/아니오)"))
        self.assertEqual(c["anchors"], {"공고문 5.가": "2026-12-09T10:00:00", "제안요청서 Ⅳ-1": "2026-12-10T10:00:00"})


class Closing(unittest.TestCase):
    X = "1. The deadline for questions is 20 November 2026, 17:00 (UTC+09:00).\n\n2. Questions about appeals go to the registry."
    Y = "1. The deadline for submission of bids is 1 December 2026, 17:00 (UTC+09:00)."
    QX = "The deadline for questions is 20 November 2026, 17:00 (UTC+09:00)."
    QY = "The deadline for submission of bids is 1 December 2026, 17:00 (UTC+09:00)."

    def setUp(self):
        self.bundle = two_doc_bundle(self.X, self.Y)
        self.vs = [
            vreq(req(self.QX, "X_NOTICE", "X_NOTICE-L1", tmp_id="R1", category="deadline", subject="deadline",
                     deadline={"kind": "submission", "raw": "20 November 2026, 17:00 (UTC+09:00)",
                               "iso_local": "2026-11-20T17:00:00+09:00", "tz_quote": ""}), self.bundle),
            vreq(req(self.QY, "Y_RFP", "Y_RFP-L1", tmp_id="R2", category="deadline", subject="deadline",
                     deadline={"kind": "submission", "raw": "1 December 2026, 17:00 (UTC+09:00)",
                               "iso_local": "2026-12-01T17:00:00+09:00", "tz_quote": ""}), self.bundle)]
        self.cands = K.find_candidates(self.vs)

    def adj(self, qa, qb, same="no"):
        return {"adjudications": [{"candidate_id": "K1", "same_obligation": same, "distinguishing_quote_a": qa,
                                   "distinguishing_quote_b": qb, "explanation": "Questions vs submission."}],
                "new_conflicts": [], "questions": []}

    def test_verified_distinguishing_quotes_close(self):
        res = K.apply_adjudication(self.cands, self.adj("deadline for questions", "deadline for submission of bids"), self.bundle)
        self.assertEqual(res["conflicts"], [])
        self.assertEqual(res["closed"][0]["distinguishing_quotes"], ["deadline for questions", "deadline for submission of bids"])
        self.assertEqual(res["log"][0]["action"], "closed")

    def test_unverified_or_weak_quotes_keep_it_open(self):
        cases = [
            (("deadline for appeals", "deadline for submission of bids"), "(Q1)"),
            (("Questions about appeals", "deadline for submission of bids"), "(Q2)"),
            (("The deadline for", "deadline for submission of bids"), "other side"),
            (("", "deadline for submission of bids"), "missing"),
            (("20 November 2026", "1 December 2026"), "notation"),
        ]
        for (qa, qb), why in cases:
            with self.subTest(qa=qa):
                res = K.apply_adjudication(self.cands, self.adj(qa, qb), self.bundle)
                self.assertEqual([c["id"] for c in res["conflicts"]], ["C1"])
                self.assertIn(why, res["log"][0]["why"])

    def test_unknown_candidate_id_is_ignored(self):
        x = {"adjudications": [{"candidate_id": "K9", "same_obligation": "no", "distinguishing_quote_a": "a",
                                "distinguishing_quote_b": "b", "explanation": ""}], "new_conflicts": [], "questions": []}
        res = K.apply_adjudication(self.cands, x, self.bundle)
        self.assertEqual([c["id"] for c in res["conflicts"]], ["C1"])
        self.assertEqual([e["action"] for e in res["log"]], ["ignored", "kept"])


class ModelQuestions(unittest.TestCase):
    Q_A1 = ("Section 5 of the RFB states 30 November 2026, 14:00 hours (UTC+03:00), but Annex 1, BDS ITB 23.1 states "
            "2 December 2026, 14:00 hours. Is the deadline for submission of Bids 30 November 2026, 14:00 hours Corvessa "
            "time (UTC+03:00)? (Yes/No)")

    def run_with(self, question):
        A, vs = a_deadlines()
        x = {"adjudications": [], "new_conflicts": [],
             "questions": [{"for_id": "K1", "question": question, "answer_type": "yes_no", "options": ["Yes", "No"]}]}
        return K.apply_adjudication(K.find_candidates(vs), x, A)["conflicts"][0]

    def test_08_style_question_is_kept(self):
        c = self.run_with(self.Q_A1)
        self.assertEqual((c["agency_question"], c["question_source"]), (self.Q_A1, "model"))

    def test_side_picking_question_is_replaced(self):
        c = self.run_with("The official deadline is 30 November 2026, 14:00; 2 December 2026, 14:00 is a typo, right?")
        self.assertEqual(c["question_source"], "template")
        self.assertNotIn("official", c["agency_question"])

    def test_question_missing_one_value_is_replaced(self):
        c = self.run_with("Is the deadline 30 November 2026, 14:00? (Yes/No)")
        self.assertEqual(c["question_source"], "template")


class OtherRules(unittest.TestCase):
    @staticmethod
    def hand(tmp, quote, **fields):
        r = req(quote, "D", "D-L1", tmp_id=tmp, subject="each file", **fields)
        return {"req": r, "check": {"tmp_id": tmp, "verified": True, "grounded": True, "remove": False, "quote": quote,
                                    "location": f"Doc {tmp}", "seg_id": "D-L1", "doc_id": "D", "deadline": None}}

    def test_k2_size(self):
        vs = [self.hand("R1", "No single file may exceed 10 MB.", format={"max_size_mb": 10}),
              self.hand("R2", "파일 1개의 용량은 30MB 이하로 한다.", format={"max_size_mb": 30})]
        c = K.find_candidates(vs)
        self.assertEqual((c[0]["rule"], c[0]["values"]), ("K2", {"a": "10 MB", "b": "30MB"}))

    def test_k3_disjoint_extensions_only(self):
        vs = [self.hand("R1", "All files shall be in PDF format.", format={"extensions": ["pdf"]}),
              self.hand("R2", "Files shall be HWP documents.", format={"extensions": [".hwp"]}),
              self.hand("R3", "PDF or DOCX files are accepted.", format={"extensions": ["pdf", "docx"]})]
        c = K.find_candidates(vs)
        self.assertEqual([(x["rule"], x["a"], x["b"]) for x in c], [("K3", "R1", "R2"), ("K3", "R2", "R3")])
        self.assertEqual(c[0]["values"], {"a": "PDF", "b": "HWP"})

    def test_k4_and_k5(self):
        vs = [self.hand("R1", "The form shall be sealed.", sign_seal="seal"),
              self.hand("R2", "The form shall be signed.", sign_seal="signature")]
        self.assertEqual([x["rule"] for x in K.find_candidates(vs)], ["K4"])
        vs = [self.hand("R1", "Valid for 90 days from its date of issue.",
                        validity={"rule_type": "valid_for_from_issue", "raw": "90 days from its date of issue", "days": 90}),
              self.hand("R2", "Valid for 60 days from its date of issue.",
                        validity={"rule_type": "valid_for_from_issue", "raw": "60 days from its date of issue", "days": 60})]
        c = K.find_candidates(vs)
        self.assertEqual((c[0]["rule"], c[0]["values"]), ("K5", {"a": "90 days from its date of issue",
                                                                 "b": "60 days from its date of issue"}))

    def test_equal_values_and_unverified_rows_give_nothing(self):
        same = [self.hand("R1", "No single file may exceed 10 MB.", format={"max_size_mb": 10}),
                self.hand("R2", "Each file: at most 10 MB.", format={"max_size_mb": 10})]
        self.assertEqual(K.find_candidates(same), [])
        bad = [self.hand("R1", "No single file may exceed 10 MB.", format={"max_size_mb": 10}),
               self.hand("R2", "파일 1개의 용량은 30MB 이하로 한다.", format={"max_size_mb": 30})]
        bad[1]["check"]["grounded"] = False
        self.assertEqual(K.find_candidates(bad), [])

    def test_new_conflicts_need_verified_requirements(self):
        A, vs = a_deadlines()
        form = vreq(req("Form of Bid (Form BF-1), signed by the authorized signatory and bearing the Bidder's company seal",
                        "A_BODY", "A_BODY-L12", tmp_id="R3", subject="form of bid"), A)
        sec = vreq(req("[Signature of authorized signatory] [Company seal]", "A_ANNEX2", "A_ANNEX2-L6", tmp_id="R4",
                       subject="form of bid"), A)
        x = {"adjudications": [], "questions": [],
             "new_conflicts": [{"req_a": "R3", "req_b": "R4", "explanation": "x"},
                               {"req_a": "R3", "req_b": "R99", "explanation": "x"}]}
        res = K.apply_adjudication([], x, A, vreqs=vs + [form, sec])
        self.assertEqual([(c["id"], c["rule"], c["members"]) for c in res["conflicts"]], [("C1", "model", ["R3", "R4"])])
        self.assertEqual([e["action"] for e in res["log"]], ["added", "ignored"])
        self.assertEqual(K.apply_adjudication([], x, A)["conflicts"], [])


class Merge(unittest.TestCase):
    def test_earlier_document_keeps_the_row(self):
        A, vs = a_deadlines(order=("annex", "body"))
        other = vreq(req("Technical Proposal", "A_BODY", "A_BODY-L17", tmp_id="R3", subject="technical proposal"), A)
        conflicts = K.apply_adjudication(K.find_candidates(vs), None, A)["conflicts"]
        merged = K.merge_conflicting(vs + [other], conflicts)
        self.assertEqual([(K.tmp_id_of(v), v.get("conflict_id")) for v in merged], [("R1", "C1"), ("R3", None)])
        self.assertEqual(merged[0]["check"]["quote"], S5)
        self.assertEqual(merged[0]["merged_members"], ["R2"])
        self.assertNotIn("conflict_id", vs[1])

    def test_three_way_group_is_one_row(self):
        bundle = build_source_bundle([
            {"doc_id": "X", "title": "X", "text": "1. Bids are due by 1 December 2026, 17:00 (UTC+09:00)."},
            {"doc_id": "Y", "title": "Y", "text": "1. Bids are due by 1 December 2026, 18:00 (UTC+09:00)."},
            {"doc_id": "Z", "title": "Z", "text": "1. Bids are due by 1 December 2026, 19:00 (UTC+09:00)."}],
            bundle_id="T3", output_lang="en")
        vs = []
        for n, (doc, h) in enumerate((("Z", "19:00"), ("X", "17:00"), ("Y", "18:00")), 1):
            vs.append(vreq(req(f"Bids are due by 1 December 2026, {h} (UTC+09:00).", doc, f"{doc}-L1", tmp_id=f"R{n}",
                               category="deadline", deadline={"kind": "submission", "raw": f"1 December 2026, {h} (UTC+09:00)",
                                                              "iso_local": f"2026-12-01T{h}:00+09:00", "tz_quote": ""}), bundle))
        conflicts = K.apply_adjudication(K.find_candidates(vs), None, bundle)["conflicts"]
        self.assertEqual(len(conflicts), 3)
        merged = K.merge_conflicting(vs, conflicts)
        self.assertEqual([(K.tmp_id_of(v), v["conflict_ids"]) for v in merged], [("R2", ["C1", "C2", "C3"])])


class Wiring(unittest.TestCase):
    def test_anchors_and_conflict_info(self):
        A, vs = a_deadlines()
        conflicts = K.apply_adjudication(K.find_candidates(vs), None, A)["conflicts"]
        anchors, cid = K.deadline_anchors(vs, conflicts)
        self.assertEqual((anchors, cid), ({"Body §5": "2026-11-30T14:00:00+03:00",
                                           "Annex 1 ITB 23.1": "2026-12-02T14:00:00+03:00"}, "C1"))
        tax = vreq(req("A Tax Clearance Certificate is valid for 90 days from its date of issue (for example, a certificate "
                       "issued on 1 March is valid through 30 May).", "A_BODY", "A_BODY-L27", tmp_id="R5",
                       category="validity", validity={"rule_type": "valid_for_from_issue", "raw": "valid for 90 days from its date of issue",
                                                      "days": 90, "anchor_event": "submission_deadline"}), A)
        merged = K.merge_conflicting(vs + [tax], conflicts)
        self.assertEqual(K.conflict_info_for(merged[0], conflicts), {"conflict_id": "C1", "depends_on_conflict": None})
        self.assertEqual(K.conflict_info_for(merged[1], conflicts), {"conflict_id": None, "depends_on_conflict": "C1"})
        self.assertEqual(K.deadline_anchors(vs[:1], []), ({"Body §5": "2026-11-30T14:00:00+03:00"}, None))


if __name__ == "__main__":
    unittest.main()
