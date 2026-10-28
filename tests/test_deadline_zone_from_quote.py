"""Card T03 K=3 (2026-10-28): in two of three runs the model's deadline.raw left out the zone that the same quoted
sentence states ("…14:00 hours Corvessa time (UTC+03:00)"), so the upload time was never converted (20:30 KST =
14:30 UTC+03:00, late for §5). Code now reads the zone from the verified quote when raw and tz_quote give none and
the quote states exactly one. Expected values are literals."""
import unittest

from engine.checks import _q5

QUOTE = "The deadline for submission of Bids is 30 November 2026, 14:00 hours Corvessa time (UTC+03:00)."


def req(raw, quote=QUOTE, iso="2026-11-30T14:00:00", tz_quote=""):
    return {"quote": quote, "deadline": {"kind": "submission", "raw": raw, "iso_local": iso, "tz_quote": tz_quote},
            "validity": {"rule_type": "none"}}


class ZoneFromQuote(unittest.TestCase):
    def test_zone_read_from_the_quote(self):
        result, _, code, parsed, info = _q5(req("30 November 2026, 14:00 hours"), tz_ok=False)
        self.assertEqual((result, code, parsed["offset"], parsed["tz_source"]), ("pass", "", "+03:00", "quote"))
        self.assertNotIn("TZ_NOT_STATED", info)

    def test_raw_zone_still_first(self):
        _, _, _, parsed, _ = _q5(req("30 November 2026, 14:00 hours (UTC+03:00)"), tz_ok=False)
        self.assertEqual((parsed["offset"], parsed["tz_source"]), ("+03:00", "raw"))

    def test_two_zones_in_the_quote_are_not_guessed(self):
        q = "Bids are due 30 November 2026, 14:00 hours (UTC+03:00); the opening is streamed at 12:00 UTC."
        result, _, _, parsed, info = _q5(req("30 November 2026, 14:00 hours", quote=q), tz_ok=False)
        self.assertEqual((result, parsed["offset"]), ("pass", ""))
        self.assertIn("TZ_NOT_STATED", info)

    def test_no_zone_anywhere(self):
        q = "The deadline for submission of Bids is: Date: 2 December 2026; Time: 14:00 hours."
        _, _, _, parsed, info = _q5(req("Date: 2 December 2026; Time: 14:00 hours", quote=q, iso="2026-12-02T14:00:00"), tz_ok=False)
        self.assertEqual(parsed["offset"], "")
        self.assertIn("TZ_NOT_STATED", info)


if __name__ == "__main__":
    unittest.main()
