"""S0-S15 (design 09 §5, spec 08 §1.2) on hand-built VReq / FileCheck / Match dicts.
Expected statuses are the 08 §2.5 / §3.5 cells named in each test (hand-derived in 08), not
recomputed here; times are the 08 §2.5 hand calculation (14:30 UTC+03:00, 20:00 KST)."""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine import rules  # noqa: E402

NC = "needs-confirmation"
BASE_REQ = {
    "tmp_id": "R1", "category": "document", "subject": "x", "requirement": "x", "quote": "q",
    "location": {"doc_id": "D", "seg_id": "D-L1"}, "mandatory": "mandatory", "conditions": [], "attribute_of": "",
    "doc_code": "", "quantity_rule": "none", "member_rule": {"max_members": 0, "lead_share_min_pct": 0},
    "amount": {"currency": "", "value": 0, "ratio_pct": 0},
    "deadline": {"kind": "none", "raw": "", "iso_local": "", "tz_quote": ""},
    "format": {"extensions": [], "max_size_mb": 0, "filename_template": "", "merge_single_pdf": "not_stated", "merge_scope": []},
    "sign_seal": "not_stated", "copy_type": "not_stated", "notarized": "not_stated",
    "validity": {"rule_type": "none", "raw": "", "months": 0, "days": 0, "until_iso": "", "anchor_event": "not_stated"},
}
OK_CHECK = {"tmp_id": "R1", "verified": True, "grounded": True, "remove": False, "reason_codes": [], "info_codes": [],
            "quote": "q", "location": "Doc 1.", "seg_id": "D-L1", "deadline": None}
JV = [{"condition_quote": "If the Bid is submitted by a JV", "fact_key": "consortium", "applies_when": "yes"}]
USAGE = [{"condition_quote": "사용인감을 날인하는 경우에 한하여", "fact_key": "usage_seal", "applies_when": "yes"}]
PARSED_S5 = {"ok": True, "date": "2026-11-30", "time": "14:00", "offset": "+03:00", "iso_local": "2026-11-30T14:00:00+03:00"}
PARSED_B5GA = {"ok": True, "date": "2026-12-09", "time": "10:00", "offset": "", "iso_local": "2026-12-09T10:00"}


def vr(check=None, **req_fields):
    r = copy.deepcopy(BASE_REQ)
    r.update(req_fields)
    c = copy.deepcopy(OK_CHECK)
    c.update(check or {})
    c["tmp_id"] = r["tmp_id"]
    return {"req": r, "check": c}


def fc(*checks, **extra):
    out = {"checks": [{"id": i, "result": res, "detail": ""} for i, res in checks], "quantity_short": False,
           "unknown_fields": [], "reason_codes": [], "readings": {}}
    out.update(extra)
    return out


def rows(*ids, hint="no_concern", tmp="R1"):
    return {"by_req": {tmp: {"row_ids": list(ids), "status_hint": hint, "hint_reason": "", "rationale": ""}}}


def facts(**kw):
    base = {"consortium": "unknown", "usage_seal": "unknown", "foreign_entity": "unknown", "subcontracting": "unknown",
            "sme": "unknown"}
    base.update(kw)
    return {"facts": base, "planned_upload": ""}


def decide(v, **kw):
    kw.setdefault("as_of", "2026-11-25T09:00:00+09:00")
    return rules.decide(v, **kw)


def sc(d):
    return (d["status"], d["condition_state"])


class VerificationFirst(unittest.TestCase):
    def test_s1_unverified_quote(self):
        d = decide(vr({"verified": False}), file_check=fc(("F5", "pass")), match=rows("M1"))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S1", NC, ["UNVERIFIED_QUOTE"]))

    def test_s2_field_not_grounded(self):
        d = decide(vr({"grounded": False, "reason_codes": ["FIELD_NOT_GROUNDED", "DATE_MISMATCH"]}), match=rows("M1"))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S2", NC, ["FIELD_NOT_GROUNDED", "DATE_MISMATCH"]))

    def test_s1_before_conditions(self):
        d = decide(vr({"verified": False}, conditions=JV), profile=facts(consortium="no"))
        self.assertEqual((d["rule"], d["status"], d["condition_state"]), ("S1", NC, "not_triggered"))


