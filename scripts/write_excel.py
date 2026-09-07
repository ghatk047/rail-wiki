#!/usr/bin/env python3
"""Local progress tracker: data/rail_wiki_progress.xlsx.

Not committed -- data/*.xlsx is already gitignored (spec §8), and this file
is explicitly meant to be watched in a local spreadsheet app while a batch
run works through the terminal, not to be a build artefact.

    python3 scripts/write_excel.py --init      # seed every PID from the taxonomy, status PENDING
    python3 scripts/write_excel.py --sync      # refresh rows from data/processes.json
    python3 scripts/write_excel.py --show      # print a status summary, no write

scripts/run_batch.py imports update_row() and calls it after every PID, so
the sheet updates one row at a time as a batch run progresses -- you can
have it open and watch it change.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

REPO = Path(__file__).resolve().parent.parent
TAXONOMY = REPO / "data" / "taxonomy.json"
PROCESSES = REPO / "data" / "processes.json"
DEFAULT_XLSX = REPO / "data" / "rail_wiki_progress.xlsx"

COLUMNS = ["PID", "L1", "L2", "Name", "Status", "Confidence", "Words",
           "Steps", "Gates", "Sources", "Attempts", "Updated"]
COL_IDX = {c: i + 1 for i, c in enumerate(COLUMNS)}

STATUS_FILL = {
    "PENDING": None,
    "written": "C6EFCE",
    "updated": "C6EFCE",
    "rejected_generation": "FFC7CE",
    "rejected_validation": "FFC7CE",
    "error": "FFC7CE",
    "dry_run": "FFEB9C",
}


class ExcelError(RuntimeError):
    pass


def _load_taxonomy() -> list[dict]:
    if not TAXONOMY.exists():
        raise ExcelError(f"{TAXONOMY.relative_to(REPO)} not found — run build_taxonomy.py first")
    return json.loads(TAXONOMY.read_text(encoding="utf-8"))["processes"]


def _new_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "progress"
    ws.append(COLUMNS)
    for c in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    widths = {"PID": 14, "L1": 30, "L2": 34, "Name": 38, "Status": 20,
              "Confidence": 11, "Words": 8, "Steps": 7, "Gates": 7,
              "Sources": 8, "Attempts": 9, "Updated": 12}
    for name, w in widths.items():
        ws.column_dimensions[get_column_letter(COL_IDX[name])].width = w
    return wb


def init_workbook(path: Path = DEFAULT_XLSX) -> int:
    """Seed one row per taxonomy PID, status PENDING. Overwrites any existing
    file — this is the "start fresh" entry point, not an incremental one."""
    procs = _load_taxonomy()
    wb = _new_workbook()
    ws = wb["progress"]
    for p in procs:
        ws.append([p["pid"], p["l1"], p["l2"], "", "PENDING", "", "", "", "", "", "", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return len(procs)


def _open_or_init(path: Path) -> Workbook:
    if path.exists():
        try:
            return load_workbook(path)
        except Exception as e:  # noqa: BLE001 -- openpyxl raises several types
            raise ExcelError(f"{path} exists but could not be opened: {e}") from e
    init_workbook(path)
    return load_workbook(path)


def _find_row(ws, pid: str) -> int | None:
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=COL_IDX["PID"]).value == pid:
            return row
    return None


def update_row(pid: str, status: str, *, path: Path = DEFAULT_XLSX, **fields) -> None:
    """Upsert one PID's row. Safe to call after every single generation in a
    batch run -- the file is opened, updated, and saved each time so it is
    never left partially written if the process is interrupted mid-batch."""
    wb = _open_or_init(path)
    ws = wb["progress"]
    row = _find_row(ws, pid)
    if row is None:
        ws.append([pid, "", "", "", "PENDING", "", "", "", "", "", "", ""])
        row = ws.max_row

    ws.cell(row=row, column=COL_IDX["Status"]).value = status
    for key, col in (("name", "Name"), ("l1", "L1"), ("l2", "L2"),
                     ("confidence", "Confidence"), ("word_count", "Words"),
                     ("step_count", "Steps"), ("gate_count", "Gates"),
                     ("sources", "Sources"), ("attempts", "Attempts")):
        if key in fields and fields[key] is not None:
            ws.cell(row=row, column=COL_IDX[col]).value = fields[key]
    ws.cell(row=row, column=COL_IDX["Updated"]).value = datetime.now().strftime("%Y-%m-%d %H:%M")

    fill_hex = STATUS_FILL.get(status)
    fill = PatternFill(start_color=fill_hex, end_color=fill_hex, fill_type="solid") if fill_hex else None
    for c in range(1, len(COLUMNS) + 1):
        ws.cell(row=row, column=c).fill = fill or PatternFill(fill_type=None)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def sync_from_processes(path: Path = DEFAULT_XLSX) -> int:
    """Refresh every row from data/processes.json's current contents. Useful
    to catch the sheet up after processes.json changed outside a batch run."""
    if not PROCESSES.exists():
        return 0
    procs = json.loads(PROCESSES.read_text(encoding="utf-8")).get("processes", {})
    n = 0
    for pid, p in procs.items():
        update_row(
            pid, "written", path=path,
            name=p.get("name"), l1=p.get("l1"), l2=p.get("l2"),
            confidence=p.get("confidence"),
            word_count=len((p.get("description") or "").split()),
            step_count=len(p.get("steps") or []),
            gate_count=sum(1 for s in (p.get("steps") or [])
                           if str(s.get("decision_point", "N")).upper() == "Y"
                           or str(s.get("exception", "N")).upper() == "Y"),
            sources=len(p.get("sources") or []),
        )
        n += 1
    return n


def summary(path: Path = DEFAULT_XLSX) -> dict:
    if not path.exists():
        return {"total": 0}
    wb = load_workbook(path)
    ws = wb["progress"]
    counts: dict[str, int] = {}
    total = 0
    for row in range(2, ws.max_row + 1):
        status = ws.cell(row=row, column=COL_IDX["Status"]).value or "PENDING"
        counts[status] = counts.get(status, 0) + 1
        total += 1
    return {"total": total, **counts}


def main() -> int:
    ap = argparse.ArgumentParser(description="Local xlsx progress tracker (not committed).")
    ap.add_argument("--path", default=str(DEFAULT_XLSX))
    ap.add_argument("--init", action="store_true", help="seed every taxonomy PID, status PENDING")
    ap.add_argument("--sync", action="store_true", help="refresh rows from data/processes.json")
    ap.add_argument("--show", action="store_true", help="print a status summary")
    args = ap.parse_args()
    path = Path(args.path)

    try:
        if args.init:
            n = init_workbook(path)
            print(f"seeded {n} PIDs -> {path}")
        if args.sync:
            n = sync_from_processes(path)
            print(f"synced {n} written process(es) -> {path}")
        if args.show or not (args.init or args.sync):
            s = summary(path)
            if s["total"] == 0:
                print(f"{path} does not exist yet — run --init first")
                return 0
            print(f"{path}")
            for k in ("PENDING", "written", "updated", "dry_run",
                     "rejected_generation", "rejected_validation", "error"):
                if k in s:
                    print(f"  {k:22s} {s[k]}")
            print(f"  {'TOTAL':22s} {s['total']}")
    except ExcelError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
