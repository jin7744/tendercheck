"""FakeClient and the shared call loop (engine/model_client.py). No network: FakeClient never opens a socket.

Expected usage sums and costs are hand-computed literals (see comments).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import model_client as mc  # noqa: E402
from engine.ledger import Caps, Ledger  # noqa: E402

ASK_OK = {"asked_items": [{"item": "ISO 27001 certificate", "search_terms": ["ISO 27001"]}]}
REVIEW_OK = {"flags": []}
MSGS = [{"role": "system", "content": "Extract only."}, {"role": "user", "content": "Also check ISO 27001."}]


def usage(prompt, completion, cached=0):
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached}}


class FakeClientBasics(unittest.TestCase):
    def test_valid_reply(self):
        fake = mc.FakeClient({("U", 0): mc.FakeResponse(ASK_OK, usage=usage(1200, 300), latency_ms=41)})
        r = fake.complete("U", 0, MSGS, "x_ask")
        self.assertTrue(r.ok)
        self.assertEqual(r.parsed, ASK_OK)
        self.assertIsNone(r.error)
        self.assertIsNone(r.error_code)
        self.assertEqual(r.usage, mc.Usage(1200, 0, 300, 0, True))
        self.assertEqual(r.model_resolved, "fake-model")
        self.assertEqual(r.latency_ms, 41)
        self.assertEqual(r.raw["choices"][0]["message"]["content"], json.dumps(ASK_OK))
        self.assertEqual(fake.calls, [("U", 0, 0)])

    def test_invalid_then_valid_is_retried_once_and_usage_summed(self):
        fake = mc.FakeClient({("E2", 1): [mc.FakeResponse("Sure! Here it is: {", usage=usage(1000, 50), latency_ms=10),
                                          mc.FakeResponse(REVIEW_OK, usage=usage(1100, 40), latency_ms=12)]})
        r = fake.complete("E2", 1, MSGS, "x_review")
        self.assertTrue(r.ok)
        self.assertEqual(fake.calls, [("E2", 1, 0), ("E2", 1, 1)])
        # 1000 + 1100 = 2100 input, 50 + 40 = 90 output, 10 + 12 = 22 ms
        self.assertEqual(r.usage, mc.Usage(2100, 0, 90, 0, True))
        self.assertEqual(r.latency_ms, 22)
        retry_messages = fake.bodies[1]["messages"]
        self.assertEqual(retry_messages[:2], MSGS)
        self.assertEqual(retry_messages[2]["role"], "user")
        self.assertIn("rejected by the output validator (json_parse_error", retry_messages[2]["content"])

    def test_invalid_twice_returns_error_not_exception(self):
        bad = {"flags": [], "status": "met"}
        fake = mc.FakeClient({("M", 0): [bad, bad]})
        r = fake.complete("M", 0, MSGS, "x_review")
        self.assertFalse(r.ok)
        self.assertIsNone(r.parsed)
        self.assertEqual(r.error, 'schema_invalid: $: additional property "status" not allowed')
        self.assertEqual(r.error_code, "schema_invalid")
        self.assertEqual(len(fake.calls), 2)

    def test_zero_retries_stops_after_one(self):
        fake = mc.FakeClient({("M", 0): ["not json", REVIEW_OK]}, invalid_output_retries=0)
        r = fake.complete("M", 0, MSGS, "x_review")
        self.assertEqual(r.error_code, "json_parse_error")
        self.assertEqual(fake.calls, [("M", 0, 0)])

    def test_truncation_and_refusal_are_not_retried(self):
        fake = mc.FakeClient({("E1", 0): [mc.FakeResponse('{"flags": [', finish_reason="length"), REVIEW_OK],
                              ("E1", 1): [mc.FakeResponse(None, refusal="No."), REVIEW_OK]})
        self.assertEqual(fake.complete("E1", 0, MSGS, "x_review").error_code, "truncated")
        self.assertEqual(fake.complete("E1", 1, MSGS, "x_review").error, "refusal: No.")
        self.assertEqual(fake.calls, [("E1", 0, 0), ("E1", 1, 0)])

    def test_contributor_model_in_response_is_an_error(self):
        fake = mc.FakeClient({("C", 0): mc.FakeResponse(REVIEW_OK, model="muse-spark-1.3-contributor")})
        r = fake.complete("C", 0, MSGS, "x_review")
        self.assertIsNone(r.parsed)
        self.assertEqual(r.error, "contributor_model_resolved: muse-spark-1.3-contributor")

    def test_contributor_model_id_refused_at_construction(self):
        with self.assertRaises(mc.ContributorModelRefused):
            mc.FakeClient({}, model="muse-spark-1.3-contributor")

    def test_fixture_exception_is_raised(self):
        err = mc.BillingNotConfigured(402, error_type="billing_error", error_code="billing_not_configured")
        fake = mc.FakeClient({("E1", 0): err})
        with self.assertRaises(mc.BillingNotConfigured) as ctx:
            fake.complete("E1", 0, MSGS, "x_review")
        self.assertEqual(str(ctx.exception), "HTTP 402 billing_not_configured")

    def test_missing_fixture(self):
        with self.assertRaises(mc.FixtureMissing):
            mc.FakeClient({}).complete("U", 0, MSGS, "x_ask")
        with self.assertRaises(mc.FixtureMissing):  # retry wanted but only one reply scripted
            mc.FakeClient({("U", 0): ["bad"]}).complete("U", 0, MSGS, "x_ask")

    def test_argument_checks(self):
        fake = mc.FakeClient({("U", 0): ASK_OK})
        for stage, attempt, msgs in [("", 0, MSGS), ("U 1", 0, MSGS), ("U", -1, MSGS), ("U", True, MSGS),
                                     ("U", 0, []), ("U", 0, [{"role": "tool", "content": "x"}]),
                                     ("U", 0, [{"role": "user"}])]:
            with self.assertRaises(ValueError, msg=(stage, attempt, msgs)):
                fake.complete(stage, attempt, msgs, "x_ask")
        with self.assertRaises(mc.SchemaError):
            fake.complete("U", 0, MSGS, "x_missing")

    def test_from_file(self):
        doc = {"model": "fixture-model", "fixtures": [
            {"stage": "U", "attempt": 0, "responses": [{"content": ASK_OK, "usage": usage(10, 2), "latency_ms": 5}]},
            {"stage": "E1", "attempt": 0, "responses": [{"content_text": "oops"}, {"content": REVIEW_OK}]},
            {"stage": "M", "attempt": 0, "responses": [{"error": {"status": 402, "body": {"error": {
                "type": "billing_error", "code": "billing_not_configured", "message": "No payment method"}}}}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixtures.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            fake = mc.FakeClient.from_file(path)
        r = fake.complete("U", 0, MSGS, "x_ask")
        self.assertEqual((r.parsed, r.model_resolved, r.latency_ms, r.usage.output_tokens), (ASK_OK, "fixture-model", 5, 2))
        self.assertTrue(fake.complete("E1", 0, MSGS, "x_review").ok)
        with self.assertRaises(mc.BillingNotConfigured) as ctx:
            fake.complete("M", 0, MSGS, "x_match")
        self.assertEqual(str(ctx.exception), "HTTP 402 billing_not_configured: No payment method")

    def test_messages_are_not_mutated(self):
        msgs = [dict(m) for m in MSGS]
        fake = mc.FakeClient({("E2", 0): ["bad", REVIEW_OK]})
        fake.complete("E2", 0, msgs, "x_review")
        self.assertEqual(msgs, MSGS)


class FakeClientWithLedger(unittest.TestCase):
    def test_actual_cost_recorded_from_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.jsonl")
            fake = mc.FakeClient({("E1", 0): mc.FakeResponse(REVIEW_OK, usage=usage(10_000, 2_000))},
                                 ledger=ledger, run_id="unit-run")
            fake.complete("E1", 0, MSGS, "x_review")
            totals = ledger.totals("unit-run")
        # 10,000 in x $1.25/1M = 0.0125 ; 2,000 out x $4.25/1M = 0.0085 ; total 0.0210 USD = 2,100,000 e8
        self.assertEqual(totals.run_e8, 2_100_000)
        self.assertEqual(totals.run_calls, 1)
        self.assertEqual(totals.pending, 0)

    def test_retry_counts_as_second_billable_call_and_budget_stops_before_third(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.jsonl", caps=Caps(run_calls=2))
            fake = mc.FakeClient({("E2", 0): ["bad", REVIEW_OK], ("E2", 1): [REVIEW_OK]}, ledger=ledger, run_id="r")
            self.assertTrue(fake.complete("E2", 0, MSGS, "x_review").ok)
            with self.assertRaises(mc.BudgetExceeded) as ctx:
                fake.complete("E2", 1, MSGS, "x_review")
            self.assertEqual(ctx.exception.reason, "run_calls")
            self.assertEqual(fake.calls, [("E2", 0, 0), ("E2", 0, 1)])  # third request never reached the provider
            self.assertEqual(ledger.totals("r").run_calls, 2)

    def test_usage_missing_keeps_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.jsonl")
            fake = mc.FakeClient({("E1", 0): mc.FakeResponse(REVIEW_OK, omit_usage=True)}, ledger=ledger, run_id="r",
                                 max_output_tokens=1000)
            r = fake.complete("E1", 0, MSGS, "x_review")
            rows = [json.loads(line) for line in (Path(tmp) / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertFalse(r.usage.reported)
        self.assertEqual([row["event"] for row in rows], ["reserve", "unknown"])
        self.assertEqual(rows[1]["charged_e8"], rows[0]["estimate_e8"])
        self.assertGreaterEqual(rows[0]["estimate_e8"], 1000 * 425)  # at least the full output budget at $4.25/1M


if __name__ == "__main__":
    unittest.main()
