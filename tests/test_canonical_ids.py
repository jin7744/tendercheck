"""Regression for the first live run (2026-10-27): the model echoed seg ids as rendered, `[A_BODY-L12]`,
and every requirement failed Q6. Expected values are literals."""
import unittest

from engine.checks import check_requirement
from engine.normalize import canonical_id
from engine.stages import _canonicalize_ids
from test_engine_fixtures import bundle_a

BASE = {
    "tmp_id": "R1", "category": "document", "subject": "form of bid", "requirement": "Submit the Form of Bid.",
    "quote": "Form of Bid (Form BF-1), signed by the authorized signatory and bearing the Bidder's company seal",
    "location": {"doc_id": "A_BODY", "seg_id": "[A_BODY-L12]"}, "mandatory": "mandatory", "conditions": [],
    "attribute_of": "", "doc_code": "", "quantity_rule": "none",
    "member_rule": {"max_members": 0, "lead_share_min_pct": 0}, "amount": {"currency": "", "value": 0, "ratio_pct": 0},
    "deadline": {"kind": "none", "raw": "", "iso_local": "", "tz_quote": ""},
    "format": {"extensions": [], "max_size_mb": 0, "filename_template": "", "merge_single_pdf": "not_stated",
               "merge_scope": []},
    "sign_seal": "not_stated", "copy_type": "not_stated", "notarized": "not_stated",
    "validity": {"rule_type": "none", "raw": "", "months": 0, "days": 0, "until_iso": "", "anchor_event": "not_stated"},
}


class CanonicalIds(unittest.TestCase):
    def test_strips_one_wrapper_only(self):
        self.assertEqual(canonical_id("[A_BODY-L12]"), "A_BODY-L12")
        self.assertEqual(canonical_id("  A_BODY-L12 "), "A_BODY-L12")
        self.assertEqual(canonical_id('"M3"'), "M3")
        self.assertEqual(canonical_id("[[A_BODY-L12]]"), "[A_BODY-L12]")
        self.assertEqual(canonical_id("A_BODY-L12]"), "A_BODY-L12]")

    def test_bracketed_seg_id_passes_q6(self):
        r = check_requirement(BASE, bundle_a())
        self.assertEqual(r["checks"]["Q6"]["result"], "pass", r["checks"]["Q6"])
        self.assertEqual(r["seg_id"], "A_BODY-L12")

    def test_unknown_id_still_fails_after_stripping(self):
        req = {**BASE, "location": {"doc_id": "A_BODY", "seg_id": "[A_BODY-L999]"}}
        self.assertEqual(check_requirement(req, bundle_a())["checks"]["Q6"]["result"], "fail")

    def test_parsed_reply_rewritten_in_place(self):
        reply = {"requirements": [{"tmp_id": " R1 ", "location": {"doc_id": "A_BODY", "seg_id": "[A_BODY-L12]"},
                                   "attribute_of": "", "format": {"merge_scope": ["[R2]"]}}],
                 "matches": [{"req_id": "R1", "row_ids": ["[M1]", "M2"]}]}
        self.assertEqual(_canonicalize_ids(reply), 4)
        self.assertEqual(reply["requirements"][0]["location"]["seg_id"], "A_BODY-L12")
        self.assertEqual(reply["requirements"][0]["tmp_id"], "R1")
        self.assertEqual(reply["requirements"][0]["format"]["merge_scope"], ["R2"])
        self.assertEqual(reply["matches"][0]["row_ids"], ["M1", "M2"])


if __name__ == "__main__":
    unittest.main()
