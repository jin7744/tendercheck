"""Run one scenario (08 package + manifest [+ test card message]) through engine.pipeline, then grade it.

  python3 -m tools.run_scenario --run A-M1 --mode fake --fake-fixture fixture.json --run-id dev_01
  python3 -m tools.run_scenario --run A-M1 --mode replay --run-id dev_01            (replays runs/dev_01/A-M1)
  TENDERCHECK_LIVE=1 python3 -m tools.run_scenario --run A-M1 --mode live --run-id live_20261028_01 --global-cap-usd 10
  TENDERCHECK_LIVE=1 python3 -m tools.run_scenario --run A-M1 --pages A-M1 --mode live --hybrid-replay replays/A-M1       --run-id live_20261102_pages_r1 --global-cap-usd 140 --no-grade
      (scanned pages: every recorded text stage is replayed from replays/A-M1 and only stage V is called live)

Writes runs/<run_id>/<run>/{report.json, grading.json, report.txt, calls.jsonl, run.json} and grades with
`python3 -m eval.grade` in a separate process (the engine never imports eval/). Live mode spends money: it needs
TENDERCHECK_LIVE=1, a run id starting with live_ (git-ignored), --global-cap-usd, the cost ledger and the
recorder; MetaClient.from_env reads the key only after its own live guard.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from engine import pipeline
from engine.ledger import E8_PER_USD, Caps, Ledger
from engine.model_client import FakeClient, MetaClient, ReplayClient
from engine.replay import CALLS_FILENAME, Recorder

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"
# The grader process needs no key and no live switch.
CHILD_ENV_DROP = ("MODEL_API_KEY", "META_API_KEY", "TENDERCHECK_LIVE")


def packages() -> dict:
    return json.loads((SCENARIOS / "packages.json").read_text(encoding="utf-8"))


def find_run(run: str) -> tuple[str, dict]:
    for pkg_id, cfg in packages().items():
        if run in cfg.get("manifests", {}):
            return pkg_id, cfg
    raise SystemExit(f"unknown run {run!r}: not a manifest in scenarios/packages.json")


def user_message_for(run: str, test: str | None, cfg: dict) -> str:
    if not test:
        return cfg.get("default_user_message", "")
    card = json.loads((SCENARIOS / "tests" / f"{test}.json").read_text(encoding="utf-8"))
    if card.get("manifest") == run:
        return card.get("user_message", "")
    for aux in card.get("aux_runs", []):
        if aux.get("manifest") == run:
            return aux.get("user_message", "")
    raise SystemExit(f"test {test} has no run {run}")


def load_pages(set_name: str) -> list[dict]:
    """Scanned pages of one set, read from file names only (<file stem>-p<n>.png -> <file stem>.pdf page n).
    The set's answers.json is never read here or by the engine."""
    d = SCENARIOS / "pages" / set_name
    pages = []
    for p in sorted(d.glob("*-p*.png")):
        stem, _, n = p.stem.rpartition("-p")
        pages.append({"file": stem + ".pdf", "page": int(n), "png": p.read_bytes(), "image": f"{set_name}/{p.name}"})
    if not pages:
        raise SystemExit(f"no pages in {d}")
    return pages


class HybridClient:
    """Replays every recorded stage of one run and sends only the listed stages to the live client. Used to add
    stage V to a recorded run: the text stages' requests are unchanged, so the difference comes from the pages."""

    def __init__(self, replay, live, live_stages=("V",)):
        object.__setattr__(self, "_replay", replay)
        object.__setattr__(self, "_live", live)
        object.__setattr__(self, "_live_stages", frozenset(live_stages))

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_live"), name)

    def __setattr__(self, name, value):          # per-call knobs only matter for the live call
        setattr(object.__getattribute__(self, "_live"), name, value)

    def complete(self, stage, attempt, messages, schema_name):
        target = self._live if stage in self._live_stages else self._replay
        return target.complete(stage, attempt, messages, schema_name)


def scenario_inputs(run: str, test: str | None = None) -> dict:
    pkg_id, cfg = find_run(run)
    docs = [{"doc_id": d["doc_id"], "title": d["title"], "header": d["header"],
             "text": (SCENARIOS / d["file"]).read_text(encoding="utf-8")} for d in cfg["docs"]]
    return {"package": pkg_id, "docs": docs, "lang": cfg["output_lang"], "as_of": cfg["as_of"],
            "manifest_text": (SCENARIOS / cfg["manifests"][run]).read_text(encoding="utf-8"),
            "user_message": user_message_for(run, test, cfg)}


