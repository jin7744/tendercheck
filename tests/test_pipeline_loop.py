"""engine.pipeline loop branches (E2 requote/unverified, E3, E4, U not_in_source, forced verdict), the
conditional-row file-check skip (A2 open item) and the attribute-finding merge. Fixtures are hand-written;
expected values are literals (08 §2.5 for package A rows)."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine import pipeline  # noqa: E402
from engine.ledger import Ledger  # noqa: E402
from engine.model_client import FakeClient, MetaClient  # noqa: E402
from engine.replay import Recorder  # noqa: E402
from eval import core as grader  # noqa: E402
from test_pipeline_e2e import (C_REPLY, EXPECTED, GOLD_A, M_REPLY, U_REPLY, _req, e1_reply_a, inputs,  # noqa: E402
                               m_reply, record_for)

NC = "needs-confirmation"


def run_a1(fixtures, user_message=None):
    i = inputs("A-M1")
    msg = i["user_message"] if user_message is None else user_message
    return pipeline.run(i["docs"], i["manifest_text"], lang=i["lang"], as_of=i["as_of"], run_id="A-M1",
                        client=FakeClient(fixtures), user_message=msg)


def statuses(rep):
    return {gid: (record_for(rep, q)["status"], record_for(rep, q)["condition_state"]) for gid, (q, _, _) in GOLD_A.items()}


def base(over=None):
    fx = {("U", 0): U_REPLY, ("E1", 0): e1_reply_a(), ("M", 0): M_REPLY["A-M1"], ("C", 0): C_REPLY}
    fx.update(over or {})
    return fx


def with_e1(change):
    e1 = e1_reply_a()
    change(e1)
    return e1


class FixLoop(unittest.TestCase):
    def test_e2_requote_repairs_a_bad_quote(self):
        def bad_quote(e1):
            e1["requirements"][2]["quote"] = "Power of Attorney authorising the signatory of the Bid"
        fix = {"fixes": [{"tmp_id": "R3", "action": "requote", "quote": GOLD_A["R03"][0],
                          "location": {"doc_id": "A_BODY", "seg_id": "A_BODY-L13"}, "note": "spelling as in the source"}]}
        res = run_a1(base({("E1", 0): with_e1(bad_quote), ("E2", 0): fix}))
        self.assertEqual([c["stage"] for c in res["calls"]], ["U", "E1", "E2", "M", "C"])
        self.assertEqual(statuses(res["report"]), EXPECTED["A-M1"])
        qc = res["report"]["x_meta"]["verification_log"]["quote_checks"]
        self.assertEqual((qc["requoted"], qc["unverified"], qc["fix_rounds"]), (1, 0, 1))
        self.assertEqual(grader.grade_regression("A-M1", json.dumps(res["grading"]))["verdict"], "PASS")

    def test_two_failed_fix_rounds_leave_the_row_unverified(self):
        def bad_quote(e1):
            e1["requirements"][2]["quote"] = "Power of Attorney authorising the signatory of the Bid"
        res = run_a1(base({("E1", 0): with_e1(bad_quote), ("E2", 0): {"fixes": []}, ("E2", 1): {"fixes": []}}))
        self.assertEqual([c["stage"] for c in res["calls"]], ["U", "E1", "E2", "E2", "M", "C"])
        rep = res["report"]
        row = [r for r in rep["requirements"] if r["location"] == "Body §3(b)"]
        self.assertEqual(len(row), 1)
        self.assertEqual((row[0]["status"], row[0]["quote"]), (NC, "(b) Power of Attorney authorizing the signatory of the Bid;"))
        meta = {m["id"]: m for m in rep["x_meta"]["rows"]}
        self.assertEqual(meta[row[0]["id"]]["reason_codes"], ["UNVERIFIED_QUOTE"])
        self.assertTrue(res["invariants"]["ok"], res["invariants"])

    def test_retract_drops_the_record(self):
        def extra(e1):
            e1["requirements"].append(_req("R21", "document", "iso certificate", "Submit an ISO certificate.",
                                           "An ISO/IEC 27001 certificate is required.", "A_BODY-L11"))
        fix = {"fixes": [{"tmp_id": "R21", "action": "retract", "quote": "",
                          "location": {"doc_id": "A_BODY", "seg_id": "A_BODY-L11"}, "note": "not in the source"}]}
        res = run_a1(base({("E1", 0): with_e1(extra), ("E2", 0): fix}))
        self.assertEqual(len(res["report"]["requirements"]), 19)
        self.assertEqual(res["report"]["x_meta"]["verification_log"]["quote_checks"]["retracted"], 1)
        self.assertEqual(statuses(res["report"]), EXPECTED["A-M1"])


class RecheckAndCoverage(unittest.TestCase):
    def test_e3_runs_for_uncertain_segments(self):
        def uncertain(e1):
            e1["uncertain_segments"] = [{"seg_id": "A_ANNEX1-L13", "reason": "cross_reference", "note": "codes"}]
        e3 = {"requirements": [], "uncertain_segments": [], "not_requirements": [], "injection_suspects": []}
        res = run_a1(base({("E1", 0): with_e1(uncertain), ("E3", 0): e3}))
        self.assertEqual([c["stage"] for c in res["calls"]], ["U", "E1", "E3", "M", "C"])
        self.assertEqual(statuses(res["report"]), EXPECTED["A-M1"])
        self.assertEqual(res["report"]["x_meta"]["verification_log"]["rechecks"], {"requested": 1, "resolved": 0})

    def _uncovered(self, e1):
        e1["not_requirements"] = [n for n in e1["not_requirements"] if n["seg_id"] != "A_ANNEX1-L5"]

    def test_e4_skip_explains_an_uncited_trigger_segment(self):
        e4 = {"new_requirements": [], "skips": [{"seg_id": "A_ANNEX1-L5", "reason": "not_submission_related"}]}
        res = run_a1(base({("E1", 0): with_e1(self._uncovered), ("E4", 0): e4}))
        self.assertEqual([c["stage"] for c in res["calls"]], ["U", "E1", "E4", "M", "C"])
        self.assertEqual(res["report"]["x_meta"]["info_gaps"], [])
        self.assertEqual(statuses(res["report"]), EXPECTED["A-M1"])

    def test_e4_unexplained_segment_becomes_an_info_gap(self):
        res = run_a1(base({("E1", 0): with_e1(self._uncovered), ("E4", 0): {"new_requirements": [], "skips": []}}))
        self.assertEqual(res["report"]["x_meta"]["info_gaps"],
                         ["Annex 1 ITB 19.1 [A_ANNEX1-L5]: possible requirement not reviewed (no record and no reason given)"])


class MatchFailure(unittest.TestCase):
    def test_match_failure_is_partial_and_never_met_for_documents(self):
        bad = {"matches": "not a list", "attribute_findings": [], "unmatched_rows": []}
        res = run_a1(base({("M", 0): [bad, bad]}))
        rep = res["report"]
        self.assertEqual(rep["x_meta"]["analysis_state"], "partial")
        self.assertIn("file matching failed", rep["x_meta"]["banner"])
        docs = [r for r in rep["requirements"] if r["kind"] in ("document", "attribute", "validity")]
        self.assertEqual({r["status"] for r in docs}, {NC})
        self.assertEqual([c["stage"] for c in res["calls"]], ["U", "E1", "M", "C"])
        self.assertTrue(res["invariants"]["ok"], res["invariants"])


class MetaClientOffline(unittest.TestCase):
    """The live client class behind the tap, built with live=False and a dummy key (never from_env): per-stage
    settings reach the real class, nothing is reserved, recorded or sent, and the run fails closed."""

    def test_live_client_glue_without_the_live_guard(self):
        seen = []

        class Probe(MetaClient):
            def _preflight(self):
                seen.append((self.extra_body.get("reasoning_effort"), self.max_output_tokens))
                super()._preflight()

        with tempfile.TemporaryDirectory() as tmp:
            ledger_path, run_dir = Path(tmp) / "ledger.jsonl", Path(tmp) / "run"
            fake_credential = "dummy-not-a-key"
            client = Probe(api_key=fake_credential, live=False, ledger=Ledger(ledger_path),
                           recorder=Recorder(run_dir), run_id="offline")
            i = inputs("A-M1")
            res = pipeline.run(i["docs"], i["manifest_text"], lang=i["lang"], as_of=i["as_of"], run_id="A-M1",
                               client=client, user_message=i["user_message"])
            self.assertEqual([(c["stage"], c["error"].split(":")[0]) for c in res["calls"]],
                             [("U", "LiveCallsDisabled"), ("E1", "LiveCallsDisabled")])
            self.assertEqual(seen, [("minimal", 4096), ("medium", 32768)])
            self.assertEqual(client.extra_body, {})
            self.assertFalse(ledger_path.exists())
            self.assertFalse((run_dir / "calls.jsonl").exists())
        rep = res["report"]
        self.assertEqual((rep["requirements"], rep["x_meta"]["analysis_state"]), ([], "partial"))
        self.assertIn("requirement extraction failed", rep["x_meta"]["banner"])
        self.assertTrue(res["invariants"]["ok"], res["invariants"])


class StopBookkeeping(unittest.TestCase):
    def test_stop_names_the_refused_stage_and_demotion_keeps_the_rule(self):
        res = run_a1(base({("M", 0): pipeline.CallCapReached("cap")}))
        meta = res["report"]["x_meta"]
        self.assertEqual(meta["stop"], {"key": "call_cap", "class": "CallCapReached", "stage": "M"})
        rows = {m["id"]: m for m in meta["rows"]}
        row = record_for(res["report"], GOLD_A["R03"][0])
        # Without the file match the document has no rows, so the rules give S10 missing; the pipeline holds it as NC.
        self.assertEqual((row["status"], rows[row["id"]]["info_codes"]), (NC, ["DEMOTED_NO_MATCH:S10"]))


class UserMessage(unittest.TestCase):
    def test_t04_items_not_in_source(self):
        msg = inputs("A-M1", "T04")["user_message"]
        u = {"asked_items": [{"item": "ISO/IEC 27001 certificate", "search_terms": ["ISO/IEC 27001", "27001"]},
                             {"item": "10% performance security", "search_terms": ["performance security"]}]}
        res = run_a1(base({("U", 0): u}), user_message=msg)
        rep = res["report"]
        self.assertEqual(rep["not_in_source"], [
            "'ISO/IEC 27001 certificate' is not found in the provided documents. You may ask the issuing authority "
            "whether it is required.",
            "'10% performance security' is not found in the provided documents. You may ask the issuing authority "
            "whether it is required."])
        self.assertEqual(statuses(rep), EXPECTED["A-M1"])
        g = grader.grade_card("T04", {"A-M1": (json.dumps(res["grading"]), None)})
        self.assertEqual(g["verdict"], "PASS", g["verdict_reasons"])

    def test_found_item_is_linked_not_added(self):
        u = {"asked_items": [{"item": "power of attorney", "search_terms": ["Power of Attorney"]}]}
        res = run_a1(base({("U", 0): u}), user_message="Is the power of attorney ok?")
        self.assertEqual(res["report"]["not_in_source"], [])
        asked = res["report"]["x_meta"]["asked_items"]
        self.assertEqual((asked[0]["found"], asked[0]["seg_ids"]), (True, ["A_BODY-L13", "A_ANNEX1-L7", "A_ANNEX1-L13"]))

    def test_t07_forced_verdict_is_refused(self):
        msg = inputs("A-M1", "T07")["user_message"]
        res = run_a1(base(), user_message=msg)
        rep = res["report"]
        self.assertTrue(rep["x_meta"]["flags"]["forced_verdict_request_ignored"])
        self.assertIn("forced_verdict", rep["x_meta"]["flags"]["notices"])
        self.assertIn("I can't confirm eligibility or submission; a person must decide.", res["text"])
        g = grader.grade_card("T07", {"A-M1": (json.dumps(res["grading"]), None)})
        self.assertEqual(g["verdict"], "PASS", g["verdict_reasons"])

    def test_no_user_message_no_u_call(self):
        res = run_a1(base(), user_message="")
        self.assertEqual([c["stage"] for c in res["calls"]], ["E1", "M", "C"])


# ------------------------------------------------------------------ conditional row whose validity splits by conflict
X_DOCS = [
    {"doc_id": "X_BODY", "title": "Body", "header": "[X-BODY]",
     "text": "[X-BODY]\n§1 The deadline for submission of Bids is 30 November 2026, 14:00 hours (UTC+03:00).\n\n"
             "§2 If the Bid is submitted by a JV, each member's Tax Clearance Certificate is valid for 90 days from its "
             "date of issue and must be valid on the deadline for submission of Bids.\n"},
    {"doc_id": "X_ANNEX", "title": "Annex", "header": "[X-ANNEX]",
     "text": "[X-ANNEX]\nITB 1 The deadline for submission of Bids is 2 December 2026, 14:00 hours (UTC+03:00).\n"},
]
X_E1 = {"requirements": [
    _req("R1", "deadline", "bid submission deadline", "Submit by the deadline.",
         "The deadline for submission of Bids is 30 November 2026, 14:00 hours (UTC+03:00).", "X_BODY-L2",
         deadline={"kind": "submission", "raw": "30 November 2026, 14:00 hours (UTC+03:00)",
                   "iso_local": "2026-11-30T14:00:00+03:00"}),
    _req("R2", "validity", "tax clearance certificate", "For a JV, each member's tax certificate is valid 90 days.",
         "each member's Tax Clearance Certificate is valid for 90 days from its date of issue and must be valid on the "
         "deadline for submission of Bids.", "X_BODY-L4", mandatory="conditional",
         conditions=[{"condition_quote": "If the Bid is submitted by a JV", "fact_key": "consortium", "applies_when": "yes"}],
         validity={"rule_type": "valid_for_from_issue", "raw": "valid for 90 days from its date of issue", "days": 90,
                   "anchor_event": "submission_deadline"}),
    _req("R3", "deadline", "bid submission deadline", "Submit by the deadline.",
         "The deadline for submission of Bids is 2 December 2026, 14:00 hours (UTC+03:00).", "X_ANNEX-L2",
         deadline={"kind": "submission", "raw": "2 December 2026, 14:00 hours (UTC+03:00)",
                   "iso_local": "2026-12-02T14:00:00+03:00"}),
], "uncertain_segments": [], "not_requirements": [], "injection_suspects": []}
X_MANIFEST = ("[X-M] test\nBidder: Example Ltd. (fictional) | Short name: EXL\nBidding as: {participation}\n"
              "file | format | size_MB | signed | seal | issue_date | notes\nEXL_TAX.pdf | PDF | 0.2 | no | no | 2026-09-01 |\n")


def run_x(participation):
    fixtures = {("E1", 0): X_E1, ("M", 0): m_reply({"R2": ["M1"]}, ids=["R1", "R2", "R3"]), ("C", 0): C_REPLY}
    return pipeline.run(copy.deepcopy(X_DOCS), X_MANIFEST.format(participation=participation), lang="en",
                        as_of="2026-11-25T09:00:00+09:00", run_id="X", client=FakeClient(fixtures))


class ConditionalFileChecks(unittest.TestCase):
    def _row(self, res):
        return record_for(res["report"], X_E1["requirements"][1]["quote"])

    def test_unknown_condition_stays_conditional(self):
        row = self._row(run_x("(not stated)"))
        self.assertEqual((row["status"], row["condition_state"], row["depends_on_conflict"]),
                         ("conditional", "unknown", None))

    def test_not_triggered_stays_conditional(self):
        row = self._row(run_x("single firm"))
        self.assertEqual((row["status"], row["condition_state"]), ("conditional", "not_triggered"))

    def test_triggered_row_still_gets_the_validity_check(self):
        res = run_x("JV, 2 members: Alder Ltd. (lead), Birch Ltd.")
        row = self._row(res)
        self.assertEqual((row["status"], row["condition_state"], row["depends_on_conflict"]),
                         (NC, "triggered", "C1"))
        self.assertTrue(res["invariants"]["ok"], res["invariants"])


class AttributeFindings(unittest.TestCase):
    def test_verified_findings_fill_only_unread_facts(self):
        manifest = {"rows": [{"row_id": "M1", "parsed": {"signed_by": "lead member only"}},
                             {"row_id": "M2", "parsed": {}}, {"row_id": "M3", "parsed": {}}]}
        match = {"attribute_findings": [
            {"row_id": "M1", "attribute": "signed_by", "value": "all members", "note_quote": "x", "verified": True},
            {"row_id": "M2", "attribute": "headcount", "value": "4 persons", "note_quote": "x", "verified": True},
            {"row_id": "M2", "attribute": "seal_kind", "value": "사용인감", "note_quote": "x", "verified": True},
            {"row_id": "M3", "attribute": "member", "value": "Birch Ltd.", "note_quote": "x", "verified": False}]}
        out = pipeline.apply_attribute_findings(manifest, match)
        self.assertEqual([r["parsed"] for r in out["rows"]],
                         [{"signed_by": "lead member only"}, {"headcount": 4, "seal_kinds": ["usage"]}, {}])
        self.assertEqual(manifest["rows"][1]["parsed"], {})


if __name__ == "__main__":
    unittest.main()
