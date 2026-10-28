"""Live K=3 (2026-10-28) B-M2 k3: the qualification documents were three separate files, but the match stage gave
the merge rule no rows ("no merged file found"), so the rule read "value not in the list" instead of error. A merge
rule is about the files of the documents it names. Expected values are literals."""
import unittest

from engine.pipeline import merge_rule_rows


def v(tmp, category, scope=()):
    return {"req": {"tmp_id": tmp, "category": category, "format": {"merge_scope": list(scope)}},
            "check": {"tmp_id": tmp}}


ENTRY = {"status_hint": "no_concern", "hint_reason": "", "rationale": ""}


class MergeRuleRows(unittest.TestCase):
    def test_empty_merge_rule_takes_the_scope_rows(self):
        by_req = {"R3": {**ENTRY, "row_ids": ["M2"]}, "R4": {**ENTRY, "row_ids": ["M3"]},
                  "R5": {**ENTRY, "row_ids": ["M4", "M3"]}, "R6": {**ENTRY, "row_ids": []},
                  "R19": {**ENTRY, "row_ids": [], "rationale": "no merged file"}}
        out = merge_rule_rows([v("R19", "merge_rule", ["R3", "R4", "R5", "R6"])], by_req)
        self.assertEqual(out["R19"]["row_ids"], ["M2", "M3", "M4"])
        self.assertEqual(out["R19"]["rationale"], "no merged file")
        self.assertEqual(by_req["R19"]["row_ids"], [])

    def test_left_alone(self):
        by_req = {"R3": {**ENTRY, "row_ids": ["M2"]}, "R19": {**ENTRY, "row_ids": ["M9"]}}
        cases = [v("R19", "merge_rule", ["R3"]),          # the match already gave rows
                 v("R20", "merge_rule", []),              # no scope
                 v("R21", "merge_rule", ["R8"]),          # scope documents have no rows
                 v("R22", "document", ["R3"])]            # not a merge rule
        self.assertEqual(merge_rule_rows(cases, by_req), by_req)


if __name__ == "__main__":
    unittest.main()