class Conflicts(unittest.TestCase):
    def test_s3_conflict_row_is_nc_even_when_upload_is_before_both(self):
        # 08 §2.5 R01 A-M1: upload 2026-11-27 10:00 UTC+03:00 is before both candidates, still NC (08 D5).
        v = vr({"deadline": PARSED_S5}, category="deadline",
               deadline={"kind": "submission", "raw": "30 November 2026", "iso_local": "", "tz_quote": ""})
        d = decide(v, conflict_info={"conflict_id": "C1"},
                   profile={"facts": {}, "planned_upload": "2026-11-27T10:00:00+03:00"})
        self.assertEqual((d["rule"], d["status"], d["reason_codes"], d["depends_on_conflict"]), ("S3", NC, ["CONFLICT"], "C1"))

    def test_s4_split_by_conflict(self):
        # 08 §2.5 R07 A-M3: valid through 11-30, fails if the deadline is 12-02 -> NC, depends_on_conflict C1.
        v = vr(category="validity")
        d = decide(v, file_check=fc(("F7", "unknown"), reason_codes=["CONFLICT_DEPENDENT"]), match=rows("M4"),
                   conflict_info={"depends_on_conflict": "C1"})
        self.assertEqual((d["rule"], d["status"], d["reason_codes"], d["depends_on_conflict"]),
                         ("S4", NC, ["CONFLICT_DEPENDENT"], "C1"))

    def test_s4_with_counting(self):
        # 08 §3.5 R11 B-M3: depends on C1 and on the counting convention -> NC, agency question Q-B2.
        d = decide(vr(category="validity"), file_check=fc(("F7", "unknown"), reason_codes=["CONFLICT_DEPENDENT", "COUNTING_AMBIGUOUS"]),
                   match=rows("M9"), conflict_info={"depends_on_conflict": "C1"})
        self.assertEqual((d["status"], d["reason_codes"], d["agency_question_keys"]),
                         (NC, ["CONFLICT_DEPENDENT", "COUNTING_AMBIGUOUS"], ["counting"]))

    def test_s4_prime_same_under_both_readings(self):
        # 08 §2.5 R07 A-M1 (met) and A-M2 (error): the same under both readings of C1.
        met = decide(vr(category="validity"), file_check=fc(("F7", "pass"), reason_codes=["SAME_UNDER_ALL_READINGS"]),
                     match=rows("M4"), conflict_info={"depends_on_conflict": "C1"})
        self.assertEqual((met["status"], met["reason_codes"], met["depends_on_conflict"]),
                         ("met", ["ALL_CHECKS_PASSED", "SAME_UNDER_ALL_READINGS"], "C1"))
        err = decide(vr(category="validity"), file_check=fc(("F7", "fail"), reason_codes=["SAME_UNDER_ALL_READINGS"]),
                     match=rows("M4"), conflict_info={"depends_on_conflict": "C1"})
        self.assertEqual((err["status"], err["reason_codes"]), ("error", ["CHECK_FAILED:F7", "SAME_UNDER_ALL_READINGS"]))


class ConditionalRows(unittest.TestCase):
    def test_s5_unknown_fact_is_conditional_not_missing(self):
        # 08 T02 critical: B-M3 R12/R13/R14 conditional + unknown, never missing; R07 likewise for the seal.
        for conds, key in ((JV, "fact:consortium"), (USAGE, "fact:usage_seal")):
            with self.subTest(key=key):
                d = decide(vr(conditions=conds, mandatory="conditional"), profile=facts())
                self.assertEqual((d["rule"], sc(d), d["reason_codes"], d["bidder_question_keys"]),
                                 ("S5", ("conditional", "unknown"), ["CONDITION_UNKNOWN"], [key]))

    def test_s6_not_triggered(self):
        # 08 §2.5 R14-R16 A-M1 (single firm), §3.5 R07 B-M2 (corporate seal).
        d = decide(vr(conditions=JV, mandatory="conditional"), profile=facts(consortium="no"))
        self.assertEqual((d["rule"], sc(d), d["reason_codes"]), ("S6", ("conditional", "not_triggered"), ["NOT_TRIGGERED"]))

    def test_triggered_rows_use_the_lower_rules(self):
        p = facts(consortium="yes")
        met = decide(vr(conditions=JV), profile=p, file_check=fc(("F10", "pass")), match=rows("M10"))
        self.assertEqual(sc(met), ("met", "triggered"))                       # 08 §3.5 R12 B-M1
        err = decide(vr(conditions=JV), profile=p, file_check=fc(("F10", "fail")), match=rows("M10"))
        self.assertEqual(sc(err), ("error", "triggered"))                     # 08 §2.5 R14 A-M2
        short = decide(vr(conditions=JV, quantity_rule="per_member"), profile=p,
                       file_check=fc(("F12", "fail"), quantity_short=True), match=rows("M11"))
        self.assertEqual(sc(short), ("missing", "triggered"))                # 08 §2.5 R15 A-M2
        size = decide(vr(conditions=JV, category="other"), profile=p, file_check=fc(("F14", "pass")))
        self.assertEqual(sc(size), ("met", "triggered"))                      # 08 §2.5 R16 A-M2

    def test_conditional_without_condition_records(self):
        d = decide(vr(mandatory="conditional"), profile=facts())
        self.assertEqual((sc(d), d["bidder_question_keys"]), (("conditional", "unknown"), ["fact:other"]))

    def test_attribute_of_unknown_parent_condition(self):
        parent = {"status": "conditional", "condition_state": "unknown", "reason_codes": ["CONDITION_UNKNOWN"],
                  "bidder_question_keys": ["fact:consortium"]}
        d = decide(vr(attribute_of="R7"), parent=parent)
        self.assertEqual((sc(d), d["depends_on"], d["bidder_question_keys"]),
                         (("conditional", "unknown"), "R7", ["fact:consortium"]))


