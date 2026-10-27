"""Regression for the second live run (2026-10-27): the model split one validity obligation into a period
record and an anchor record on the same source line, so the anchor record had no period and became
needs-confirmation. Expected values are literals."""
import copy
import unittest

from engine.checks import check_requirements
from engine.normalize import segment_map
from engine.pipeline import merge_split_validity
from test_canonical_ids import BASE
from test_engine_fixtures import bundle_a

PERIOD = {**BASE, "tmp_id": "R14", "category": "validity", "subject": "tax clearance certificate",
          "quote": "A Tax Clearance Certificate is valid for 90 days from its date of issue",
          "location": {"doc_id": "A_BODY", "seg_id": "A_BODY-L27"}, "attribute_of": "",
          "validity": {"rule_type": "valid_for_from_issue", "raw": "valid for 90 days from its date of issue",
                       "months": 0, "days": 90, "until_iso": "", "anchor_event": "not_stated"}}
ANCHOR = {**BASE, "tmp_id": "R15", "category": "validity", "subject": "tax clearance certificate",
          "quote": "The certificate must be valid on the deadline for submission of Bids",
          "location": {"doc_id": "A_BODY", "seg_id": "A_BODY-L27"}, "attribute_of": "",
          "validity": {"rule_type": "valid_at_least_after_anchor",
                       "raw": "must be valid on the deadline for submission of Bids",
                       "months": 0, "days": 0, "until_iso": "", "anchor_event": "submission_deadline"}}
EXPECTED_QUOTE = ("A Tax Clearance Certificate is valid for 90 days from its date of issue (for example, a "
                  "certificate issued on 1 March is valid through 30 May). The certificate must be valid on the "
                  "deadline for submission of Bids")


def vreqs(reqs, bundle):
    res = check_requirements(copy.deepcopy(reqs), bundle)
    return [{"req": r, "check": c} for r, c in zip(reqs, res["results"])]


class SplitValidity(unittest.TestCase):
    def setUp(self):
        self.bundle = bundle_a()
        self.segs = segment_map(self.bundle)

    def test_period_and_anchor_on_one_line_are_joined(self):
        out, merges = merge_split_validity(vreqs([PERIOD, ANCHOR], self.bundle), self.segs)
        self.assertEqual(merges, [{"kept": "R14", "absorbed": "R15", "seg_id": "A_BODY-L27"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["quote"], EXPECTED_QUOTE)
        self.assertEqual(out[0]["validity"]["days"], 90)
        self.assertEqual(out[0]["validity"]["anchor_event"], "submission_deadline")
        check = check_requirements(out, self.bundle)["results"][0]
        self.assertTrue(check["verified"] and check["grounded"], check["failures"])

    def test_different_lines_are_not_joined(self):
        other = {**ANCHOR, "quote": "The Bid Security shall remain valid until 28 April 2027",
                 "location": {"doc_id": "A_BODY", "seg_id": "A_BODY-L19"}}
        out, merges = merge_split_validity(vreqs([PERIOD, other], self.bundle), self.segs)
        self.assertEqual(merges, [])
        self.assertEqual(len(out), 2)

    def test_two_periods_are_not_joined(self):
        twin = {**PERIOD, "tmp_id": "R15", "quote": ANCHOR["quote"]}
        out, merges = merge_split_validity(vreqs([PERIOD, twin], self.bundle), self.segs)
        self.assertEqual(merges, [])


if __name__ == "__main__":
    unittest.main()
