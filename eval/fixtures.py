"""Read-only access to scenarios/ and contracts/."""
import json
import unicodedata
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCENARIOS = REPO / "scenarios"
CONTRACTS = REPO / "contracts"


def read_text(path):
    return Path(path).read_bytes().decode("utf-8")


def read_json(path):
    return json.loads(read_text(path))


@lru_cache(maxsize=None)
def packages():
    return read_json(SCENARIOS / "packages.json")


@lru_cache(maxsize=None)
def answers(pkg_id):
    return read_json(SCENARIOS / "answers" / f"{pkg_id}.json")


@lru_cache(maxsize=None)
def card(card_id):
    return read_json(SCENARIOS / "tests" / f"{card_id}.json")


@lru_cache(maxsize=None)
def regression():
    return read_json(SCENARIOS / "regression.json")


@lru_cache(maxsize=None)
def grading_schema():
    return read_json(CONTRACTS / "report_grading.schema.json")["schema"]


@lru_cache(maxsize=None)
def fixed_text():
    return read_json(CONTRACTS / "fixed_text.json")["fixed_text"]


def package_of_manifest(manifest_id):
    for pkg_id, pkg in packages().items():
        if manifest_id in pkg["manifests"]:
            return pkg_id
    raise KeyError(f"unknown manifest {manifest_id}")


@lru_cache(maxsize=None)
def package_docs(pkg_id):
    """Source documents without their [label] line: [(doc_id, title, text)]."""
    out = []
    for doc in packages()[pkg_id]["docs"]:
        text = read_text(SCENARIOS / doc["file"])
        first, _, rest = text.partition("\n")
        if first != doc["header"]:
            raise ValueError(f"{doc['file']}: first line {first!r} != header {doc['header']!r}")
        out.append((doc["doc_id"], doc["title"], rest))
    return tuple(out)


def manifest_text(manifest_id):
    pkg = packages()[package_of_manifest(manifest_id)]
    return read_text(SCENARIOS / pkg["manifests"][manifest_id])


_FILE_COLUMNS = ("file", "제출 파일명")


def manifest_files(text):
    """File names from the manifest table (column 'file' or '제출 파일명')."""
    lines = text.split("\n")
    for h, line in enumerate(lines):
        cells = [c.strip() for c in line.split("|")]
        hit = [c for c in _FILE_COLUMNS if c in cells]
        if hit:
            col = cells.index(hit[0])
            break
    else:
        raise ValueError("manifest table header not found")
    files = []
    for line in lines[h + 1:]:
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        name = unicodedata.normalize("NFC", cells[col])
        if name and name not in files:
            files.append(name)
    return files