class MandatoryFlags(unittest.TestCase):
    def test_s7_unclear(self):
        d = decide(vr(mandatory="unclear"), match=rows("M1"), file_check=fc(("F1", "pass")))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S7", NC, ["MANDATORY_UNCLEAR"]))

    def test_s7b_optional_without_file(self):
        d = decide(vr(mandatory="optional"))
        self.assertEqual((d["rule"], sc(d), d["reason_codes"]), ("S7b", ("conditional", "unknown"), ["OPTIONAL_ITEM"]))

    def test_optional_with_file(self):
        d = decide(vr(mandatory="optional"), match=rows("M1"), file_check=fc(("F1", "pass")))
        self.assertEqual(sc(d), ("met", None))


class Deadlines(unittest.TestCase):
    def deadline(self, parsed=PARSED_S5, kind="submission"):
        return vr({"deadline": parsed}, category="deadline",
                  deadline={"kind": kind, "raw": "30 November 2026, 14:00 hours", "iso_local": "", "tz_quote": ""})

    def test_s8_upload_after(self):
        # 08 §2.5 R01 M3 hand calculation: 2026-11-30 20:30 KST = 14:30 UTC+03:00, after 14:00; 14:00 = 20:00 KST.
        d = decide(self.deadline(), profile={"facts": {}, "planned_upload": "2026-11-30T20:30:00+09:00"})
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S8", "error", ["UPLOAD_AFTER_DEADLINE"]))
        self.assertEqual(d["timing"]["upload_in_deadline_tz"], "2026-11-30T14:30:00+03:00")
        self.assertEqual(d["timing"]["deadline_in_upload_tz"], "2026-11-30T20:00:00+09:00")

    def test_s8_upload_before(self):
        d = decide(self.deadline(), profile={"facts": {}, "planned_upload": "2026-11-27T10:00:00+03:00"})
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S8", "met", ["UPLOAD_BEFORE_DEADLINE"]))

    def test_s8_deadline_without_zone_is_not_compared(self):
        d = decide(self.deadline(PARSED_B5GA), profile={"facts": {}, "planned_upload": "2026-12-08T15:00:00+09:00"})
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S8", NC, ["ACTION_PENDING"]))

    def test_s9_no_upload(self):
        d = decide(self.deadline(), profile=facts())
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S9", NC, ["ACTION_PENDING"]))

    def test_s9_deadline_passed(self):
        d = decide(self.deadline(), profile=facts(), as_of="2026-12-01T09:00:00+09:00")
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S9", "error", ["DEADLINE_PASSED"]))

    def test_submission_method_is_not_missing(self):
        d = decide(vr(category="submission_method"), profile=facts())
        self.assertEqual((d["rule"], d["status"]), ("S9", NC))


