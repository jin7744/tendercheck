"""Second live K=3 (2026-10-28): all six remaining mismatches were one shape. The model split a document's own list
entry ("④ 법인등기부등본(…): 입찰서 제출 마감일 기준 최근 3개월 이내 발급분", "⑩ 입찰보증보험증권: 보증금액은 …,
보증기간은 …") into the document record plus attribute records on the same line. The attribute held the right status,
the document read met. req_rules keeps a property written inside the list entry in the document's own record, so code
folds such attributes back. Expected values are literals."""
import copy
import unittest

from engine.checks import check_requirements
from engine.normalize import segment_map
from engine.pipeline import rejoin_list_entry_attributes
from test_canonical_ids import BASE
from test_engine_fixtures import bundle_b

V3M = {"rule_type": "issued_within_before_anchor", "raw": "입찰서 제출 마감일 기준 최근 3개월 이내 발급분",
       "months": 3, "days": 0, "until_iso": "", "anchor_event": "submission_deadline"}
BOND = "입찰보증보험증권: 보증금액은 입찰금액의 100분의 5 이상, 보증기간은 입찰서 제출 마감일부터 30일 이상으로 한다."


def rec(tmp, quote, seg, **fields):
    r = copy.deepcopy(BASE)
    r.update({"tmp_id": tmp, "quote": quote, "subject": "x", "location": {"doc_id": "B_NOTICE", "seg_id": seg}})
    r.update(fields)
    return r


def vreqs(reqs, bundle):
    res = check_requirements(copy.deepcopy(reqs), bundle)
    return [{"req": r, "check": c} for r, c in zip(reqs, res["results"])]


class ListEntryRejoin(unittest.TestCase):
    def setUp(self):
        self.bundle = bundle_b()
        self.segs = segment_map(self.bundle)

    def run_(self, reqs):
        return rejoin_list_entry_attributes(vreqs(reqs, self.bundle), self.segs)

    def test_validity_after_the_title_joins_the_document(self):
        doc = rec("R6", "법인등기부등본(법인등기사항전부증명서)", "B_NOTICE-L19")
        attr = rec("R7", "입찰서 제출 마감일 기준 최근 3개월 이내 발급분", "B_NOTICE-L19", category="validity",
                   attribute_of="R6", validity=V3M)
        out, joins = self.run_([doc, attr])
        self.assertEqual(joins, [{"kept": "R6", "absorbed": "R7", "seg_id": "B_NOTICE-L19"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["quote"], "법인등기부등본(법인등기사항전부증명서): 입찰서 제출 마감일 기준 최근 3개월 이내 발급분")
        self.assertEqual((out[0]["category"], out[0]["validity"]["months"]), ("document", 3))
        chk = check_requirements(out, self.bundle)["results"][0]
        self.assertTrue(chk["verified"] and chk["grounded"], chk["failures"])

    def test_bond_rate_and_period_fold_into_the_bond(self):
        period = {"rule_type": "valid_at_least_after_anchor", "raw": "입찰서 제출 마감일부터 30일 이상", "months": 0,
                  "days": 30, "until_iso": "", "anchor_event": "submission_deadline"}
        doc = rec("R12", BOND, "B_NOTICE-L25")
        rate = rec("R13", "보증금액은 입찰금액의 100분의 5 이상", "B_NOTICE-L25", attribute_of="R12",
                   amount={"currency": "", "value": 0, "ratio_pct": 5})
        per = rec("R14", "보증기간은 입찰서 제출 마감일부터 30일 이상으로 한다.", "B_NOTICE-L25", category="validity",
                  attribute_of="R12", validity=period)
        out, joins = self.run_([doc, rate, per])
        self.assertEqual([j["absorbed"] for j in joins], ["R13", "R14"])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["quote"], out[0]["amount"]["ratio_pct"], out[0]["validity"]["days"]), (BOND, 5, 30))

    def test_left_alone(self):
        doc = rec("R6", "법인등기부등본(법인등기사항전부증명서)", "B_NOTICE-L19")
        other_line = rec("R7", "입찰서 제출 마감일 기준 최근 3개월 이내 발급분", "B_NOTICE-L20", category="validity",
                         attribute_of="R6", validity=V3M)
        clash_doc = rec("R8", "법인등기부등본(법인등기사항전부증명서)", "B_NOTICE-L19",
                        validity={**V3M, "months": 6, "raw": "법인등기부등본"})
        clash = rec("R9", "입찰서 제출 마감일 기준 최근 3개월 이내 발급분", "B_NOTICE-L19", category="validity",
                    attribute_of="R8", validity=V3M)
        conditional = rec("R10", "입찰서 제출 마감일 기준 최근 3개월 이내 발급분", "B_NOTICE-L19", category="validity",
                          attribute_of="R6", validity=V3M, mandatory="conditional",
                          conditions=[{"condition_quote": "입찰서", "fact_key": "other", "applies_when": "yes"}])
        for reqs in ([doc, other_line], [clash_doc, clash], [doc, conditional]):
            with self.subTest(child=reqs[1]["tmp_id"]):
                out, joins = self.run_(reqs)
                self.assertEqual((joins, [r["tmp_id"] for r in out]), ([], [r["tmp_id"] for r in reqs]))

    def test_records_pointing_at_an_absorbed_attribute_follow_it(self):
        doc = rec("R6", "법인등기부등본(법인등기사항전부증명서)", "B_NOTICE-L19")
        attr = rec("R7", "입찰서 제출 마감일 기준 최근 3개월 이내 발급분", "B_NOTICE-L19", category="validity",
                   attribute_of="R6", validity=V3M)
        grand = rec("R8", "법인인감증명서", "B_NOTICE-L20", attribute_of="R7")
        out, _ = self.run_([doc, attr, grand])
        self.assertEqual([(r["tmp_id"], r["attribute_of"]) for r in out], [("R6", ""), ("R8", "R6")])


if __name__ == "__main__":
    unittest.main()
