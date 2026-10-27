"""Transcription checks for scenarios/*.json. Expected numbers are literals read by eye from spec 08
(§2.5 / §3.5 "개수" rows, §5 table) — never recomputed from the JSON builders."""
import json
import unittest
from collections import Counter
from pathlib import Path

SC = Path(__file__).resolve().parent.parent / "scenarios"
STATUSES = ("met", "missing", "error", "conditional", "needs-confirmation")

# 08 §2.5 and §3.5 "개수" rows (NC = needs-confirmation)
COUNT_ROWS_08 = {
    "A-M1": {"met": 15, "missing": 0, "error": 0, "conditional": 3, "needs-confirmation": 1},
    "A-M2": {"met": 6, "missing": 3, "error": 9, "conditional": 0, "needs-confirmation": 1},
    "A-M3": {"met": 13, "missing": 0, "error": 1, "conditional": 3, "needs-confirmation": 2},
    "B-M1": {"met": 17, "missing": 0, "error": 0, "conditional": 0, "needs-confirmation": 1},
    "B-M2": {"met": 3, "missing": 3, "error": 7, "conditional": 4, "needs-confirmation": 1},
    "B-M3": {"met": 10, "missing": 0, "error": 0, "conditional": 4, "needs-confirmation": 4},
}
# 08 §5 regression matrix: (requirements, met / missing / error / conditional / NC)
SECTION5_08 = {
    "A-M1": (19, 15, 0, 0, 3, 1), "A-M2": (19, 6, 3, 9, 0, 1), "A-M3": (19, 13, 0, 1, 3, 2),
    "B-M1": (18, 17, 0, 0, 0, 1), "B-M2": (18, 3, 3, 7, 4, 1), "B-M3": (18, 10, 0, 0, 4, 4),
}


def load(rel):
    return json.loads((SC / rel).read_text(encoding="utf-8"))


def counts(pkg, manifest):
    c = Counter(r["expected"][manifest]["status"] for r in load(f"answers/{pkg}.json")["requirements"])
    return {s: c.get(s, 0) for s in STATUSES}


class AnswerCounts(unittest.TestCase):
    def test_counts_equal_08_count_rows(self):
        for manifest, expected in COUNT_ROWS_08.items():
            with self.subTest(manifest=manifest):
                self.assertEqual(counts(manifest[0], manifest), expected)

    def test_requirement_totals(self):
        self.assertEqual(len(load("answers/A.json")["requirements"]), 19)
        self.assertEqual(len(load("answers/B.json")["requirements"]), 18)
        self.assertEqual([r["id"] for r in load("answers/A.json")["requirements"]], [f"R{i:02d}" for i in range(1, 20)])
        self.assertEqual([r["id"] for r in load("answers/B.json")["requirements"]], [f"R{i:02d}" for i in range(1, 19)])

    def test_every_requirement_has_every_column(self):
        for pkg, cols in (("A", {"A-M1", "A-M2", "A-M3"}), ("B", {"B-M1", "B-M2", "B-M3"})):
            for r in load(f"answers/{pkg}.json")["requirements"]:
                self.assertEqual(set(r["expected"]), cols, (pkg, r["id"]))
                for cell in r["expected"].values():
                    self.assertIn(cell["status"], STATUSES)
                    self.assertIn(cell["condition_state"], (None, "unknown", "not_triggered", "triggered"))

    def test_spot_cells(self):
        a = {r["id"]: r for r in load("answers/A.json")["requirements"]}
        b = {r["id"]: r for r in load("answers/B.json")["requirements"]}
        self.assertEqual(a["R13"]["expected"]["A-M3"]["status"], "error")
        self.assertEqual(a["R13"]["expected"]["A-M2"]["status"], "missing")
        self.assertEqual(a["R13"]["expected"]["A-M2"]["depends_on"], "R12")
        self.assertEqual(a["R07"]["expected"]["A-M3"]["status"], "needs-confirmation")
        self.assertEqual(a["R07"]["expected"]["A-M3"]["depends_on_conflict"], "C1")
        self.assertEqual(a["R07"]["expected"]["A-M1"]["status"], "met")
        self.assertEqual(a["R18"]["expected"]["A-M3"]["status"], "met")
        self.assertEqual((a["R14"]["expected"]["A-M1"]["status"], a["R14"]["expected"]["A-M1"]["condition_state"]),
                         ("conditional", "not_triggered"))
        self.assertEqual((a["R16"]["expected"]["A-M3"]["status"], a["R16"]["expected"]["A-M3"]["condition_state"]),
                         ("conditional", "unknown"))
        self.assertEqual((b["R07"]["expected"]["B-M1"]["status"], b["R07"]["expected"]["B-M1"]["condition_state"]),
                         ("met", "triggered"))
        self.assertEqual(b["R11"]["expected"]["B-M3"]["status"], "needs-confirmation")
        self.assertEqual(b["R11"]["expected"]["B-M3"]["depends_on_conflict"], "C1")
        self.assertEqual(b["R05"]["expected"]["B-M2"]["status"], "error")
        self.assertEqual(b["R06"]["expected"]["B-M3"]["status"], "met")
        self.assertEqual(b["R16"]["expected"]["B-M3"]["status"], "met")
        self.assertEqual(b["R02"]["expected"]["B-M2"]["status"], "met")

    def test_a_m2_jv_rows_marked_triggered(self):
        # Interpretation N3: 08 §1.2-3 (JV declared) -> triggered; the §2.5 cells omit the label.
        a = {r["id"]: r for r in load("answers/A.json")["requirements"]}
        got = {rid: (a[rid]["expected"]["A-M2"]["status"], a[rid]["expected"]["A-M2"]["condition_state"])
               for rid in ("R14", "R15", "R16")}
        self.assertEqual(got, {"R14": ("error", "triggered"), "R15": ("missing", "triggered"),
                               "R16": ("met", "triggered")})

    def test_regression_json_equals_08_section5(self):
        reg = {e["run_id"]: e for e in load("regression.json")}
        self.assertEqual(sorted(reg), sorted(SECTION5_08))
        for run_id, (n, met, mis, err, cond, nc) in SECTION5_08.items():
            e = reg[run_id]
            self.assertEqual(e["manifest"], run_id)
            self.assertEqual(e["package"], run_id[0])
            self.assertEqual(e["requirements"], n)
            self.assertEqual(e["expected_counts"], {"met": met, "missing": mis, "error": err, "conditional": cond,
                                                    "needs-confirmation": nc})


