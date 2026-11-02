"""Stage V (engine/vision.py). Expected values are literals written by hand, not recomputed by the code under test."""
import json
import struct
import unittest
import zlib
from pathlib import Path

from engine import vision
from engine.model_client import IMAGE_PART_TOKENS, estimate_input_tokens

ROOT = Path(__file__).resolve().parent.parent
RED, WHITE, GREY = (200, 30, 40), (250, 249, 245), (90, 90, 90)


def png_rgb(pixels: list[list[tuple]]) -> bytes:
    h, w = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def png_palette(indices: list[list[int]], palette: list[tuple]) -> bytes:
    h, w = len(indices), len(indices[0])
    raw = b"".join(b"\x00" + bytes(row) for row in indices)

    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", bytes(c for px in palette for c in px)) + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def page(w=200, h=200, red_box=None):
    px = [[WHITE] * w for _ in range(h)]
    if red_box:
        x1, y1, x2, y2 = red_box
        for y in range(y1, y2):
            for x in range(x1, x2):
                px[y][x] = RED
    return px


class Decode(unittest.TestCase):
    def test_rgb_pixels(self):
        w, h, rows = vision.decode_png(png_rgb([[RED, WHITE], [GREY, RED]]))
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(tuple(rows[0][0:3]), (200, 30, 40))
        self.assertEqual(tuple(rows[1][0:3]), (90, 90, 90))

    def test_palette_pixels(self):
        w, h, rows = vision.decode_png(png_palette([[0, 1], [1, 0]], [WHITE, RED]))
        self.assertEqual(tuple(rows[0][3:6]), (200, 30, 40))
        self.assertEqual(tuple(rows[1][3:6]), (250, 249, 245))

    def test_not_png(self):
        with self.assertRaises(vision.PNGError):
            vision.decode_png(b"GIF89a....")


class PixelChecks(unittest.TestCase):
    def test_ink_ratio(self):
        _, _, rows = vision.decode_png(png_rgb(page(red_box=(0, 0, 10, 10))))
        self.assertEqual(vision.ink_ratio(rows, (0, 0, 10, 10)), 1.0)
        self.assertEqual(vision.ink_ratio(rows, (0, 0, 20, 10)), 0.5)
        self.assertEqual(vision.ink_ratio(rows, (100, 100, 150, 150)), 0.0)

    def test_stamp_sized_mark_found_and_tiny_mark_ignored(self):
        _, _, rows = vision.decode_png(png_rgb(page(red_box=(40, 40, 120, 120))))
        self.assertEqual(vision.stamp_marks(200, 200, rows), [(40, 40, 120, 120)])
        _, _, rows = vision.decode_png(png_rgb(page(red_box=(40, 40, 50, 50))))
        self.assertEqual(vision.stamp_marks(200, 200, rows), [])


class CheckPage(unittest.TestCase):
    def reply(self, state):
        # box (40, 40)-(120, 120) on a 200x200 page = 200..600 in 0-1000 units
        return {"page_ref": "F.pdf#p1", "seal_fields": [{"label_text": "[Company seal]", "state": state, "x_min": 200,
                                                         "y_min": 200, "x_max": 600, "y_max": 600}],
                "printed_dates": [{"label_text": "Date of issue", "value_text": "15 July 2026"}]}

    def run_check(self, red_box, state):
        p = {"file": "F.pdf", "page": 1, "png": png_rgb(page(red_box=red_box))}
        return vision.check_page(p, self.reply(state))

    def test_empty_confirmed_by_code(self):
        out = self.run_check(None, "empty")
        self.assertEqual(out["seal_fields"][0]["state"], "empty")
        self.assertEqual(out["printed_dates"][0]["iso"], "2026-07-15")

    def test_empty_box_but_a_stamp_elsewhere_on_the_page(self):
        # the model's box (40,40)-(120,120) is clean; a stamp-sized red block sits at (130,130)-(190,190)
        self.assertEqual(self.run_check((130, 130, 190, 190), "empty")["seal_fields"][0]["state"], "unknown")

    def test_model_says_empty_but_ink_is_there(self):
        self.assertEqual(self.run_check((40, 40, 120, 120), "empty")["seal_fields"][0]["state"], "unknown")

    def test_stamped_confirmed_by_code(self):
        self.assertEqual(self.run_check((40, 40, 120, 120), "stamped")["seal_fields"][0]["state"], "stamped")

    def test_model_says_stamped_but_no_ink(self):
        self.assertEqual(self.run_check(None, "stamped")["seal_fields"][0]["state"], "unknown")

    def test_no_reply_for_page(self):
        out = vision.check_page({"file": "F.pdf", "page": 1, "png": png_rgb(page())}, None)
        self.assertEqual(out["seal_fields"], [])
        self.assertFalse(out["read"])


