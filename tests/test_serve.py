"""tools/serve.py input guards (security review 2026-10-28). Expected values are literals."""
import unittest

from tools.serve import _custom_inputs, _visitor


class Visitor(unittest.TestCase):
    def test_addresses(self):
        self.assertEqual(_visitor("203.0.113.7"), "203.0.113.7")
        self.assertEqual(_visitor(" 203.0.113.7 "), "203.0.113.7")
        self.assertEqual(_visitor("::ffff:203.0.113.7"), "203.0.113.7")
        # one IPv6 user owns a /64, so the limit is per /64
        self.assertEqual(_visitor("2001:db8:1:2:aaaa::1"), "2001:db8:1:2::/64")
        self.assertEqual(_visitor("2001:db8:1:2:bbbb::9"), "2001:db8:1:2::/64")

    def test_not_an_address(self):
        for raw in ("", "not-an-ip AAAA", "203.0.113.7, 198.51.100.9", "999.1.1.1"):
            with self.subTest(raw=raw):
                self.assertIsNone(_visitor(raw))


class CustomInputs(unittest.TestCase):
    BASE = {"docs": [{"title": "T", "text": "1. Submit the form."}], "list_text": "file | format\nf.pdf | PDF"}

    def test_ok(self):
        inp, why = _custom_inputs(dict(self.BASE))
        self.assertEqual((why, inp["docs"][0]["doc_id"], inp["lang"]), ("", "D1", "en"))

    def test_refusals(self):
        cases = {
            "list too long": {**self.BASE, "list_text": "a | b\n" + "x" * 20_001},
            "unpaired surrogate in text": {**self.BASE, "docs": [{"title": "T", "text": "bad \ud800 text"}]},
            "unpaired surrogate in note": {**self.BASE, "user_message": "\udfff"},
            "no docs": {**self.BASE, "docs": []},
            "too much source": {**self.BASE, "docs": [{"title": "T", "text": "x" * 60_001}]},
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                inp, why = _custom_inputs(body)
                self.assertIsNone(inp)
                self.assertTrue(why)


if __name__ == "__main__":
    unittest.main()
