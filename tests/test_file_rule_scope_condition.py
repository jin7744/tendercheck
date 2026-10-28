"""Cards T01-T07 second live pass (2026-10-28): in 5 of 60 runs the model marked the B merge rule mandatory and
still attached "공동수급인 경우 ⑫ 포함" as its condition. That words which documents join the merge (merge_scope),
not whether the rule applies, yet code read it as an applicability condition: a bidder with unknown JV status got
"conditional / unknown", and a single firm would have been told the rule is not needed (fail-open). A file rule
applies to whatever is submitted, so code drops such a condition when the model itself says mandatory. Records the
model marks conditional, and every non-file rule, keep their conditions. Expected values are literals."""
import unittest

from engine import rules
from engine.pipeline import drop_file_rule_scope_conditions

Q = "입찰참가자격 확인서류(공고문 3.의 ②~⑤, 공동수급인 경우 ⑫ 포함)는 하나의 PDF 파일로 병합하여 제출한다."
JV = [{"condition_quote": "공동수급인 경우 ⑫ 포함", "fact_key": "consortium", "applies_when": "yes"}]
OK = {"verified": True, "grounded": True, "remove": False, "reason_codes": [], "info_codes": []}


def vreq(category="merge_rule", mandatory="mandatory", conditions=JV, verified=True):
    req = {"tmp_id": "R21", "category": category, "subject": "qualification documents merge", "mandatory": mandatory,
           "conditions": [dict(c) for c in conditions], "attribute_of": "", "quote": Q,
           "deadline": {"kind": "none"}, "format": {"merge_single_pdf": "required", "merge_scope": ["R2", "R3"]}}
    return {"req": req, "check": {**OK, "tmp_id": "R21", "quote": Q, "verified": verified}}


def decide(req, consortium):
    profile = {"facts": {"consortium": consortium}, "planned_upload": ""}
    fc = {"checks": [{"id": "F4", "result": "pass", "detail": ""}], "quantity_short": False, "unknown_fields": [],
          "reason_codes": [], "readings": {}}
    match = {"by_req": {"R21": {"row_ids": ["M2", "M3"], "status_hint": "no_concern"}}}
    return rules.decide({"req": req, "check": {**OK, "tmp_id": "R21", "quote": Q}}, file_check=fc, match=match,
                        profile=profile, as_of="2026-12-01T09:00:00+09:00")


class FileRuleScopeCondition(unittest.TestCase):
    def test_merge_rule_condition_dropped(self):
        out, log = drop_file_rule_scope_conditions([vreq()])
        self.assertEqual((out[0]["conditions"], out[0]["mandatory"]), ([], "mandatory"))
        self.assertEqual(log, [{"tmp_id": "R21", "condition_quote": "공동수급인 경우 ⑫ 포함"}])

    def test_single_firm_and_unknown_status_get_the_rule_checked(self):
        out, _ = drop_file_rule_scope_conditions([vreq()])
        for consortium in ("no", "unknown", "yes"):
            with self.subTest(consortium=consortium):
                d = decide(out[0], consortium)
                self.assertEqual((d["rule"], d["status"], d["condition_state"]), ("S15", "met", None))

    def test_before_the_fix_a_single_firm_was_told_not_needed(self):
        d = decide(vreq()["req"], "no")
        self.assertEqual((d["rule"], d["status"], d["condition_state"]), ("S6", "conditional", "not_triggered"))

    def test_left_alone(self):
        cases = [vreq(mandatory="conditional"),            # the model says the rule itself is conditional
                 vreq(category="document"),                # documents keep conditions (08 T02)
                 vreq(category="signature_seal"),
                 vreq(conditions=[]),
                 vreq(verified=False)]
        for v in cases:
            with self.subTest(category=v["req"]["category"], mandatory=v["req"]["mandatory"]):
                before = dict(v["req"])
                out, log = drop_file_rule_scope_conditions([v])
                self.assertEqual((out[0], log), (before, []))


if __name__ == "__main__":
    unittest.main()
