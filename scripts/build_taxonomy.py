#!/usr/bin/env python3
"""Phase B — emit the PID skeleton from BUILD-SPEC-v2 §2.

Names only. No prose, no systems, no regulations, no facts. Nothing in this
file may be sourced from a model; every L1 and L2 name is parsed verbatim out
of the spec, which is the single source of truth for the taxonomy.

Allocation rule (the spec fixes per-L1 totals in §1 but gives no per-L2
weights, so this is a stated assumption, not a spec quotation):

    Each L1's process budget is divided evenly across its L2 clusters; any
    remainder is distributed one per cluster from the top of the §2 list.

That is deterministic and reproducible, and it is the only defensible default
absent per-L2 weights in the spec. If weights are added later they belong in
the spec, not here.

Usage:
    python3 scripts/build_taxonomy.py                 # write data/taxonomy.json
    python3 scripts/build_taxonomy.py --domain 06     # write + print one domain
    python3 scripts/build_taxonomy.py --dry-run       # print only, write nothing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "docs" / "rail-wiki-build-spec-v2.md"
OUT = REPO / "data" / "taxonomy.json"

# "### RR-01 · Service Design & Network Planning (20)"
L1_HEADER = re.compile(r"^###\s+RR-(\d{2})\s+·\s+(.+?)\s+\((\d+)\)\s*$")
# "| 01 | Service Design & Network Planning | 20 | Where PSR actually lives |"
L1_TABLE_ROW = re.compile(r"^\|\s*(\d{2})\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|")


def read_spec() -> str:
    if not SPEC.exists():
        sys.exit(f"FATAL: spec not found at {SPEC}")
    return SPEC.read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        sys.exit(f"FATAL: could not locate '{start}' in the spec")
    j = text.find(end, i + len(start))
    return text[i : j if j > 0 else len(text)]


def parse_l1_totals(text: str) -> dict[str, tuple[str, int]]:
    """Per-L1 process budgets from the §1 weighted-allocation table."""
    out: dict[str, tuple[str, int]] = {}
    for line in section(text, "## 1. Revised L1 taxonomy", "## 2. L2 structure").splitlines():
        m = L1_TABLE_ROW.match(line.strip())
        if m:
            out[m.group(1)] = (m.group(2).strip(), int(m.group(3)))
    return out


def parse_l2_structure(text: str) -> list[dict]:
    """L1 headers and their ' · '-separated L2 clusters, from §2."""
    domains: list[dict] = []
    lines = section(text, "## 2. L2 structure per domain", "## 3. Per-process schema").splitlines()
    i = 0
    while i < len(lines):
        m = L1_HEADER.match(lines[i].strip())
        if not m:
            i += 1
            continue
        l1_num, l1_name, total = m.group(1), m.group(2).strip(), int(m.group(3))
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            sys.exit(f"FATAL: RR-{l1_num} header has no L2 line beneath it")
        l2_names = [s.strip() for s in lines[j].strip().split("·") if s.strip()]
        if not l2_names:
            sys.exit(f"FATAL: RR-{l1_num} L2 line parsed to zero clusters")
        domains.append({"l1_num": l1_num, "l1": l1_name, "total": total, "l2_names": l2_names})
        i = j + 1
    return domains


def allocate(total: int, buckets: int) -> list[int]:
    """Even split; remainder distributed one per bucket from the top."""
    if buckets == 0:
        sys.exit("FATAL: cannot allocate across zero L2 clusters")
    base, rem = divmod(total, buckets)
    return [base + (1 if k < rem else 0) for k in range(buckets)]


def build() -> tuple[dict, list[str]]:
    text = read_spec()
    totals = parse_l1_totals(text)
    domains = parse_l2_structure(text)
    warnings: list[str] = []

    if len(domains) != 15:
        warnings.append(f"§2 yielded {len(domains)} L1 domains, expected 15")

    processes: list[dict] = []
    summary: list[dict] = []

    for d in domains:
        l1_num, l1_name, total, l2_names = d["l1_num"], d["l1"], d["total"], d["l2_names"]

        # Cross-check §2 header count against the §1 weighted table.
        if l1_num in totals:
            t_name, t_total = totals[l1_num]
            if t_total != total:
                warnings.append(
                    f"RR-{l1_num}: §1 table says {t_total} processes, §2 header says {total}"
                )
            if t_name != l1_name:
                warnings.append(
                    f"RR-{l1_num}: §1 name {t_name!r} != §2 name {l1_name!r} (using §2)"
                )
        else:
            warnings.append(f"RR-{l1_num}: no row found in the §1 table")

        counts = allocate(total, len(l2_names))
        if min(counts) < 1:
            warnings.append(
                f"RR-{l1_num}: {total} processes across {len(l2_names)} L2 clusters "
                f"leaves at least one cluster empty"
            )
        for k, (l2_name, n) in enumerate(zip(l2_names, counts), start=1):
            for seq in range(1, n + 1):
                processes.append(
                    {
                        "pid": f"RR-{l1_num}-{k:02d}-{seq:02d}",
                        "l1": l1_name,
                        "l2": l2_name,
                    }
                )
        summary.append(
            {
                "l1_id": f"RR-{l1_num}",
                "l1": l1_name,
                "l2_clusters": len(l2_names),
                "processes": sum(counts),
                "spec_total": total,
                "per_l2": counts,
            }
        )

    grand = len(processes)
    if grand != 290:
        warnings.append(f"generated {grand} PIDs, spec §1 total is 290")

    doc = {
        "_meta": {
            "generated_by": "scripts/build_taxonomy.py",
            "source_of_truth": "docs/rail-wiki-build-spec-v2.md §1 (totals) and §2 (L1/L2 names)",
            "pid_format": "RR-{L1}-{L2}-{NN}",
            "allocation_rule": (
                "Per-L1 total from §1, divided evenly across that L1's §2 L2 clusters; "
                "remainder distributed one per cluster from the top of the §2 list. "
                "The spec fixes L1 totals but not L2 weights — this rule is an assumption."
            ),
            "l1_count": len(domains),
            "l2_count": sum(s["l2_clusters"] for s in summary),
            "process_count": grand,
            "domains": summary,
        },
        "processes": processes,
    }
    return doc, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="Emit the rail-wiki PID skeleton.")
    ap.add_argument("--domain", help="print only this L1, e.g. 06")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    doc, warnings = build()

    if not args.dry_run:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    m = doc["_meta"]
    print(
        f"taxonomy: {m['l1_count']} L1 · {m['l2_count']} L2 · {m['process_count']} PIDs"
        + ("" if args.dry_run else f"  ->  {OUT.relative_to(REPO)}")
    )

    if args.domain:
        num = args.domain.replace("RR-", "").zfill(2)
        rows = [p for p in doc["processes"] if p["pid"].startswith(f"RR-{num}-")]
        if not rows:
            print(f"no PIDs for domain {num}", file=sys.stderr)
            return 1
        info = next(s for s in m["domains"] if s["l1_id"] == f"RR-{num}")
        print()
        print(f"{info['l1_id']} · {info['l1']}")
        print(f"{info['processes']} processes across {info['l2_clusters']} L2 clusters "
              f"(spec §1 total: {info['spec_total']})")
        print()
        current = None
        for p in rows:
            if p["l2"] != current:
                current = p["l2"]
                l2_id = p["pid"].split("-")[2]
                print(f"  {l2_id}  {current}")
            print(f"        {p['pid']}")

    if warnings:
        print("\nWARNINGS", file=sys.stderr)
        for w in warnings:
            print(f"  ! {w}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
