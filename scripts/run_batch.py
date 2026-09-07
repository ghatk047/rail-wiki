#!/usr/bin/env python3
"""Batch driver: generate N processes in one run, resuming from wherever the
last run left off, tracking progress locally, and pushing a live heartbeat.

    python3 scripts/run_batch.py --count 5
    python3 scripts/run_batch.py --count 50 --push-every 5
    python3 scripts/run_batch.py --count 3 --mock          # loop mechanics only, no push, no publish

What happens each run
----------------------
1. Read data/taxonomy.json (all 290 PIDs) and data/processes.json (already
   generated ones); the next --count PIDs NOT already in processes.json are
   this run's queue, in taxonomy order. Re-running with the same --count
   picks up where the last run stopped -- nothing needs to be remembered
   between invocations except processes.json itself.
2. For each PID: call generate_one() in-process (no shelling out to
   generate_rail_wiki.py). One PID failing -- exhausted retries, or a
   validation rejection -- is logged and the batch moves on to the next PID.
   It does not abort the run.
3. After EVERY PID, update data/rail_wiki_progress.xlsx (local, gitignored,
   never committed) via write_excel.update_row().
4. After every --push-every successful PIDs (default 5), and once more at
   the end of the run if there is anything un-pushed: render the newly
   written processes to site/preview/, rewrite the live status page, and
   git add + commit + push. This is the only network-visible side effect
   in the whole script, and it touches only site/ -- data/processes.json
   and the xlsx are never staged, matching spec §8.

--mock is for exercising the loop itself (resume logic, retry handling, the
excel writes) without spending real model time. It deliberately never
renders or pushes: publishing canned mock content as if it were generated
output would misrepresent it on the live site, so --mock forces --no-push
and --no-render regardless of other flags.
"""

from __future__ import annotations

import argparse
import html
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_rail_wiki as gen  # noqa: E402
import render_preview as rp  # noqa: E402
import write_excel as we  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
STATUS_PAGE = REPO / "site" / "preview" / "status.html"

TERMINAL_STATUSES = {"written", "updated", "rejected_generation",
                     "rejected_validation", "error"}


def pending_pids(count: int, out: Path) -> list[str]:
    taxonomy = gen.load_taxonomy_full()
    all_pids = [p["pid"] for p in taxonomy["processes"]]
    done = set()
    if out.exists():
        import json
        doc = json.loads(out.read_text(encoding="utf-8"))
        done = set(doc.get("processes", {}))
    remaining = [pid for pid in all_pids if pid not in done]
    return remaining[:count], len(all_pids), len(done), len(remaining)