def live_preconditions(run_id: str, global_cap_usd: float | None, env) -> str:
    """'' when a live run may start, else why not. Checked before any client is built."""
    if env.get("TENDERCHECK_LIVE") != "1":
        return "live mode needs TENDERCHECK_LIVE=1 in the environment"
    if not run_id.startswith("live_"):
        return "a live run id must start with live_ (runs/live_*/ is git-ignored)"
    if global_cap_usd is None or global_cap_usd <= 0:
        return "live mode needs --global-cap-usd (the live stop for the whole ledger)"
    return ""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def build_client(args, out_dir: Path):
    if args.mode == "fake":
        if not args.fake_fixture:
            raise SystemExit("--mode fake needs --fake-fixture (FakeClient.from_file JSON)")
        if (out_dir / CALLS_FILENAME).exists():
            raise SystemExit(f"{out_dir / CALLS_FILENAME} exists; use a new --run-id")
        return FakeClient.from_file(args.fake_fixture, run_id=f"{args.run_id}-{args.run}",
                                    recorder=Recorder(out_dir))
    if args.mode == "replay":
        src = Path(args.replay_from) if args.replay_from else out_dir
        if not (src / CALLS_FILENAME).exists():
            raise SystemExit(f"no {CALLS_FILENAME} in {src}")
        if src.resolve() != out_dir.resolve():
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / CALLS_FILENAME, out_dir / CALLS_FILENAME)
        return ReplayClient(src)
    why = live_preconditions(args.run_id, args.global_cap_usd, os.environ)
    if why:
        raise SystemExit(why)
    if (out_dir / CALLS_FILENAME).exists():
        raise SystemExit(f"{out_dir / CALLS_FILENAME} exists; use a new --run-id")
    ledger = Ledger(caps=Caps(global_stop_e8=int(round(args.global_cap_usd * E8_PER_USD))))
    if args.hybrid_replay:
        src = Path(args.hybrid_replay)
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / CALLS_FILENAME, out_dir / CALLS_FILENAME)
        live = MetaClient.from_env(live=True, ledger=ledger, recorder=Recorder(out_dir),
                                   run_id=f"{args.run_id}-{args.run}")
        return HybridClient(ReplayClient(src), live)
    return MetaClient.from_env(live=True, ledger=ledger, recorder=Recorder(out_dir),
                               run_id=f"{args.run_id}-{args.run}")


def grade(args, out_dir: Path, runs_dir: Path) -> dict:
    results = {}
    log = out_dir / CALLS_FILENAME
    regression = {e["run_id"] for e in json.loads((SCENARIOS / "regression.json").read_text(encoding="utf-8"))}
    cmds = []
    if args.run in regression:
        cmd = [sys.executable, "-m", "eval.grade", "regression", args.run, str(out_dir / "grading.json"),
               "--out", str(out_dir / "grade.json")]
        if log.exists():
            cmd += ["--log", str(log)]
        cmds.append(("regression", cmd))
    if args.test:
        card = json.loads((SCENARIOS / "tests" / f"{args.test}.json").read_text(encoding="utf-8"))
        names = [card["manifest"]] + [a["manifest"] for a in card.get("aux_runs", [])]
        cmd = [sys.executable, "-m", "eval.grade", "card", args.test, "--out", str(out_dir / f"grade_{args.test}.json")]
        for name in names:
            g = runs_dir / name / "grading.json"
            if g.exists():
                cmd += ["--grading", f"{name}={g}"]
                if (runs_dir / name / CALLS_FILENAME).exists():
                    cmd += ["--log", f"{name}={runs_dir / name / CALLS_FILENAME}"]
        cmds.append((f"card {args.test}", cmd))
    env = {k: v for k, v in os.environ.items() if k not in CHILD_ENV_DROP}
    env["PYTHONUTF8"] = "1"
    for name, cmd in cmds:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=env)
        results[name] = {"returncode": p.returncode, "line": p.stdout.strip(), "stderr": p.stderr.strip()[-2000:]}
    return results


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python3 -m tools.run_scenario")
    ap.add_argument("--run", required=True, help="manifest id, e.g. A-M1")
    ap.add_argument("--test", help="test card id (T01-T07) whose user message to use")
    ap.add_argument("--mode", required=True, choices=("fake", "replay", "live"))
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--global-cap-usd", type=float)
    ap.add_argument("--fake-fixture")
    ap.add_argument("--replay-from", help="run directory holding calls.jsonl (default: the output directory)")
    ap.add_argument("--runs-root", default=str(ROOT / "runs"))
    ap.add_argument("--no-grade", action="store_true")
    ap.add_argument("--pages", help="scanned page set under scenarios/pages/ (adds stage V)")
    ap.add_argument("--hybrid-replay", help="with --mode live: replay every recorded stage from this run dir, call V live")
    args = ap.parse_args(argv)

    runs_dir = Path(args.runs_root) / args.run_id
    out_dir = runs_dir / args.run
    inputs = scenario_inputs(args.run, args.test)
    client = build_client(args, out_dir)
    result = pipeline.run(inputs["docs"], inputs["manifest_text"], lang=inputs["lang"], as_of=inputs["as_of"],
                          run_id=args.run, client=client, user_message=inputs["user_message"],
                          pages=load_pages(args.pages) if args.pages else None)
    _write(out_dir / "report.json", dumps(result["report"]))
    _write(out_dir / "grading.json", dumps(result["grading"]))
    _write(out_dir / "report.txt", result["text"])
    meta = result["report"]["x_meta"]
    info = {"run": args.run, "run_id": args.run_id, "test": args.test, "mode": args.mode,
            "provider": getattr(client, "provider", ""), "package": inputs["package"],
            "status_counts": result["report"]["status_counts"], "analysis_state": meta.get("analysis_state"),
            "banner": meta.get("banner", ""), "invariants": result["invariants"], "calls": result["calls"],
            "cost_total_usd": meta.get("cost_total_usd")}
    if not args.no_grade:
        info["grades"] = grade(args, out_dir, runs_dir)
    _write(out_dir / "run.json", dumps(info))
    sc = result["report"]["status_counts"]
    print(f"{args.run} [{args.mode}] state={meta.get('analysis_state')} calls={len(result['calls'])} "
          f"cost=${meta.get('cost_total_usd', 0):.6f} counts=" + "/".join(str(sc[k]) for k in sc)
          + f" invariants={'ok' if result['invariants']['ok'] else 'FAILED'}")
    for name, g in (info.get("grades") or {}).items():
        print(f"  grade {name}: {g['line'] or g['stderr'][-300:]}")
    print(f"  out: {out_dir}")
    return 0 if result["invariants"]["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
