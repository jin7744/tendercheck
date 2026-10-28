"""Budget ledger (engine/ledger.py). Expected costs are hand-computed literals; prices from design 09 section 2-14:
cost = in x 1.25e-6 + cached_in x 0.15e-6 + out x 4.25e-6 USD. 1 USD = 100,000,000 e8.
"""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import ledger  # noqa: E402
from engine.ledger import (BudgetExceeded, Caps, Ledger, LedgerCorrupt, Pricing, Reservation,  # noqa: E402
                           atomic_append_line, cost_e8, cost_usd, e8_to_usd)


def reserve(ledger, run_id="run-a", inp=0, out=200_000, stage="E1", attempt=0, try_index=0):
    return ledger.reserve(run_id=run_id, stage=stage, attempt=attempt, try_index=try_index, est_input_tokens=inp,
                          max_output_tokens=out, provider="test", model="m")


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


class Costs(unittest.TestCase):
    def test_hand_computed_literals(self):
        # 10,000 in + 2,000 out -> 0.0125 + 0.0085 = 0.0210 USD
        self.assertEqual(cost_e8(10_000, 0, 2_000), 2_100_000)
        self.assertEqual(cost_usd(10_000, 0, 2_000), 0.021)
        # design 09 10-2 "normal" call: 12,000 in + 8,000 out -> 0.015 + 0.034 = 0.049 USD
        self.assertEqual(cost_e8(12_000, 0, 8_000), 4_900_000)
        self.assertEqual(cost_usd(12_000, 0, 8_000), 0.049)
        # design 09 10-2 "pessimistic" call: 40,000 in + 30,000 out -> 0.050 + 0.1275 = 0.1775 USD
        self.assertEqual(cost_e8(40_000, 0, 30_000), 17_750_000)
        # API notes 06 1-3: 100,000 in + 10,000 out -> 0.125 + 0.0425 = 0.1675 USD
        self.assertEqual(cost_e8(100_000, 0, 10_000), 16_750_000)
        # cached: 10,000 in + 4,000 cached + 1,000 out -> 0.0125 + 0.0006 + 0.00425 = 0.01735 USD
        self.assertEqual(cost_e8(10_000, 4_000, 1_000), 1_735_000)
        self.assertEqual(e8_to_usd(1_735_000), 0.01735)

    def test_prompt_includes_cached_variant(self):
        # 10,000 reported in of which 4,000 cached: 6,000 x 1.25e-6 = 0.0075 ; + 0.0006 + 0.00425 = 0.01235 USD
        self.assertEqual(Pricing(prompt_includes_cached=True).cost_e8(10_000, 4_000, 1_000), 1_235_000)

    def test_estimate_is_full_output_budget_without_cache(self):
        # 1,000 in x 125 e8 = 125,000 ; 32,768 out x 425 e8 = 13,926,400 ; total 14,051,400 e8 = $0.140514
        self.assertEqual(Pricing().estimate_e8(1_000, 32_768), 14_051_400)

    def test_rejects_bad_token_counts(self):
        for bad in (-1, True, 1.5, "10"):
            with self.assertRaises(ValueError, msg=bad):
                cost_e8(bad, 0, 0)


class CapsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.jsonl"

    def test_run_cost_cap_counts_estimates_until_settled(self):
        led = Ledger(self.path)
        # each estimate: 200,000 out x $4.25/1M = $0.85 = 85,000,000 e8
        r1, r2 = reserve(led), reserve(led)  # 0.85 + 0.85 = 1.70 <= 2.00
        with self.assertRaises(BudgetExceeded) as ctx:
            reserve(led)  # 1.70 + 0.85 = 2.55 > 2.00
        exc = ctx.exception
        self.assertEqual((exc.reason, exc.spent_e8, exc.estimate_e8, exc.cap), ("run_cost", 170_000_000, 85_000_000, 200_000_000))
        self.assertEqual(str(exc), "budget: run run-a spent $1.700000 + estimate $0.850000 would exceed the run cap $2.00")
        for r in (r1, r2):  # actual 10,000 in + 2,000 out = $0.021 each -> $0.042
            self.assertEqual(led.settle(r, input_tokens=10_000, cached_input_tokens=0, output_tokens=2_000), 2_100_000)
        reserve(led)  # 0.042 + 0.85 = 0.892 <= 2.00
        totals = led.totals("run-a")
        # 2 x 2,100,000 + 85,000,000 = 89,200,000 e8
        self.assertEqual((totals.run_e8, totals.run_calls, totals.pending), (89_200_000, 3, 1))
        self.assertEqual(totals.run_usd, 0.892)

    def test_nine_calls_per_run(self):
        led = Ledger(self.path)
        for attempt in range(9):
            reserve(led, out=1, attempt=attempt)
        with self.assertRaises(BudgetExceeded) as ctx:
            reserve(led, out=1, attempt=9)
        self.assertEqual((ctx.exception.reason, ctx.exception.calls, ctx.exception.cap), ("run_calls", 9, 9))
        reserve(led, run_id="run-b", out=1)  # another run is unaffected

    def test_global_stop_at_140(self):
        seed = Ledger(self.path, caps=Caps(run_cost_e8=10 ** 12))
        # 111,600,000 in x $1.25/1M = $139.50 = 13,950,000,000 e8
        r = reserve(seed, run_id="old", inp=111_600_000, out=0)
        seed.settle(r, input_tokens=111_600_000, cached_input_tokens=0, output_tokens=0)
        led = Ledger(self.path)
        with self.assertRaises(BudgetExceeded) as ctx:
            reserve(led, run_id="new", out=200_000)  # 139.50 + 0.85 = 140.35 > 140
        self.assertEqual(ctx.exception.reason, "global_stop")
        self.assertEqual(ctx.exception.spent_e8, 13_950_000_000)
        reserve(led, run_id="new", inp=10_000, out=2_000)  # 139.50 + 0.021 = 139.521 <= 140
        self.assertEqual(led.totals().global_e8, 13_952_100_000)

    def test_refusal_is_logged_but_not_counted(self):
        led = Ledger(self.path, caps=Caps(run_calls=1))
        reserve(led, out=1)
        with self.assertRaises(BudgetExceeded):
            reserve(led, out=1)
        self.assertEqual([r["event"] for r in rows(self.path)], ["reserve", "refused"])
        self.assertEqual(rows(self.path)[1]["reason"], "run_calls")
        self.assertEqual(led.totals("run-a").run_calls, 1)

    def test_unknown_outcome_keeps_estimate(self):
        led = Ledger(self.path)
        r = reserve(led, out=1_000)  # 1,000 x 425 = 425,000 e8
        self.assertEqual(led.settle_unknown(r, reason="timeout"), 425_000)
        totals = led.totals("run-a")
        self.assertEqual((totals.run_e8, totals.pending), (425_000, 0))

    def test_state_survives_new_instance(self):
        led = Ledger(self.path)
        r = reserve(led)
        led.settle(r, input_tokens=12_000, cached_input_tokens=0, output_tokens=8_000)  # $0.049
        self.assertEqual(Ledger(self.path).totals("run-a").run_e8, 4_900_000)

    def test_settle_errors(self):
        led = Ledger(self.path)
        r = reserve(led)
        led.settle(r, input_tokens=0, cached_input_tokens=0, output_tokens=0)
        with self.assertRaises(ValueError):
            led.settle(r, input_tokens=0, cached_input_tokens=0, output_tokens=0)
        with self.assertRaises(LedgerCorrupt):
            led.settle(Reservation("not-a-rid", "run-a", "E1", 0, 0, 1), input_tokens=0, cached_input_tokens=0,
                       output_tokens=0)

    def test_run_id_required(self):
        with self.assertRaises(ValueError):
            reserve(Ledger(self.path), run_id="")


