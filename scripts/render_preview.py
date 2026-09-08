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
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROCESSES = REPO / "data" / "processes.json"
OUT_DIR = REPO / "site" / "preview"

# Pinned, not "latest" -- a page rendered today must still look the same in a
# year. jsdelivr is an approved CDN host; only the script tag is external,
# everything else on the page is inline.
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

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
.tbl-wrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: .82rem; white-space: nowrap; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--rule); }
th {
  background: var(--panel); color: var(--muted); font-size: .68rem;
  text-transform: uppercase; letter-spacing: .04em; position: sticky; top: 0;
}
tr:last-child td { border-bottom: none; }
td.flag span { display: inline-block; width: 1.1rem; text-align: center; border-radius: 3px; }
td.flag .yes { background: var(--badge-company-bg); color: var(--badge-company-ink); }
td.flag .no { color: var(--muted); }
.diagram-wrap {
  border: 1px solid var(--rule); border-radius: 6px; padding: 1rem;
  background: var(--panel); overflow-x: auto;
}
.diagram-fallback summary { cursor: pointer; color: var(--muted); font-size: .8rem; margin-top: .5rem; }
.diagram-fallback pre {
  font-size: .75rem; overflow-x: auto; background: var(--panel);
  border: 1px solid var(--rule); border-radius: 6px; padding: .75rem;
}
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


