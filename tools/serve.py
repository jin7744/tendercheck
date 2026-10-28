"""TenderCheck web server (design 09 §8-2): static page + replay bundle + optional live runs. Standard library only.

  python3 -m tools.serve                       # replay only, http://127.0.0.1:8765
  TENDERCHECK_LIVE=1 MODEL_API_KEY=… TENDERCHECK_GLOBAL_CAP_USD=10 python3 -m tools.serve --live

Live runs are off unless --live is passed AND the live switch, the key and a global cap are all in the
environment. Limits per design: body ≤ 200 KB, source text ≤ 60,000 characters, bidder list ≤ 40 rows,
3 runs per IP per hour, 40 runs per day, $2.00 per run (ledger cap), jobs kept 1 hour. Input text is never
written to the server log.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in (None, ""):  # run as a script: python tools/serve.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import pipeline  # noqa: E402
from engine.ledger import E8_PER_USD, Caps, Ledger  # noqa: E402
from engine.model_client import API_KEY_ENV, MetaClient, live_enabled_by_env  # noqa: E402
from engine.model_client import ReplayClient  # noqa: E402
from engine.replay import Recorder  # noqa: E402
from tools.build_web_bundle import DEFAULT_RUNS  # noqa: E402
from tools.run_scenario import scenario_inputs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = WEB / "data"
VERSION = "0.4.0"
KST = timezone(timedelta(hours=9))

MAX_BODY = 200_000
MAX_SOURCE_CHARS = 60_000
MAX_DOCS = 6
MAX_LIST_ROWS = 40
PER_IP_PER_HOUR = 3
PER_DAY = 40
JOB_TTL_S = 3600
SAMPLE_ID = re.compile(r"^[A-Z]-M\d(?:-[A-Z]+)?$")
JOB_ID = re.compile(r"^[a-f0-9]{16}$")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


class Live:
    """Live-run gate, rate limits and job store. Every decision is made under one lock."""

    def __init__(self, enabled: bool, global_cap_usd: float, kind: str = "live"):
        """kind "live" calls the model; kind "rerun" sends a bundled case's recorded replies back through the
        current code (no model call, $0) so the progress path can be shown and tested."""
        self.enabled, self.global_cap_usd, self.kind = enabled, global_cap_usd, kind
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.by_ip: dict[str, list[float]] = {}
        self.day: tuple[str, int] = ("", 0)

    def status(self, ip: str | None = None) -> dict:
        if not self.enabled:
            return {"live_enabled": False, "kind": self.kind, "remaining_today": 0,
                    "reason": "live runs are off on this server"}
        with self.lock:
            today = datetime.now(KST).strftime("%Y-%m-%d")
            used = self.day[1] if self.day[0] == today else 0
            out = {"live_enabled": used < PER_DAY, "kind": self.kind, "remaining_today": max(0, PER_DAY - used),
                   "reason": "" if used < PER_DAY else "daily limit reached"}
            if ip is not None:
                recent = [t for t in self.by_ip.get(ip, []) if t > time.time() - 3600]
                out["remaining_this_hour"] = max(0, PER_IP_PER_HOUR - len(recent))
            return out

    def admit(self, ip: str) -> str:
        """'' when a run may start (and counts it), else the reason."""
        if not self.enabled:
            return "live runs are off on this server"
        with self.lock:
            now = time.time()
            today = datetime.now(KST).strftime("%Y-%m-%d")
            used = self.day[1] if self.day[0] == today else 0
            recent = [t for t in self.by_ip.get(ip, []) if t > now - 3600]
            if used >= PER_DAY:
                return "daily limit reached"
            if len(recent) >= PER_IP_PER_HOUR:
                return "hourly limit for your address reached"
            self.by_ip[ip] = recent + [now]
            self.day = (today, used + 1)
            for jid in [j for j, v in self.jobs.items() if v["created"] < now - JOB_TTL_S]:
                del self.jobs[jid]
            return ""

    def start(self, inputs: dict) -> str:
        job_id = secrets.token_hex(8)
        job = {"state": "running", "events": [], "report": None, "error": "", "created": time.time()}
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job_id, job, inputs), daemon=True).start()
        return job_id

    def _run(self, job_id: str, job: dict, inputs: dict) -> None:
        def on_event(ev: dict) -> None:
            with self.lock:
                job["events"].append({**ev, "t": round(time.time() - job["created"], 1)})

        run_id = f"live_web_{job_id}"
        try:
            if self.kind == "rerun":
                client = ReplayClient(ROOT / DEFAULT_RUNS[inputs["sample_id"]])
                paced = on_event

                def on_event(ev: dict) -> None:  # noqa: F811 - pace the instant replay so each stage is visible
                    time.sleep(0.7)
                    paced(ev)
            else:
                ledger = Ledger(caps=Caps(global_stop_e8=int(round(self.global_cap_usd * E8_PER_USD))))
                client = MetaClient.from_env(live=True, ledger=ledger,
                                             recorder=Recorder(ROOT / "runs" / run_id / "job"), run_id=f"{run_id}-job")
            result = pipeline.run(inputs["docs"], inputs["manifest_text"], lang=inputs["lang"], as_of=inputs["as_of"],
                                  run_id="job", client=client, user_message=inputs["user_message"],
                                  on_event=on_event)
            report = result["report"]
            x = report["x_meta"]
            if self.kind == "rerun" and (x.get("extract_error") or x.get("match_error") or x.get("stop")):
                raise RuntimeError("RecordingDoesNotMatchCurrentPrompts")
            x["mode"] = "live" if self.kind == "live" else "replay"
            with self.lock:
                job["report"], job["state"] = report, "done"
        except Exception as exc:  # noqa: BLE001 - shown to the user as a short class name only
            with self.lock:
                job["state"], job["error"] = "failed", type(exc).__name__

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            return {"state": job["state"], "stage_events": list(job["events"]), "report": job["report"],
                    "error": job["error"]}


def _sample_inputs(sample_id: str) -> dict:
    inp = scenario_inputs(sample_id)
    return {"docs": inp["docs"], "manifest_text": inp["manifest_text"], "lang": inp["lang"], "as_of": inp["as_of"],
            "user_message": inp["user_message"], "sample_id": sample_id}


def _custom_inputs(body: dict) -> tuple[dict | None, str]:
    docs_in = body.get("docs")
    if not isinstance(docs_in, list) or not 1 <= len(docs_in) <= MAX_DOCS:
        return None, f"give 1 to {MAX_DOCS} source documents"
    docs, total = [], 0
    for i, d in enumerate(docs_in):
        if not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"].strip():
            return None, f"document {i + 1} has no text"
        title = str(d.get("title") or f"Document {i + 1}")[:80]
        total += len(d["text"])
        docs.append({"doc_id": f"D{i + 1}", "title": title, "text": d["text"], "header": title})
    if total > MAX_SOURCE_CHARS:
        return None, f"source text is over {MAX_SOURCE_CHARS} characters"
    list_text = body.get("list_text")
    if not isinstance(list_text, str) or not list_text.strip():
        return None, "the bidder list is empty"
    if sum(1 for line in list_text.splitlines() if "|" in line) > MAX_LIST_ROWS + 1:
        return None, f"the bidder list has more than {MAX_LIST_ROWS} rows"
    lang = body.get("output_lang") if body.get("output_lang") in ("en", "ko") else "en"
    msg = body.get("user_message") if isinstance(body.get("user_message"), str) else ""
    return {"docs": docs, "manifest_text": list_text, "lang": lang, "user_message": msg[:2000],
            "as_of": datetime.now(KST).replace(microsecond=0).isoformat()}, ""


def make_handler(live: Live, trust_proxy: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TenderCheck"
        sys_version = ""

        def log_message(self, fmt, *args):  # path and status only; never bodies
            sys.stderr.write(f"{self.log_date_time_string()} {self.command} {urlsplit(self.path).path} "
                             f"{args[1] if len(args) > 1 else ''}\n")

        def _ip(self) -> str:
            if trust_proxy:
                ip = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0]
                if ip.strip():
                    return ip.strip()
            return self.client_address[0]

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/health":
                return self._json(200, {"ok": True, "version": VERSION, "live_enabled": live.status()["live_enabled"]})
            if path == "/api/budget":
                return self._json(200, live.status(self._ip()))
            if path == "/api/samples":
                return self._file(DATA / "samples.json")
            m = re.fullmatch(r"/api/replay/([A-Za-z0-9-]+)", path)
            if m:
                if not SAMPLE_ID.match(m.group(1)):
                    return self._json(404, {"error": "unknown sample"})
                return self._file(DATA / f"{m.group(1)}.json")
            m = re.fullmatch(r"/api/jobs/([a-f0-9]+)", path)
            if m:
                job = live.get(m.group(1)) if JOB_ID.match(m.group(1)) else None
                return self._json(200, job) if job else self._json(404, {"error": "unknown or expired job"})
            return self._static(path)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path != "/api/jobs":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._json(413, {"error": f"body must be 1 to {MAX_BODY} bytes"})
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(400, {"error": "body is not JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "body must be a JSON object"})
            if isinstance(body.get("sample_id"), str):
                if not SAMPLE_ID.match(body["sample_id"]) or not (DATA / f"{body['sample_id']}.json").exists():
                    return self._json(400, {"error": "unknown sample"})
                inputs, why = _sample_inputs(body["sample_id"]), ""
            elif live.kind == "rerun":
                return self._json(400, {"error": "this server only re-runs the bundled cases"})
            else:
                inputs, why = _custom_inputs(body)
            if why:
                return self._json(400, {"error": why})
            why = live.admit(self._ip())
            if why:
                return self._json(429 if "limit" in why else 503, {"error": why})
            digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
            job_id = live.start(inputs)
            sys.stderr.write(f"job {job_id} input sha256:{digest}\n")
            return self._json(202, {"job_id": job_id})

        def _file(self, p: Path) -> None:
            if not p.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json", "application/javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, p.read_bytes(), ctype, cache="no-cache")

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEB / rel).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self._json(404, {"error": "not found"})
            return self._file(target)

    return Handler


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python3 -m tools.serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--live", action="store_true", help="allow live runs (also needs the env switch, key and cap)")
    ap.add_argument("--trust-proxy", action="store_true", help="take the client address from CF-Connecting-IP")
    ap.add_argument("--rerun", action="store_true",
                    help="no model calls: 'run' re-sends a bundled case's recorded replies through the current code")
    a = ap.parse_args(argv)
    cap = float(os.environ.get("TENDERCHECK_GLOBAL_CAP_USD") or 0)
    enabled = bool(a.live and live_enabled_by_env() and os.environ.get(API_KEY_ENV) and cap > 0)
    live = Live(True, 0.0, kind="rerun") if a.rerun and not enabled else Live(enabled, cap)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(live, a.trust_proxy))
    print(f"TenderCheck {VERSION} on http://{a.host}:{a.port}  runs={live.kind if live.enabled else 'off'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
