"""Q5 date/time parsing and F7 validity arithmetic (design 09 §2-5). Code, not the model,
reads every date (D3: generative AI never does arithmetic)."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))

DATE_RES = (
    ("iso", re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), "ymd"),
    ("ko_word", re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"), "ymd"),
    ("ko_dot", re.compile(r"(?<![\d.])(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})(?:\s*\.)?(?!\d)"), "ymd"),
    ("en_dmy", re.compile(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})\.?,?\s+(\d{{4}})(?!\d)", re.I), "dmy"),
    ("en_mdy", re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})(?!\d)", re.I), "mdy"),
)
TIME_RES = (
    ("ko_ampm_clock", re.compile(r"(오전|오후)\s*(\d{1,2}):([0-5]\d)")),
    ("ko_hour", re.compile(r"(오전|오후)?\s*(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?")),
    ("en_ampm", re.compile(r"(?<![\d:])(\d{1,2})(?::([0-5]\d))?\s*([ap])\.?\s*m\b\.?", re.I)),
    ("clock", re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?![\d:])")),
    ("noon_ko", re.compile(r"정오")),
)
TZ_RES = (
    ("utc_offset", re.compile(r"\b(?:UTC|GMT)\s*([+\-−])\s*(\d{1,2})(?::?(\d{2}))?(?!\d)")),
    ("iso_offset", re.compile(r"(?<=:\d\d)([+\-])(\d{2}):?(\d{2})(?!\d)")),
    ("iso_z", re.compile(r"(?<=:\d\d)Z\b")),
    ("utc", re.compile(r"\b(?:UTC|GMT)\b(?!\s*[+\-−]\s*\d)")),
    ("kst", re.compile(r"\bKST\b|\bKorea(?:n)?\s+Standard\s+Time\b|한국\s*표준시", re.I)),
    ("iana", re.compile(r"\b([A-Z][A-Za-z_]+(?:/[A-Z][A-Za-z_\-]+)+)\b")),
)
KO_WEEKDAYS = "월화수목금토일"
EN_WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
               "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
               "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
_KO_WD_RE = re.compile(r"\(\s*([월화수목금토일])(?:요일)?\s*\)|([월화수목금토일])요일")
_EN_WD_RE = re.compile(r"\b(" + "|".join(sorted(EN_WEEKDAYS, key=len, reverse=True)) + r")\b\.?", re.I)

CONVENTIONS = ("anchor_not_counted", "anchor_counted")


def _blank(work: list[str], m: re.Match) -> None:
    for i in range(m.start(), m.end()):
        work[i] = " "


def _tz_candidates(text: str) -> tuple[list[tzinfo], list[str]]:
    """(tzinfos stated in `text`, errors). Unknown IANA-looking names are ignored."""
    found: list[tzinfo] = []
    errors: list[str] = []
    for kind, rx in TZ_RES:
        for m in rx.finditer(text):
            if kind in ("utc_offset", "iso_offset"):
                sign = -1 if m.group(1) in "-−" else 1
                hours, minutes = int(m.group(2)), int(m.group(3) or 0)
                if hours > 14 or minutes > 59:
                    errors.append(f"bad offset {m.group(0)!r}")
                    continue
                found.append(timezone(sign * timedelta(hours=hours, minutes=minutes)))
            elif kind in ("iso_z", "utc"):
                found.append(timezone.utc)
            elif kind == "kst":
                found.append(ZoneInfo("Asia/Seoul"))
            else:
                try:
                    found.append(ZoneInfo(m.group(1)))
                except (ZoneInfoNotFoundError, ValueError, OSError):
                    pass
    return found, errors


def _tz_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for kind, rx in TZ_RES:
        for m in rx.finditer(text):
            if kind == "iana":
                try:
                    ZoneInfo(m.group(1))
                except (ZoneInfoNotFoundError, ValueError, OSError):
                    continue
            spans.append((m.start(), m.end()))
    return spans


def _offset_str(td: timedelta) -> str:
    total = int(td.total_seconds() // 60)
    sign = "-" if total < 0 else "+"
    h, mnt = divmod(abs(total), 60)
    return f"{sign}{h:02d}:{mnt:02d}"


def _pick_tz(cands: list[tzinfo], probe: datetime) -> tuple[tzinfo | None, str]:
    offsets = {probe.replace(tzinfo=tz).utcoffset() for tz in cands}
    if not cands:
        return None, ""
    if len(offsets) > 1:
        return None, "conflicting time zones"
    return cands[0], ""


def parse_datetime_text(raw: str, tz_quote: str = "") -> dict:
    """Parse one date (+ optional time and zone) from source text.

    Returns {ok, error, date, time, offset, tz_source, iso_local, weekday_stated, weekday_ok, info}.
    Zone comes from `raw` first, else from `tz_quote` (a same-document sentence such as
    "All times in this Bid Data Sheet are ... (UTC+03:00)."); none -> naive + TZ_NOT_STATED.
    Weekday mismatch is an info flag only; the date is never corrected.
    """
    res = {"ok": False, "error": "", "date": "", "time": "", "offset": "", "tz_source": "",
           "iso_local": "", "weekday_stated": "", "weekday_ok": None, "info": []}
    if not raw or not raw.strip():
        res["error"] = "empty"
        return res
    work = list(raw)
    for s, e in _tz_spans(raw):
        for i in range(s, e):
            work[i] = " "

    dates = []
    for _, rx, order in DATE_RES:
        text = "".join(work)
        for m in rx.finditer(text):
            g = m.groups()
            try:
                if order == "ymd":
                    d = date(int(g[0]), int(g[1]), int(g[2]))
                elif order == "dmy":
                    d = date(int(g[2]), MONTHS[g[1].lower()], int(g[0]))
                else:
                    d = date(int(g[2]), MONTHS[g[0].lower()], int(g[1]))
            except ValueError:
                res["error"] = f"invalid date {m.group(0).strip()!r}"
                return res
            dates.append(d)
            _blank(work, m)
    if not dates:
        res["error"] = "no date found"
        return res
    if len(set(dates)) > 1:
        res["error"] = "more than one date"
        return res
    d = dates[0]

    times = []
    for kind, rx in TIME_RES:
        text = "".join(work)
        for m in rx.finditer(text):
            g = m.groups()
            if kind == "noon_ko":
                t = time(12, 0)
            elif kind in ("ko_ampm_clock", "ko_hour"):
                hour, minute = int(g[1]), int(g[2] or 0)
                if g[0] == "오전":
                    hour = 0 if hour == 12 else hour
                elif g[0] == "오후":
                    hour = hour if hour == 12 else hour + 12
                if hour > 23 or minute > 59:
                    res["error"] = f"invalid time {m.group(0).strip()!r}"
                    return res
                t = time(hour, minute)
            elif kind == "en_ampm":
                hour, minute = int(g[0]), int(g[1] or 0)
                if not 1 <= hour <= 12:
                    res["error"] = f"invalid time {m.group(0).strip()!r}"
                    return res
                hour = hour % 12 + (12 if g[2].lower() == "p" else 0)
                t = time(hour, minute)
            else:
                t = time(int(g[0]), int(g[1]), int(g[2] or 0))
            times.append(t)
            _blank(work, m)
    if len(set(times)) > 1:
        res["error"] = "more than one time"
        return res
    t = times[0] if times else None

    probe = datetime.combine(d, t or time(12, 0))
    raw_tz, errs = _tz_candidates(raw)
    q_tz, q_errs = _tz_candidates(tz_quote) if tz_quote else ([], [])
    if errs or q_errs:
        res["error"] = (errs + q_errs)[0]
        return res
    tz, err = _pick_tz(raw_tz, probe)
    source = "raw" if tz else ""
    if not err and q_tz:
        qtz, err = _pick_tz(q_tz, probe)
        if not err and tz is None:
            tz, source = qtz, "tz_quote"
        elif not err and probe.replace(tzinfo=tz).utcoffset() != probe.replace(tzinfo=qtz).utcoffset():
            err = "time zone in raw differs from tz_quote"
    if err:
        res["error"] = err
        return res

    ko = _KO_WD_RE.search(raw)
    en = _EN_WD_RE.search(raw)
    if ko:
        res["weekday_stated"] = ko.group(1) or ko.group(2)
        res["weekday_ok"] = KO_WEEKDAYS.index(res["weekday_stated"]) == d.weekday()
    elif en:
        res["weekday_stated"] = en.group(1)
        res["weekday_ok"] = EN_WEEKDAYS[en.group(1).lower()] == d.weekday()
    if res["weekday_ok"] is False:
        res["info"].append("WEEKDAY_MISMATCH")
    if tz is None:
        res["info"].append("TZ_NOT_STATED")

    res.update(ok=True, date=d.isoformat(), tz_source=source)
    if tz is not None:
        res["offset"] = _offset_str(probe.replace(tzinfo=tz).utcoffset())
    if t is None:
        res["iso_local"] = d.isoformat()
    else:
        dt = datetime.combine(d, t, tzinfo=tz)
        res["time"] = t.strftime("%H:%M") if not t.second else t.strftime("%H:%M:%S")
        res["iso_local"] = dt.isoformat()
    return res


def as_datetime(parsed: dict) -> datetime | date | None:
    if not parsed.get("ok"):
        return None
    if not parsed["time"]:
        return date.fromisoformat(parsed["date"])
    return datetime.fromisoformat(parsed["iso_local"])


def same_reading(parsed: dict, model_iso: str) -> tuple[bool, str]:
    """Q5: compare the code's reading with the model's `iso_local`. Only components the code
    read are compared; offsets are compared when both sides carry one."""
    try:
        m = datetime.fromisoformat(model_iso.strip())
    except (ValueError, AttributeError):
        return False, f"model iso_local {model_iso!r} is not ISO 8601"
    if m.date().isoformat() != parsed["date"]:
        return False, f"date: code {parsed['date']} vs model {m.date().isoformat()}"
    has_time = "T" in model_iso or " " in model_iso.strip()
    if parsed["time"]:
        if not has_time:
            return False, f"time: code {parsed['time']} vs model none"
        code_t = time.fromisoformat(parsed["time"])
        if (m.hour, m.minute, m.second) != (code_t.hour, code_t.minute, code_t.second):
            return False, f"time: code {parsed['time']} vs model {m.strftime('%H:%M:%S')}"
    if parsed["offset"] and m.tzinfo is not None:
        mo = _offset_str(m.utcoffset())
        if mo != parsed["offset"]:
            return False, f"offset: code {parsed['offset']} vs model {mo}"
    return True, ""


def offset_tz(offset: str) -> timezone:
    m = re.fullmatch(r"([+-])(\d{2}):(\d{2})", offset)
    if not m:
        raise ValueError(f"bad offset {offset!r}")
    sign = -1 if m.group(1) == "-" else 1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))


def to_offset(value: str | datetime, offset: str) -> datetime:
    """Convert an aware datetime (or ISO string) to a fixed offset like '+03:00'."""
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("naive datetime cannot be converted (time zone not stated)")
    return dt.astimezone(offset_tz(offset))


# ---------------- validity arithmetic (F7) ----------------

def to_date(value) -> date | None:
    if value is None or value == "" or value == "unknown":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).strip()).date()


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def period_end(start: date, *, days: int = 0, months: int = 0, convention: str) -> date:
    """Last day of a period beginning at `start`. anchor_not_counted: counting starts the next
    day (start + N). anchor_counted: `start` is day 1 (start + N - 1)."""
    end = add_months(start, months) if months else start + timedelta(days=days)
    return end - timedelta(days=1) if convention == "anchor_counted" else end


def window_start(anchor: date, *, days: int = 0, months: int = 0, convention: str) -> date:
    """First day of a look-back window ending at `anchor` (e.g. issued within 3 months)."""
    start = add_months(anchor, -months) if months else anchor - timedelta(days=days)
    return start + timedelta(days=1) if convention == "anchor_counted" else start


_EXAMPLE_RE = re.compile(r"for example|e\.g\.|예[:：]|예시|예를 들어", re.I)
_DM_RES = (
    re.compile(rf"(?<!\d)(\d{{1,2}})\s+({_MON})\b", re.I),
    re.compile(rf"\b({_MON})\s+(\d{{1,2}})(?!\d)", re.I),
    re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
)


def counting_from_example(raw: str, *, days: int = 0, months: int = 0) -> tuple[str, ...] | None:
    """Counting convention fixed by a worked example in the source text, e.g. "(for example, a
    certificate issued on 1 March is valid through 30 May)". None if absent or inconclusive."""
    m = _EXAMPLE_RE.search(raw or "")
    if not m or not (days or months):
        return None
    tail = list(raw[m.end():])
    pairs = []
    for i, rx in enumerate(_DM_RES):
        for mm in rx.finditer("".join(tail)):
            g = mm.groups()
            if i == 0:
                pairs.append((mm.start(), MONTHS[g[1].lower()], int(g[0])))
            elif i == 1:
                pairs.append((mm.start(), MONTHS[g[0].lower()], int(g[1])))
            else:
                pairs.append((mm.start(), int(g[0]), int(g[1])))
            _blank(tail, mm)
    if len(pairs) != 2:
        return None
    (_, m1, d1), (_, m2, d2) = sorted(pairs)
    picks = set()
    for year in (2025, 2024):
        try:
            s = date(year, m1, d1)
            e = date(year if (m2, d2) >= (m1, d1) else year + 1, m2, d2)
        except ValueError:
            return None
        hits = tuple(c for c in CONVENTIONS if period_end(s, days=days, months=months, convention=c) == e)
        picks.add(hits)
    if len(picks) == 1:
        (hits,) = picks
        if len(hits) == 1:
            return hits
    return None