class Documents(unittest.TestCase):
    def test_s10_missing_document(self):
        # 08 §2.5 R12 A-M2: no Bid Security file.
        d = decide(vr(), match={"by_req": {}})
        self.assertEqual((d["rule"], d["status"], d["reason_codes"], d["evidence_row_ids"]),
                         ("S10", "missing", ["NO_MATCHING_FILE"], []))

    def test_s11_parent_missing(self):
        # 08 §2.5 R13 A-M2: missing, depends_on R12.
        v = vr(tmp_id="R13", attribute_of="R12", category="validity")
        d = decide(v, match={"by_req": {"R12": {"row_ids": []}, "R13": {"row_ids": []}}})
        self.assertEqual((d["rule"], d["status"], d["reason_codes"], d["depends_on"]),
                         ("S11", "missing", ["PARENT_MISSING"], "R12"))
        parent = {"status": "missing", "reason_codes": ["NO_MATCHING_FILE"]}
        self.assertEqual(decide(v, parent=parent)["rule"], "S11")

    def test_attribute_with_parent_present_uses_checks(self):
        # 08 §2.5 R13 A-M3: guarantee valid until 2027-04-27 < 2027-04-28 -> error.
        v = vr(tmp_id="R13", attribute_of="R12", category="validity")
        d = decide(v, match={"by_req": {"R12": {"row_ids": ["M8"]}}}, file_check=fc(("F7", "fail")))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"], d["evidence_row_ids"]),
                         ("S13", "error", ["CHECK_FAILED:F7"], ["M8"]))

    def test_s12_quantity_short(self):
        # 08 §3.5 R09 B-M2: 4 staff, 3 signatures -> missing.
        d = decide(vr(quantity_rule="per_person"), match=rows("M5"), file_check=fc(("F13", "fail"), ("F2", "fail")))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S12", "missing", ["QUANTITY_SHORT"]))

    def test_s13_errors(self):
        d = decide(vr(category="file_format"), match=rows("M1"), file_check=fc(("F2", "fail"), ("F3", "fail")))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S13", "error", ["CHECK_FAILED:F2", "CHECK_FAILED:F3"]))
        d = decide(vr(category="merge_rule"), match=rows("M1"), file_check=fc(("F4", "fail")))
        self.assertEqual(d["reason_codes"], ["NOT_MERGED"])

    def test_f8_never_fails_a_row(self):
        d = decide(vr(), match=rows("M1"), file_check=fc(("F8", "fail"), ("F1", "pass"),
                                                          reason_codes=["ISSUE_DATE_AFTER_AS_OF"]))
        self.assertEqual((d["status"], d["info_codes"]), ("met", ["ISSUE_DATE_AFTER_AS_OF"]))

    def test_s14_missing_values(self):
        # 08 §3.5 R05 B-M3 (issue date unknown) and R09 B-M3 (headcount not stated) -> NC + bidder question.
        d = decide(vr(category="validity"), match=rows("M3"), file_check=fc(("F7", "unknown"), unknown_fields=["issue_date"]))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"], d["bidder_question_keys"]),
                         ("S14", NC, ["ATTRIBUTE_UNKNOWN:issue_date"], ["attr:issue_date"]))
        d = decide(vr(quantity_rule="per_person"), match=rows("M6"), file_check=fc(("F13", "unknown")))
        self.assertEqual((d["reason_codes"], d["bidder_question_keys"]), (["ATTRIBUTE_UNKNOWN:headcount"], ["attr:headcount"]))

    def test_s14_rule_side_gap_has_no_bidder_question(self):
        d = decide(vr(category="validity"), match=rows("M3"),
                   file_check=fc(("F7", "unknown"), reason_codes=["ATTRIBUTE_UNKNOWN:anchor_date"]))
        self.assertEqual((d["status"], d["reason_codes"], d["bidder_question_keys"]), (NC, ["ATTRIBUTE_UNKNOWN:anchor_date"], []))

    def test_s14_counting_without_conflict(self):
        d = decide(vr(category="validity"), match=rows("M3"), file_check=fc(("F7", "unknown"), reason_codes=["COUNTING_AMBIGUOUS"]))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"], d["agency_question_keys"], d["bidder_question_keys"]),
                         ("S14", NC, ["COUNTING_AMBIGUOUS"], ["counting"], []))

    def test_no_evidence_is_never_met(self):
        d = decide(vr(category="other"))
        self.assertEqual((d["status"], d["reason_codes"]), (NC, ["ATTRIBUTE_UNKNOWN:evidence"]))

    def test_file_rule_without_rows_but_passing_check(self):
        d = decide(vr(category="file_format"), file_check=fc(("F1", "pass")))
        self.assertEqual((d["rule"], d["status"]), ("S15", "met"))


