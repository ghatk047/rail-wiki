#!/usr/bin/env python3
"""Render one or more processes from data/processes.json as static HTML for
human review.

    python3 scripts/render_preview.py --pid RR-03-05-01
    python3 scripts/render_preview.py --all

This is a review aid for the §11 pilot gate, not the eventual site build
(that is build_index.py, not yet written). It reads the local, gitignored
data/processes.json and writes plain HTML under site/preview/ — the rendered
HTML is what gets committed and served by Pages; the JSON source never is.

Every page carries a DRAFT banner: nothing rendered here has had the §11
human review (hand it to someone with real rail operations experience) that
the spec requires before content is trusted.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROCESSES = REPO / "data" / "processes.json"
OUT_DIR = REPO / "site" / "preview"

STYLE = """
:root {
  --bg: #f7f7f5; --panel: #ffffff; --ink: #1c1d1f; --muted: #5c5f66;
  --rule: #d9d9d4; --amber: #c8791a; --charcoal: #2b2e33;
  --badge-company-bg: #fdecd8; --badge-company-ink: #8a4b0a;
  --badge-industry-bg: #e6e9ec; --badge-industry-ink: #3d4348;
  --draft-bg: #fff4e5; --draft-border: #c8791a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17181a; --panel: #1f2124; --ink: #ececea; --muted: #9a9da3;
    --rule: #33363b; --amber: #e0952f; --charcoal: #ececea;
    --badge-company-bg: #3a2a12; --badge-company-ink: #e0952f;
    --badge-industry-bg: #2a2d31; --badge-industry-ink: #c3c7cc;
    --draft-bg: #2a2011; --draft-border: #e0952f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  padding: 2rem 1.25rem;
}
main { max-width: 44rem; margin: 0 auto; }
.draft {
  background: var(--draft-bg); border: 1px solid var(--draft-border);
  border-radius: 6px; padding: .75rem 1rem; margin-bottom: 1.5rem;
  font-size: .85rem; line-height: 1.5;
}
.draft strong { color: var(--amber); }
.pid { font-size: .8rem; color: var(--muted); letter-spacing: .04em; margin-bottom: .25rem; }
h1 { font-size: 1.4rem; margin: 0 0 .25rem; color: var(--charcoal); }
.breadcrumb { color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; }
section { margin-bottom: 1.75rem; }
h2 {
  font-size: .75rem; text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted); border-bottom: 1px solid var(--rule);
  padding-bottom: .4rem; margin: 0 0 .75rem;
}
p.description { margin: 0; }
ul { margin: 0; padding-left: 1.25rem; }
li { margin-bottom: .3rem; }
.pill-row { display: flex; flex-wrap: wrap; gap: .4rem; }
.pill {
  display: inline-flex; align-items: center; gap: .35rem;
  border: 1px solid var(--rule); border-radius: 999px;
  padding: .25rem .65rem; font-size: .85rem; background: var(--panel);
}
.badge {
  font-size: .68rem; text-transform: uppercase; letter-spacing: .04em;
  border-radius: 3px; padding: .1rem .4rem;
}
.badge.company { background: var(--badge-company-bg); color: var(--badge-company-ink); }
.badge.industry { background: var(--badge-industry-bg); color: var(--badge-industry-ink); }
.kv { display: grid; grid-template-columns: auto 1fr; gap: .3rem 1rem; font-size: .9rem; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; }
.sources li a { color: var(--amber); word-break: break-all; }
footer {
  margin-top: 2.5rem; padding-top: 1.25rem; border-top: 1px solid var(--rule);
  font-size: .8rem; color: var(--muted);
}
"""


def scope_badge(scope: str | None) -> str:
    if scope == "company_specific":
        return '<span class="badge company">company-specific</span>'
    if scope == "industry_typical":
        return '<span class="badge industry">industry-typical</span>'
    return f'<span class="badge">{html.escape(scope or "unscoped")}</span>'


def pill_list(items: list[str]) -> str:
    if not items:
        return '<p style="color:var(--muted); font-size:.85rem;">(none)</p>'
    return '<div class="pill-row">' + "".join(
        f'<span class="pill">{html.escape(str(i))}</span>' for i in items
    ) + "</div>"


def render_one(p: dict) -> str:
    systems_html = "".join(
        f'<li><strong>{html.escape(s["name"])}</strong> '
        f'{scope_badge(s.get("scope"))} '
        f'<span style="color:var(--muted); font-size:.8rem;">{html.escape(s.get("source_id",""))}</span></li>'
        for s in p.get("systems", [])
    ) or "<li>(none)</li>"

    sources_html = "".join(
        f'<li><a href="{html.escape(u)}" target="_blank" rel="noopener">{html.escape(u)}</a></li>'
        for u in p.get("sources", [])
    ) or "<li>(none)</li>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(p['pid'])} — {html.escape(p['name'])}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
  <div class="draft">
    <strong>DRAFT — pilot generation, not yet human-reviewed.</strong>
    Per BUILD-SPEC-v2 §11, this content is only trusted for scaling once it
    has been reviewed by someone with real rail operations experience and no
    factual error is found. Registry grounding has been machine-validated;
    subject-matter accuracy has not yet been checked by a person.
  </div>

  <div class="breadcrumb"><a href="index.html">&larr; preview index</a></div>

  <div class="pid">{html.escape(p['pid'])} &middot; confidence: {html.escape(p.get('confidence','?'))}</div>
  <h1>{html.escape(p['name'])}</h1>
  <div class="breadcrumb">{html.escape(p['l1'])} &rsaquo; {html.escape(p['l2'])}</div>

  <section>
    <h2>Description</h2>
    <p class="description">{html.escape(p.get('description',''))}</p>
  </section>

  <section>
    <h2>Actors</h2>
    {pill_list(p.get('actors', []))}
  </section>

  <section>
    <h2>Systems</h2>
    <ul>{systems_html}</ul>
  </section>

  <section>
    <h2>Inputs / Outputs</h2>
    <div class="kv">
      <dt>Inputs</dt><dd>{html.escape(", ".join(p.get('inputs', [])) or "(none)")}</dd>
      <dt>Outputs</dt><dd>{html.escape(", ".join(p.get('outputs', [])) or "(none)")}</dd>
    </div>
  </section>

  <section>
    <h2>Regulatory &amp; Rules</h2>
    <div class="kv">
      <dt>Regulatory hook</dt><dd>{html.escape(", ".join(p.get('regulatory_hook', [])) or "(none)")}</dd>
      <dt>Operating rule ref</dt><dd>{html.escape(p.get('operating_rule_ref','') or "(none)")}</dd>
    </div>
  </section>

  <section>
    <h2>KPIs Moved</h2>
    {pill_list(p.get('kpi_moved', []))}
  </section>

  <section>
    <h2>Pain Points (spec §7, closed list)</h2>
    <ul>{"".join(f"<li>{html.escape(x)}</li>" for x in p.get('pain_points', [])) or "<li>(none)</li>"}</ul>
  </section>

  <section>
    <h2>Sources</h2>
    <ul class="sources">{sources_html}</ul>
  </section>

  <footer>
    Last reviewed (machine): {html.escape(p.get('last_reviewed',''))} &middot;
    Independently compiled from public sources. Not affiliated with,
    sponsored by, or endorsed by any railroad. Illustrative of US Class I
    practice.
  </footer>
</main>
</body>
</html>
"""


