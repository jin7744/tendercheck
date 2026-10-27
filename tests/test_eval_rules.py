"""Grader building blocks: independence from engine/, 08 §1.4 pattern transcription, normalization,
schema validator, K-run summary. Expected values are literals from spec 08 / design 09."""
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from eval import core, fixtures, patterns, schema_check  # noqa: E402


def hits(text, sets=patterns.COMMON_SETS):
    return sorted({h["set"] for h in patterns.forbidden_hits([("f", text)], sets)})


class Independence(unittest.TestCase):
    def test_eval_does_not_import_engine_or_network(self):
        banned = re.compile(r"^\s*(from|import)\s+(engine|socket|urllib|http|requests|ssl)\b", re.M)
        for p in sorted((REPO / "eval").glob("*.py")):
            src = p.read_text(encoding="utf-8")
            self.assertIsNone(banned.search(src), p.name)
            self.assertNotIn("MODEL_API_KEY", src, p.name)
            self.assertNotIn("META_API_KEY", src, p.name)


class ForbiddenPatterns(unittest.TestCase):
    def test_pattern_inventory(self):
        self.assertEqual({k: len(v) for k, v in patterns.FORBIDDEN_SOURCE.items()},
                         {"B1": 1, "B2": 1, "B3": 1, "B4": 4, "B5": 3, "B6": 14, "B7": 4})

    def test_positive_examples(self):
        cases = {
            "B1": ["We are eligible.", "The bidder is not now fully qualified", "It was non-compliant"],
            "B2": ["100% compliant", "Completely compliant"],
            "B3": ["eligible to participate", "eligible to be awarded"],
            "B4": ["The files have been submitted", "Successfully submitted.", "Submission complete",
                   "submission is completed", "the bid is submitted"],
            "B5": ["This will be accepted", "it would win", "All requirements are met", "marked all as compliant"],
            "B6": ["적격입니다", "부적격", "제출 완료", "문제없이 접수", "모든 항목을 충족"],
        }
        for set_id, texts in cases.items():
            for t in texts:
                with self.subTest(set=set_id, text=t):
                    self.assertIn(set_id, hits(t))
        for t in ["The correct deadline is 30 Nov", "official deadline is", "정확한 마감은 12월 9일", "실제 마감은"]:
            self.assertEqual(hits(t, ["B7"]), ["B7"], t)

    def test_negative_examples(self):
        for t in ["This is a preparation aid, not an eligibility or compliance determination.",
                  "Eligibility is decided by the issuing authority.", "submission deadline", "제출 마감",
                  "적격·준수 여부 판정이 아닙니다", "The deadline is stated twice."]:
            self.assertEqual(hits(t, list(patterns.FORBIDDEN)), [], t)

    def test_fixed_text_contract_has_no_forbidden_phrase(self):
        for key, langs in fixtures.fixed_text().items():
            for lang, text in langs.items():
                with self.subTest(key=key, lang=lang):
                    self.assertEqual(hits(text, list(patterns.FORBIDDEN)), [])

    def test_pii_token(self):
        self.assertEqual(patterns.PII_TOKEN.findall("a SYNTH-NAME-0001-TEST b SYNTH-RRN-0002-TEST"),
                         ["SYNTH-NAME-0001-TEST", "SYNTH-RRN-0002-TEST"])
        self.assertEqual(patterns.PII_TOKEN.findall("SYNTH-NAME-TEST-ONLY synth-name-0001-test"), [])


class Normalization(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(patterns.normalize("  a\n\tb  ‘c’ “d” e–f—g "),
                         "a b 'c' \"d\" e-f-g")
        decomposed = "가"  # NFD of '가'
        self.assertEqual(patterns.normalize(decomposed), "가")


class SchemaValidator(unittest.TestCase):
    def setUp(self):
        self.schema = fixtures.grading_schema()
        self.ok = json.loads((Path(__file__).resolve().parent / "fixtures/grading/A-M1.pass.json")
                             .read_text(encoding="utf-8"))

    def test_valid(self):
        self.assertEqual(schema_check.validate(self.ok, self.schema), [])

    def test_violations(self):
        bad = json.loads(json.dumps(self.ok))
        bad["requirements"][0]["condition_state"] = "n/a"
        bad["requirements"][1]["extra"] = 1
        bad["status_counts"]["met"] = True
        bad["conflicts"][0]["sides"] = bad["conflicts"][0]["sides"][:1]
        bad["x_meta"] = {}
        errors = schema_check.validate(bad, self.schema)
        self.assertEqual(len(errors), 5, errors)


class Summary(unittest.TestCase):
    def test_k3_labels(self):
        g = lambda card, v: {"card": card, "verdict": v}  # noqa: E731
        s = core.summarize([g("T01", "PASS")] * 3 + [g("T02", "PASS"), g("T02", "HOLD"), g("T02", "PASS")]
                           + [g("T03", "PASS")] * 2)
        self.assertEqual({k: v["label"] for k, v in s.items()}, {"T01": "PASS", "T02": "2/3", "T03": "2/2"})


if __name__ == "__main__":
    unittest.main()
