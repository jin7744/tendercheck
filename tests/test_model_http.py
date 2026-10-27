"""MetaClient / OpenAICompatibleClient over a fake urlopen (engine/model_client.py).

Zero real network: every test in this file blocks socket.create_connection / getaddrinfo / socket.connect,
and replaces urllib.request.urlopen with a scripted fake. The API key used here is a dummy literal; the real
MODEL_API_KEY is never read (environments that need a key are cleared first).
"""
import email.message
import io
import json
import logging
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import model_client as mc  # noqa: E402
from engine.ledger import Ledger, ZERO_PRICING  # noqa: E402
from engine.replay import Recorder, load_calls  # noqa: E402

DUMMY = "unit-test-dummy-key-01"  # not a real credential
MSGS = [{"role": "system", "content": "Extract only."}, {"role": "user", "content": "Also check ISO 27001."}]
ASK_OK = {"asked_items": [{"item": "ISO 27001 certificate", "search_terms": ["ISO 27001"]}]}
REVIEW_OK = {"flags": []}
LIVE_ENV_ON = {"TENDERCHECK_LIVE": "1"}


def headers_msg(headers=None):
    msg = email.message.Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return msg


def as_bytes(body):
    return body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")


def chat_response(content, *, model="muse-spark-1.3", prompt=10_000, completion=2_000, cached=0, finish="stop"):
    return {"id": "chatcmpl-test", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant",
                                                 "content": content if isinstance(content, str) else json.dumps(content)},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
                      "prompt_tokens_details": {"cached_tokens": cached}}}


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = as_bytes(body)
        self.headers = headers_msg(headers)

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUrlopen:
    """Script items: (status, body[, headers]) or an exception instance."""

    def __init__(self, test, script):
        self.test = test
        self.script = list(script)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append({"url": request.full_url, "method": request.get_method(),
                              "headers": dict(request.header_items()),
                              "body": json.loads(request.data.decode("utf-8")), "timeout": timeout})
        if not self.script:
            self.test.fail("unexpected extra HTTP request")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, body = item[0], item[1]
        headers = item[2] if len(item) > 2 else {}
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", headers_msg(headers), io.BytesIO(as_bytes(body)))
        return FakeResponse(status, body, headers)