def render_index(pids: list[str], procs: dict[str, dict]) -> str:
    rows = "".join(
        f'<li><a href="{html.escape(pid)}.html">{html.escape(pid)}</a> '
        f'— {html.escape(procs[pid]["name"])} '
        f'<span style="color:var(--muted); font-size:.85rem;">'
        f'({html.escape(procs[pid]["l1"])})</span></li>'
        for pid in pids
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Process Preview Index</title>
<style>{STYLE}</style>
</head>
<body>
<main>
  <div class="draft">
    <strong>DRAFT preview index.</strong> Rendered from data/processes.json
    (not committed) for human review under BUILD-SPEC-v2 §11. Not the final
    site.
  </div>
  <h1>Generated Process Previews</h1>
  <ul>{rows or "<li>(none generated yet)</li>"}</ul>
  <footer>
    Independently compiled from public sources. Not affiliated with,
    sponsored by, or endorsed by any railroad. Illustrative of US Class I
    practice.
  </footer>
</main>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Render process(es) as static HTML for review.")
    ap.add_argument("--pid", help="render just this PID")
    ap.add_argument("--all", action="store_true", help="render every process currently written")
    args = ap.parse_args()

    if not PROCESSES.exists():
        print(f"FATAL: {PROCESSES.relative_to(REPO)} not found — generate something first",
              file=sys.stderr)
        return 2
    doc = json.loads(PROCESSES.read_text(encoding="utf-8"))
    all_procs: dict[str, dict] = doc.get("processes", {})

    if args.all:
        pids = sorted(all_procs)
    elif args.pid:
        if args.pid not in all_procs:
            print(f"FATAL: {args.pid} is not in {PROCESSES.relative_to(REPO)}. "
                  f"Available: {sorted(all_procs) or 'none'}", file=sys.stderr)
            return 2
        pids = [args.pid]
    else:
        print("FATAL: pass --pid <PID> or --all", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pid in pids:
        out = OUT_DIR / f"{pid}.html"
        out.write_text(render_one(all_procs[pid]), encoding="utf-8")
        print(f"  wrote {out.relative_to(REPO)}")

    # Index covers every process ever rendered to this directory, not just
    # this run's — so an earlier preview isn't orphaned by a later one.
    existing_pids = sorted(p.stem for p in OUT_DIR.glob("*.html") if p.stem != "index")
    index_pids = [p for p in existing_pids if p in all_procs]
    (OUT_DIR / "index.html").write_text(render_index(index_pids, all_procs), encoding="utf-8")
    print(f"  wrote {(OUT_DIR / 'index.html').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
