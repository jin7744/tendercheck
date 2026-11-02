"""Synthetic scanned pages of the bidder's files, for the page-reading stage (V).

Every page is fictional and says so in its footer. The answer key for each set is written by hand in
scenarios/pages/<set>/answers.json and committed BEFORE any page-reading code; this script only draws what the key
says. The engine never reads the key or this script's layout (tests/test_vision.py checks that).

  python -m tools.gen_pages            # writes scenarios/pages/A-M1/*.png and scenarios/pages/gate/*.png

Needs Pillow (a tool dependency only; the engine stays standard library).
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "scenarios" / "pages"
W, H = 1240, 1754                      # A4 at 150 dpi
INK = (24, 26, 30)
SEAL_RED = (196, 32, 44)
SIG_BLUE = (24, 44, 128)
FONTS = Path("C:/Windows/Fonts") if sys.platform == "win32" else Path("/mnt/c/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


SERIF, SERIF_B = "NotoSerif-Regular.ttf", "NotoSerif-Bold.ttf"
KR, KR_B = "malgun.ttf", "malgunbd.ttf"


def blank() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), (250, 249, 245))
    return img, ImageDraw.Draw(img)


def footer(d: ImageDraw.ImageDraw, page: str):
    f = font(SERIF, 18)
    d.line([(90, H - 110), (W - 90, H - 110)], fill=(170, 170, 170), width=1)
    d.text((90, H - 96), f"SYNTHETIC DOCUMENT - fictional parties, TenderCheck test data  |  {page}", font=f, fill=(110, 110, 110))


def para(d: ImageDraw.ImageDraw, x: int, y: int, text: str, f, width: int, gap: int = 10) -> int:
    words, line = text.split(), ""
    for w in words:
        t = (line + " " + w).strip()
        if d.textlength(t, font=f) > width and line:
            d.text((x, y), line, font=f, fill=INK)
            y += f.size + gap
            line = w
        else:
            line = t
    if line:
        d.text((x, y), line, font=f, fill=INK)
        y += f.size + gap
    return y


def stamp(img: Image.Image, cx: int, cy: int, r: int, lines: list[str], rng: random.Random, kr: bool = False):
    """A round ink seal: double ring, text in the middle, uneven pressure and speckles."""
    s = 3
    layer = Image.new("L", (2 * r * s + 8, 2 * r * s + 8), 0)
    d = ImageDraw.Draw(layer)
    c = layer.size[0] // 2
    d.ellipse([c - r * s, c - r * s, c + r * s, c + r * s], outline=255, width=int(r * s * 0.09))
    d.ellipse([c - int(r * s * 0.80), c - int(r * s * 0.80), c + int(r * s * 0.80), c + int(r * s * 0.80)],
              outline=255, width=max(2, int(r * s * 0.03)))
    f = font(KR_B if kr else SERIF_B, int(r * s * (0.42 if len(lines) == 1 else 0.30)))
    total = sum(f.size for _ in lines) + (len(lines) - 1) * 6
    y = c - total // 2 - 6
    for t in lines:
        tw = d.textlength(t, font=f)
        d.text((c - tw / 2, y), t, font=f, fill=255)
        y += f.size + 6
    layer = layer.rotate(rng.uniform(-14, 14), resample=Image.BICUBIC)
    px = layer.load()
    for _ in range(int(layer.size[0] * layer.size[1] * 0.05)):    # speckles where the ink did not take
        x, y = rng.randrange(layer.size[0]), rng.randrange(layer.size[1])
        px[x, y] = 0
    fade = rng.uniform(0, 2 * math.pi)
    for yy in range(0, layer.size[1], 2):                            # uneven pressure across the seal
        for xx in range(0, layer.size[0], 2):
            v = px[xx, yy]
            if v:
                k = 0.70 + 0.30 * math.cos(fade + xx / layer.size[0] * 3.0)
                px[xx, yy] = int(v * k)
    layer = layer.filter(ImageFilter.GaussianBlur(1.2)).resize((layer.size[0] // s, layer.size[1] // s), Image.LANCZOS)
    ink = Image.new("RGB", layer.size, SEAL_RED)
    img.paste(ink, (cx - layer.size[0] // 2, cy - layer.size[1] // 2), layer.point(lambda v: int(v * 0.86)))


def signature(d: ImageDraw.ImageDraw, x: int, y: int, w: int, rng: random.Random):
    pts, px, py = [], x, y
    for i in range(9):
        px = x + int(w * i / 8) + rng.randint(-6, 6)
        py = y + rng.randint(-26, 26)
        pts.append((px, py))
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        mx, my = (x1 + x2) / 2 + rng.randint(-18, 18), (y1 + y2) / 2 + rng.randint(-30, 30)
        seg = [((1 - t) ** 2 * x1 + 2 * (1 - t) * t * mx + t * t * x2, (1 - t) ** 2 * y1 + 2 * (1 - t) * t * my + t * t * y2)
               for t in [k / 12 for k in range(13)]]
        d.line(seg, fill=SIG_BLUE, width=3, joint="curve")
    d.line([(x - 10, y + 30), (x + w * 0.7, y + 18)], fill=SIG_BLUE, width=2)


def logo(d: ImageDraw.ImageDraw, x: int, y: int):
    """A printed company logo in red: a solid shape, not an ink impression."""
    d.rounded_rectangle([x, y, x + 120, y + 120], radius=18, fill=SEAL_RED)
    f = font(SERIF_B, 64)
    d.text((x + 60 - d.textlength("Q", font=f) / 2, y + 18), "Q", font=f, fill=(250, 249, 245))
    d.text((x + 140, y + 30), "Quillfield Data Services Ltd.", font=font(SERIF_B, 30), fill=INK)
    d.text((x + 140, y + 72), "(fictional)  12 Example Road, Corvessa", font=font(SERIF, 22), fill=(80, 80, 80))


def seal_box(d: ImageDraw.ImageDraw, x: int, y: int, label: str) -> tuple[int, int, int, int]:
    d.rectangle([x, y, x + 230, y + 230], outline=(90, 90, 90), width=2)
    f = font(SERIF, 22)
    d.text((x + 115 - d.textlength(label, font=f) / 2, y + 236), label, font=f, fill=(70, 70, 70))
    return (x, y, x + 230, y + 230)


# ---------------- pages -------------------------------------------------------------------

def bf1_page1(rng: random.Random) -> Image.Image:
    img, d = blank()
    logo(d, 90, 90)
    d.text((90, 300), "FORM BF-1  FORM OF BID", font=font(SERIF_B, 40), fill=INK)
    y = 380
    fb = font(SERIF, 26)
    for t in ["To: Corvessa Metropolitan Water Authority (fictional)",
              "RFB No. CMWA/ITM/2026/014 - IT Maintenance Services for the Customer Billing System",
              "Date: 20 November 2026"]:
        d.text((90, y), t, font=fb, fill=INK)
        y += 48
    y += 20
    for t in ["We, the undersigned, offer to provide the IT maintenance services for the Customer Billing System "
              "for a period of 24 months in accordance with the Bidding Document and its annexes.",
              "Our bid shall remain valid for 120 days from the deadline for the submission of bids, and it shall "
              "remain binding upon us and may be accepted at any time before the expiration of that period.",
              "We understand that you are not bound to accept the lowest evaluated bid or any other bid that you "
              "may receive.",
              "Total bid price: USD 1,284,000 (synthetic figure) for the full contract period, as detailed in the "
              "Price Schedule (Form BF-3)."]:
        y = para(d, 90, y, t, fb, W - 180) + 18
    d.text((90, H - 170), "Page 1 of 2", font=font(SERIF, 22), fill=(90, 90, 90))
    footer(d, "QDS_BF1.pdf p.1")
    return img


def bf1_page2(rng: random.Random, sealed: bool) -> Image.Image:
    img, d = blank()
    d.text((90, 100), "FORM BF-1  FORM OF BID (continued)", font=font(SERIF_B, 34), fill=INK)
    fb = font(SERIF, 26)
    y = 180
    for t in ["We confirm that we are a single firm, that we are not subject to any sanction of the Authority, and "
              "that the information in this bid is true and complete.",
              "Signed by the authorized signatory for and on behalf of the Bidder:"]:
        y = para(d, 90, y, t, fb, W - 180) + 18
    sx, sy = 120, y + 120
    signature(d, sx, sy, 330, rng)
    d.line([(100, sy + 70), (560, sy + 70)], fill=INK, width=2)
    d.text((100, sy + 82), "Signature of authorized signatory", font=font(SERIF, 22), fill=(70, 70, 70))
    d.text((100, sy + 130), "Name: A. Fieldmore (fictional)    Title: Director", font=fb, fill=INK)
    d.text((100, sy + 176), "Date: 20 November 2026", font=fb, fill=INK)
    bx = seal_box(d, 760, sy - 60, "[Company seal]")
    if sealed:
        stamp(img, (bx[0] + bx[2]) // 2 + rng.randint(-12, 12), (bx[1] + bx[3]) // 2 + rng.randint(-12, 12), 92,
              ["QDS", "SEAL"], rng)
    d.text((90, H - 170), "Page 2 of 2", font=font(SERIF, 22), fill=(90, 90, 90))
    footer(d, "QDS_BF1.pdf p.2")
    return img


def tax_page(rng: random.Random, issued: str, issued_text: str) -> Image.Image:
    img, d = blank()
    d.text((90, 100), "CORVESSA REVENUE AUTHORITY (fictional)", font=font(SERIF_B, 30), fill=INK)
    d.text((90, 190), "TAX CLEARANCE CERTIFICATE", font=font(SERIF_B, 44), fill=INK)
    fb = font(SERIF, 26)
    y = 300
    rows = [("Certificate No.", "CRA-TCC-2026-%05d" % rng.randint(10000, 99999)),
            ("Taxpayer", "Quillfield Data Services Ltd. (fictional)"),
            ("Taxpayer ID", "SYNTH-TIN-0000-TEST"),
            ("Date of issue", issued_text),
            ("Validity", "This certificate is valid for 90 days from its date of issue.")]
    for k, v in rows:
        d.text((90, y), k, font=font(SERIF_B, 26), fill=INK)
        para(d, 400, y, v, fb, W - 490)
        y += 70
    y += 20
    y = para(d, 90, y, "This is to certify that the taxpayer named above has filed all returns and paid all taxes "
                       "due as of the date of issue.", fb, W - 180)
    d.text((760, y + 80), "Official stamp", font=font(SERIF, 22), fill=(70, 70, 70))
    stamp(img, 870, y + 230, 96, ["CRA", "OFFICIAL"], rng)
    d.text((90, y + 120), "Issuing officer: R. Example (fictional)", font=fb, fill=INK)
    footer(d, "QDS_TAX.pdf p.1")
    return img


def scan(img: Image.Image, rng: random.Random, level: int = 1) -> Image.Image:
    """A light scanner look: slight skew, a little blur and grain. Saved as 8-bit RGB PNG."""
    img = img.rotate(rng.uniform(-0.6, 0.6), resample=Image.BICUBIC, fillcolor=(250, 249, 245))
    if level >= 1:
        img = img.filter(ImageFilter.GaussianBlur(0.6))
    if level >= 2:
        img = img.resize((W // 2, H // 2), Image.BILINEAR).resize((W, H), Image.BILINEAR)
    px = img.load()
    for _ in range(W * H // 60):
        x, y = rng.randrange(W), rng.randrange(H)
        r, g, b = px[x, y]
        n = rng.randint(-18, 18)
        px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    return img


def render(spec: dict, out_dir: Path):
    rng = random.Random(spec["seed"])
    kind = spec["template"]
    if kind == "bf1_p1":
        img = bf1_page1(rng)
    elif kind == "bf1_p2":
        img = bf1_page2(rng, sealed=spec["sealed"])
    elif kind == "tax":
        img = tax_page(rng, spec["issued"], spec["issued_text"])
    else:
        raise ValueError(kind)
    img = scan(img, rng, spec.get("scan_level", 1))
    out_dir.mkdir(parents=True, exist_ok=True)
    # 8-bit palette PNG keeps the red ink and stays small; engine/vision.py decodes palette and RGB PNGs
    img.quantize(colors=64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(out_dir / spec["image"], optimize=True)


def main():
    for set_dir in sorted(p for p in PAGES.iterdir() if (p / "answers.json").exists()):
        key = json.loads((set_dir / "answers.json").read_text(encoding="utf-8"))
        for spec in key["pages"]:
            render(spec["render"], set_dir)
        print(set_dir.name, len(key["pages"]), "pages")


if __name__ == "__main__":
    main()