class NoNetworkTestCase(unittest.TestCase):
    def setUp(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("network access attempted in a unit test")

        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect",
                       "socket.socket.connect_ex"):
            patcher = mock.patch(target, forbidden)
            patcher.start()
            self.addCleanup(patcher.stop)
        logger = logging.getLogger("tendercheck.model")
        handler = logging.NullHandler()
        logger.addHandler(handler)
        old = logger.propagate
        logger.propagate = False
        self.addCleanup(lambda: (logger.removeHandler(handler), setattr(logger, "propagate", old)))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sleeps = []

    def install(self, script):
        fake = FakeUrlopen(self, script)
        patcher = mock.patch("urllib.request.urlopen", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def forbid_urlopen(self):
        def fail(*args, **kwargs):
            self.fail("urlopen must not be called")

        patcher = mock.patch("urllib.request.urlopen", fail)
        patcher.start()
        self.addCleanup(patcher.stop)

    def live_env(self):
        patcher = mock.patch.dict(os.environ, LIVE_ENV_ON)
        patcher.start()
        self.addCleanup(patcher.stop)

    def meta(self, **kwargs):
        run_id = kwargs.setdefault("run_id", "live_unit")
        kwargs.setdefault("ledger", Ledger(self.root / "ledger.jsonl"))
        kwargs.setdefault("recorder", Recorder(self.root / "runs" / run_id))
        kwargs.setdefault("sleep", self.sleeps.append)
        return mc.MetaClient(api_key=DUMMY, live=True, **kwargs)

    def calls_text(self):
        path = self.root / "runs" / "live_unit" / "calls.jsonl"
        return path.read_text(encoding="utf-8") if path.exists() else ""


class LiveGuard(NoNetworkTestCase):
    def test_default_construction_cannot_send(self):
        self.forbid_urlopen()
        with mock.patch.dict(os.environ, {}, clear=True):
            client = mc.MetaClient(api_key=DUMMY, ledger=Ledger(self.root / "l.jsonl"),
                                   recorder=Recorder(self.root / "run"), run_id="r")
            with self.assertRaises(mc.LiveCallsDisabled):
                client.complete("U", 0, MSGS, "x_ask")
        self.assertFalse((self.root / "l.jsonl").exists())  # guard ran before the ledger was touched
        self.assertFalse((self.root / "run" / "calls.jsonl").exists())

    def test_env_flag_alone_is_not_enough(self):
        self.forbid_urlopen()
        self.live_env()
        client = mc.MetaClient(api_key=DUMMY, live=False, ledger=Ledger(self.root / "l.jsonl"),
                               recorder=Recorder(self.root / "run"), run_id="r")
        with self.assertRaises(mc.LiveCallsDisabled):
            client.complete("U", 0, MSGS, "x_ask")

    def test_constructor_flag_alone_is_not_enough(self):
        self.forbid_urlopen()
        for env in ({}, {"TENDERCHECK_LIVE": "true"}, {"TENDERCHECK_LIVE": "0"}):
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(mc.LiveCallsDisabled, msg=env):
                    mc.MetaClient(api_key=DUMMY, live=True)
                with self.assertRaises(mc.LiveCallsDisabled, msg=env):
                    mc.OpenAICompatibleClient(base_url="http://127.0.0.1:9/v1", model="m", live=True)

    def test_env_switched_off_after_construction_still_blocks(self):
        self.forbid_urlopen()
        with mock.patch.dict(os.environ, LIVE_ENV_ON):
            client = self.meta()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(mc.LiveCallsDisabled):
                client.complete("U", 0, MSGS, "x_ask")

    def test_compatible_and_local_clients_are_guarded_too(self):
        self.forbid_urlopen()
        with mock.patch.dict(os.environ, {}, clear=True):
            for client in (mc.OpenAICompatibleClient(base_url="https://example.invalid/v1", model="m"),
                           mc.local_client(model="glimmer")):
                with self.assertRaises(mc.LiveCallsDisabled):
                    client.complete("U", 0, MSGS, "x_ask")

    def test_from_env_checks_guard_before_reading_key(self):
        self.forbid_urlopen()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(mc.LiveCallsDisabled):
                mc.MetaClient.from_env()
            with self.assertRaises(mc.LiveCallsDisabled):
                mc.MetaClient.from_env(live=True)
        with mock.patch.dict(os.environ, LIVE_ENV_ON, clear=True):  # guard passes, key variable absent
            with self.assertRaises(mc.MissingApiKey):
                mc.MetaClient.from_env(live=True)

    def test_live_call_needs_key_ledger_recorder_and_run_id(self):
        self.forbid_urlopen()
        self.live_env()
        with self.assertRaises(mc.MissingApiKey):
            mc.MetaClient(live=True, ledger=Ledger(self.root / "l"), recorder=Recorder(self.root / "r"),
                          run_id="r").complete("U", 0, MSGS, "x_ask")
        for missing in ("ledger", "recorder", "run_id"):
            kwargs = {"ledger": Ledger(self.root / "l"), "recorder": Recorder(self.root / "r"), "run_id": "r"}
            kwargs[missing] = None
            with self.assertRaises(mc.MissingLiveDependency, msg=missing):
                mc.MetaClient(api_key=DUMMY, live=True, **kwargs).complete("U", 0, MSGS, "x_ask")

    def test_model_id_rules(self):
        with self.assertRaises(mc.ContributorModelRefused):
            mc.MetaClient(api_key=DUMMY, model="muse-spark-1.3-contributor")
        with self.assertRaises(mc.UnsupportedModel):
            mc.MetaClient(api_key=DUMMY, model="muse-spark-1.2")
        with self.assertRaises(mc.ContributorModelRefused):
            mc.OpenAICompatibleClient(base_url="https://example.invalid/v1", model="muse-spark-1.2-contributor")
        with self.assertRaises(TypeError):  # base URL of the Meta client is not configurable
            mc.MetaClient(api_key=DUMMY, base_url="https://example.invalid/v1")

    def test_repr_hides_key(self):
        self.assertNotIn(DUMMY, repr(mc.MetaClient(api_key=DUMMY)))


class MetaSuccess(NoNetworkTestCase):
    def test_request_shape_usage_ledger_and_record(self):
        self.live_env()
        fake = self.install([(200, chat_response(ASK_OK),
                              {"x-ratelimit-remaining-tokens": "3990000", "Set-Cookie": "sid=abc"})])
        client = self.meta(max_output_tokens=8000)
        result = client.complete("U", 0, MSGS, "x_ask")

        self.assertTrue(result.ok)
        self.assertEqual(result.parsed, ASK_OK)
        self.assertEqual(result.model_resolved, "muse-spark-1.3")
        self.assertEqual(result.usage, mc.Usage(10_000, 0, 2_000, 0, True))

        req = fake.requests[0]
        self.assertEqual(req["url"], "https://api.meta.ai/v1/chat/completions")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + DUMMY)
        contract = json.loads((ROOT / "contracts" / "model_x_ask.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(req["body"], {
            "model": "muse-spark-1.3",
            "messages": MSGS,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "x_ask", "schema": contract["schema"], "strict": True}},
            "max_completion_tokens": 8000,
        })

        # 10,000 in x $1.25/1M = 0.0125 ; 2,000 out x $4.25/1M = 0.0085 ; total 0.0210 USD = 2,100,000 e8
        totals = client.ledger.totals("live_unit")
        self.assertEqual((totals.run_e8, totals.run_calls, totals.pending), (2_100_000, 1, 0))

        entries = load_calls(self.root / "runs" / "live_unit")
        entry = entries[("U", 0, 0)]
        self.assertEqual(entry["outcome"], "response")
        self.assertEqual(entry["cost_e8"], 2_100_000)
        self.assertEqual(entry["cost_usd"], 0.021)
        self.assertEqual(entry["model_resolved"], "muse-spark-1.3")
        self.assertEqual(entry["request_headers"]["authorization"], "[REDACTED]")
        self.assertEqual(entry["response_headers"]["set-cookie"], "[REDACTED]")
        self.assertEqual(entry["response_headers"]["x-ratelimit-remaining-tokens"], "3990000")
        self.assertNotIn("messages", entry)
        text = self.calls_text()
        self.assertNotIn(DUMMY, text)
        self.assertNotIn("Bearer", text)

    def test_recorded_live_run_replays_to_equal_result(self):
        self.live_env()
        self.install([(200, chat_response("{oops", prompt=900, completion=30)),
                      (200, chat_response(REVIEW_OK, prompt=950, completion=20))])
        live = self.meta().complete("E2", 1, MSGS, "x_review")
        self.assertTrue(live.ok)
        self.assertEqual(live.usage, mc.Usage(1_850, 0, 50, 0, True))  # 900 + 950 in, 30 + 20 out
        replay = mc.ReplayClient(self.root / "runs" / "live_unit")
        self.assertEqual(replay.complete("E2", 1, MSGS, "x_review"), live)
        self.assertEqual(replay.calls, [("E2", 1, 0), ("E2", 1, 1)])

    def test_retry_after_invalid_output_sends_corrective_message(self):
        self.live_env()
        fake = self.install([(200, chat_response('{"flags": [], "status": "met"}')), (200, chat_response(REVIEW_OK))])
        result = self.meta().complete("E1", 0, MSGS, "x_review")
        self.assertTrue(result.ok)
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(fake.requests[1]["body"]["messages"][:2], MSGS)
        self.assertIn('additional property "status" not allowed', fake.requests[1]["body"]["messages"][2]["content"])

    def test_extra_body_passthrough_for_unverified_params(self):
        self.live_env()
        fake = self.install([(200, chat_response(REVIEW_OK))])
        self.meta(extra_body={"reasoning_effort": "high"}).complete("E1", 0, MSGS, "x_review")
        self.assertEqual(fake.requests[0]["body"]["reasoning_effort"], "high")
        for clash in ({"tools": []}, {"model": "x"}, {"max_completion_tokens": 5}, {"stream": True}):
            with self.assertRaises(ValueError, msg=clash):
                mc.MetaClient(api_key=DUMMY, extra_body=clash)

    def test_contributor_model_in_response_is_rejected(self):
        self.live_env()
        self.install([(200, chat_response(REVIEW_OK, model="muse-spark-1.3-contributor"))])
        result = self.meta().complete("E1", 0, MSGS, "x_review")
        self.assertIsNone(result.parsed)
        self.assertEqual(result.error_code, "contributor_model_resolved")

    def test_truncated_reply_not_retried(self):
        self.live_env()
        fake = self.install([(200, chat_response('{"flags": [', finish="length"))])
        result = self.meta().complete("E1", 0, MSGS, "x_review")
        self.assertEqual(result.error_code, "truncated")
        self.assertEqual(len(fake.requests), 1)


