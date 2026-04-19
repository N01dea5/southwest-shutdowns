#!/usr/bin/env python3
"""Convert an MS Project Gantt PDF export into the dashboard's task-tree JSON.

The source-of-truth PDF exports go into `data/raw/` and are keyed into
`GANTT_MAP` below by filename stem. Output lands at
`data/gantt/<shutdown_id>.json` and feeds the Execution tab on the
dashboard. Per-task progress overlays come from SharePoint via a separate
sync script; this script only produces the baseline tree.

Why parse the PDF directly
--------------------------
The MS Project source lives with the shutdown scheduler; the PDF is what
we're given and what gets emailed to site each morning. `pdftotext -raw`
yields 1-3 lines per task which we reassemble back into a record. The
layout has enough anchors (duration "N hrs?", dates "DD/MM/YY HH:MM",
"N%", Outline Level numeral) that a small regex pipeline is reliable for
the current template. If MS Project adds/removes a column it will break —
add a new test case in `tests/gantt_samples/` before fixing so we don't
drift silently.

Supported fields per task
-------------------------
id (from the leading numeric), wbs (FLOC), wo (work order), name,
outline_level (1..N; drives hierarchy), start_iso, finish_iso,
duration_hours, predecessors (list of "{ref}{mode}" strings e.g. "184SS",
"128FS+2"), resources (WC Description + Team + Resource Names),
baseline_percent_complete. `type` is derived: "milestone" when duration
== 0, "summary" when outline_level < max observed AND has children at
level+1, else "task".
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
import sys


# Map each Gantt PDF's filename stem → (shutdown_id, source label).
# Same shape as ROSTER_MAP in parse_rapidcrews.py. Keep lowercase comparisons
# tolerant so "SRG Roller Crew Apr15 Draft.pdf" matches even if the scheduler
# renames to "SRG Roller Crew Apr16 Draft.pdf" — we just bump this table.
GANTT_MAP: dict[str, tuple[str, str]] = {
    "SRG Roller Crew Apr15 Draft": (
        "covalent-2026-04",
        "MTHS2603 SRG Roller Crew — draft 15 Apr",
    ),
}

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR   = REPO_ROOT / "data" / "raw"
OUT_DIR   = REPO_ROOT / "data" / "gantt"


# --------------------------------------------------------------------------- pdftotext

def _pdftotext(pdf_path: pathlib.Path) -> str:
    """Run `pdftotext -raw`. -raw strips the visual Gantt chart columns,
    leaving a text stream that's ~1 line per task-row with occasional
    overflow onto a second line for leaf tasks (resource info continuation).
    """
    try:
        out = subprocess.check_output(
            ["pdftotext", "-raw", str(pdf_path), "-"],
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError as e:
        raise SystemExit(
            "pdftotext not found. Install poppler-utils "
            "(apt install poppler-utils / brew install poppler)."
        ) from e
    return out.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- block splitter

# Task ID appears on its own line. MS Project's task IDs are 1-6 digits; we
# reject obvious numeric noise (long unique-IDs happen inside the body).
_ID_RE = re.compile(r"^\s*(\d{1,6})\s*$")


def _split_blocks(text: str) -> list[tuple[int, str]]:
    """Group the raw text into (task_id, body) tuples. Body is the whitespace-
    normalised concatenation of the lines between this ID and the next ID.
    Leading boilerplate (header row, cover text) is discarded."""
    blocks: list[tuple[int, list[str]]] = []
    current: tuple[int, list[str]] | None = None
    for line in text.splitlines():
        m = _ID_RE.match(line)
        if m:
            if current is not None:
                blocks.append(current)
            current = (int(m.group(1)), [])
        elif current is not None:
            if line.strip():
                current[1].append(line.strip())
    if current is not None:
        blocks.append(current)
    # Normalise each block's body to a single space-separated string.
    return [(tid, re.sub(r"\s+", " ", " ".join(lines)).strip())
            for tid, lines in blocks]


# --------------------------------------------------------------------------- field extractors

# Duration anchor: "N hrs", "N.NN hrs", "N mins", "N days", optionally with "?"
# for estimated durations. Followed by " M ehrs" for the leveling-delay
# column. This pair is our primary split point — everything before it is
# (preds/succs/FLOC/WO/name), everything after is tail.
_DUR_ANCHOR = re.compile(
    r"\s(\d+(?:\.\d+)?)\s*(hrs?|mins?|days?|edays?)(\?)?\s+"
    r"(\d+(?:\.\d+)?)\s*(ehrs?|edays?|emins?)\s+"
)
_DATE_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{2})\s+(\d{1,2}):(\d{2})"
)

# Tail ends with: "... <pct>% <unique_id> <constraint_date> <outline_level> <...rest>"
# where constraint_date is either "NA" or a date. pct is 0-100 (sometimes
# "100%" on complete tasks).
_TAIL_RE = re.compile(
    r"(\d{1,3})%\s+(\d+)\s+(NA|\d{1,2}/\d{1,2}/\d{2})\s+(\d)\s+(.*)$"
)

# Predecessor/successor refs can run together without spaces ("184SS120-121-CV-001"
# means predecessor "184SS" then FLOC "120-121-CV-001"). A pred ref is one or
# more digits optionally followed by a relationship type (FS/SS/FF/SF). We
# deliberately DON'T model lag offsets ("128FS+2d") — none appear in the
# current export, and a bare "[+-]\d+" would false-match FLOC hyphens like
# "120-121". Add stricter lag parsing back if a future schedule needs it.
_PRED_REF  = re.compile(r"\d+(?:SS|FS|FF|SF)?(?:,\d+(?:SS|FS|FF|SF)?)*")
# After consuming a pred ref with a relationship type (the 2-letter suffix
# SS/FS/FF/SF), anything that follows is the start of the FLOC token — which
# might itself start with a digit ("120-121-CV-001") or a letter ("CV-023").
# So the lookahead is "any character": the boundary is just "we already
# matched a complete relationship type, stop here and let the remainder
# become the FLOC column".
_PRED_HEAD = re.compile(r"^(\d+(?:SS|FS|FF|SF))(?=.)")


def _hours_of(dur_value: float, unit: str) -> float:
    u = unit.lower().rstrip("s")
    if u in ("hr", "ehr"):  return dur_value
    if u in ("min", "emin"): return dur_value / 60.0
    if u in ("day", "eday"): return dur_value * 24.0
    return dur_value


def _parse_date(d: str, m: str, y: str, hh: str, mm: str) -> str:
    """Project dates are DD/MM/YY (Australian). Two-digit year → 20YY."""
    year = 2000 + int(y)
    return dt.datetime(year, int(m), int(d), int(hh), int(mm)).isoformat()


def _extract_preds(preamble: str) -> tuple[list[str], str]:
    """From the pre-duration preamble, peel off leading predecessor refs.
    A predecessor ref looks like `128`, `184SS`, `128,199`. The final ref
    before the FLOC often has no separating space ("184SS120-121-CV-001");
    we split on the boundary where digits+relationship-type meets a letter.
    Everything after the predecessor run is considered FLOC / WO / name."""
    preds: list[str] = []
    tokens = preamble.split(" ")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _PRED_REF.fullmatch(tok):
            preds.extend(t for t in tok.split(",") if t)
            i += 1
            continue
        # Handle the "<pred><FLOC-starts-with-letter>" glued case.
        head = _PRED_HEAD.match(tok)
        if head:
            preds.append(head.group(1))
            rest = tok[head.end():]
            tokens[i] = rest
            break
        # Handle two refs smashed together ("2219SS2221SS").
        split_m = re.fullmatch(r"(\d+(?:SS|FS|FF|SF)?)(\d+(?:SS|FS|FF|SF))", tok)
        if split_m and preds:
            preds.extend([split_m.group(1), split_m.group(2)])
            i += 1
            continue
        break
    return preds, " ".join(tokens[i:]).strip()


def _extract_floc_wo_name(preamble: str) -> tuple[str, str, str]:
    """After predecessors are peeled, preamble is one of:
        <FLOC> <WO> <name>          (leaf task, WO is 7-digit number)
        <FLOC> <name>               (summary with FLOC but no WO)
        <name>                      (top-level summary)
    Detect by shape: FLOC is a hyphen-bearing alphanumeric token
    ("120-121-CV-001", "121-CV", "120-121"); WO is a 7-digit number.
    """
    if not preamble:
        return "", "", ""
    toks = preamble.split(" ")
    floc = wo = ""
    name_start = 0
    # Tok 0 as FLOC: must contain a hyphen (or be a short all-caps site code).
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\-]*-[A-Z0-9\-]*", toks[0]):
        floc = toks[0]
        name_start = 1
        if name_start < len(toks) and re.fullmatch(r"\d{7}", toks[name_start]):
            wo = toks[name_start]
            name_start += 1
    name = " ".join(toks[name_start:]).strip()
    # MS Project's Level-5 equipment rows repeat FLOC as the name ("120-121-CV-001
    # 120-121-CV-001"); keep as-is, the UI can dedupe visually.
    return floc, wo, name


def _stable_task_key(shutdown_id: str, task_id: int, name: str) -> str:
    """Hash of shutdown + ID + name so a SharePoint progress row survives a
    re-parse of the same schedule — MS Project's task IDs do renumber when
    rows are inserted / deleted. Name-hashing absorbs renumbers when IDs
    shift but the task name is stable."""
    h = hashlib.sha1(f"{shutdown_id}|{task_id}|{name}".encode()).hexdigest()
    return f"{shutdown_id}.{task_id}.{h[:8]}"


# --------------------------------------------------------------------------- per-block parse

def _parse_block(task_id: int, body: str, shutdown_id: str) -> dict | None:
    # Locate the duration anchor (first occurrence).
    m = _DUR_ANCHOR.search(" " + body + " ")
    if not m:
        return None
    # Indices adjust for the leading space we prepended.
    anchor_start = m.start() - 1
    anchor_end   = m.end() - 1
    preamble = body[:anchor_start].strip()
    tail     = body[anchor_end:].strip()

    dur_hours = _hours_of(float(m.group(1)), m.group(2))
    dur_estimated = bool(m.group(3))

    # First two date+time stamps in the tail are start + finish.
    dates = _DATE_RE.findall(tail)
    if len(dates) < 2:
        return None
    start_iso  = _parse_date(*dates[0])
    finish_iso = _parse_date(*dates[1])

    # Snip everything up to finish-date so _TAIL_RE sees a short string.
    fin_match_end = _DATE_RE.search(tail, _DATE_RE.search(tail).end()).end()
    after_finish  = tail[fin_match_end:].strip()

    t_m = _TAIL_RE.search(after_finish)
    if not t_m:
        return None
    pct = int(t_m.group(1))
    unique_id = int(t_m.group(2))
    constraint_date = t_m.group(3)
    outline_level   = int(t_m.group(4))
    leftover        = t_m.group(5)

    # The middle chunk between (finish date + constraint + capacity) and the
    # pct% tail holds optional Work Centre / Team / Resource Names / WC Desc.
    mid = after_finish[:t_m.start()].strip()
    # Skip "N NA N" (constraint + capacity) prefix if present.
    mid = re.sub(r"^\d+\s+NA\s+\d+\s*", "", mid).strip()

    # Split the preamble into (predecessors, rest)
    preds, rest = _extract_preds(preamble)
    floc, wo, name = _extract_floc_wo_name(rest)
    if not name:
        # Summary rows with no FLOC end up with name in `rest`.
        name = rest

    # The task calendar is the last non-empty piece of `leftover`; earlier
    # tokens are sub team / area owner / resource initials / resource group.
    task_calendar = leftover.split("  ")[-1].strip() if leftover else ""

    resources = mid.strip()
    return {
        "task_key":       _stable_task_key(shutdown_id, task_id, name),
        "ms_project_id":  task_id,
        "unique_id":      unique_id,
        "wbs":            floc or None,
        "wo":             wo or None,
        "name":           name,
        "outline_level":  outline_level,
        "start":          start_iso,
        "finish":         finish_iso,
        "duration_hours": round(dur_hours, 2),
        "duration_estimated": dur_estimated,
        "predecessors":   preds,
        "resources":      resources or None,
        "task_calendar":  task_calendar or None,
        "baseline_percent_complete": pct,
        "constraint_date": None if constraint_date == "NA" else constraint_date,
    }


# --------------------------------------------------------------------------- tree shaping

def _assign_types_and_parents(tasks: list[dict]) -> None:
    """Outline Level encodes hierarchy: each task's parent is the nearest
    preceding task at a lower level. Also stamps `type`:
      - "milestone" when duration == 0
      - "summary"   when any later task (before next sibling) has level+1
      - "task"      otherwise
    Also stamps `children_count` for quick UI collapse/expand sizing."""
    stack: list[dict] = []   # parents in level order
    for t in tasks:
        while stack and stack[-1]["outline_level"] >= t["outline_level"]:
            stack.pop()
        t["parent_key"] = stack[-1]["task_key"] if stack else None
        stack.append(t)

    # Summary detection in a second pass — look ahead for an immediate child.
    by_level = {t["task_key"]: t for t in tasks}
    for t in tasks:
        t["children_count"] = 0
    for t in tasks:
        if t.get("parent_key"):
            by_level[t["parent_key"]]["children_count"] += 1

    for t in tasks:
        if t["duration_hours"] == 0 and t["outline_level"] > 1:
            t["type"] = "milestone"
        elif t["children_count"] > 0:
            t["type"] = "summary"
        else:
            t["type"] = "task"


# --------------------------------------------------------------------------- build payload

def _build(pdf_path: pathlib.Path, shutdown_id: str, source_label: str
          ) -> dict:
    text = _pdftotext(pdf_path)
    blocks = _split_blocks(text)

    tasks: list[dict] = []
    skipped = 0
    for tid, body in blocks:
        try:
            rec = _parse_block(tid, body, shutdown_id)
        except Exception as e:
            print(f"  parse error @ id={tid}: {e}", file=sys.stderr)
            rec = None
        if rec is None:
            skipped += 1
            continue
        tasks.append(rec)

    # MS Project exports tasks in schedule order; preserve that but also sort
    # defensively so re-parses are deterministic even if the PDF order shifts.
    tasks.sort(key=lambda t: (t["start"], t["ms_project_id"]))
    _assign_types_and_parents(tasks)

    # Overall window = min start / max finish across level-1 (root) or all.
    if tasks:
        baseline_start  = min(t["start"] for t in tasks)
        baseline_finish = max(t["finish"] for t in tasks)
    else:
        baseline_start = baseline_finish = None

    return {
        "shutdown_id":   shutdown_id,
        "source_file":   pdf_path.name,
        "source_label":  source_label,
        "parsed_at":     dt.datetime.now(dt.timezone.utc)
                            .replace(microsecond=0).isoformat()
                            .replace("+00:00", "Z"),
        "baseline_start":  baseline_start,
        "baseline_finish": baseline_finish,
        "task_count":    len(tasks),
        "skipped_rows":  skipped,
        "tasks":         tasks,
    }


# --------------------------------------------------------------------------- main

def main() -> int:
    if not RAW_DIR.exists():
        print(f"No raw dir at {RAW_DIR}", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    any_parsed = False
    for pdf in sorted(RAW_DIR.glob("*.pdf")):
        key = pdf.stem.strip()
        if key not in GANTT_MAP:
            print(f"  skip unmapped gantt {key}: {pdf.name}")
            continue
        shutdown_id, source_label = GANTT_MAP[key]
        payload = _build(pdf, shutdown_id, source_label)
        out = OUT_DIR / f"{shutdown_id}.json"
        out.write_text(json.dumps(payload, indent=2))
        print(f"  {pdf.name:<40} {shutdown_id:<22} "
              f"tasks={payload['task_count']:>3}  "
              f"skipped={payload['skipped_rows']:>2}  "
              f"{payload['baseline_start']} → {payload['baseline_finish']}")
        any_parsed = True

    if not any_parsed:
        print("No Gantt PDFs processed (none mapped in GANTT_MAP).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
