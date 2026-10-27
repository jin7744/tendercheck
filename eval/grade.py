"""CLI for the independent grader.

  python3 -m eval.grade card T01 --grading A-M1=runs/.../grading.json [--grading B-M1=...] [--log A-M1=calls.jsonl] [--out grade.json]
  python3 -m eval.grade regression A-M1 runs/.../grading.json [--log calls.jsonl] [--out grade.json]
  python3 -m eval.grade summary grade1.json grade2.json ... [--k 3]
"""
import argparse
import json
import sys
from pathlib import Path

from . import core
from .fixtures import read_text


def _pairs(items, flag):
    out = {}
    for item in items or []:
        key, sep, path = item.partition("=")
        if not sep or not key or not path:
            raise SystemExit(f"{flag} expects MANIFEST=PATH, got {item!r}")
        out[key] = (read_text(path), path)
    return out


def _emit(result, out):
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python3 -m eval.grade")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("card")
    c.add_argument("card_id")
    c.add_argument("--grading", action="append", required=True)
    c.add_argument("--log", action="append")
    c.add_argument("--out")
    r = sub.add_parser("regression")
    r.add_argument("run_id")
    r.add_argument("grading")
    r.add_argument("--log")
    r.add_argument("--out")
    s = sub.add_parser("summary")
    s.add_argument("grades", nargs="+")
    s.add_argument("--k", type=int, default=3)
    s.add_argument("--out")
    a = ap.parse_args(argv)

    if a.cmd == "card":
        result = core.grade_card(a.card_id, _pairs(a.grading, "--grading"), _pairs(a.log, "--log"))
        line = f"{a.card_id} {result['verdict']}"
    elif a.cmd == "regression":
        log = (read_text(a.log), a.log) if a.log else (None, None)
        result = core.grade_regression(a.run_id, read_text(a.grading), a.grading, log[0], log[1])
        line = f"{a.run_id} {result['verdict']} {result['record']['status_match']}"
    else:
        result = core.summarize([json.loads(read_text(p)) for p in a.grades], k=a.k)
        line = " ".join(f"{k}={v['label']}" for k, v in result.items())
    _emit(result, a.out)
    if a.out:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