class MetaErrors(NoNetworkTestCase):
    def setUp(self):
        super().setUp()
        self.live_env()

    def assert_settled_zero(self, client):
        totals = client.ledger.totals("live_unit")
        self.assertEqual((totals.run_e8, totals.run_calls, totals.pending), (0, 1, 0))

    def test_402_billing_not_configured(self):
        body = {"error": {"type": "billing_error", "code": "billing_not_configured",
                          "message": "Billing is not configured for key " + DUMMY}}
        self.install([(402, body)])
        client = self.meta()
        with self.assertLogs("tendercheck.model", level="WARNING") as logs:
            with self.assertRaises(mc.BillingNotConfigured) as ctx:
                client.complete("E1", 0, MSGS, "x_review")
        exc = ctx.exception
        self.assertEqual((exc.status, exc.error_type, exc.error_code), (402, "billing_error", "billing_not_configured"))
        self.assertEqual(str(exc), "HTTP 402 billing_not_configured: Billing is not configured for key [REDACTED]")
        self.assertNotIn(DUMMY, exc.body_excerpt)
        self.assertNotIn(DUMMY, "\n".join(logs.output))
        self.assertIn("billing_not_configured", "\n".join(logs.output))
        self.assertNotIn(DUMMY, self.calls_text())
        self.assert_settled_zero(client)
        # the recorded failure replays as the same typed error
        with self.assertRaises(mc.BillingNotConfigured) as again:
            mc.ReplayClient(self.root / "runs" / "live_unit").complete("E1", 0, MSGS, "x_review")
        self.assertEqual(str(again.exception), str(exc))

    def test_402_detected_by_status_alone(self):
        # the error body shape is unverified until the live smoke test: the status code must suffice
        for n, body in enumerate([b"", b"Payment Required", {"error": "payment required"}]):
            self.install([(402, body)])
            with self.assertRaises(mc.BillingNotConfigured, msg=body):
                self.meta(run_id=f"live_unit_{n}").complete("E1", 0, MSGS, "x_review")

    def test_402_detected_by_body_code_even_with_other_status(self):
        self.install([(403, {"error": {"type": "billing_error", "code": "billing_not_configured"}})])
        with self.assertRaises(mc.BillingNotConfigured):
            self.meta().complete("E1", 0, MSGS, "x_review")

    def test_401_404_400_typed_errors(self):
        cases = [(401, {"error": {"type": "authentication_error", "code": "invalid_api_key", "message": "bad key"}},
                  mc.InvalidApiKey, "HTTP 401 invalid_api_key: bad key"),
                 (404, {"error": {"type": "not_found_error", "code": "model_not_found", "message": "no such model"}},
                  mc.ModelNotFound, "HTTP 404 model_not_found: no such model"),
                 (400, {"error": {"type": "invalid_request_error", "message": "response_format.json_schema invalid"}},
                  mc.BadRequest, "HTTP 400 invalid_request_error: response_format.json_schema invalid")]
        for n, (status, body, cls, text) in enumerate(cases):
            self.install([(status, body)])
            with self.assertLogs("tendercheck.model", level="WARNING"):
                with self.assertRaises(cls) as ctx:
                    self.meta(run_id=f"live_unit_{n}").complete("E1", 0, MSGS, "x_review")
            self.assertEqual(str(ctx.exception), text)
            self.assertEqual(ctx.exception.body_excerpt, json.dumps(body))

    def test_non_json_error_body_is_truncated_and_scrubbed(self):
        body = ("<html>Bearer " + DUMMY + " " + "x" * 5000 + "</html>").encode("utf-8")
        self.install([(400, body)])
        with self.assertLogs("tendercheck.model", level="WARNING"):
            with self.assertRaises(mc.BadRequest) as ctx:
                self.meta().complete("E1", 0, MSGS, "x_review")
        self.assertEqual(len(ctx.exception.body_excerpt), 2000)
        self.assertTrue(ctx.exception.body_excerpt.startswith("<html>Bearer [REDACTED] xxx"))
        self.assertEqual(str(ctx.exception), "HTTP 400 error")

    def test_500_keeps_estimate_in_ledger(self):
        self.install([(503, {"error": {"type": "server_error", "message": "overloaded"}})])
        client = self.meta(max_output_tokens=1000)
        with self.assertLogs("tendercheck.model", level="WARNING"):
            with self.assertRaises(mc.ServerError):
                client.complete("E1", 0, MSGS, "x_review")
        rows = [json.loads(x) for x in (self.root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["event"] for r in rows], ["reserve", "unknown"])
        self.assertEqual(client.ledger.totals("live_unit").run_e8, rows[0]["estimate_e8"])
        self.assertGreaterEqual(rows[0]["estimate_e8"], 1000 * 425)

    def test_network_errors_become_transport_errors(self):
        for n, err in enumerate([urllib.error.URLError("Name or service not known"), TimeoutError("timed out"),
                                 ConnectionResetError("reset")]):
            self.install([err])
            with self.assertRaises(mc.TransportError):
                self.meta(run_id=f"live_unit_{n}").complete("E1", 0, MSGS, "x_review")

    def test_budget_refusal_happens_before_any_request(self):
        self.forbid_urlopen()
        # estimate >= 500,000 output tokens x $4.25/1M = $2.125 > $2.00 run cap
        client = self.meta(max_output_tokens=500_000)
        with self.assertRaises(mc.BudgetExceeded) as ctx:
            client.complete("E1", 0, MSGS, "x_review")
        self.assertEqual(ctx.exception.reason, "run_cost")
        self.assertEqual(load_calls(self.root / "runs" / "live_unit")[("E1", 0, 0)]["outcome"], "budget_exceeded")
        with self.assertRaises(mc.BudgetExceeded):  # and it replays as the same stop
            mc.ReplayClient(self.root / "runs" / "live_unit").complete("E1", 0, MSGS, "x_review")


