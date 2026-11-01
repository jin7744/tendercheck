"""Build the replay bundle the web page reads: web/data/samples.json + web/data/<sample>.json.

Each sample file holds one recorded live run's report.json (unchanged), the source documents split into the same
segments the engine cites (so the page can open the exact line), the bidder list text, and run facts read from the
run itself (model names, calls, cost, stage log). Nothing is recomputed here and no model is called.

  python3 -m tools.build_web_bundle                 # default run choice below
  python3 -m tools.build_web_bundle --run A-M1=runs/live_20261028_v2_k1/A-M1
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from engine.normalize import build_source_bundle
from tools.run_scenario import SCENARIOS, find_run, scenario_inputs

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data"
REPLAYS = ROOT / "replays"  # committed: runs/live_* is git-ignored, and the server's re-run needs the replies

# One recorded live run per sample, all recorded on 2026-11-01 on the frozen engine (5aa4ba5, fixes 1-9), so
# "re-run" replays them byte for byte. Earlier recordings are listed in docs/live_runs_20261028.md.
DEFAULT_RUNS = {m: f"runs/live_20261101_freeze/{m}" for m in
                ("A-M1", "A-M2", "A-M3", "B-M1", "B-M2", "B-M3", "B-M2-PII")}
TITLES = {
    "A-M1": ("Package A · clean bid", "English tender, every document in order"),
    "A-M2": ("Package A · missing & wrong", "Joint venture bid with gaps, expired and unsigned files"),
    "A-M3": ("Package A · edge cases", "Two deadlines that disagree, unknown JV status"),
    "B-M1": ("Package B · clean bid", "Korean tender, usage seal, every document in order"),
    "B-M2": ("Package B · missing & wrong", "HWP file, old certificates, short signatures"),
    "B-M3": ("Package B · edge cases", "Deadline conflict, cut-off scan, unknown JV status"),
    "B-M2-PII": ("Package B · sensitive data", "A bidder list carrying personal numbers, masked before the model"),
}


def _calls(run_dir: Path) -> list[dict]:
    path = run_dir / "calls.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        usage = c.get("usage") or {}
        out.append({"stage": c.get("stage"), "attempt": c.get("attempt"), "outcome": c.get("outcome"),
                    "model": c.get("model_resolved") or c.get("model_requested"), "latency_ms": c.get("latency_ms"),
                    "tokens_in": usage.get("input_tokens"), "tokens_out": usage.get("output_tokens"),
                    "tokens_reasoning": usage.get("reasoning_tokens"), "cost_usd": c.get("cost_usd")})
    return out


def sample(sample_id: str, run_dir: Path) -> dict:
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    inp = scenario_inputs(sample_id)
    bundle = build_source_bundle(inp["docs"], bundle_id=f"web-{sample_id}", output_lang=inp["lang"])
    docs = []
    for d in bundle["docs"]:
        lines = [{"seg_id": s["seg_id"], "n": s["line"], "text": s["text"], "location": s["location"]}
                 for s in bundle["segments"] if s["doc_id"] == d["doc_id"]]
        docs.append({"doc_id": d["doc_id"], "title": d["title"], "lines": lines})
    calls = _calls(run_dir)
    title, blurb = TITLES.get(sample_id, (sample_id, ""))
    pkg, _ = find_run(sample_id)
    return {
        "id": sample_id, "title": title, "blurb": blurb, "package": pkg, "lang": inp["lang"],
        "run": {"run_dir": run_dir.as_posix().split("runs/", 1)[-1], "mode": "replay",
                "models": sorted({c["model"] for c in calls if c["model"]}),
                "calls": sum(1 for c in calls if c["outcome"] == "response"),
                "cost_usd": round(sum(c["cost_usd"] or 0 for c in calls), 6),
                "call_log": calls},
        "sources": docs,
        "manifest_text": inp["manifest_text"],
        "user_message": inp["user_message"],
        "report": report,
    }


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run", action="append", default=[], help="SAMPLE=runs/<run_id>/<manifest> (overrides default)")
    a = p.parse_args(argv)
    runs = dict(DEFAULT_RUNS)
    for item in a.run:
        k, v = item.split("=", 1)
        runs[k] = v
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for sid, rel in runs.items():
        run_dir = ROOT / rel
        if not (run_dir / "report.json").exists():
            print(f"skip {sid}: no report.json in {rel}")
            continue
        data = sample(sid, run_dir)
        (OUT / f"{sid}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (REPLAYS / sid).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run_dir / "calls.jsonl", REPLAYS / sid / "calls.jsonl")
        sc = data["report"]["status_counts"]
        index.append({"id": sid, "title": data["title"], "blurb": data["blurb"], "package": data["package"],
                      "lang": data["lang"], "status_counts": sc})
        print(f"{sid}: {data['run']['run_dir']} calls={data['run']['calls']} cost=${data['run']['cost_usd']:.4f} {sc}")
    (OUT / "samples.json").write_text(json.dumps({"samples": index}, ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
