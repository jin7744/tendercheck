"""Grade a run with scanned pages against scenarios/pages/<set>/answers.json (expected_on_replayed_A-M1).

  python3 -m tools.page_grade --run-dir runs/live_20261102_pages_r1/A-M1 --set A-M1 --base web/data/A-M1.json

Every requirement must keep its list-only status (from --base, the recorded list-only run) except the rows the key
names, which must reach the status the key names. Prints one line per moved or expected row and PASS / FAIL.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m tools.page_grade")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--set", required=True)
    ap.add_argument("--base", required=True, help="web bundle JSON of the list-only recorded run")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = json.loads((ROOT / "scenarios" / "pages" / args.set / "answers.json").read_text(encoding="utf-8"))
    expect = {k: re.search(r"->\s*(\S+)", v).group(1) for k, v in key["expected_on_replayed_A-M1"].items()
              if re.fullmatch(r"R\d+", k)}
    base = {r["id"]: r["status"] for r in json.loads(Path(args.base).read_text(encoding="utf-8"))["report"]["requirements"]}
    got = {r["id"]: r["status"] for r in json.loads((Path(args.run_dir) / "report.json").read_text(encoding="utf-8"))["requirements"]}
    bad = []
    for rid in sorted(set(base) | set(got)):
        want = expect.get(rid, base.get(rid))
        if got.get(rid) != want:
            bad.append(rid)
        if rid in expect or got.get(rid) != base.get(rid):
            print(f"{rid}: list-only {base.get(rid)} -> with pages {got.get(rid)} (key: {want}) {'ok' if got.get(rid) == want else 'WRONG'}")
    rows = len(set(base) | set(got))
    print(f"{rows - len(bad)}/{rows} rows as the key says -> {'PASS' if not bad else 'FAIL'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
