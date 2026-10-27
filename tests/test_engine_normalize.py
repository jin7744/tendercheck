"""N1 normalization. Location labels are literals from spec 08 §2.2/§2.4/§3.2/§3.4 and design
09 §2-3; seg_ids are physical line numbers counted by hand on the 08 blocks (header = line 1)."""
import json
import unittest
from pathlib import Path

from test_engine_fixtures import A_BODY_TEXT, bundle_a, bundle_b

from engine.normalize import build_source_bundle, location_label, render_segments, segment_map


class LocationLabelsPackageA(unittest.TestCase):
    def setUp(self):
        self.segs = segment_map(bundle_a())

    def test_08_labels(self):
        expected = {
            "A_BODY-L12": "Body §3(a)",
            "A_BODY-L13": "Body §3(b)",
            "A_BODY-L19": "Body §3(h)",
            "A_BODY-L9": "Body §2",
            "A_BODY-L22": "Body §4(i)",
            "A_BODY-L23": "Body §4(ii)",
            "A_BODY-L25": "Body §5",
            "A_BODY-L27": "Body §6",
            "A_BODY-L29": "Body §7",
            "A_ANNEX1-L6": "Annex 1 ITB 20.2",
            "A_ANNEX1-L7": "Annex 1 ITB 21.2",
            "A_ANNEX1-L8": "Annex 1 ITB 22.1",
            "A_ANNEX1-L9": "Annex 1 ITB 22.3",
            "A_ANNEX1-L10": "Annex 1 ITB 23.1",
            "A_ANNEX1-L12": "Annex 1 Checklist",
            "A_ANNEX1-L13": "Annex 1 Checklist",
            "A_ANNEX2-L4": "Annex 2 Form BF-1",
            "A_ANNEX2-L6": "Annex 2 Form BF-1",
            "A_ANNEX2-L9": "Annex 2 Form BF-3",
        }
        self.assertEqual({k: self.segs[k]["location"] for k in expected}, expected)

    def test_unnumbered_lines_are_not_stated(self):
        self.assertEqual(self.segs["A_BODY-L2"]["location"], "Body (section not stated)")
        self.assertEqual(self.segs["A_ANNEX1-L3"]["location"], "Annex 1 (section not stated)")

    def test_footer_after_blank_line_gets_no_section(self):
        # 09 §2-3: the footer block has no number, so no section is pulled from the block above.
        # 08 writes "Annex 2 footer"; the 08 T06 check only needs "Annex 2".
        self.assertEqual(self.segs["A_ANNEX2-L12"]["location"], "Annex 2 (section not stated)")

    def test_header_line_is_not_a_segment(self):
        self.assertNotIn("A_BODY-L1", self.segs)
        self.assertEqual(bundle_a()["segments"][0]["seg_id"], "A_BODY-L2")

    def test_blocks(self):
        self.assertEqual(self.segs["A_BODY-L11"]["block"], self.segs["A_BODY-L19"]["block"])
        self.assertNotEqual(self.segs["A_BODY-L19"]["block"], self.segs["A_BODY-L21"]["block"])


class LocationLabelsPackageB(unittest.TestCase):
    def setUp(self):
        self.segs = segment_map(bundle_b())

    def test_08_labels(self):
        expected = {
            "B_NOTICE-L13": "공고문 2.나",
            "B_NOTICE-L16": "공고문 3.①",
            "B_NOTICE-L19": "공고문 3.④",
            "B_NOTICE-L20": "공고문 3.⑤",
            "B_NOTICE-L25": "공고문 3.⑩",
            "B_NOTICE-L27": "공고문 3.⑫",
            "B_NOTICE-L32": "공고문 5.가",
            "B_RFP-L4": "제안요청서 Ⅳ-1",
            "B_RFP-L5": "제안요청서 Ⅳ-2",
            "B_RFP-L8": "제안요청서 Ⅳ-5",
            "B_FORMS-L5": "별첨 서식 제3호",
            "B_FORMS-L6": "별첨 서식 제4호",
        }
        self.assertEqual({k: self.segs[k]["location"] for k in expected}, expected)

    def test_roman_numeral_is_u2163(self):
        self.assertEqual(self.segs["B_RFP-L4"]["section"], "Ⅳ-1")

    def test_injected_note_inherits_form_within_block(self):
        # 08 writes "별첨 서식 제4호 하단"; the code-computed label is the form number only.
        self.assertEqual(self.segs["B_FORMS-L7"]["location"], "별첨 서식 제4호")

    def test_unnumbered_lines_ko(self):
        self.assertEqual(self.segs["B_NOTICE-L2"]["location"], "공고문 (위치 미제공)")
        self.assertEqual(self.segs["B_FORMS-L2"]["location"], "별첨 서식 (위치 미제공)")