class ListAdjustment(unittest.TestCase):
    MANIFEST = {"rows": [{"row_id": "M1", "file_name": "F.pdf", "sealed": "yes", "issue_date": "2026-10-15"},
                         {"row_id": "M2", "file_name": "G.pdf", "sealed": "yes", "issue_date": "2026-10-01"}]}

    def facts(self, seal_state, iso):
        return vision.file_facts([{"page_ref": "F.pdf#p1", "file": "F.pdf", "page": 1,
                                   "seal_fields": [{"label": "[Company seal]", "state": seal_state, "model_state": seal_state,
                                                    "box": [0, 0, 1, 1], "ink": 0.0, "code": ""}],
                                   "printed_dates": [{"label": "Date of issue", "value": "x", "iso": iso}]}])

    def test_empty_seal_and_printed_date_change_the_list_copy(self):
        adjusted, changes = vision.adjust_manifest(self.MANIFEST, self.facts("empty", "2026-07-15"))
        self.assertEqual(adjusted["rows"][0]["sealed"], "no")
        self.assertEqual(adjusted["rows"][0]["issue_date"], "2026-07-15")
        self.assertEqual(adjusted["rows"][1]["sealed"], "yes")            # a file without pages is untouched
        self.assertEqual(self.MANIFEST["rows"][0]["sealed"], "yes")       # the list itself is never edited
        self.assertEqual([c["field"] for c in changes], ["sealed", "issue_date"])

    def test_disagreeing_seal_reads_unknown(self):
        adjusted, _ = vision.adjust_manifest(self.MANIFEST, self.facts("unknown", ""))
        self.assertEqual(adjusted["rows"][0]["sealed"], "unknown")

    def test_stamped_page_changes_nothing(self):
        adjusted, changes = vision.adjust_manifest(self.MANIFEST, self.facts("stamped", "2026-10-15"))
        self.assertEqual(changes, [])
        self.assertEqual(adjusted, self.MANIFEST)


class OneWay(unittest.TestCase):
    def test_pages_never_make_a_row_met(self):
        cases = [("met", "error", "error"), ("met", "needs-confirmation", "needs-confirmation"),
                 ("error", "met", "error"), ("missing", "met", "missing"), ("needs-confirmation", "met",
                 "needs-confirmation"), ("conditional", "error", "error"), ("met", "met", "met")]
        for listed, paged, kept in cases:
            self.assertEqual(vision.page_cautious(listed, paged), kept, (listed, paged))


class Boundaries(unittest.TestCase):
    def test_engine_never_reads_the_answer_keys_or_the_generator(self):
        for path in (ROOT / "engine").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("answers.json", text.replace("scenarios/pages/*/answers.json", ""), path.name)
            self.assertNotIn("gen_pages", text.replace("tools/gen_pages.py", ""), path.name)

    def test_text_only_estimate_unchanged(self):
        body = {"model": "m", "messages": [{"role": "user", "content": "hello 안녕"}]}
        self.assertEqual(estimate_input_tokens(body), len(json.dumps(body, ensure_ascii=False).encode("utf-8")))

    def test_image_parts_counted_by_a_fixed_estimate(self):
        body = {"model": "m", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "x"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 400000}}]}]}
        est = estimate_input_tokens(body)
        self.assertLess(est, 10000)
        self.assertGreaterEqual(est, IMAGE_PART_TOKENS)


if __name__ == "__main__":
    unittest.main()
