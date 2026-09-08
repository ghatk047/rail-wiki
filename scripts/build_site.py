#!/usr/bin/env python3
"""The navigational scaffold: Home -> L1 domain -> L2 process group -> L3
process, for every PID in data/taxonomy.json.

    python3 scripts/build_site.py

Model-free and taxonomy-driven: every page this script writes comes from
data/taxonomy.json (structure) and data/processes.json (content for whatever
has actually been generated). A PID with no entry in processes.json gets a
QUEUED placeholder page, matching the pattern used by every prior wiki this
pipeline is modelled on -- "defined in the taxonomy, not yet authored" is a
real, honest state, not a hidden gap.

URL layout mirrors the reference site directly (domain/group/pid folders at
the repo root, not nested under site/) so Pages serves the same shape:

    /                                          home: 15 L1 domain cards
    {l1-slug}/                                 L2 process-group cards
    {l1-slug}/{l2-slug}/                       L3 process cards (complete/queued)
    {l1-slug}/{l2-slug}/{pid}/                 full process page, or queued placeholder

This supersedes site/preview/ (the flat Phase-4 review list) -- that
structure had no navigation and no queued-state concept, and having two
different site shapes live at once would just be confusing. main() removes
it once the replacement is written for the same content.
"""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagram_lightbox import LIGHTBOX_CSS, LIGHTBOX_HTML, POST_RENDER_FIX_JS  # noqa: E402
from render_preview import (  # noqa: E402
    MERMAID_CDN,
    build_mermaid,
    pill_list,
    render_steps_table,
    scope_badge,
)

REPO = Path(__file__).resolve().parent.parent
TAXONOMY = REPO / "data" / "taxonomy.json"

# Stamped into every generated page as an HTML comment so a stale page (one
# from before a template/rendering change) is greppable across all 290+
# pages, rather than discoverable only by noticing something looks off.
# Bump this whenever page_shell()'s HTML/CSS shape changes.
TEMPLATE_VERSION = "template-v1"
PROCESSES = REPO / "data" / "processes.json"
OLD_PREVIEW_DIR = REPO / "site" / "preview"