class Structure(unittest.TestCase):
    def doc(self, text, title="Body", lang="en"):
        b = build_source_bundle([{"doc_id": "X", "title": title, "text": text}], output_lang=lang)
        return [(s["seg_id"], s["location"]) for s in b["segments"]]

    def test_page_markers(self):
        text = "[[page 3]]\n§2 Bids shall be in English.\n(a) Form of Bid\n[[page 4]]\ncontinued text\n"
        self.assertEqual(self.doc(text), [
            ("X-L2", "Body §2 p.3"),
            ("X-L3", "Body §2(a) p.3"),
            ("X-L5", "Body (section not stated) p.4"),
        ])

    def test_orphan_child_tokens(self):
        self.assertEqual(self.doc("(a) orphan item\n"), [("X-L1", "Body (section not stated)")])
        self.assertEqual(self.doc(" ① 고아 항목\n", title="공고문", lang="ko"), [("X-L1", "공고문 (위치 미제공)")])

    def test_child_does_not_cross_blank_line(self):
        self.assertEqual(self.doc("3. 제출서류\n\n ① 신청서\n", title="공고문", lang="ko"),
                         [("X-L1", "공고문 3."), ("X-L3", "공고문 (위치 미제공)")])

    def test_crlf_and_nfc(self):
        b = build_source_bundle([{"doc_id": "X", "title": "T", "text": "가 line\r\n§2 x\r\n"}])
        self.assertEqual([s["text"] for s in b["segments"]], ["가 line", "§2 x"])

    def test_location_label(self):
        self.assertEqual(location_label("별첨 서식", "서식 제3호", 0, "ko"), "별첨 서식 제3호")
        self.assertEqual(location_label("Annex 1", "ITB 23.1", 0, "en"), "Annex 1 ITB 23.1")
        self.assertEqual(location_label("Body", "§5", 2, "en"), "Body §5 p.2")
        self.assertEqual(location_label("제안요청서", "", 0, "ko"), "제안요청서 (위치 미제공)")


class ScenarioFixtureFiles(unittest.TestCase):
    """The same labels when the bundle is built from scenarios/packages.json and its text files."""

    def build(self, key):
        root = Path(__file__).resolve().parent.parent / "scenarios"
        pkg = json.loads((root / "packages.json").read_text(encoding="utf-8"))[key]
        docs = [{**d, "text": (root / d["file"]).read_text(encoding="utf-8")} for d in pkg["docs"]]
        return build_source_bundle(docs, bundle_id=key, output_lang=pkg["output_lang"], as_of=pkg["as_of"])

    def test_files_give_the_08_labels(self):
        a, b = segment_map(self.build("A")), segment_map(self.build("B"))
        got = {k: a[k]["location"] for k in ("A_BODY-L12", "A_BODY-L25", "A_ANNEX1-L10", "A_ANNEX1-L13")}
        got.update({k: b[k]["location"] for k in ("B_NOTICE-L19", "B_NOTICE-L32", "B_RFP-L4", "B_FORMS-L5")})
        self.assertEqual(got, {
            "A_BODY-L12": "Body §3(a)", "A_BODY-L25": "Body §5", "A_ANNEX1-L10": "Annex 1 ITB 23.1",
            "A_ANNEX1-L13": "Annex 1 Checklist", "B_NOTICE-L19": "공고문 3.④", "B_NOTICE-L32": "공고문 5.가",
            "B_RFP-L4": "제안요청서 Ⅳ-1", "B_FORMS-L5": "별첨 서식 제3호"})

    def test_files_match_embedded_08_blocks(self):
        self.assertEqual(self.build("A")["segments"], bundle_a()["segments"])
        self.assertEqual(self.build("B")["segments"], bundle_b()["segments"])


class Bundle(unittest.TestCase):
    def test_contract_fields(self):
        b = bundle_a()
        self.assertEqual(b["bundle_id"], "A")
        self.assertEqual(b["source_lang"], "en")
        self.assertEqual(bundle_b()["source_lang"], "ko")
        self.assertEqual(b["as_of"], "2026-11-25T09:00:00+09:00")
        self.assertEqual([d["doc_id"] for d in b["docs"]], ["A_BODY", "A_ANNEX1", "A_ANNEX2"])
        self.assertEqual(len(b["source_sha256"]), 64)
        self.assertFalse(b["source_missing"])

    def test_08_text_unchanged_and_no_pii(self):
        b = bundle_a()
        self.assertEqual(b["docs"][0]["text"], A_BODY_TEXT)
        self.assertEqual(b["pii_masked"], [])
        self.assertEqual(bundle_b()["pii_masked"], [])

    def test_hash_tracks_raw_input(self):
        docs = [{"doc_id": "X", "title": "T", "text": "§1 a"}]
        h1 = build_source_bundle(docs)["source_sha256"]
        h2 = build_source_bundle([{**docs[0], "text": "§1 b"}])["source_sha256"]
        self.assertEqual(h1, build_source_bundle(docs)["source_sha256"])
        self.assertNotEqual(h1, h2)

    def test_source_missing(self):
        b = build_source_bundle([{"doc_id": "X", "title": "T", "text": "  \n\n"}])
        self.assertTrue(b["source_missing"])
        self.assertEqual(b["segments"], [])

    def test_validation(self):
        with self.assertRaises(ValueError):
            build_source_bundle([{"doc_id": "a_body", "title": "T", "text": "x"}])
        with self.assertRaises(ValueError):
            build_source_bundle([{"doc_id": "X", "title": "T", "text": "x"}] * 2)
        with self.assertRaises(ValueError):
            build_source_bundle([{"doc_id": "X", "title": "T", "text": "x"}], output_lang="fr")

    def test_render_segments(self):
        lines = render_segments(bundle_b(), "B_RFP").split("\n")
        self.assertEqual(lines[0], "[B_RFP-L2] 제안요청서(발췌)")
        self.assertEqual(lines[2], "[B_RFP-L4] 1. 제안서 제출 마감 일시: 2026년 12월 10일(목) 오전 10시")


if __name__ == "__main__":
    unittest.main()