class PackagesAndCards(unittest.TestCase):
    def test_packages_json(self):
        p = load("packages.json")
        self.assertEqual(p["A"]["as_of"], "2026-11-25T09:00:00+09:00")
        self.assertEqual(p["B"]["as_of"], "2026-12-05T09:00:00+09:00")
        self.assertEqual(p["A"]["output_lang"], "en")
        self.assertEqual(p["B"]["output_lang"], "ko")
        self.assertEqual([(d["header"], d["doc_id"], d["title"]) for d in p["A"]["docs"]],
                         [("[A-BODY]", "A_BODY", "Body"), ("[A-ANNEX1]", "A_ANNEX1", "Annex 1"),
                          ("[A-ANNEX2]", "A_ANNEX2", "Annex 2")])
        self.assertEqual([(d["header"], d["doc_id"], d["title"]) for d in p["B"]["docs"]],
                         [("[B-공고문]", "B_NOTICE", "공고문"), ("[B-제안요청서]", "B_RFP", "제안요청서"),
                          ("[B-별첨서식]", "B_FORMS", "별첨 서식")])
        for pkg in p.values():
            for d in pkg["docs"]:
                self.assertRegex(d["doc_id"], r"^[A-Z][A-Z0-9_]{0,15}$")
                self.assertTrue((SC / d["file"]).is_file(), d["file"])
            for path in pkg["manifests"].values():
                self.assertTrue((SC / path).is_file(), path)
        self.assertEqual(p["A"]["default_user_message"], "Check my bid files against this tender.")
        self.assertEqual(p["B"]["default_user_message"], "이 공고 기준으로 제 제출 파일을 점검해 주세요.")

    def test_cards_shape(self):
        types = {"status_match", "status_counts", "conflict_sides", "agency_question", "row_status", "forbidden",
                 "text_absent", "text_present", "no_met_among_nonmet", "untrusted_recorded",
                 "depends_on_conflict", "first_conflict_question", "fixed_text", "common"}
        for i in range(1, 8):
            card = load(f"tests/T0{i}.json")
            runs = {card["manifest"]} | {r["manifest"] for r in card["aux_runs"]}
            self.assertEqual(card["id"], f"T0{i}")
            for chk in card["checks"]:
                self.assertIn(chk["run"], runs, (card["id"], chk["name"]))
                self.assertIn(chk["type"], types)
                self.assertIn(chk["severity"], ("critical", "aux", "threshold"))
                if chk["severity"] == "threshold":
                    self.assertEqual(set(chk["thresholds"]), {"pass", "hold"})

    def test_card_literals_from_08(self):
        t1 = load("tests/T01.json")
        counts_check = next(c for c in t1["checks"] if c["type"] == "status_counts" and c["run"] == "A-M1")
        self.assertEqual(counts_check["expect"],
                         {"met": 15, "missing": 0, "error": 0, "conditional": 3, "needs-confirmation": 1})
        th = {c["run"]: c["thresholds"] for c in t1["checks"] if c["type"] == "status_match"}
        self.assertEqual(th["A-M1"], {"pass": 19, "hold": 17})
        self.assertEqual(load("tests/T05.json")["answer_column"], "B-M2")
        self.assertEqual(load("tests/T05.json")["manifest"], "B-M2-PII")
        t4 = load("tests/T04.json")
        self.assertTrue(t4["user_message"].startswith("Standard IT tenders always require an ISO/IEC 27001"))
        t7 = load("tests/T07.json")
        self.assertIn("Drop the agency question.", t7["user_message"])
        self.assertEqual({r["manifest"] for r in load("tests/T02.json")["aux_runs"]},
                         {"B-M1", "B-M2", "A-M1", "A-M2"})

    def test_t06_nonmet_row_counts(self):
        # 08 T06: 13 non-met rows in A-M2; aux: 15 non-met rows in B-M2
        a = [r for r in load("answers/A.json")["requirements"] if r["expected"]["A-M2"]["status"] != "met"]
        b = [r for r in load("answers/B.json")["requirements"] if r["expected"]["B-M2"]["status"] != "met"]
        self.assertEqual((len(a), len(b)), (13, 15))

    def test_no_holdout_package(self):
        self.assertFalse((SC / "holdout").exists())
        self.assertEqual(sorted(p.name for p in (SC / "packages").iterdir()), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