class RateLimiting(NoNetworkTestCase):
    def setUp(self):
        super().setUp()
        self.live_env()

    def test_retry_after_seconds_is_honoured(self):
        fake = self.install([(429, {"error": {"type": "rate_limit_error"}}, {"Retry-After": "7"}),
                             (200, chat_response(REVIEW_OK))])
        client = self.meta()
        result = client.complete("E1", 0, MSGS, "x_review")
        self.assertTrue(result.ok)
        self.assertEqual(self.sleeps, [7.0])
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(client.ledger.totals("live_unit").run_calls, 1)  # one reservation covers the retries

    def test_retry_after_http_date(self):
        self.install([(429, {}, {"Retry-After": "Wed, 28 Oct 2026 00:00:10 GMT"}), (200, chat_response(REVIEW_OK))])
        # wall clock 2026-10-28T00:00:00Z = 1793145600 -> Retry-After is 10 s later
        self.meta(wall_clock=lambda: 1793145600.0).complete("E1", 0, MSGS, "x_review")
        self.assertEqual(self.sleeps, [10.0])

    def test_exponential_backoff_without_header_then_give_up_after_three_retries(self):
        fake = self.install([(429, {"error": {"type": "rate_limit_error", "message": "slow down"}})] * 4)
        client = self.meta()
        with self.assertLogs("tendercheck.model", level="WARNING"):
            with self.assertRaises(mc.RateLimited) as ctx:
                client.complete("E1", 0, MSGS, "x_review")
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0])
        self.assertEqual(len(fake.requests), 4)
        self.assertEqual(str(ctx.exception), "HTTP 429 rate_limit_error: slow down (gave up after 3 retries)")
        totals = client.ledger.totals("live_unit")
        self.assertEqual((totals.run_e8, totals.run_calls), (0, 1))

    def test_three_429_then_success(self):
        self.install([(429, {}), (429, {}), (429, {}), (200, chat_response(REVIEW_OK))])
        self.assertTrue(self.meta().complete("E1", 0, MSGS, "x_review").ok)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0])

    def test_retry_after_above_cap_gives_up_without_sleeping(self):
        fake = self.install([(429, {}, {"Retry-After": "3600"})])
        with self.assertLogs("tendercheck.model", level="WARNING"):
            with self.assertRaises(mc.RateLimited) as ctx:
                self.meta().complete("E1", 0, MSGS, "x_review")
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(str(ctx.exception), "HTTP 429 error: Retry-After 3600s exceeds cap 60s")


