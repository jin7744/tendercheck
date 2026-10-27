"""Pre-commit scrub: block secrets, local paths and private identifiers from entering the public repo.

Usage:
  python tools/scrub_check.py            # scan staged files (git diff --cached)
  python tools/scrub_check.py --all      # scan every tracked + untracked-not-ignored file
  python tools/scrub_check.py FILE ...   # scan given files

Private patterns (names, handles, hostnames) live in tools/scrub_private_patterns.txt,
which is git-ignored so the public repo never contains them. One regex per line, '#' comments.
Exit code 1 if anything matches.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_FILE = ROOT / "tools" / "scrub_private_patterns.txt"

PUBLIC_PATTERNS: list[tuple[str, str]] = [
    ("meta_api_key", r"LLM_[0-9]{6,}_[A-Za-z0-9_\-]{8,}"),
    ("bearer_literal", r"(?i)bearer\s+[A-Za-z0-9_\-\.]{24,}"),
    ("openai_style_key", r"\bsk-[A-Za-z0-9_\-]{20,}"),
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    ("api_key_assignment", r"(?i)api_key\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']"),
    ("windows_user_path", r"(?i)[A-Z]:\\Users\\"),
    ("wsl_mount_path", r"/mnt/[a-z]/Users/"),
    ("private_ipv4", r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
]

BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".mp4", ".webm", ".zip", ".ico", ".woff", ".woff2", ".ttf", ".otf"}
SELF = Path(__file__).resolve()


def load_patterns() -> list[tuple[str, re.Pattern[str]]]:
    pats = [(name, re.compile(rx)) for name, rx in PUBLIC_PATTERNS]
    if PRIVATE_FILE.exists():
        for i, line in enumerate(PRIVATE_FILE.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append((f"private#{i}", re.compile(line)))
    key = os.environ.get("MODEL_API_KEY", "")
    if len(key) >= 16:
        pats.append(("env_MODEL_API_KEY_value", re.compile(re.escape(key))))
    return pats


def git_lines(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return [l for l in out.stdout.splitlines() if l.strip()]


def target_files(argv: list[str]) -> list[Path]:
    if argv and argv[0] == "--all":
        names = git_lines("ls-files") + git_lines("ls-files", "--others", "--exclude-standard")
    elif argv:
        return [Path(a).resolve() for a in argv]
    else:
        names = git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [(ROOT / n).resolve() for n in names]


def scan(path: Path, pats: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    if path == SELF or path == PRIVATE_FILE or path.suffix.lower() in BINARY_EXT or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{path.name}: not utf-8 text (check manually)"]
    hits = []
    for ln, line in enumerate(text.splitlines(), 1):
        for name, rx in pats:
            if rx.search(line):
                # never echo the matched text itself (it may be the secret)
                hits.append(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}:{ln}: {name}")
    return hits


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pats = load_patterns()
    hits = [h for f in target_files(argv) for h in scan(f, pats)]
    if hits:
        print("SCRUB FAIL — remove these before committing:")
        print("\n".join(hits))
        return 1
    print(f"SCRUB OK ({len(pats)} patterns)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