class Corruption(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.jsonl"

    def assert_refuses(self, content: bytes):
        self.path.write_bytes(content)
        with self.assertRaises(LedgerCorrupt):
            reserve(Ledger(self.path), out=1)

    def test_garbage_line(self):
        self.assert_refuses(b"{not json\n")

    def test_incomplete_last_line(self):
        led = Ledger(self.path)
        reserve(led, out=1)
        self.assert_refuses(self.path.read_bytes() + b'{"v":1,"event":"reserve"')

    def test_unknown_event_and_orphan_settle(self):
        self.assert_refuses(b'{"event":"bonus","rid":"x"}\n')
        self.assert_refuses(b'{"event":"settle","rid":"x","cost_e8":0}\n')
        self.assert_refuses(b'\n')

    def test_negative_or_bool_cost_rows(self):
        self.assert_refuses(b'{"event":"reserve","rid":"a","run_id":"r","estimate_e8":-5}\n')
        self.assert_refuses(b'{"event":"reserve","rid":"a","run_id":"r","estimate_e8":true}\n')

    def test_atomic_append_rejects_multiline(self):
        with self.assertRaises(ValueError):
            atomic_append_line(self.path, "a\nb")


class Concurrency(unittest.TestCase):
    def test_threads_append_whole_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            led = Ledger(path)
            errors = []

            def work(t):
                try:
                    for j in range(20):
                        reserve(led, run_id=f"run-{t}-{j}", out=1)
                except Exception as exc:  # collected and asserted below
                    errors.append(exc)

            threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(rows(path)), 160)
            self.assertEqual(led.totals().global_e8, 160 * 425)  # 160 reservations x 1 out token x 425 e8

    def test_threads_cannot_slip_past_the_call_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Ledger(Path(tmp) / "ledger.jsonl", caps=Caps(run_calls=9))
            successes = []
            barrier = threading.Barrier(16)

            def work(t):
                barrier.wait()
                for j in range(5):
                    try:
                        reserve(led, run_id="shared", out=1, attempt=t * 10 + j)
                        successes.append((t, j))
                    except BudgetExceeded:
                        pass

            threads = [threading.Thread(target=work, args=(t,)) for t in range(16)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            self.assertEqual(len(successes), 9)  # 16 x 5 attempts against a cap of 9
            self.assertEqual(led.totals("shared").run_calls, 9)

    @unittest.skipIf(ledger.fcntl is None, "the cross-process lock is POSIX-only (docs/model_layer_notes.md); "
                                           "run live mode under Linux or WSL")
    def test_processes_cannot_slip_past_the_call_cap(self):
        child = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from engine.ledger import Ledger, Caps, BudgetExceeded\n"
            "led = Ledger(sys.argv[2], caps=Caps(run_calls=9))\n"
            "ok = 0\n"
            "for i in range(5):\n"
            "    try:\n"
            "        led.reserve(run_id='shared', stage='E1', attempt=i, try_index=0, est_input_tokens=0,\n"
            "                    max_output_tokens=1)\n"
            "        ok += 1\n"
            "    except BudgetExceeded:\n"
            "        pass\n"
            "print(ok)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            procs = [subprocess.Popen([sys.executable, "-c", child, str(ROOT), str(path)], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(4)]
            outputs = [p.communicate(timeout=120) for p in procs]
            for (out, err), p in zip(outputs, procs):
                self.assertEqual(p.returncode, 0, err)
            self.assertEqual(sum(int(out.strip()) for out, _ in outputs), 9)  # 4 x 5 attempts, cap 9
            events = [r["event"] for r in rows(path)]
            self.assertEqual((events.count("reserve"), events.count("refused")), (9, 11))


if __name__ == "__main__":
    unittest.main()
