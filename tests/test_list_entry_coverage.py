"""Cards second live pass (2026-10-28), T07 k1 aux B-M1: E1 skipped "⑨ 제안서(제안요청서 Ⅲ의 목차에 따름)". The line
names a document and has no obligation word, so the coverage sweep (TRIGGER_RE) never sent it to E4 and gold R10
went unmatched. An entry of a numbered list whose siblings were cited is now a coverage target too. Expected
seg_ids are literals from the 08 packages."""
import unittest

from test_engine_fixtures import bundle_a, bundle_b

from engine.pipeline import TRIGGER_RE, list_sibling_targets


def ids(doc, *lines):
    return {f"{doc}-L{n}" for n in lines}


class ListEntryCoverage(unittest.TestCase):
    def test_the_line_has_no_trigger_word(self):
        seg = next(s for s in bundle_b()["segments"] if s["seg_id"] == "B_NOTICE-L24")
        self.assertIsNone(TRIGGER_RE.search(seg["text"]))

    def test_uncited_korean_list_entries(self):
        # 공고문 3.①-⑫ cited except ② (L17) and ⑨ (L24); the heading "3. 제출서류" (L15) is not an entry.
        cited = ids("B_NOTICE", 16, 18, 19, 20, 21, 22, 23, 25, 26, 27)
        self.assertEqual(list_sibling_targets(bundle_b()["segments"], cited), ["B_NOTICE-L17", "B_NOTICE-L24"])

    def test_uncited_english_list_entry(self):
        cited = ids("A_BODY", 12, 13, 14, 15, 16, 18, 19)
        self.assertEqual(list_sibling_targets(bundle_a()["segments"], cited), ["A_BODY-L17"])  # §3(f) Technical Proposal

    def test_no_cited_sibling_no_target(self):
        self.assertEqual(list_sibling_targets(bundle_b()["segments"], ids("B_NOTICE", 32)), ["B_NOTICE-L33"])
        self.assertEqual(list_sibling_targets(bundle_b()["segments"], set()), [])
        self.assertEqual(list_sibling_targets(bundle_a()["segments"], ids("A_BODY", 25, 27)), [])  # §5, §6: no list


if __name__ == "__main__":
    unittest.main()
