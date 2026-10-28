"""TenderCheck web server (design 09 §8-2): static page + replay bundle + optional runs. Standard library only.

  python3 -m tools.serve                       # replay only, http://127.0.0.1:8765
  python3 -m tools.serve --rerun               # + "re-run": recorded model replies through the current code, $0
  TENDERCHECK_LIVE=1 MODEL_API_KEY=… TENDERCHECK_WEB_CAP_USD=5 python3 -m tools.serve --live

Live runs are off unless --live is passed AND the live switch, the key and a web budget are all in the environment.
The web budget is counted on its own ledger (runs/web_ledger.jsonl), not on the development ledger.
Limits (design 09 §8-2): body ≤ 200 KB, source ≤ 60,000 characters, bidder list ≤ 40 rows / 20,000 characters,
live: 3 runs per address per hour and 40 per day, $2.00 per run; re-run: 30 per address per hour, 1,000 per day.
Jobs are kept 1 hour. Input text is never written to the server log. Behind cloudflared pass --trust-proxy
(loopback host only) so limits apply per visitor, not to the tunnel's address.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
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
from engine.model_client import API_KEY_ENV, MetaClient, ReplayClient, live_enabled_by_env  # noqa: E402
from engine.replay import Recorder  # noqa: E402
from tools.run_scenario import scenario_inputs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = WEB / "data"
REPLAYS = ROOT / "replays"          # committed copies of the bundled cases' recorded replies (build_web_bundle)
WEB_LEDGER = ROOT / "runs" / "web_ledger.jsonl"
VERSION = "0.5.0"
KST = timezone(timedelta(hours=9))

MAX_BODY = 200_000
MAX_SOURCE_CHARS = 60_000
MAX_LIST_CHARS = 20_000
MAX_DOCS = 6
MAX_LIST_ROWS = 40
LIMITS = {"live": (3, 40), "rerun": (30, 1000)}   # per address per hour, per day
JOB_TTL_S = 3600
WEB_RUN_KEEP_S = 24 * 3600
SOCKET_TIMEOUT_S = 20
SAMPLE_ID = re.compile(r"[A-Z]-M\d(?:-[A-Z]+)?")
JOB_ID = re.compile(r"[a-f0-9]{16}")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin",
}
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class Runs:
    """Run gate, rate limits and job store. Every decision is made under one lock; a run that fails before it
    reaches the model gives its slot back."""

    def __init__(self, enabled: bool, kind: str, web_cap_usd: float = 0.0):
        self.enabled, self.kind, self.web_cap_usd = enabled, kind, web_cap_usd
        self.per_hour, self.per_day = LIMITS[kind]
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.by_ip: dict[str, list[float]] = {}
        self.day: tuple[str, int] = ("", 0)

    def budget_left(self) -> float | None:
        if self.kind != "live":
            return None
        return self.web_cap_usd - Ledger(WEB_LEDGER).totals().global_usd

    def status(self, ip: str | None = None) -> dict:
        if not self.enabled:
            return {"live_enabled": False, "kind": self.kind, "remaining_today": 0, "reason": "runs are off on this server"}
        left = self.budget_left()
        with self.lock:
            today = datetime.now(KST).strftime("%Y-%m-%d")
            used = self.day[1] if self.day[0] == today else 0
            ok = used < self.per_day and (left is None or left >= 2.0)
            reason = "" if ok else ("daily limit reached" if used >= self.per_day else "web budget used up")
            out = {"live_enabled": ok, "kind": self.kind, "remaining_today": max(0, self.per_day - used), "reason": reason}
            if ip is not None:
                recent = [t for t in self.by_ip.get(ip, []) if t > time.time() - 3600]
                out["remaining_this_hour"] = max(0, self.per_hour - len(recent))
            return out

    def admit(self, ip: str) -> str:
        """'' when a run may start (and counts it), else the reason."""
        if not self.enabled:
            return "runs are off on this server"
        left = self.budget_left()
        if left is not None and left < 2.0:
            return "web budget used up"
        with self.lock:
            now = time.time()
            today = datetime.now(KST).strftime("%Y-%m-%d")
            used = self.day[1] if self.day[0] == today else 0
            recent = [t for t in self.by_ip.get(ip, []) if t > now - 3600]
            if used >= self.per_day:
                return "daily limit reached"
            if len(recent) >= self.per_hour:
                return "hourly limit for your address reached"
            self.by_ip[ip] = recent + [now]
            self.day = (today, used + 1)
            self._expire(now)
            return ""

    def refund(self, ip: str) -> None:
        with self.lock:
            stamps = self.by_ip.get(ip, [])
            if stamps:
                stamps.pop()
            if self.day[1] > 0:
                self.day = (self.day[0], self.day[1] - 1)

    def _expire(self, now: float) -> None:
        for jid in [j for j, v in self.jobs.items() if v["created"] < now - JOB_TTL_S]:
            del self.jobs[jid]

    def start(self, inputs: dict, ip: str) -> str:
        job_id = secrets.token_hex(8)
        job = {"state": "running", "events": [], "report": None, "error": "", "created": time.time()}
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job_id, job, inputs, ip), daemon=True).start()
        return job_id

    def _run(self, job_id: str, job: dict, inputs: dict, ip: str) -> None:
        def record(ev: dict) -> None:
            with self.lock:
                job["events"].append({**ev, "t": round(time.time() - job["created"], 1)})

        def paced(ev: dict) -> None:  # an instant replay, slowed so each stage is visible
            time.sleep(0.7)
            record(ev)

        run_id = f"live_web_{job_id}"
        try:
            if self.kind == "rerun":
                client = ReplayClient(REPLAYS / inputs["sample_id"])
            else:
                ledger = Ledger(WEB_LEDGER, caps=Caps(global_stop_e8=int(round(self.web_cap_usd * E8_PER_USD))))
                client = MetaClient.from_env(live=True, ledger=ledger,
                                             recorder=Recorder(ROOT / "runs" / run_id / "job"), run_id=f"{run_id}-job")
            result = pipeline.run(inputs["docs"], inputs["manifest_text"], lang=inputs["lang"], as_of=inputs["as_of"],
                                  run_id="job", client=client, user_message=inputs["user_message"],
                                  on_event=paced if self.kind == "rerun" else record)
            report = result["report"]
            x = report["x_meta"]
            if self.kind == "rerun" and (x.get("extract_error") or x.get("match_error") or x.get("stop")):
                raise RuntimeError("the recording does not match the current code; nothing was re-run")
            x["mode"] = "live" if self.kind == "live" else "replay"
            with self.lock:
                job["report"], job["state"] = report, "done"
        except Exception as exc:  # noqa: BLE001 - the user sees a short message, the log gets the class
            sys.stderr.write(f"job {job_id} failed: {type(exc).__name__}: {CONTROL.sub('?', str(exc))[:300]}\n")
            if self.kind == "rerun":
                self.refund(ip)
            message = str(exc) if isinstance(exc, RuntimeError) else f"the run stopped ({type(exc).__name__})"
            with self.lock:
                job["state"], job["error"] = "failed", message

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            self._expire(time.time())
            job = self.jobs.get(job_id)
            if job is None:
                return None
            return {"state": job["state"], "stage_events": list(job["events"]), "report": job["report"],
                    "error": job["error"]}


def _sample_inputs(sample_id: str) -> dict:
    inp = scenario_inputs(sample_id)
    return {"docs": inp["docs"], "manifest_text": inp["manifest_text"], "lang": inp["lang"], "as_of": inp["as_of"],
            "user_message": inp["user_message"], "sample_id": sample_id}


def _clean_text(value: str) -> bool:
    try:
        value.encode("utf-8")
        return True
    except UnicodeEncodeError:  # unpaired surrogates
        return False


def _custom_inputs(body: dict) -> tuple[dict | None, str]:
    docs_in = body.get("docs")
    if not isinstance(docs_in, list) or not 1 <= len(docs_in) <= MAX_DOCS:
        return None, f"give 1 to {MAX_DOCS} source documents"
    docs, total = [], 0
    for i, d in enumerate(docs_in):
        if not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"].strip():
            return None, f"document {i + 1} has no text"
        title = str(d.get("title") or f"Document {i + 1}")[:80]
        if not _clean_text(d["text"]) or not _clean_text(title):
            return None, f"document {i + 1} is not valid text"
        total += len(d["text"])
        docs.append({"doc_id": f"D{i + 1}", "title": title, "text": d["text"], "header": title})
    if total > MAX_SOURCE_CHARS:
        return None, f"source text is over {MAX_SOURCE_CHARS} characters"
    list_text = body.get("list_text")
    if not isinstance(list_text, str) or not list_text.strip() or not _clean_text(list_text):
        return None, "the bidder list is empty or not valid text"
    if len(list_text) > MAX_LIST_CHARS or sum(1 for line in list_text.splitlines() if "|" in line) > MAX_LIST_ROWS + 1:
        return None, f"the bidder list is over {MAX_LIST_ROWS} rows or {MAX_LIST_CHARS} characters"
    lang = body.get("output_lang") if body.get("output_lang") in ("en", "ko") else "en"
    msg = body.get("user_message") if isinstance(body.get("user_message"), str) else ""
    if not _clean_text(msg):
        return None, "the note is not valid text"
    return {"docs": docs, "manifest_text": list_text, "lang": lang, "user_message": msg[:2000],
            "as_of": datetime.now(KST).replace(microsecond=0).isoformat()}, ""


def _visitor(raw: str) -> str | None:
    """A rate-limit key for one visitor: the address itself, or its /64 for IPv6. None when not an address."""
    try:
        ip = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def make_handler(runs: Runs, trust_proxy: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TenderCheck"
        sys_version = ""
        timeout = SOCKET_TIMEOUT_S  # a client that stops sending is dropped, not kept on a thread

        def log_message(self, fmt, *args):  # method, path (control characters replaced) and status; never bodies
            path = CONTROL.sub("?", urlsplit(self.path).path)[:200] if isinstance(self.path, str) else "?"
            sys.stderr.write(f"{self.log_date_time_string()} {self.command} {path} {args[1] if len(args) > 1 else ''}\n")

        def end_headers(self):  # every response, including send_error pages, carries the security headers
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            super().end_headers()

        def _ip(self) -> str:
            if trust_proxy:
                key = _visitor(self.headers.get("CF-Connecting-IP") or "")
                if key:
                    return key
            return _visitor(self.client_address[0]) or self.client_address[0]

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store", extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj, extra: dict | None = None) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                       extra=extra)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/health":
                return self._json(200, {"ok": True, "version": VERSION, "live_enabled": runs.status()["live_enabled"]})
            if path == "/api/budget":
                return self._json(200, runs.status(self._ip()))
            if path == "/api/samples":
                return self._file(DATA / "samples.json")
            m = re.fullmatch(r"/api/replay/([A-Za-z0-9-]+)", path)
            if m:
                if not SAMPLE_ID.fullmatch(m.group(1)):
                    return self._json(404, {"error": "unknown sample"})
                return self._file(DATA / f"{m.group(1)}.json")
            m = re.fullmatch(r"/api/jobs/([a-f0-9]+)", path)
            if m:
                job = runs.get(m.group(1)) if JOB_ID.fullmatch(m.group(1)) else None
                return self._json(200, job) if job else self._json(404, {"error": "unknown or expired job"})
            return self._static(path)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path != "/api/jobs":
                return self._json(404, {"error": "not found"})
            # Only same-origin JSON: a cross-site form or text/plain POST cannot start (and bill) a run.
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                return self._json(415, {"error": "send application/json"})
            if (self.headers.get("Sec-Fetch-Site") or "same-origin") not in ("same-origin", "none"):
                return self._json(403, {"error": "cross-site request refused"})
            raw_len = self.headers.get("Content-Length")
            if raw_len is None:
                return self._json(411, {"error": "Content-Length required"})
            try:
                length = int(raw_len)
            except ValueError:
                return self._json(400, {"error": "bad Content-Length"})
            if length <= 0 or length > MAX_BODY:
                return self._json(413 if length > MAX_BODY else 400, {"error": f"body must be 1 to {MAX_BODY} bytes"})
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                return self._json(400, {"error": "body is not JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "body must be a JSON object"})
            if isinstance(body.get("sample_id"), str):
                sid = body["sample_id"]
                if not SAMPLE_ID.fullmatch(sid) or not (DATA / f"{sid}.json").exists():
                    return self._json(400, {"error": "unknown sample"})
                if runs.kind == "rerun" and not (REPLAYS / sid / "calls.jsonl").exists():
                    return self._json(400, {"error": "this case has no recording on this server"})
                inputs, why = _sample_inputs(sid), ""
            elif runs.kind == "rerun":
                return self._json(400, {"error": "this server only re-runs the bundled cases"})
            else:
                inputs, why = _custom_inputs(body)
            if why:
                return self._json(400, {"error": why})
            digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=False).encode("utf-8", "surrogatepass")).hexdigest()[:12]
            ip = self._ip()
            why = runs.admit(ip)
            if why:
                code = 429 if "limit" in why else 503
                return self._json(code, {"error": why}, extra={"Retry-After": "3600"} if code == 429 else None)
            job_id = runs.start(inputs, ip)
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
            try:
                target = (WEB / rel).resolve()
            except (ValueError, OSError):
                return self._json(404, {"error": "not found"})
            if WEB.resolve() not in target.parents or not target.is_file():
                return self._json(404, {"error": "not found"})
            return self._file(target)

    return Handler


def prune_web_runs(now: float | None = None) -> int:
    """Delete this server's own live job folders (runs/live_web_*) older than a day. Returns how many."""
    now = now or time.time()
    gone = 0
    for d in (ROOT / "runs").glob("live_web_*"):
        if d.is_dir() and d.stat().st_mtime < now - WEB_RUN_KEEP_S:
            shutil.rmtree(d, ignore_errors=True)
            gone += 1
    return gone


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python3 -m tools.serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--live", action="store_true", help="allow live runs (also needs the env switch, key and web cap)")
    ap.add_argument("--rerun", action="store_true",
                    help="no model calls: 'run' re-sends a bundled case's recorded replies through the current code")
    ap.add_argument("--trust-proxy", action="store_true",
                    help="take the visitor address from CF-Connecting-IP (only with a loopback --host, behind cloudflared)")
    a = ap.parse_args(argv)
    if a.trust_proxy and a.host not in ("127.0.0.1", "::1", "localhost"):
        ap.error("--trust-proxy needs a loopback --host: anyone reaching the port directly could fake the header")
    try:
        cap = float(os.environ.get("TENDERCHECK_WEB_CAP_USD") or 0)
    except ValueError:
        cap = 0.0
    if not math.isfinite(cap) or cap < 0 or cap > 140:
        ap.error("TENDERCHECK_WEB_CAP_USD must be a number from 0 to 140")
    live_ok = bool(a.live and live_enabled_by_env() and os.environ.get(API_KEY_ENV) and cap > 0)
    if a.live and not live_ok:
        print("live runs stay off: needs TENDERCHECK_LIVE=1, MODEL_API_KEY and TENDERCHECK_WEB_CAP_USD > 0")
    runs = Runs(True, "live", cap) if live_ok else Runs(bool(a.rerun), "rerun")
    pruned = prune_web_runs()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(runs, a.trust_proxy))
    srv.daemon_threads = True
    print(f"TenderCheck {VERSION} on http://{a.host}:{a.port}  runs={runs.kind if runs.enabled else 'off'}"
          f"{f'  pruned {pruned} old job folders' if pruned else ''}")
    if not a.trust_proxy:
        print("note: behind cloudflared, start with --trust-proxy or every visitor shares one rate limit")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