class ModelHint(unittest.TestCase):
    def test_s15_met(self):
        d = decide(vr(), match=rows("M1"), file_check=fc(("F5", "pass")))
        self.assertEqual((d["rule"], d["status"], d["reason_codes"], d["evidence_row_ids"]),
                         ("S15", "met", ["ALL_CHECKS_PASSED"], ["M1"]))

    def test_hint_moves_met_to_nc_only(self):
        d = decide(vr(), match=rows("M1", hint=NC), file_check=fc(("F5", "pass")))
        self.assertEqual((d["status"], d["reason_codes"]), (NC, ["MODEL_FLAGGED"]))
        cases = [
            (vr(), {"by_req": {"R1": {"row_ids": [], "status_hint": NC}}}, None, "missing"),
            (vr(), rows("M1", hint=NC), fc(("F2", "fail")), "error"),
            (vr(conditions=JV), rows("M1", hint=NC), None, "conditional"),
        ]
        for v, m, f, want in cases:
            with self.subTest(want=want):
                self.assertEqual(decide(v, match=m, file_check=f, profile=facts(consortium="no"))["status"], want)

    def test_period_rule_without_a_document_is_not_missing(self):
        # Live K=3 2026-10-28: "Bids shall remain valid until 31 March 2027" (the bid's own validity) read
        # `missing` in 8 of 9 A runs, telling the bidder to add a file that does not exist. A period rule tied to
        # no document and no listed file cannot be checked from the list.
        for subject in ("bid validity", "Bid", "bids"):
            with self.subTest(subject=subject):
                d = decide(vr(category="validity", subject=subject), match=rows())
                self.assertEqual((d["rule"], d["status"], d["reason_codes"]),
                                 ("S14", NC, ["ATTRIBUTE_UNKNOWN:evidence"]))
        # A document written as its validity ("법인인감증명서: … 최근 3개월 이내 발급분", 08 B R06 in B-M2) is
        # still a file to add; so is a document whose subject merely starts with "bid".
        for subject in ("corporate seal certificate", "bid security"):
            with self.subTest(subject=subject):
                d = decide(vr(category="validity", subject=subject), match=rows())
                self.assertEqual((d["rule"], d["status"]), ("S10", "missing"))

    def test_hint_does_not_reach_file_rules(self):
        # Live K=3 2026-10-28, B-M3 k1/k2: the doubt "scan top cut off" on one merged document was put on the
        # merge rule. Format, naming and merge rules are decided from file facts; a doubt about a document's
        # content belongs to that document's own record.
        for cat in ("merge_rule", "file_format", "file_naming"):
            with self.subTest(cat=cat):
                d = decide(vr(category=cat), match=rows("M1", hint=NC), file_check=fc(("F4", "pass")))
                self.assertEqual((d["rule"], d["status"], d["reason_codes"]), ("S15", "met", ["ALL_CHECKS_PASSED"]))

    def test_review_flag(self):
        d = decide(vr(), match=rows("M1"), file_check=fc(("F5", "pass")), review_flag=True)
        self.assertEqual((d["status"], d["reason_codes"]), (NC, ["REVIEW_FLAG"]))

    def test_match_entry_form(self):
        d = decide(vr(), match={"row_ids": ["M1"], "status_hint": "no_concern"}, file_check=fc(("F5", "pass")))
        self.assertEqual(d["status"], "met")


class Kinds(unittest.TestCase):
    def test_kind_mapping_on_08_rows(self):
        cases = [  # 08 §2.4 `구분` column
            ({"category": "deadline"}, "deadline"),                                  # R01
            ({"category": "document"}, "document"),                                  # R02
            ({"category": "signature_seal", "attribute_of": "R3"}, "attribute"),      # R04
            ({"category": "validity"}, "validity"),                                  # R07
            ({"category": "merge_rule"}, "file_rule"),                               # R09
            ({"category": "document", "conditions": JV}, "conditional"),              # R14
            ({"category": "file_format"}, "file_rule"),                              # R17
            ({"category": "file_naming"}, "file_rule"),                              # R19
            ({"category": "submission_method"}, "deadline"),
            ({"category": "other"}, "document"),
            ({"category": "validity", "attribute_of": "R12"}, "attribute"),          # R13
        ]
        for fields, want in cases:
            r = copy.deepcopy(BASE_REQ)
            r.update(fields)
            with self.subTest(fields=fields):
                self.assertEqual(rules.kind_of(r), want)

    def test_source_missing(self):
        self.assertTrue(rules.source_missing({"segments": [], "source_missing": True}))
        self.assertFalse(rules.source_missing({"segments": [{"seg_id": "A-L1"}], "source_missing": False}))


