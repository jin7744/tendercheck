"""In-code JSON-schema validator, contract loading and reply post-processing (engine/model_client.py).

Expected error strings are literals written by hand from the validator's documented path format, not
produced by calling the validator.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import model_client as mc  # noqa: E402

CONTRACT_NAMES = ["x_ask", "x_conflict", "x_coverage", "x_extract", "x_fix", "x_match", "x_review"]

# One complete X-Extract requirement, written by hand against contracts/model_x_extract.schema.json.
REQ = {
    "tmp_id": "r1",
    "category": "document",
    "subject": "Form of Bid",
    "requirement": "Submit the Form of Bid signed by an authorized representative.",
    "quote": "Form of Bid, signed by the authorized representative",
    "location": {"doc_id": "A_BODY", "seg_id": "A_BODY-L12"},
    "mandatory": "mandatory",
    "conditions": [],
    "attribute_of": "",
    "doc_code": "BF-1",
    "quantity_rule": "none",
    "member_rule": {"max_members": 0, "lead_share_min_pct": 0},
    "amount": {"currency": "", "value": 0, "ratio_pct": 0},
    "deadline": {"kind": "none", "raw": "", "iso_local": "", "tz_quote": ""},
    "format": {"extensions": ["pdf"], "max_size_mb": 10.0, "filename_template": "", "merge_single_pdf": "not_stated",
               "merge_scope": []},
    "sign_seal": "signature",
    "copy_type": "not_stated",
    "notarized": "not_stated",
    "validity": {"rule_type": "none", "raw": "", "months": 0, "days": 0, "until_iso": "", "anchor_event": "not_stated"},
}

MINIMAL_VALID = {
    "x_ask": {"asked_items": [{"item": "ISO 27001 certificate", "search_terms": ["ISO 27001", "ISO/IEC 27001"]}]},
    "x_conflict": {"adjudications": [{"candidate_id": "K1", "same_obligation": "unclear", "distinguishing_quote_a": "",
                                      "distinguishing_quote_b": "", "explanation": "Both describe the bid deadline."}],
                   "new_conflicts": [], "questions": [{"for_id": "K1", "question": "Is the deadline 30 November?",
                                                        "answer_type": "yes_no", "options": ["Yes", "No"]}]},
    "x_coverage": {"new_requirements": [], "skips": [{"seg_id": "A_BODY-L3", "reason": "background"}]},
    "x_extract": {"requirements": [REQ], "uncertain_segments": [], "not_requirements": [], "injection_suspects": []},
    "x_fix": {"fixes": [{"tmp_id": "r1", "action": "retract", "quote": "", "location": {"doc_id": "", "seg_id": ""},
                         "note": "not in source"}]},
    "x_match": {"matches": [{"req_id": "R01", "row_ids": ["row-1"], "rationale": "QDS_BF1.pdf is the Form of Bid.",
                             "status_hint": "no_concern", "hint_reason": ""}],
                "attribute_findings": [], "unmatched_rows": []},
    "x_review": {"flags": []},
}


def schema(name):
    return mc.load_schema(name).schema


class ContractLoading(unittest.TestCase):
    def test_all_seven_contracts_load_and_use_only_supported_keywords(self):
        for name in CONTRACT_NAMES:
            doc = mc.load_schema(name)
            self.assertEqual(doc.name, name)
            self.assertEqual(doc.version, "1.0.0")
            self.assertEqual(doc.schema["type"], "object")
            self.assertRegex(doc.sha256, r"^[0-9a-f]{64}$")

    def test_hand_written_instance_of_each_contract_is_valid(self):
        for name in CONTRACT_NAMES:
            self.assertEqual(mc.validate(MINIMAL_VALID[name], schema(name)), [], name)

    def test_bad_schema_names_are_rejected(self):
        for bad in ["../contracts/x_ask", "X_ASK", "", "x ask", "nope"]:
            with self.assertRaises(mc.SchemaError, msg=bad):
                mc.load_schema(bad)

    def test_unsupported_keywords_fail_closed(self):
        with self.assertRaises(mc.SchemaError):
            mc.check_schema_supported({"type": "string", "pattern": "^a"})
        with self.assertRaises(mc.SchemaError):
            mc.check_schema_supported({"type": "array", "items": [{"type": "string"}]})
        with self.assertRaises(mc.SchemaError):
            mc.check_schema_supported({"type": "date"})
        with self.assertRaises(mc.SchemaError):
            mc.check_schema_supported({"type": "object", "properties": {"a": {"type": "string", "format": "email"}}})
        mc.check_schema_supported({"type": ["string", "null"], "description": "ok", "minItems": 0})


class Validator(unittest.TestCase):
    def test_missing_required(self):
        inst = {"asked_items": [{"item": "x"}]}
        self.assertEqual(mc.validate(inst, schema("x_ask")),
                         ["$.asked_items[0]: missing required property 'search_terms'"])

    def test_additional_property(self):
        self.assertEqual(mc.validate({"asked_items": [], "extra": 1}, schema("x_ask")),
                         ['$: additional property "extra" not allowed'])

    def test_wrong_type_stops_descent(self):
        self.assertEqual(mc.validate({"asked_items": "none"}, schema("x_ask")),
                         ["$.asked_items: expected array, got string"])

    def test_enum_violation_model_cannot_emit_final_status(self):
        inst = copy.deepcopy(MINIMAL_VALID["x_match"])
        inst["matches"][0]["status_hint"] = "met"
        self.assertEqual(mc.validate(inst, schema("x_match")),
                         ['$.matches[0].status_hint: value "met" not in enum'])

    def test_nested_enum_path(self):
        inst = copy.deepcopy(MINIMAL_VALID["x_extract"])
        inst["requirements"][0]["deadline"]["kind"] = "due"
        self.assertEqual(mc.validate(inst, schema("x_extract")),
                         ['$.requirements[0].deadline.kind: value "due" not in enum'])

    def test_integer_rules(self):
        s = schema("x_extract")
        for value, ok in [(3, True), (3.0, True), (3.5, False), (True, False), ("3", False), (None, False)]:
            inst = copy.deepcopy(MINIMAL_VALID["x_extract"])
            inst["requirements"][0]["member_rule"]["max_members"] = value
            errors = mc.validate(inst, s)
            self.assertEqual(errors == [], ok, (value, errors))
        inst = copy.deepcopy(MINIMAL_VALID["x_extract"])
        inst["requirements"][0]["member_rule"]["max_members"] = True
        self.assertEqual(mc.validate(inst, s), ["$.requirements[0].member_rule.max_members: expected integer, got boolean"])

    def test_number_rules(self):
        s = {"type": "number"}
        self.assertEqual(mc.validate(10, s), [])
        self.assertEqual(mc.validate(10.5, s), [])
        self.assertEqual(mc.validate(False, s), ["$: expected number, got boolean"])
        self.assertEqual(mc.validate(float("nan"), s), ["$: expected number, got non-finite number"])

    def test_min_max_items(self):
        s = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2}
        self.assertEqual(mc.validate(["a"], s), [])
        self.assertEqual(mc.validate([], s), ["$: 0 items, fewer than minItems 1"])
        self.assertEqual(mc.validate(["a", "b", "c"], s), ["$: 3 items, more than maxItems 2"])
        self.assertEqual(mc.validate(["a", 1], s), ["$[1]: expected string, got integer"])

    def test_type_list_with_null(self):
        s = {"type": ["string", "null"]}
        self.assertEqual(mc.validate(None, s), [])
        self.assertEqual(mc.validate("x", s), [])
        self.assertEqual(mc.validate(1, s), ["$: expected string|null, got integer"])

    def test_enum_does_not_confuse_bool_and_int(self):
        self.assertEqual(mc.validate(True, {"enum": [1]}), ["$: value true not in enum"])
        self.assertEqual(mc.validate(1, {"enum": [True]}), ["$: value 1 not in enum"])
        self.assertEqual(mc.validate(1.0, {"enum": [1]}), [])


class StrictJson(unittest.TestCase):
    def test_rejects_nan_infinity_and_duplicate_keys(self):
        for text in ['{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1, "a": 2}']:
            with self.assertRaises(ValueError, msg=text):
                mc.parse_json_strict(text)
        self.assertEqual(mc.parse_json_strict('{"a": [1, 2.5, "가"]}'), {"a": [1, 2.5, "가"]})


def chat(content, finish="stop", refusal=None):
    message = {"role": "assistant", "content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return {"model": "m", "choices": [{"index": 0, "message": message, "finish_reason": finish}]}


class Postprocess(unittest.TestCase):
    def setUp(self):
        self.s = schema("x_review")

    def test_valid(self):
        self.assertEqual(mc.postprocess(chat('{"flags": []}'), self.s), ({"flags": []}, None))

    def test_code_fence_only_tolerated_when_lenient(self):
        fenced = '```json\n{"flags": []}\n```'
        parsed, err = mc.postprocess(chat(fenced), self.s)
        self.assertIsNone(parsed)
        self.assertTrue(err.startswith("json_parse_error: "), err)
        self.assertEqual(mc.postprocess(chat(fenced), self.s, lenient=True), ({"flags": []}, None))

    def test_schema_invalid_message(self):
        self.assertEqual(mc.postprocess(chat('{"flags": [], "status": "met"}'), self.s),
                         (None, 'schema_invalid: $: additional property "status" not allowed'))

    def test_schema_invalid_lists_at_most_five(self):
        s = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        _, err = mc.postprocess(chat('{"a":1,"b":2,"c":3,"d":4,"e":5,"f":6,"g":7}'), s)
        self.assertTrue(err.endswith(" (+2 more)"), err)
        self.assertEqual(err.count("not allowed"), 5)

    def test_truncated_refusal_empty_and_shape(self):
        self.assertEqual(mc.postprocess(chat('{"fla', finish="length"), self.s)[1].split(":")[0], "truncated")
        self.assertEqual(mc.postprocess(chat(None, refusal="I can't help with that."), self.s),
                         (None, "refusal: I can't help with that."))
        self.assertEqual(mc.postprocess(chat("   "), self.s), (None, "empty_content"))
        self.assertEqual(mc.postprocess({"choices": []}, self.s), (None, "bad_response_shape: no choices[0]"))
        self.assertEqual(mc.postprocess(chat('{"flags": []}', finish="content_filter"), self.s)[1].split(":")[0],
                         "content_filter")

    def test_content_parts_are_joined(self):
        raw = chat([{"type": "text", "text": '{"flags"'}, {"type": "text", "text": ": []}"}])
        self.assertEqual(mc.postprocess(raw, self.s), ({"flags": []}, None))

    def test_usage_parsing_both_naming_styles(self):
        chat_style = {"usage": {"prompt_tokens": 12000, "completion_tokens": 8000,
                                "prompt_tokens_details": {"cached_tokens": 4000},
                                "completion_tokens_details": {"reasoning_tokens": 5000}}}
        self.assertEqual(mc.parse_usage(chat_style), mc.Usage(12000, 4000, 8000, 5000, True))
        responses_style = {"usage": {"input_tokens": 10, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 1}}}
        self.assertEqual(mc.parse_usage(responses_style), mc.Usage(10, 1, 2, 0, True))
        self.assertEqual(mc.parse_usage({}), mc.Usage(0, 0, 0, 0, False))
        self.assertEqual(mc.parse_usage({"usage": {"prompt_tokens": -1, "completion_tokens": True}}).reported, False)


if __name__ == "__main__":
    unittest.main()