CSS = """
:root {
  --bg: #f7f7f5; --panel: #ffffff; --ink: #1c1d1f; --muted: #5c5f66;
  --rule: #d9d9d4; --amber: #c8791a; --charcoal: #2b2e33; --header: #20242b;
  --ok-bg: #e8f5e9; --ok-ink: #1a7f37; --queued-bg: #f2f2f0; --queued-ink: #5c5f66;
  --badge-company-bg: #fdecd8; --badge-company-ink: #8a4b0a;
  --badge-industry-bg: #e6e9ec; --badge-industry-ink: #3d4348;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17181a; --panel: #1f2124; --ink: #ececea; --muted: #9a9da3;
    --rule: #33363b; --amber: #e0952f; --charcoal: #ececea; --header: #14161a;
    --ok-bg: #123321; --ok-ink: #4ade80; --queued-bg: #232527; --queued-ink: #9a9da3;
    --badge-company-bg: #3a2a12; --badge-company-ink: #e0952f;
    --badge-industry-bg: #2a2d31; --badge-industry-ink: #c3c7cc;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
a { color: var(--amber); text-decoration: none; }
a:hover { text-decoration: underline; }
header.top {
  background: var(--header); color: #fff; padding: .85rem 1.5rem;
  display: flex; align-items: center; gap: .75rem; position: sticky; top: 0; z-index: 10;
}
header.top .brand { font-weight: 700; }
header.top .brand .accent { color: var(--amber); }
header.top .pill {
  background: var(--amber); color: #1c1200; font-size: .68rem; font-weight: 700;
  border-radius: 3px; padding: .12rem .45rem; letter-spacing: .03em;
}
.shell { display: flex; min-height: calc(100vh - 54px); }
nav.side {
  width: 15rem; flex: none; border-right: 1px solid var(--rule); padding: 1.25rem 1rem;
  font-size: .82rem;
}
nav.side .l1-link { display: block; padding: .35rem .5rem; border-radius: 4px; color: var(--ink); }
nav.side .l1-link:hover { background: var(--panel); text-decoration: none; }
nav.side .l1-link.active { background: var(--badge-company-bg); color: var(--badge-company-ink); font-weight: 600; }
nav.side .l2-list { margin: .15rem 0 .6rem 1rem; padding-left: .6rem; border-left: 2px solid var(--rule); }
nav.side .l2-list a { display: block; padding: .2rem 0; color: var(--muted); font-size: .78rem; }
nav.side .l2-list a.active { color: var(--amber); font-weight: 600; }
main.content { flex: 1; min-width: 0; padding: 2rem 2.25rem; max-width: 74rem; }
.breadcrumb { color: var(--muted); font-size: .82rem; margin-bottom: 1rem; }
h1 { font-size: 1.5rem; margin: 0 0 .35rem; color: var(--charcoal); }
p.lede { color: var(--muted); margin: 0 0 1.5rem; max-width: 48rem; }
.progress-row { display: flex; align-items: center; gap: .6rem; font-size: .85rem; color: var(--muted); margin-bottom: 1.5rem; }
.bar { flex: none; width: 10rem; height: .45rem; background: var(--rule); border-radius: 999px; overflow: hidden; }
.bar > i { display: block; height: 100%; background: var(--amber); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(15.5rem, 1fr)); gap: 1rem; }
.card {
  border: 1px solid var(--rule); border-radius: 8px; overflow: hidden; background: var(--panel);
  display: flex; flex-direction: column;
}
.card .card-head { background: var(--header); color: #fff; padding: .9rem 1rem; }
.card .card-head .n { font-size: .68rem; color: #cfd3d8; letter-spacing: .04em; }
.card .card-head .t { font-weight: 700; margin-top: .15rem; }
.card .card-body { padding: .8rem 1rem 1rem; font-size: .85rem; flex: 1; display: flex; flex-direction: column; gap: .5rem; }
.card .card-body .stat { color: var(--muted); }
.badge-status { display: inline-flex; align-items: center; gap: .3rem; font-size: .74rem;
  border-radius: 999px; padding: .15rem .55rem; width: fit-content; }
.badge-status.ok { background: var(--ok-bg); color: var(--ok-ink); }
.badge-status.queued { background: var(--queued-bg); color: var(--queued-ink); }
.pid-tag { display: inline-block; background: var(--amber); color: #1c1200; font-weight: 700;
  font-size: .72rem; border-radius: 4px; padding: .1rem .4rem; margin-right: .35rem; }
section { margin-bottom: 1.75rem; }
h2 { font-size: .75rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted);
  border-bottom: 1px solid var(--rule); padding-bottom: .4rem; margin: 0 0 .75rem; }
.pill-row { display: flex; flex-wrap: wrap; gap: .4rem; }
.pill { display: inline-flex; align-items: center; gap: .35rem; border: 1px solid var(--rule);
  border-radius: 999px; padding: .25rem .65rem; font-size: .85rem; background: var(--panel); }
.badge { font-size: .68rem; text-transform: uppercase; letter-spacing: .04em; border-radius: 3px; padding: .1rem .4rem; }
.badge.company { background: var(--badge-company-bg); color: var(--badge-company-ink); }
.badge.industry { background: var(--badge-industry-bg); color: var(--badge-industry-ink); }
.kv { display: grid; grid-template-columns: auto 1fr; gap: .3rem 1rem; font-size: .9rem; }
.kv dt { color: var(--muted); } .kv dd { margin: 0; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: .82rem; white-space: nowrap; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--rule); }
th { background: var(--panel); color: var(--muted); font-size: .68rem; text-transform: uppercase; letter-spacing: .04em; }
tr:last-child td { border-bottom: none; }
td.flag .yes { background: var(--badge-company-bg); color: var(--badge-company-ink); border-radius: 3px; padding: 0 .3rem; }
td.flag .no { color: var(--muted); }
.diagram-wrap { border: 1px solid var(--rule); border-radius: 6px; padding: 1rem; background: var(--panel); overflow-x: auto; }
.queued-box { border: 1px dashed var(--rule); border-radius: 8px; padding: 1.5rem; color: var(--muted); background: var(--panel); }
footer.site-footer { margin-top: 2.5rem; padding-top: 1.25rem; border-top: 1px solid var(--rule); font-size: .8rem; color: var(--muted); }
""" + LIGHTBOX_CSS


