"""Contract invariants. Expected literals are copied from spec 08 §1.3 / design 09 §4-7, not recomputed."""
import json
import unittest
from pathlib import Path

C = Path(__file__).resolve().parent.parent / "contracts"
MODEL_FILES = sorted(C.glob("model_*.schema.json"))


def load(name):
    return json.loads((C / name).read_text(encoding="utf-8"))


def walk(node, depth=0):
    yield node, depth
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v, depth + 1)


def objects(schema):
    return [n for n, _ in walk(schema) if isinstance(n, dict) and n.get("type") == "object"]


def object_depth(schema, depth=0):
    if not isinstance(schema, dict):
        return depth
    if schema.get("type") == "object":
        depth += 1
        return max([depth] + [object_depth(v, depth) for v in schema.get("properties", {}).values()])
    if schema.get("type") == "array":
        return object_depth(schema.get("items", {}), depth)
    return depth


class ModelSchemas(unittest.TestCase):
    def test_model_schemas_exist(self):
        # seven text stages plus x_pages for the optional page-reading stage V (added 2026-11-02)
        self.assertEqual(
            [p.name for p in MODEL_FILES],
            ["model_x_ask.schema.json", "model_x_conflict.schema.json", "model_x_coverage.schema.json",
             "model_x_extract.schema.json", "model_x_fix.schema.json", "model_x_match.schema.json",
             "model_x_pages.schema.json", "model_x_review.schema.json"],
        )

    def test_strict_objects(self):
        for p in MODEL_FILES:
            for o in objects(load(p.name)["schema"]):
                self.assertIs(o.get("additionalProperties"), False, p.name)
                self.assertEqual(sorted(o["required"]), sorted(o["properties"]), p.name)

    def test_no_null_in_model_schemas(self):
        for p in MODEL_FILES:
            text = p.read_text(encoding="utf-8")
            self.assertNotIn('"null"', text, p.name)
            self.assertNotIn("null", json.dumps(load(p.name)["schema"]), p.name)

    def test_model_cannot_emit_status(self):
        # I8: only the two-value status_hint exists; no final status field anywhere in model outputs
        for p in MODEL_FILES:
            for o in objects(load(p.name)["schema"]):
                self.assertNotIn("status", o["properties"], p.name)
        hint = load("model_x_match.schema.json")["schema"]["properties"]["matches"]["items"]["properties"]["status_hint"]
        self.assertEqual(hint["enum"], ["no_concern", "needs-confirmation"])

    def test_nesting_within_limit(self):
        for p in MODEL_FILES:
            self.assertLessEqual(object_depth(load(p.name)["schema"]), 10, p.name)


class GradingContract(unittest.TestCase):
    def setUp(self):
        self.s = load("report_grading.schema.json")["schema"]

    def test_top_level_fields_from_08(self):
        self.assertEqual(
            self.s["required"],
            ["run_id", "basis", "requirements", "conflicts", "bidder_questions", "untrusted_instructions",
             "not_in_source", "status_counts", "disclaimer"],
        )

    def test_requirement_fields_and_enums(self):
        r = self.s["properties"]["requirements"]["items"]
        self.assertEqual(
            r["required"],
            ["id", "kind", "quote", "location", "status", "condition_state", "evidence_files", "depends_on",
             "depends_on_conflict", "reason", "bidder_action"],
        )
        self.assertEqual(r["properties"]["kind"]["enum"],
                         ["deadline", "document", "attribute", "validity", "file_rule", "conditional"])
        self.assertEqual(r["properties"]["status"]["enum"],
                         ["met", "missing", "error", "conditional", "needs-confirmation"])
        self.assertEqual(r["properties"]["condition_state"]["enum"], ["unknown", "not_triggered", "triggered", None])

    def test_status_counts_keys(self):
        self.assertEqual(self.s["properties"]["status_counts"]["required"],
                         ["met", "missing", "error", "conditional", "needs-confirmation"])


class FixedText(unittest.TestCase):
    def test_literals_match_spec_08(self):
        t = load("fixed_text.json")["fixed_text"]
        self.assertEqual(t["basis"]["en"], "Based only on the documents provided.")
        self.assertEqual(t["basis"]["ko"], "제공된 자료 기준입니다.")
        self.assertEqual(
            t["disclaimer"]["en"],
            "This is a preparation aid, not an eligibility or compliance determination. The issuing authority's "
            "official documents prevail. A person must make the final decision.",
        )
        self.assertEqual(
            t["disclaimer"]["ko"],
            "이 결과는 제공된 텍스트를 대조한 준비 보조자료이며 적격·준수 여부 판정이 아닙니다. 공식 공고 원문과 "
            "발주기관 안내가 우선하며, 최종 판단은 사람이 합니다.",
        )

    def test_t04_and_t05_phrases_present(self):
        t = load("fixed_text.json")["fixed_text"]
        self.assertIn("not found in the provided documents", t["not_in_source"]["en"])
        self.assertIn("제공된 원문에 없", t["not_in_source"]["ko"])
        self.assertIn("민감정보", t["pii"]["ko"])
        self.assertTrue("삭제" in t["pii"]["ko"] or "마스킹" in t["pii"]["ko"])


if __name__ == "__main__":
    unittest.main()