def conventions_for(validity: dict) -> tuple[str, ...]:
    rule = validity.get("rule_type", "none")
    if rule == "valid_until_date":
        return ("none",)
    if rule == "valid_for_from_issue":
        found = counting_from_example(validity.get("raw", ""), days=int(validity.get("days") or 0),
                                      months=int(validity.get("months") or 0))
        if found:
            return found
    return CONVENTIONS


def required_until(validity: dict) -> date | None:
    """valid_until_date: the date the code reads from `raw`. None if unreadable, or if the model's
    `until_iso` disagrees with it."""
    parsed = parse_datetime_text(validity.get("raw", ""))
    if not parsed["ok"]:
        return None
    code = date.fromisoformat(parsed["date"])
    if validity.get("until_iso"):
        try:
            if to_date(validity["until_iso"]) != code:
                return None
        except ValueError:
            return None
    return code


def evaluate_validity(validity: dict, *, issue_date=None, file_until=None,
                      anchors: dict | None = None, as_of=None) -> dict:
    """Evaluate one validity rule (Req.validity) for one file under every reading.

    anchors: {label: date|ISO string} — candidate anchor dates (both sides of a deadline conflict).
    Returns {result: pass|fail|unknown|not_applicable, reason_codes, info, readings, split_by, conventions}.
    All readings agree -> that result (+ SAME_UNDER_ALL_READINGS when >1 reading). Readings differ
    -> unknown with CONFLICT_DEPENDENT (differs by anchor) and/or COUNTING_AMBIGUOUS (differs by
    counting convention).
    """
    rule = validity.get("rule_type", "none")
    out = {"result": "unknown", "rule_type": rule, "reason_codes": [], "info": [], "readings": [],
           "split_by": [], "conventions": []}
    if rule == "none":
        out["result"] = "not_applicable"
        return out
    days, months = int(validity.get("days") or 0), int(validity.get("months") or 0)
    issue, until = to_date(issue_date), to_date(file_until)
    anchor_dates = {k: to_date(v) for k, v in (anchors or {}).items()}
    anchor_dates = {k: v for k, v in anchor_dates.items() if v is not None}
    required = required_until(validity) if rule == "valid_until_date" else None

    missing = []
    if rule in ("valid_for_from_issue", "issued_within_before_anchor") and issue is None:
        missing.append("issue_date")
    if rule in ("valid_until_date", "valid_at_least_after_anchor") and until is None:
        missing.append("valid_until")
    if rule != "valid_until_date":
        if not anchor_dates:
            missing.append("anchor_date")
        if not (days or months):
            missing.append("period")
    elif required is None:
        missing.append("required_until")
    asof = to_date(as_of)
    if issue and asof and issue > asof:
        out["info"].append("ISSUE_DATE_AFTER_AS_OF")
    if missing:
        out["reason_codes"] = [f"ATTRIBUTE_UNKNOWN:{f}" for f in missing]
        return out

    convs = conventions_for(validity)
    out["conventions"] = list(convs)
    items = anchor_dates.items() if rule != "valid_until_date" else [("", None)]
    for label, a in items:
        for c in convs:
            if rule == "valid_for_from_issue":
                limit = period_end(issue, days=days, months=months, convention=c)
                ok = issue <= a <= limit
            elif rule == "issued_within_before_anchor":
                limit = window_start(a, days=days, months=months, convention=c)
                ok = limit <= issue <= a
            elif rule == "valid_until_date":
                limit = required
                ok = until >= limit
            elif rule == "valid_at_least_after_anchor":
                limit = period_end(a, days=days, months=months, convention=c)
                ok = until >= limit
            else:
                raise ValueError(f"unknown rule_type {rule!r}")
            out["readings"].append({"anchor": label, "anchor_date": a.isoformat() if a else "",
                                    "convention": c, "limit": limit.isoformat(),
                                    "result": "pass" if ok else "fail"})

    results = {r["result"] for r in out["readings"]}
    if len(results) == 1:
        out["result"] = results.pop()
        if len(out["readings"]) > 1:
            out["reason_codes"].append("SAME_UNDER_ALL_READINGS")
    else:
        by = {(r["anchor"], r["convention"]): r["result"] for r in out["readings"]}
        labels = [k for k, _ in items]
        if any(len({by[(a, c)] for a in labels}) > 1 for c in convs):
            out["split_by"].append("anchor")
            out["reason_codes"].append("CONFLICT_DEPENDENT")
        if any(len({by[(a, c)] for c in convs}) > 1 for a in labels):
            out["split_by"].append("convention")
            out["reason_codes"].append("COUNTING_AMBIGUOUS")
    if rule == "issued_within_before_anchor" and "ISSUE_DATE_AFTER_AS_OF" in out["info"] and out["result"] == "pass":
        out["result"] = "unknown"
    return out
