#!/usr/bin/env python3
"""Phase 5 — EA landscape diagrams (BUILD-SPEC-v2 §5): one Mermaid flowchart
per L1 domain, showing that domain's registered systems grouped by scope.

    python3 scripts/build_ea_diagrams.py

Deterministic and model-free: every diagram is built directly from
registries/systems.json (the reviewed source of truth) and data/taxonomy.json
(L2 group names). Nothing here is generated or inferred -- a domain with zero
sourced systems gets a diagram that says so, not a filled-in guess.

Honesty note, worth reading before asking for richer diagrams: rail-wiki's
systems.json currently has 0-8 entries per domain (4 of 15 domains have
zero). The Air Canada / airline-wiki reference this was modelled on has
20-40+ systems per domain landscape. That density gap is a registry
substrate gap (Phase A research), not something this generator can paper
over without inventing system names the registry doesn't ground -- which is
exactly what the whole registry-first pipeline exists to prevent. Widening
these diagrams means adding sourced entries to registries/systems.json, not
changing this script.

Output:
    ea-diagrams/{l1-slug}.mmd     Mermaid source, one per L1
    site/ea-diagrams/{l1-slug}.html   rendered page (client-side Mermaid)
    site/ea-diagrams/index.html       listing of all 15
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagram_lightbox import LIGHTBOX_CSS, LIGHTBOX_HTML  # noqa: E402
from registry_loader import REGISTRY_DIR, load  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TAXONOMY = REPO / "data" / "taxonomy.json"
MMD_DIR = REPO / "ea-diagrams"
HTML_DIR = REPO / "site" / "ea-diagrams"

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

STYLE = """
:root {
  --bg: #f7f7f5; --panel: #ffffff; --ink: #1c1d1f; --muted: #5c5f66;
  --rule: #d9d9d4; --amber: #c8791a; --charcoal: #2b2e33;
  --draft-bg: #fff4e5; --draft-border: #c8791a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17181a; --panel: #1f2124; --ink: #ececea; --muted: #9a9da3;
    --rule: #33363b; --amber: #e0952f; --charcoal: #ececea;
    --draft-bg: #2a2011; --draft-border: #e0952f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  padding: 2rem 1.25rem;
}
main { max-width: 62rem; margin: 0 auto; }
.draft {
  background: var(--draft-bg); border: 1px solid var(--draft-border);
  border-radius: 6px; padding: .75rem 1rem; margin-bottom: 1.5rem;
  font-size: .85rem; line-height: 1.5;
}
.draft strong { color: var(--amber); }
h1 { font-size: 1.4rem; margin: 0 0 .25rem; color: var(--charcoal); }
.breadcrumb { color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; }
.breadcrumb a { color: var(--amber); text-decoration: none; }
.diagram-wrap {
  border: 1px solid var(--rule); border-radius: 6px; padding: 1rem;
  background: var(--panel); overflow-x: auto; margin-bottom: 1.5rem;
}
.legend { display: flex; gap: 1.25rem; font-size: .8rem; color: var(--muted); margin-bottom: 1rem; }
.legend .sw { display: inline-block; width: .7rem; height: .7rem; border-radius: 2px; margin-right: .35rem; }
ul.grid { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem,1fr)); gap: .6rem; }
ul.grid li a {
  display: block; border: 1px solid var(--rule); border-radius: 6px; padding: .75rem .9rem;
  background: var(--panel); color: var(--ink); text-decoration: none; font-size: .9rem;
}
ul.grid li a b { color: var(--charcoal); }
ul.grid li a .n { color: var(--muted); font-size: .78rem; }
footer { margin-top: 2.5rem; padding-top: 1.25rem; border-top: 1px solid var(--rule); font-size: .8rem; color: var(--muted); }
""" + LIGHTBOX_CSS


def slugify(name: str) -> str:
    s = re.sub(r"[:&,]", "", name.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _mmd_id(prefix: str, i: int) -> str:
    return f"{prefix}{i}"


def _label(text: str, limit: int = 42) -> str:
    t = re.sub(r'["\[\]{}()<>|]', "", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return t


def build_domain_mermaid(l1_id: str, l1_name: str, l2_names: list[str],
                         systems: list) -> str:
    lines = [
        "%%{init: {'flowchart': {'curve': 'basis'}, 'theme':'base'}}%%",
        "flowchart TB",
        "",
        f'  subgraph PROCS["{_label(l1_name, 60)} — L2 process groups"]',
        "    direction LR",
    ]
    l2_ids = []
    for i, l2 in enumerate(l2_names, start=1):
        nid = _mmd_id("G", i)
        l2_ids.append(nid)
        lines.append(f'    {nid}["{_label(l2, 30)}"]')
    lines.append("  end")
    lines.append("")

    company = [e for e in systems if e.scope == "company_specific"]
    industry = [e for e in systems if e.scope == "industry_typical"]
    other = [e for e in systems if e not in company and e not in industry]

    if not systems:
        lines.append(f'  NONE["No systems yet sourced in registries/systems.json\\n'
                     f'for {_label(l1_name, 40)}"]:::empty')
    else:
        if company:
            lines.append('  subgraph COMPANY["Company-specific (UP-confirmed)"]')
            lines.append("    direction TB")
            for i, e in enumerate(company, start=1):
                nid = _mmd_id("C", i)
                lines.append(f'    {nid}["{_label(e.key)}\\n{e.entry_id}"]:::company')
            lines.append("  end")
        if industry:
            lines.append('  subgraph INDUSTRY["Industry-typical"]')
            lines.append("    direction TB")
            for i, e in enumerate(industry, start=1):
                nid = _mmd_id("I", i)
                lines.append(f'    {nid}["{_label(e.key)}\\n{e.entry_id}"]:::industry')
            lines.append("  end")
        if other:
            lines.append('  subgraph OTHER["Other / unscoped"]')
            lines.append("    direction TB")
            for i, e in enumerate(other, start=1):
                nid = _mmd_id("O", i)
                lines.append(f'    {nid}["{_label(e.key)}\\n{e.entry_id}"]:::other')
            lines.append("  end")

    lines += [
        "",
        "classDef company fill:#fdecd8,stroke:#c8791a,color:#5a3407",
        "classDef industry fill:#e6e9ec,stroke:#5c5f66,color:#2b2e33",
        "classDef other fill:#f2f2f0,stroke:#9a9da3,color:#3d4348",
        "classDef empty fill:#fff4e5,stroke:#c8791a,color:#5a3407,stroke-dasharray: 4 3",
    ]
    return "\n".join(lines)


def render_page(l1_id: str, l1_name: str, mmd: str, n_company: int,
                n_industry: int, n_other: int) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(l1_id)} EA Landscape — {html.escape(l1_name)}</title>
<style>{STYLE}</style>
<script src="{MERMAID_CDN}"></script>
</head>
<body>
<main>
  <div class="draft">
    <strong>Deterministic, registry-only.</strong> Built directly from
    registries/systems.json and data/taxonomy.json -- no model call, nothing
    inferred. A sparse or empty domain here means the registry has not been
    sourced for it yet, not that a diagram is missing.
  </div>
  <div class="breadcrumb"><a href="index.html">&larr; EA diagrams</a></div>
  <h1>{html.escape(l1_id)} &middot; {html.escape(l1_name)}</h1>
  <div class="legend">
    <span><span class="sw" style="background:#c8791a"></span>company-specific ({n_company})</span>
    <span><span class="sw" style="background:#5c5f66"></span>industry-typical ({n_industry})</span>
    <span><span class="sw" style="background:#9a9da3"></span>other/unscoped ({n_other})</span>
  </div>
  <div class="diagram-wrap"><pre class="mermaid">{html.escape(mmd)}</pre></div>
  <details>
    <summary style="cursor:pointer; color:var(--muted); font-size:.8rem;">Mermaid source</summary>
    <pre style="font-size:.75rem; overflow-x:auto; background:var(--panel); border:1px solid var(--rule); border-radius:6px; padding:.75rem;">{html.escape(mmd)}</pre>
  </details>
  <footer>
    Independently compiled from public sources. Not affiliated with,
    sponsored by, or endorsed by any railroad. Illustrative of US Class I
    practice.
  </footer>
</main>
{LIGHTBOX_HTML}
<script>
  if (window.mermaid) {{
    mermaid.initialize({{startOnLoad:true,
      theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'base',
      securityLevel: 'strict'}});
  }}
</script>
</body>
</html>
"""