class ReasonCodesContract(unittest.TestCase):
    def test_every_emitted_code_is_in_the_contract(self):
        contract = json.loads((REPO / "contracts" / "reason_codes.json").read_text(encoding="utf-8"))["reason_codes"]
        pats = [re.escape(c).replace(r"\{n\}", r"\d+").replace(r"\{field\}", r"[a-z_]+") for c in contract]
        valid = re.compile("^(?:" + "|".join(pats) + ")$")
        dl = {"deadline": PARSED_S5}
        dl_req = {"category": "deadline", "deadline": {"kind": "submission", "raw": "x", "iso_local": "", "tz_quote": ""}}
        calls = [
            (vr({"verified": False}), {}),
            (vr({"grounded": False, "reason_codes": ["DATE_UNPARSED"]}), {}),
            (vr(), {"conflict_info": {"conflict_id": "C1"}}),
            (vr(), {"file_check": fc(("F7", "unknown"), reason_codes=["CONFLICT_DEPENDENT", "COUNTING_AMBIGUOUS"])}),
            (vr(), {"match": rows("M1"), "conflict_info": {"depends_on_conflict": "C1"},
                    "file_check": fc(("F7", "pass"), reason_codes=["SAME_UNDER_ALL_READINGS"])}),
            (vr(conditions=JV), {"profile": facts()}),
            (vr(conditions=JV), {"profile": facts(consortium="no")}),
            (vr(mandatory="unclear"), {}),
            (vr(mandatory="optional"), {}),
            (vr(dl, **dl_req), {"profile": {"planned_upload": "2026-11-30T20:30:00+09:00"}}),
            (vr(dl, **dl_req), {"profile": {"planned_upload": "2026-11-27T10:00:00+03:00"}}),
            (vr(dl, **dl_req), {"as_of": "2026-12-01T09:00:00+09:00"}),
            (vr(dl, **dl_req), {}),
            (vr(), {}),
            (vr(attribute_of="R9"), {"match": {"by_req": {"R9": {"row_ids": []}}}}),
            (vr(), {"match": rows("M1"), "file_check": fc(("F12", "fail"))}),
            (vr(), {"match": rows("M1"), "file_check": fc(("F4", "fail"), ("F11", "fail"))}),
            (vr(), {"match": rows("M1"), "file_check": fc(("F9", "unknown"))}),
            (vr(category="other"), {}),
            (vr(), {"match": rows("M1", hint=NC), "file_check": fc(("F1", "pass"))}),
            (vr(), {"match": rows("M1"), "file_check": fc(("F1", "pass")), "review_flag": True}),
            (vr({"info_codes": ["LOCATION_CORRECTED", "TZ_NOT_STATED", "WEEKDAY_MISMATCH"]}),
             {"match": rows("M1"), "file_check": fc(("F1", "pass"), reason_codes=["ISSUE_DATE_AFTER_AS_OF"])}),
        ]
        seen = set()
        for v, kw in calls:
            d = decide(v, **kw)
            seen.update(d["reason_codes"] + d["info_codes"])
        expected = ["UNVERIFIED_QUOTE", "DATE_UNPARSED", "CONFLICT", "CONFLICT_DEPENDENT", "COUNTING_AMBIGUOUS",
                    "ALL_CHECKS_PASSED", "SAME_UNDER_ALL_READINGS", "CONDITION_UNKNOWN", "NOT_TRIGGERED",
                    "MANDATORY_UNCLEAR", "OPTIONAL_ITEM", "UPLOAD_AFTER_DEADLINE", "UPLOAD_BEFORE_DEADLINE",
                    "DEADLINE_PASSED", "ACTION_PENDING", "NO_MATCHING_FILE", "PARENT_MISSING", "QUANTITY_SHORT",
                    "NOT_MERGED", "CHECK_FAILED:F11", "ATTRIBUTE_UNKNOWN:notarized", "ATTRIBUTE_UNKNOWN:evidence",
                    "MODEL_FLAGGED", "REVIEW_FLAG", "LOCATION_CORRECTED", "TZ_NOT_STATED", "WEEKDAY_MISMATCH",
                    "ISSUE_DATE_AFTER_AS_OF"]
        self.assertEqual(sorted(seen), sorted(expected))
        self.assertEqual(sorted(c for c in seen if not valid.match(c)), [])


if __name__ == "__main__":
    unittest.main()
