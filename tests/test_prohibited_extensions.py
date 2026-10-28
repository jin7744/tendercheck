"""Live K=3 (2026-10-28): "한글(HWP)·워드(DOCX) 파일은 접수하지 않는다." came back as a format rule with
extensions [HWP, DOCX] in 9 of 21 runs, and F1 then failed every PDF file. Extensions named in a clause that refuses
them are refused, not allowed. Expected values are literals."""
import unittest

from engine.filechecks import _f1, split_extensions


def req(quote, exts):
    return {"category": "file_format", "quote": quote, "format": {"extensions": exts}}


def files(*exts):
    return [{"row_id": f"M{i + 1}", "file_name": f"f{i + 1}.{e}", "ext": e, "declared_format": ""}
            for i, e in enumerate(exts)]


class SplitExtensions(unittest.TestCase):
    def test_refusal_sentence_korean(self):
        r = req("한글(HWP)·워드(DOCX) 파일은 접수하지 않는다.", ["HWP", "DOCX"])
        self.assertEqual(split_extensions(r), (set(), {"hwp", "docx"}))

    def test_allow_then_refuse_in_one_quote(self):
        r = req("모든 제출서류는 PDF 형식으로 제출한다. 한글(HWP)·워드(DOCX) 파일은 접수하지 않는다.", ["PDF", "HWP", "DOCX"])
        self.assertEqual(split_extensions(r), ({"pdf"}, {"hwp", "docx"}))

    def test_refusal_sentence_english(self):
        r = req("Files in DOC or XLS format will not be accepted.", ["DOC", "XLS"])
        self.assertEqual(split_extensions(r), (set(), {"doc", "xls"}))

    def test_other_than_keeps_the_extension_allowed(self):
        for q in ("PDF 이외의 파일은 접수하지 않는다.", "Files other than PDF will not be accepted."):
            with self.subTest(q=q):
                self.assertEqual(split_extensions(req(q, ["PDF"])), ({"pdf"}, set()))

    def test_plain_rule_and_dotted_names(self):
        self.assertEqual(split_extensions(req("All files shall be in PDF format.", ["pdf"])), ({"pdf"}, set()))
        r = req("Each file shall be named ABC_BF1.pdf. Scans are not accepted.", ["pdf"])
        self.assertEqual(split_extensions(r), ({"pdf"}, set()))


class F1WithRefusals(unittest.TestCase):
    def test_refused_list_fails_only_refused_files(self):
        out = _f1(req("한글(HWP)·워드(DOCX) 파일은 접수하지 않는다.", ["HWP", "DOCX"]), files("pdf", "hwp"))
        self.assertEqual([e["result"] for e in out], ["pass", "fail"])

    def test_allowed_list_unchanged(self):
        out = _f1(req("All files shall be in PDF format.", ["PDF"]), files("pdf", "docx"))
        self.assertEqual([e["result"] for e in out], ["pass", "fail"])

    def test_mixed_quote(self):
        q = "모든 제출서류는 PDF 형식으로 제출한다. 한글(HWP)·워드(DOCX) 파일은 접수하지 않는다."
        out = _f1(req(q, ["PDF", "HWP", "DOCX"]), files("pdf", "docx", "zip"))
        self.assertEqual([e["result"] for e in out], ["pass", "fail", "fail"])


if __name__ == "__main__":
    unittest.main()
