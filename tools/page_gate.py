"""Field-level gate for stage V on scenarios/pages/gate (pre-registered in its answers.json, commit 9c5e337).

  TENDERCHECK_LIVE=1 python3 -m tools.page_gate --run-id live_20261102_pagegate --global-cap-usd 140

One live V call per page (engine.vision.run_pages, the same prompt and code checks the pipeline uses), then one
scoring pass against the answer key. Writes runs/<run_id>/gate/{calls.jsonl, gate.json}. This tool reads the key;
the engine never does.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from engine import vision
from engine.ledger import E8_PER_USD, Caps, Ledger
from engine.model_client import MetaClient
from engine.replay import Recorder
from tools.run_scenario import live_preconditions

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scenarios" / "pages" / "gate"


def score_page(truth: dict, read: dict) -> dict:
    want = [f["state"] for f in truth["seal_fields"] if f.get("whose") != "the issuing authority"]
    got = [f["state"] for f in read["seal_fields"]]
    seal_wrong = 0
    if want:
        # the bidder's seal field: the page has one; it must come back in the right state (model and code agreeing)
        target = want[0]
        seal_ok = target in got and not (set(got) & ({"stamped", "empty"} - {target}))
        seal_wrong = 0 if seal_ok else (1 if any(g in ("stamped", "empty") and g != target for g in got) else 0)
    else:
        seal_ok = True
    want_issue = [d["value"] for d in truth["printed_dates"] if vision.ISSUE_LABEL.search(d["label"])]
    got_issue = [d["iso"] for d in read["printed_dates"] if vision.ISSUE_LABEL.search(d["label"]) and d["iso"]]
    date_ok = (not want_issue) or (bool(got_issue) and set(got_issue) == set(want_issue))
    return {"seal_ok": seal_ok, "seal_wrong_state": seal_wrong, "date_ok": date_ok, "ok": seal_ok and date_ok,
            "want_seal": want, "got_seal": got, "want_issue": want_issue, "got_issue": got_issue}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m tools.page_gate")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--global-cap-usd", type=float)
    args = ap.parse_args(argv)
    why = live_preconditions(args.run_id, args.global_cap_usd, os.environ)
    if why:
        raise SystemExit(why)
    key = json.loads((GATE / "answers.json").read_text(encoding="utf-8"))
    out = ROOT / "runs" / args.run_id / "gate"
    if (out / "calls.jsonl").exists():
        raise SystemExit(f"{out} exists; use a new --run-id")
    ledger = Ledger(caps=Caps(global_stop_e8=int(round(args.global_cap_usd * E8_PER_USD))))
    client = MetaClient.from_env(live=True, ledger=ledger, recorder=Recorder(out), run_id=f"{args.run_id}-gate")
    rows = []
    for i, spec in enumerate(key["pages"]):
        page = {"file": spec["file"], "page": spec["page"], "png": (GATE / spec["render"]["image"]).read_bytes(),
                "image": spec["render"]["image"]}
        env = vision.run_pages(client, [page], attempt=i)
        read = env["pages"][0] if env["pages"] else {"seal_fields": [], "printed_dates": [], "notes": [env["error"]]}
        s = score_page(spec["truth"], read)
        rows.append({"page_id": spec["page_id"], **s, "error": env["error"], "read": read})
        print(f"{spec['page_id']}: {'OK ' if s['ok'] else 'MISS'} seal want={s['want_seal']} got={s['got_seal']} "
              f"issue want={s['want_issue']} got={s['got_issue']}" + (f" error={env['error']}" if env["error"] else ""))
    ok = sum(r["ok"] for r in rows)
    wrong = sum(r["seal_wrong_state"] for r in rows)
    passed = ok >= 11 and wrong == 0
    summary = {"pass_rule": key["pass_rule"], "pages": len(rows), "fully_correct": ok, "seal_wrong_state": wrong,
               "PASS": passed, "rows": rows}
    (out / "gate.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"gate: {ok}/{len(rows)} fully correct, {wrong} seal fields in the wrong state -> {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