def render_status_page(run_log: list[dict], total: int, done_before: int) -> None:
    """The live heartbeat page: not a per-process preview, a "is this still
    running" page. Rewritten every push."""
    ok = sum(1 for r in run_log if r["status"] in ("written", "updated"))
    bad = sum(1 for r in run_log if r["status"] in ("rejected_generation", "rejected_validation", "error"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = "".join(
        f'<tr><td>{html.escape(r["pid"])}</td>'
        f'<td>{html.escape(r.get("name") or "—")}</td>'
        f'<td><span class="st-{"ok" if r["status"] in ("written","updated") else "bad"}">'
        f'{html.escape(r["status"])}</span></td>'
        f'<td>{html.escape(str(r.get("reason") or ""))}</td></tr>'
        for r in reversed(run_log)
    )
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<meta http-equiv="refresh" content="60">
<title>Batch Progress</title>
<style>{rp.STYLE}
.st-ok {{ color: #1a7f37; }}
.st-bad {{ color: #b42318; }}
</style>
</head>
<body>
<main>
  <div class="draft">
    <strong>Live batch status.</strong> Refreshes here only when the local
    terminal pushes — if the numbers stop moving, the terminal has stopped
    or is between pushes. This is a heartbeat page, not the content review
    index (<a href="index.html">that's here</a>).
  </div>
  <h1>Batch Progress</h1>
  <div class="kv" style="margin-bottom:1.5rem;">
    <dt>Taxonomy total</dt><dd>{total}</dd>
    <dt>Generated before this run</dt><dd>{done_before}</dd>
    <dt>This run so far</dt><dd>{len(run_log)} attempted, {ok} written, {bad} rejected/error</dd>
    <dt>Last push</dt><dd>{now}</dd>
  </div>
  <section>
    <h2>This run's PIDs (most recent first)</h2>
    <div class="tbl-wrap"><table>
      <thead><tr><th>PID</th><th>Name</th><th>Status</th><th>Reason</th></tr></thead>
      <tbody>{rows or "<tr><td colspan=4>(none yet)</td></tr>"}</tbody>
    </table></div>
  </section>
  <footer>
    Independently compiled from public sources. Not affiliated with,
    sponsored by, or endorsed by any railroad. Illustrative of US Class I
    practice.
  </footer>
</main>
</body>
</html>
"""
    STATUS_PAGE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PAGE.write_text(body, encoding="utf-8")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def push_progress(new_pids: list[str], total: int, done_before: int, out: Path) -> bool:
    """Renders newly-written PIDs, rewrites the status page, and pushes.
    Stages ONLY site/ -- data/processes.json and the xlsx are never touched
    by git here. Returns True if a push happened, False if there was
    genuinely nothing new to push."""
    if new_pids:
        import json
        procs = json.loads(out.read_text(encoding="utf-8"))["processes"]
        rp.OUT_DIR.mkdir(parents=True, exist_ok=True)
        for pid in new_pids:
            (rp.OUT_DIR / f"{pid}.html").write_text(rp.render_one(procs[pid]), encoding="utf-8")
        existing = sorted(p.stem for p in rp.OUT_DIR.glob("*.html")
                          if p.stem not in ("index", "status"))
        index_pids = [p for p in existing if p in procs]
        (rp.OUT_DIR / "index.html").write_text(rp.render_index(index_pids, procs), encoding="utf-8")

    status = git("status", "--porcelain", "--", "site/")
    if not status.stdout.strip():
        return False

    git("add", "site/")
    msg = (f"Batch progress: {done_before + len(new_pids)}/{total} processes "
          f"({len(new_pids)} new this push)")
    commit = git("commit", "-m", msg)
    if commit.returncode != 0:
        print(f"  note: nothing to commit ({commit.stdout.strip() or commit.stderr.strip()})")
        return False
    push = git("push", "origin", "main")
    if push.returncode != 0:
        print(f"  WARNING: git push failed: {push.stderr.strip()}", file=sys.stderr)
        return False
    print(f"  pushed: {msg}")
    return True


def run(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    queue, total, done_before, remaining_before = pending_pids(args.count, out_path)
    if not queue:
        print(f"Nothing to do: all {total} taxonomy PIDs already exist in "
              f"{out_path.relative_to(REPO) if out_path.is_relative_to(REPO) else out_path}.")
        return 0

    print(f"Batch: {len(queue)} PID(s) queued "
        f"({done_before} already generated, {remaining_before} remaining "
        f"including this batch, {total} total)")
    if args.mock:
        print("  --mock: loop mechanics only. Forcing --no-render and --no-push.")
    print()

    run_log: list[dict] = []
    since_push: list[str] = []

    for i, pid in enumerate(queue, start=1):
        print(f"[{i}/{len(queue)}] {pid}")
        res = gen.generate_one(
            pid, mock=args.mock, mock_profile=args.mock_profile, host=args.host,
            registries=args.registries, out=args.out, max_retries=args.max_retries,
            dry_run=args.dry_run, debug=False,
        )
        run_log.append(res)

        try:
            we.update_row(
                pid, res["status"], path=Path(args.excel),
                name=res.get("name"), confidence=res.get("confidence"),
                word_count=res.get("word_count"), step_count=res.get("step_count"),
                gate_count=res.get("gate_count"), sources=res.get("sources"),
                attempts=res.get("attempts"),
            )
        except we.ExcelError as e:
            print(f"  WARNING: could not update {args.excel}: {e}", file=sys.stderr)

        print(f"  -> {res['status']}" + (f" ({res['reason']})" if res.get("reason") else ""))
        print()

        if res["status"] in ("written", "updated") and not args.dry_run:
            since_push.append(pid)

        do_push = (not args.mock and not args.dry_run and not args.no_push
                  and len(since_push) >= args.push_every)
        if do_push:
            render_status_page(run_log, total, done_before)
            if push_progress(since_push, total, done_before + i, out_path):
                since_push = []

    ok = sum(1 for r in run_log if r["status"] in ("written", "updated"))
    bad = len(run_log) - ok
    print(f"Batch complete: {ok} written/updated, {bad} rejected or errored, "
        f"{len(run_log)} attempted.")

    if since_push and not args.mock and not args.dry_run and not args.no_push:
        render_status_page(run_log, total, done_before)
        push_progress(since_push, total, done_before + len(run_log), out_path)
    elif args.mock or args.dry_run or args.no_push:
        print("  (no push: --mock, --dry-run or --no-push was set)")

    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate N processes, resuming from where the last run stopped.")
    ap.add_argument("--count", type=int, required=True, help="how many PIDs to generate this run")
    ap.add_argument("--push-every", type=int, default=5,
                    help="push to Pages after this many successful writes (default 5)")
    ap.add_argument("--max-retries", type=int, default=gen.DEFAULT_RETRIES, dest="max_retries")
    ap.add_argument("--host", default=gen.OLLAMA_HOST)
    ap.add_argument("--registries", default=str(gen.REGISTRY_DIR))
    ap.add_argument("--out", default=None,
                    help="defaults to data/processes.json, or data/processes.mock.json "
                         "under --mock -- a mock batch run must not be able to write "
                         "fake content under real PIDs into the real store, where a "
                         "later live run would then skip them as \"already done\"")
    ap.add_argument("--excel", default=str(we.DEFAULT_XLSX))
    ap.add_argument("--mock", action="store_true",
                    help="test the loop with canned responses -- forces --no-push and --no-render")
    ap.add_argument("--mock-profile", default="good")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate but never write, and never push")
    ap.add_argument("--no-push", action="store_true", help="never push, even on a live run")
    args = ap.parse_args()
    if args.out is None:
        args.out = str(REPO / "data" / "processes.mock.json") if args.mock else str(gen.PROCESSES)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