class CompatibleHosts(NoNetworkTestCase):
    def setUp(self):
        super().setUp()
        self.live_env()

    def compat(self, **kwargs):
        run_id = kwargs.setdefault("run_id", "live_unit")
        kwargs.setdefault("ledger", Ledger(self.root / "ledger.jsonl"))
        kwargs.setdefault("recorder", Recorder(self.root / "runs" / run_id))
        kwargs.setdefault("sleep", self.sleeps.append)
        kwargs.setdefault("base_url", "https://inference.example.invalid/v1")
        kwargs.setdefault("model", "meta-models/muse-spark-open")
        return mc.OpenAICompatibleClient(api_key=DUMMY, live=True, **kwargs)

    def test_retry_layer_is_always_on(self):
        with self.assertRaises(ValueError):
            mc.OpenAICompatibleClient(base_url="https://example.invalid/v1", model="m", invalid_output_retries=0)

    def test_code_fence_is_tolerated(self):
        fake = self.install([(200, chat_response("```json\n" + json.dumps(REVIEW_OK) + "\n```", model="x"))])
        result = self.compat().complete("E1", 0, MSGS, "x_review")
        self.assertTrue(result.ok)
        self.assertEqual(fake.requests[0]["url"], "https://inference.example.invalid/v1/chat/completions")
        self.assertEqual(fake.requests[0]["body"]["max_tokens"], 32768)
        self.assertNotIn("max_completion_tokens", fake.requests[0]["body"])

    def test_prose_reply_is_retried_then_validated(self):
        fake = self.install([(200, chat_response("Here are the flags: none.", model="x")),
                             (200, chat_response(REVIEW_OK, model="x"))])
        self.assertTrue(self.compat().complete("E1", 0, MSGS, "x_review").ok)
        self.assertEqual(len(fake.requests), 2)

    def test_both_tries_invalid_returns_error(self):
        self.install([(200, chat_response("nope", model="x")), (200, chat_response('{"flags": 3}', model="x"))])
        result = self.compat().complete("E1", 0, MSGS, "x_review")
        self.assertEqual(result.error, "schema_invalid: $.flags: expected array, got integer")

    def test_schema_in_prompt_when_host_lacks_json_schema(self):
        fake = self.install([(200, chat_response(REVIEW_OK, model="x"))])
        self.compat(response_format_mode="none").complete("E1", 0, MSGS, "x_review")
        body = fake.requests[0]["body"]
        self.assertNotIn("response_format", body)
        self.assertEqual(body["messages"][:2], MSGS)
        self.assertTrue(body["messages"][2]["content"].startswith("Return exactly one JSON object"))

    def test_local_client_zero_pricing(self):
        fake = self.install([(200, chat_response(REVIEW_OK, model="glimmer"))])
        client = mc.local_client(model="glimmer", live=True, ledger=Ledger(self.root / "ledger.jsonl"),
                                 recorder=Recorder(self.root / "runs" / "live_unit"), run_id="live_unit")
        self.assertIs(client.pricing, ZERO_PRICING)
        self.assertTrue(client.complete("E1", 0, MSGS, "x_review").ok)
        self.assertEqual(fake.requests[0]["url"], "http://127.0.0.1:8080/v1/chat/completions")
        self.assertNotIn("Authorization", fake.requests[0]["headers"])
        self.assertEqual(client.ledger.totals("live_unit").run_e8, 0)


if __name__ == "__main__":
    unittest.main()
