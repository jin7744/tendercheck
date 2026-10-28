"""Live K=3 (2026-10-28): the model extracted consortium rules (member count, lead share) as unconditional
documents in 9 of 18 runs, so a single bidder saw "met" or "value not in the list" instead of "applies only to a
joint venture". A member rule binds only a consortium, so code adds the consortium condition when the model left
it out, quoting the consortium words from the record's own quote. Expected values are literals."""
import unittest

from engine.pipeline import add_member_rule_conditions

BASE = {"tmp_id": "R2", "category": "document", "subject": "joint venture composition", "mandatory": "mandatory",
        "conditions": [], "attribute_of": "", "member_rule": {"max_members": 0, "lead_share_min_pct": 0},
        "location": {"doc_id": "A_BODY", "seg_id": "A_BODY-L9"}}
OK = {"verified": True, "grounded": True}


def vreq(quote, **member_rule):
    req = {**BASE, "quote": quote, "member_rule": {**BASE["member_rule"], **member_rule}}
    return {"req": req, "check": {**OK, "quote": quote}}


class MemberRuleCondition(unittest.TestCase):
    def test_jv_size_rule_becomes_conditional_on_consortium(self):
        q = "Bids may be submitted by a single firm or by a joint venture (JV) of not more than three (3) firms."
        out, log = add_member_rule_conditions([vreq(q, max_members=3)])
        self.assertEqual(out[0]["mandatory"], "conditional")
        self.assertEqual(out[0]["conditions"],
                         [{"condition_quote": "joint venture", "fact_key": "consortium", "applies_when": "yes"}])
        self.assertEqual(log, [{"tmp_id": "R2", "condition_quote": "joint venture"}])

    def test_korean_lead_share_rule(self):
        q = "대표사의 지분율은 51% 이상이어야 한다."
        out, _ = add_member_rule_conditions([vreq(q, lead_share_min_pct=51)])
        self.assertEqual(out[0]["conditions"][0]["condition_quote"], "대표사")
        self.assertEqual(out[0]["mandatory"], "conditional")

    def test_korean_member_count_rule_prefers_the_consortium_word(self):
        q = "공동수급(공동이행방식)을 허용하며, 구성원은 대표사를 포함하여 2개사 이하로 한다."
        out, _ = add_member_rule_conditions([vreq(q, max_members=2)])
        self.assertEqual(out[0]["conditions"][0]["condition_quote"], "공동수급")

    def test_condition_quote_keeps_source_case(self):
        q = "A Consortium shall have no more than four members."
        out, _ = add_member_rule_conditions([vreq(q, max_members=4)])
        self.assertEqual(out[0]["conditions"][0]["condition_quote"], "Consortium")

    def test_left_alone(self):
        jv = "by a joint venture (JV) of not more than three (3) firms"
        cases = [
            vreq(jv),                                                  # no member rule
            vreq("Bids may be submitted by not more than three firms.", max_members=3),  # no consortium word
        ]
        given = vreq(jv, max_members=3)
        given["req"]["mandatory"] = "conditional"
        given["req"]["conditions"] = [{"condition_quote": "by a joint venture (JV)", "fact_key": "consortium",
                                       "applies_when": "yes"}]
        unverified = vreq(jv, max_members=3)
        unverified["check"]["verified"] = False
        for v in cases + [given, unverified]:
            before = dict(v["req"])
            out, log = add_member_rule_conditions([v])
            self.assertEqual((out[0], log), (before, []))


if __name__ == "__main__":
    unittest.main()