def render_index(rows: list[dict]) -> str:
    items = "".join(
        f'<li><a href="{html.escape(r["slug"])}.html"><b>{html.escape(r["l1_id"])}</b><br>'
        f'{html.escape(r["l1_name"])}<br>'
        f'<span class="n">{r["n_systems"]} system(s) &middot; {r["n_l2"]} L2 group(s)</span></a></li>'
        for r in rows
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>EA Landscape Diagrams</title>
<style>{STYLE}</style>
</head>
<body>
<main>
  <div class="draft">
    <strong>15 EA landscape diagrams, one per L1 domain</strong> (spec §5),
    built deterministically from registries/systems.json. No model call.
  </div>
  <div class="breadcrumb"><a href="../../index.html">&larr; rail-wiki</a></div>
  <h1>EA Landscape Diagrams</h1>
  <ul class="grid">{items}</ul>
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
    r = load(REGISTRY_DIR)
    taxonomy = __import__("json").loads(TAXONOMY.read_text(encoding="utf-8"))
    l2_by_domain: dict[str, list[str]] = {}
    for p in taxonomy["processes"]:
        l2_by_domain.setdefault(p["l1"], [])
        if p["l2"] not in l2_by_domain[p["l1"]]:
            l2_by_domain[p["l1"]].append(p["l2"])

    domains = taxonomy["_meta"]["domains"]
    MMD_DIR.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for d in domains:
        l1_id, l1_name = d["l1_id"], d["l1"]
        num = l1_id.split("-")[1]
        group = f"domain_{num}_"
        systems = [e for e in r.all("systems") if e.domain.startswith(group)]
        l2_names = l2_by_domain.get(l1_name, [])
        slug = f"{l1_id.lower()}-{slugify(l1_name)}"

        mmd = build_domain_mermaid(l1_id, l1_name, l2_names, systems)
        (MMD_DIR / f"{slug}.mmd").write_text(mmd + "\n", encoding="utf-8")

        n_company = sum(1 for e in systems if e.scope == "company_specific")
        n_industry = sum(1 for e in systems if e.scope == "industry_typical")
        n_other = len(systems) - n_company - n_industry
        page = render_page(l1_id, l1_name, mmd, n_company, n_industry, n_other)
        (HTML_DIR / f"{slug}.html").write_text(page, encoding="utf-8")

        rows.append({"slug": slug, "l1_id": l1_id, "l1_name": l1_name,
                    "n_systems": len(systems), "n_l2": len(l2_names)})
        flag = " (EMPTY — no sourced systems)" if not systems else ""
        print(f"  {l1_id}  {len(systems):2d} systems, {len(l2_names):2d} L2 groups{flag}")

    (HTML_DIR / "index.html").write_text(render_index(rows), encoding="utf-8")
    print(f"\nwrote {len(rows)} .mmd files -> {MMD_DIR.relative_to(REPO)}/")
    print(f"wrote {len(rows)} HTML pages + index -> {HTML_DIR.relative_to(REPO)}/")
    empty = [r for r in rows if r["n_systems"] == 0]
    if empty:
        print(f"\n{len(empty)} domain(s) have ZERO sourced systems and will "
              f"render an empty-state diagram: {', '.join(r['l1_id'] for r in empty)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