def slugify(name: str) -> str:
    s = re.sub(r"[:&,]", "", name.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def load_taxonomy() -> dict:
    return json.loads(TAXONOMY.read_text(encoding="utf-8"))


def load_processes() -> dict:
    if not PROCESSES.exists():
        return {}
    return json.loads(PROCESSES.read_text(encoding="utf-8")).get("processes", {})


def build_index(taxonomy: dict) -> dict:
    """{l1_id: {name, slug, l2s: {l2_name: {slug, pids: [pid,...]}}}}"""
    idx: dict = {}
    for p in taxonomy["processes"]:
        l1_id = p["pid"].split("-")[1]
        l1_key = f"RR-{l1_id}"
        dom = idx.setdefault(l1_key, {"name": p["l1"], "slug": None, "l2s": {}})
        dom["slug"] = f"{l1_key.lower()}-{slugify(p['l1'])}"
        l2 = dom["l2s"].setdefault(p["l2"], {"slug": slugify(p["l2"]), "pids": []})
        l2["pids"].append(p["pid"])
    return idx


def sidebar(idx: dict, active_l1: str | None, active_l2: str | None, prefix: str) -> str:
    items = []
    for l1_key in sorted(idx, key=lambda k: int(k.split("-")[1])):
        dom = idx[l1_key]
        cls = "l1-link active" if l1_key == active_l1 else "l1-link"
        items.append(f'<a class="{cls}" href="{prefix}{dom["slug"]}/index.html">{html.escape(l1_key)} &middot; {html.escape(dom["name"])}</a>')
        if l1_key == active_l1:
            l2_links = []
            for l2_name, l2 in dom["l2s"].items():
                a_cls = "active" if l2_name == active_l2 else ""
                l2_links.append(
                    f'<a class="{a_cls}" href="{prefix}{dom["slug"]}/{l2["slug"]}/index.html">{html.escape(l2_name)}</a>')
            items.append(f'<div class="l2-list">{"".join(l2_links)}</div>')
    return "\n".join(items)


def page_shell(title: str, breadcrumb: str, sidebar_html: str, body: str,
              prefix: str, extra_head: str = "") -> str:
    return f"""<!doctype html>
<!-- {TEMPLATE_VERSION} -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
{extra_head}
</head>
<body>
<header class="top">
  <a href="{prefix}index.html" style="color:#fff;text-decoration:none;">
    <span class="brand">Rail Wiki <span class="accent">Process Catalog</span></span>
  </a>
  <span class="pill">PHASE 4c</span>
</header>
<div class="shell">
  <nav class="side">{sidebar_html}</nav>
  <main class="content">
    <div class="breadcrumb">{breadcrumb}</div>
    {body}
    <footer class="site-footer">
      Independently compiled from public sources. Not affiliated with,
      sponsored by, or endorsed by any railroad. Illustrative of US Class I practice.
    </footer>
  </main>
</div>
</body>
</html>
"""


def status_badge(complete: bool) -> str:
    return ('<span class="badge-status ok">&check; complete</span>' if complete else
            '<span class="badge-status queued">&#8987; queued</span>')


def progress_row(done: int, total: int) -> str:
    pct = round(100 * done / total) if total else 0
    return (f'<div class="progress-row"><div class="bar"><i style="width:{pct}%"></i></div>'
            f'{done} of {total} complete</div>')


# --- HOME ----------------------------------------------------------------------
def render_home(idx: dict, procs: dict) -> str:
    cards = []
    for l1_key in sorted(idx, key=lambda k: int(k.split("-")[1])):
        dom = idx[l1_key]
        all_pids = [pid for l2 in dom["l2s"].values() for pid in l2["pids"]]
        done = sum(1 for pid in all_pids if pid in procs)
        cards.append(f"""<div class="card">
  <div class="card-head"><div class="n">{html.escape(l1_key)} &middot; {len(dom['l2s'])} process groups</div>
    <div class="t">{html.escape(dom['name'])}</div></div>
  <div class="card-body">
    <div class="stat">{done} of {len(all_pids)} subprocesses complete</div>
    <a href="{dom['slug']}/index.html">Explore domain &rarr;</a>
  </div>
</div>""")
    total_all = sum(len([pid for l2 in d["l2s"].values() for pid in l2["pids"]]) for d in idx.values())
    done_all = len(procs)
    body = f"""<p class="lede">Process Catalog</p>
<h1>US Class I Freight Railroad &mdash; Business Process Reference</h1>
<p class="lede">End-to-end L1 &rarr; L2 &rarr; L3 process documentation for a US Class I
railroad archetype, instantiated with Union Pacific public examples. Every process page
includes registry-grounded systems, roles, regulatory hooks and KPIs, with a step
breakdown and flowchart where generated.</p>
{progress_row(done_all, total_all)}
<div class="card-grid">{"".join(cards)}</div>
<section style="margin-top:2rem;">
  <h2>Architecture</h2>
  <p><a href="site/ea-diagrams/index.html">EA landscape diagrams (15, one per domain) &rarr;</a></p>
</section>"""
    return page_shell("US Class I Freight Railroad — Business Process Reference",
                      "Process Catalog", sidebar(idx, None, None, ""), body, "")


# --- L1 DOMAIN -------------------------------------------------------------------
def render_l1(l1_key: str, dom: dict, procs: dict) -> str:
    cards = []
    for l2_name, l2 in dom["l2s"].items():
        done = sum(1 for pid in l2["pids"] if pid in procs)
        cards.append(f"""<div class="card">
  <div class="card-head"><div class="n">{html.escape(dom['name'])}</div>
    <div class="t">{html.escape(l2_name)}</div></div>
  <div class="card-body">
    <div class="stat">{done} of {len(l2['pids'])} subprocesses complete</div>
    <a href="{l2['slug']}/index.html">View group &rarr;</a>
  </div>
</div>""")
    all_pids = [pid for l2 in dom["l2s"].values() for pid in l2["pids"]]
    done_all = sum(1 for pid in all_pids if pid in procs)
    body = f"""<h1>{html.escape(dom['name'])}</h1>
<p class="lede">{html.escape(l1_key)} &middot; {len(dom['l2s'])} process groups &middot; {len(all_pids)} subprocesses</p>
{progress_row(done_all, len(all_pids))}
<div class="card-grid">{"".join(cards)}</div>"""
    bc = f'<a href="../index.html">Home</a> &rsaquo; <b>{html.escape(dom["name"])}</b>'
    return page_shell(dom["name"], bc, sidebar(FULL_IDX, l1_key, None, "../"), body, "../")


# --- L2 GROUP --------------------------------------------------------------------
def render_l2(l1_key: str, dom: dict, l2_name: str, l2: dict, procs: dict, taxonomy_by_pid: dict) -> str:
    cards = []
    for pid in l2["pids"]:
        p = procs.get(pid)
        complete = p is not None
        name = p["name"] if p else taxonomy_by_pid[pid].get("_display_name", pid)
        n_steps = len(p.get("steps", [])) if p else 0
        n_gates = sum(1 for s in (p.get("steps") or []) if str(s.get("decision_point", "N")).upper() == "Y"
                     or str(s.get("exception", "N")).upper() == "Y") if p else 0
        stat = f"{n_steps} steps &middot; {n_gates} gates" if complete and n_steps else ("registry-grounded" if complete else "not yet generated")
        link = f'<a href="{pid}/index.html">View process &rarr;</a>' if complete else '<span style="color:var(--muted);">Queued</span>'
        cards.append(f"""<div class="card">
  <div class="card-head"><div class="n"><span class="pid-tag">{html.escape(pid)}</span></div>
    <div class="t">{html.escape(name)}</div></div>
  <div class="card-body">
    {status_badge(complete)}
    <div class="stat">{stat}</div>
    {link}
  </div>
</div>""")
    done = sum(1 for pid in l2["pids"] if pid in procs)
    body = f"""<h1>{html.escape(l2_name)}</h1>
<p class="lede">{html.escape(dom['name'])} &middot; {len(l2['pids'])} subprocesses</p>
{progress_row(done, len(l2['pids']))}
<div class="card-grid">{"".join(cards)}</div>"""
    bc = (f'<a href="../../index.html">Home</a> &rsaquo; '
         f'<a href="../index.html">{html.escape(dom["name"])}</a> &rsaquo; <b>{html.escape(l2_name)}</b>')
    return page_shell(f"{l2_name} — {dom['name']}", bc, sidebar(FULL_IDX, l1_key, l2_name, "../../"), body, "../../")


# --- L3 PROCESS ------------------------------------------------------------------
def render_l3_complete(l1_key: str, dom: dict, l2_name: str, l2: dict, p: dict) -> str:
    systems_html = "".join(
        f'<li><strong>{html.escape(s["name"])}</strong> {scope_badge(s.get("scope"))} '
        f'<span style="color:var(--muted); font-size:.8rem;">{html.escape(s.get("source_id",""))}</span></li>'
        for s in p.get("systems", [])
    ) or "<li>(none)</li>"
    sources_html = "".join(
        f'<li><a href="{html.escape(u)}" target="_blank" rel="noopener">{html.escape(u)}</a></li>'
        for u in p.get("sources", [])
    ) or "<li>(none)</li>"
    steps = p.get("steps") or []
    mmd = build_mermaid(steps)
    n_gates = sum(1 for s in steps if str(s.get("decision_point", "N")).upper() == "Y"
                 or str(s.get("exception", "N")).upper() == "Y")

    diagram_section = f"""<section>
  <h2>Process Flow</h2>
  <div class="diagram-wrap"><pre class="mermaid">{html.escape(mmd)}</pre></div>
</section>
<section>
  <h2>Process Steps &middot; {len(steps)} steps, {n_gates} gates</h2>
  {render_steps_table(steps)}
</section>""" if steps else ""

    body = f"""<div class="pid-tag" style="font-size:.8rem;">{html.escape(p['pid'])}</div>
<h1>{html.escape(p['name'])}</h1>
<p class="lede">{html.escape(dom['name'])} &rsaquo; {html.escape(l2_name)} &middot; confidence: {html.escape(p.get('confidence','?'))}</p>

<section>
  <h2>Description</h2>
  <p>{html.escape(p.get('description',''))}</p>
</section>
{diagram_section}
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
  <h2>Pain Points (spec &sect;7, closed list)</h2>
  <ul>{"".join(f"<li>{html.escape(x)}</li>" for x in p.get('pain_points', [])) or "<li>(none)</li>"}</ul>
</section>
<section>
  <h2>Sources</h2>
  <ul>{sources_html}</ul>
</section>
<p style="color:var(--muted); font-size:.8rem;">Last reviewed (machine): {html.escape(p.get('last_reviewed',''))}
&mdash; DRAFT, not yet human-reviewed per spec &sect;11.</p>"""

    bc = (f'<a href="../../../index.html">Home</a> &rsaquo; '
         f'<a href="../../index.html">{html.escape(dom["name"])}</a> &rsaquo; '
         f'<a href="../index.html">{html.escape(l2_name)}</a> &rsaquo; <b>{html.escape(p["pid"])}</b>')
    extra_head = f'<script src="{MERMAID_CDN}"></script>'
    page = page_shell(f"{p['pid']} — {p['name']}", bc, sidebar(FULL_IDX, l1_key, l2_name, "../../../"),
                      body + (LIGHTBOX_HTML if steps else ""),
                      "../../../", extra_head=extra_head)
    page = page.replace("</body>", f"""<script>
{POST_RENDER_FIX_JS}
  if (window.mermaid) {{
    mermaid.initialize({{startOnLoad:false,
      theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default',
      securityLevel: 'strict'}});
    mermaid.run().then(function () {{ stripSvgCaps(); }});
  }}
</script>
</body>""")
    return page


def render_l3_queued(l1_key: str, dom: dict, l2_name: str, pid: str, l3_name: str) -> str:
    body = f"""<div class="pid-tag" style="font-size:.8rem;">{html.escape(pid)}</div>
<h1>{html.escape(l3_name)}</h1>
<p class="lede">{html.escape(dom['name'])} &rsaquo; {html.escape(l2_name)}</p>
<div class="queued-box">
  <strong>Queued.</strong> This process is defined in the taxonomy but has not been
  generated yet. Run <code>python3 scripts/generate_rail_wiki.py --pid {html.escape(pid)}</code>
  or <code>python3 scripts/run_batch.py --count N</code> to generate it.
</div>"""
    bc = (f'<a href="../../../index.html">Home</a> &rsaquo; '
         f'<a href="../../index.html">{html.escape(dom["name"])}</a> &rsaquo; '
         f'<a href="../index.html">{html.escape(l2_name)}</a> &rsaquo; <b>{html.escape(pid)}</b>')
    return page_shell(f"{pid} — queued", bc, sidebar(FULL_IDX, l1_key, l2_name, "../../../"), body, "../../../")


FULL_IDX: dict = {}


def main() -> int:
    global FULL_IDX
    taxonomy = load_taxonomy()
    procs = load_processes()
    FULL_IDX = build_index(taxonomy)

    taxonomy_by_pid = {p["pid"]: p for p in taxonomy["processes"]}
    for pid, p in taxonomy_by_pid.items():
        # human-readable fallback name for queued cards -- last L2-cluster
        # segment isn't unique per PID, so just show the PID with its L2.
        p["_display_name"] = p["l2"]

    n_pages = 0
    (REPO / "index.html").write_text(render_home(FULL_IDX, procs), encoding="utf-8")
    n_pages += 1

    for l1_key, dom in FULL_IDX.items():
        l1_dir = REPO / dom["slug"]
        l1_dir.mkdir(parents=True, exist_ok=True)
        (l1_dir / "index.html").write_text(render_l1(l1_key, dom, procs), encoding="utf-8")
        n_pages += 1

        for l2_name, l2 in dom["l2s"].items():
            l2_dir = l1_dir / l2["slug"]
            l2_dir.mkdir(parents=True, exist_ok=True)
            (l2_dir / "index.html").write_text(
                render_l2(l1_key, dom, l2_name, l2, procs, taxonomy_by_pid), encoding="utf-8")
            n_pages += 1

            for pid in l2["pids"]:
                pid_dir = l2_dir / pid
                pid_dir.mkdir(parents=True, exist_ok=True)
                if pid in procs:
                    out = render_l3_complete(l1_key, dom, l2_name, l2, procs[pid])
                else:
                    out = render_l3_queued(l1_key, dom, l2_name, pid, taxonomy_by_pid[pid]["_display_name"])
                (pid_dir / "index.html").write_text(out, encoding="utf-8")
                n_pages += 1

    print(f"wrote {n_pages} pages: 1 home + {len(FULL_IDX)} L1 + "
        f"{sum(len(d['l2s']) for d in FULL_IDX.values())} L2 + "
        f"{sum(len(l2['pids']) for d in FULL_IDX.values() for l2 in d['l2s'].values())} L3")
    print(f"  {len(procs)} complete, "
        f"{sum(len(l2['pids']) for d in FULL_IDX.values() for l2 in d['l2s'].values()) - len(procs)} queued")

    if OLD_PREVIEW_DIR.exists():
        import shutil
        shutil.rmtree(OLD_PREVIEW_DIR)
        print(f"  removed superseded {OLD_PREVIEW_DIR.relative_to(REPO)}/")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