def _mmd_label(text: str, limit: int = 80) -> str:
    """Mermaid-safe node/edge label: strip characters that break flowchart
    syntax, collapse whitespace, truncate."""
    t = re.sub(r'["\[\]{}()<>|]', "", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        cut = t[:limit].rsplit(" ", 1)[0]
        t = (cut or t[:limit]).rstrip() + "…"
    return t or "(untitled)"


def _mmd_node_id(step_id: str) -> str:
    return "S" + re.sub(r"[^0-9A-Za-z]", "_", str(step_id))


def _phase_groups(n: int, target: int = 4) -> list[int]:
    """How many steps in each phase group, evenly split with the remainder
    distributed from the top -- same allocation rule as build_taxonomy.py.
    Positional groups, not model-authored phase names: there is no phases[]
    field in the generation schema (a real one is a separate, larger change
    -- see the module docstring). This gets the diagram's structural
    richness -- multiple labelled clusters, not a flat wall of steps --
    without asking the model for anything it doesn't already produce."""
    groups = min(target, n)
    base, rem = divmod(n, groups)
    return [base + (1 if k < rem else 0) for k in range(groups)]


def build_mermaid(steps: list[dict]) -> str:
    """Deterministic flowchart from steps[] -- never authored or trusted from
    the model, built the same way every time from the same validated list.
    Node shape signals what kind of step it is: diamond = decision point,
    rounded = exception, rectangle = ordinary step.

    Steps are wrapped in positional phase subgraphs (Phase 1, Phase 2, ...)
    purely for visual structure -- there is no per-phase name in the
    generation schema, unlike a fuller phased pipeline would carry. Every
    gate (decision_point or exception) labels BOTH the alternate branch and
    the expected continuation ("Yes" / "Cleared"), not just the alternate --
    a diagram with only branch labels reads as sparse even when the gate
    count is real.
    """
    if not steps:
        return "flowchart TD\n  E[No steps recorded]"

    by_id = {str(s.get("step")): s for s in steps}
    lines = ["%%{init: {'flowchart': {'curve': 'basis'}}}%%", "flowchart TD"]
    exit_n = 0

    group_sizes = _phase_groups(len(steps))
    phase_of: dict[int, int] = {}
    idx = 0
    for phase_i, size in enumerate(group_sizes, start=1):
        for _ in range(size):
            if idx < len(steps):
                phase_of[idx] = phase_i
            idx += 1

    def step_lines(i: int, s: dict) -> list[str]:
        out = []
        sid = str(s.get("step") or str(i + 1))
        nid = _mmd_node_id(sid)
        label = f"{sid}. {_mmd_label(s.get('name'))}"
        dp = str(s.get("decision_point", "N")).upper() == "Y"
        exc = str(s.get("exception", "N")).upper() == "Y"
        if dp:
            out.append(f"    {nid}{{{label}}}")
        elif exc:
            out.append(f"    {nid}({label})")
        else:
            out.append(f"    {nid}[{label}]")
        return out

    edge_lines: list[str] = []
    for i, s in enumerate(steps):
        sid = str(s.get("step") or str(i + 1))
        nid = _mmd_node_id(sid)
        dp = str(s.get("decision_point", "N")).upper() == "Y"
        exc = str(s.get("exception", "N")).upper() == "Y"

        branch = s.get("branch")
        branch_to = str(branch["to"]) if isinstance(branch, dict) and branch.get("to") else None

        nxt = str(steps[i + 1].get("step") or str(i + 2)) if i + 1 < len(steps) else None
        main_label = "Yes" if dp else ("Cleared" if exc else None)
        if nxt and nxt != branch_to:
            if main_label:
                edge_lines.append(f"  {nid} -- {main_label} --> {_mmd_node_id(nxt)}")
            else:
                edge_lines.append(f"  {nid} --> {_mmd_node_id(nxt)}")

        if branch_to:
            edge_label = _mmd_label(branch.get("label", ""), limit=18)
            if branch_to in by_id:
                edge_lines.append(f"  {nid} -- {edge_label} --> {_mmd_node_id(branch_to)}")
            else:
                exit_n += 1
                ex_id = f"X{exit_n}"
                edge_lines.append(f'  {ex_id}(["{_mmd_label(branch_to, limit=40)}"])')
                edge_lines.append(f"  {nid} -- {edge_label} --> {ex_id}")

    current_phase = None
    for i, s in enumerate(steps):
        p = phase_of.get(i, 1)
        if p != current_phase:
            if current_phase is not None:
                lines.append("  end")
            lines.append(f"  subgraph PH{p}[\"Phase {p}\"]")
            current_phase = p
        lines.extend(step_lines(i, s))
    if current_phase is not None:
        lines.append("  end")

    lines.append("")
    lines.extend(edge_lines)

    return "\n".join(lines)


def render_steps_table(steps: list[dict]) -> str:
    if not steps:
        return '<p style="color:var(--muted); font-size:.85rem;">(no steps recorded)</p>'
    rows = []
    for s in steps:
        dp = str(s.get("decision_point", "N")).upper() == "Y"
        exc = str(s.get("exception", "N")).upper() == "Y"
        sysinfo = s.get("system")
        sys_cell = (
            f'{html.escape(sysinfo["name"])} {scope_badge(sysinfo.get("scope"))}'
            if isinstance(sysinfo, dict) and sysinfo.get("name") else
            '<span style="color:var(--muted);">—</span>'
        )
        branch = s.get("branch")
        branch_note = (
            f' <span style="color:var(--muted); font-size:.78rem;">'
            f'&rarr; {html.escape(str(branch.get("label","")))}: {html.escape(str(branch.get("to","")))}</span>'
            if isinstance(branch, dict) else ""
        )
        rows.append(f"""<tr>
<td>{html.escape(str(s.get('step','')))}</td>
<td><b>{html.escape(s.get('name',''))}</b>{branch_note}</td>
<td>{html.escape(s.get('role','') or '—')}</td>
<td>{sys_cell}</td>
<td>{html.escape(s.get('input','') or '—')}</td>
<td>{html.escape(s.get('output','') or '—')}</td>
<td>{html.escape(s.get('kpi','') or '—')}</td>
<td class="flag"><span class="{'yes' if dp else 'no'}">{'Y' if dp else 'N'}</span></td>
<td class="flag"><span class="{'yes' if exc else 'no'}">{'Y' if exc else 'N'}</span></td>
<td>{html.escape(s.get('pain_point','') or '—')}</td>
</tr>""")
    return f"""<div class="tbl-wrap"><table>
<thead><tr><th>Step</th><th>Activity</th><th>Role</th><th>System</th><th>Input</th>
<th>Output</th><th>KPI</th><th>Dec</th><th>Exc</th><th>Pain point</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>"""


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

    steps = p.get("steps") or []
    mmd = build_mermaid(steps)
    steps_table = render_steps_table(steps)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(p['pid'])} — {html.escape(p['name'])}</title>
<style>{STYLE}</style>
<script src="{MERMAID_CDN}"></script>
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
    <h2>Process Flow</h2>
    <div class="diagram-wrap"><pre class="mermaid">{html.escape(mmd)}</pre></div>
    <details class="diagram-fallback">
      <summary>Mermaid source (if the diagram above didn't render)</summary>
      <pre>{html.escape(mmd)}</pre>
    </details>
  </section>

  <section>
    <h2>Process Steps</h2>
    {steps_table}
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
<script>
  if (window.mermaid) {{
    mermaid.initialize({{
      startOnLoad: true,
      theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default',
      securityLevel: 'strict',
    }});
  }}
</script>
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
