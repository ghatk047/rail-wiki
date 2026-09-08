#!/usr/bin/env python3
"""
generate_rail_ea.py — enterprise-architecture diagrams for the Rail Process Wiki.

Port of shipping-wiki/scripts/generate_shipping_ea.py. One diagram per L1 domain,
ea-01 … ea-15, published at ea-diagrams/{ea-id}/index.html so EA pages sit at the
same depth convention as process pages and share the sidebar, CSS and lightbox.

    python3 scripts/generate_rail_ea.py --id ea-03
    python3 scripts/generate_rail_ea.py --id ea-03 --force
    python3 scripts/generate_rail_ea.py --all
    python3 scripts/generate_rail_ea.py --index-only

Shares config, the sanitiser, the git transport and the sidebar builder with
generate_rail_wiki.py, so both files must sit in the same scripts/ folder.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_rail_wiki import (  # noqa: E402
    EA_DIAGRAMS, EA_DIR_SLUG, EA_TO_L1, PAGES_BASE, L1_META, TAXONOMY,
    EA_W, EA_H, DIAGRAM_DIR, IMG_DIR, DATA_DIR, TEMPLATE_VERSION,
    DEPTH_EA, DEPTH_EA_IDX,
    SYSTEM_PROMPT, SYSTEM_PROMPT_JSON, dump_raw, ollama_call, extract_json,
    sanitise_mermaid, render_mermaid, finalize_svg,
    page_shell, esc, sys_tags, prefix, write, commit_and_push, verify_live,
    log, PRIMARY_MODEL, FALLBACK_MODEL,
    registry_systems_for, in_registry, SYS_NAMES,
)

EA_TRACKER = DATA_DIR / "ea_diagrams.json"

EA_JSON_SHAPE = """{
  "description": "3-4 sentence architecture description for a US Class I freight railroad",
  "systems": ["NetControl", "I-ETMS", "Umler"],
  "layers": ["External and Interline", "Planning", "Execution Control", "Data and Analytics"]
}"""

# PORTED FIX — form reference from a different industry (aviation) so the model
# copies the CONSTRUCT and none of the content. Measured floors come from the
# airline reference repo: 30.6 nodes, 12.4 labelled edges, 5.4 subgraphs,
# 8.5 classDef/style lines across 149 real .mmd files.
FORM_EXAMPLE = """%%{init: {'theme':'base','themeVariables':{'fontSize':'13px','fontFamily':'Helvetica Neue, Helvetica, Arial, sans-serif'}}}%%
flowchart TB

  subgraph EXT["\U0001F310 External Feeds"]
    direction LR
    FAA["FAA SWIM\\nNOTAMs - TFRs - Slots"]
    WX["Jeppesen WSI\\nWeather - Winds - SIGMETs"]
  end

  subgraph DISPATCH["\U0001F4CB Dispatch and Flight Planning"]
    direction TB
    FP["NAVBLUE Lido\\nFlight plan - Fuel calc"]
    WB["Weight and Balance\\nLoad control - ZFW calc"]
    FAA -->|"NOTAMs - flow programs"| FP
    FP -->|"fuel load"| WB
  end

  subgraph NOC["\U0001F5A5 Operations Control"]
    direction TB
    SCOUT["Avtec Scout NOC\\nFlight watch - Gate - Crew"]
    DELAY["Delay Management\\nRoot cause - OTP tracking"]
    SCOUT -->|"delay codes"| DELAY
  end

  WX -->|"meteorological data"| SCOUT
  WB -->|"final load"| SCOUT

  classDef ext  fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
  classDef disp fill:#fff7ed,stroke:#ea580c,color:#7c2d12
  classDef noc  fill:#20242b,color:#fff,stroke:#20242b
  classDef key  fill:#c8791a,color:#fff,stroke:#c8791a

  class FAA,WX ext
  class FP,WB disp
  class SCOUT noc
  class DELAY key"""


def system_menu(l1_code):
    """Every allowed node name, drawn only from registries/systems.json.

    Domain-owned systems come first; the rest of the sourced estate follows,
    because a real landscape legitimately reaches systems owned by other domains
    (Umler, I-ETMS, NetControl) and every one of those is still registry-sourced.
    """
    own, other = registry_systems_for(l1_code)
    own_names = [s["name"] for s in own]
    other_names = [s["name"] for s in other]
    return own_names, other_names


def ea_prompt(ea_id, title, scope, l1_code):
    own, other = system_menu(l1_code)
    allowed = "; ".join(own + other)
    l2_names = [g["l2_name"] for g in TAXONOMY if g["l1"] == l1_code]
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Produce an enterprise architecture diagram for a US Class I freight railroad,\n"
        f"Union Pacific archetype.\n"
        f"  Diagram ID : {ea_id.upper()}\n"
        f"  Title      : {title}\n"
        f"  Scope      : {scope}\n"
        f"  Capabilities this domain owns: {'; '.join(l2_names)}\n\n"
        "STRUCTURE — copy this construct exactly. It is from a different industry, so take\n"
        "the FORM and none of the content:\n\n"
        f"{FORM_EXAMPLE}\n\n"
        "REQUIREMENTS:\n"
        "- flowchart TB at the top level. Every subgraph declares its own direction TB or LR.\n"
        "- 5 to 7 subgraphs named for architecture layers or stages, each title starting with\n"
        "  one emoji. Use rail layer names, for example: External and Interline, Planning and\n"
        "  Service Design, Dispatch and Movement Control, Terminal and Yard Execution,\n"
        "  Mechanical and Engineering, Commercial and Billing, Data and Analytics.\n"
        "- 22 to 30 nodes TOTAL, and every subgraph must contain AT LEAST 4 nodes. A layer\n  with only two boxes in it is not an architecture layer — split it or fill it.\n  EVERY node label is two lines: the real system or facility name,\n"
        "  then a literal backslash-n, then 2 or 3 capabilities separated by hyphens.\n"
        "  Example: NC[\"NetControl\\nTrain sheet - Authority - Movement planning\"]\n"
        "- EVERY arrow carries a label naming the data that flows, in the form\n"
        "  A -->|\"consist and waybill\"| B. Unlabelled arrows are not acceptable.\n"
        "  Use real rail data flows: consist, waybill, train sheet, track authority,\n"
        "  PTC initialisation, bad order card, car hire event, interchange event,\n"
        "  Umler equipment record, defect detector alarm, crew call, hours of service.\n"
        "- Intra-layer arrows go inside their subgraph. Cross-layer arrows go after the last\n"
        "  subgraph closes.\n"
        "- Finish with 5 to 7 classDef lines and matching class assignment lines. Use\n"
        "  fill:#c8791a,color:#fff,stroke:#c8791a for the single most important system in\n"
        "  this domain and a lighter palette for the rest.\n"
        f"- Use ONLY these system and facility names as nodes: {allowed}.\n"
        "  Do not invent a vendor, product or brand that is not in that list. Where a generic\n"
        "  capability is needed, name it descriptively, for example Interchange partner\n"
        "  railroad or Shipper EDI, and never as a fake product.\n"
        "- Node IDs start with a letter. No parentheses, ampersands or angle brackets\n"
        "  anywhere in a label. Never use <br/> — use a literal backslash-n.\n\n"
        f"Return exactly this JSON shape and nothing else:\n{EA_JSON_SHAPE}\n"
    )


def generate_ea(ea_id, title, scope, l1_code):
    """Call 1 — narrative and systems only. No Mermaid rules in this prompt."""
    own, other = system_menu(l1_code)
    prompt = (
        f"{SYSTEM_PROMPT_JSON}\n\n"
        f"Describe the enterprise architecture for a US Class I freight railroad,\n"
        f"Union Pacific archetype.\n"
        f"  Diagram ID : {ea_id.upper()}\n  Title : {title}\n  Scope : {scope}\n\n"
        f"Systems sourced specifically for this domain: {'; '.join(own) or 'none'}\n"
        f"Other systems in the sourced estate you may draw on: {'; '.join(other)}\n\n"
        "Name 8 to 12 systems from those lists that appear in this architecture. Do not\n"
        "invent systems that are not in the lists.\n"
        "Do NOT include a mermaid field — the diagram is requested separately.\n\n"
        f"Return exactly this JSON shape and nothing else:\n{EA_JSON_SHAPE}\n"
    )
    for i, model in enumerate((PRIMARY_MODEL, PRIMARY_MODEL, FALLBACK_MODEL)):
        try:
            raw = ollama_call(prompt, model, temperature=0.25 + 0.1 * i)
            data = extract_json(raw, required=("systems",))
            if data and data.get("systems"):
                return data
            dump_raw(f"{ea_id}-json-{i+1}", raw)
            log(f"{ea_id}: unusable JSON from {model} (try {i+1}/3)", "WARN")
        except Exception as exc:
            log(f"{ea_id}: Ollama error on {model} — {exc}", "WARN")
        time.sleep(2)
    return None


def fix_breaks(mmd):
    """Convert HTML line breaks to Mermaid's \\n before the sanitiser strips < >."""
    if not mmd:
        return mmd
    for tag in ("<br/>", "<br />", "<br>"):
        mmd = mmd.replace(tag, "\\n")
    return mmd


def generate_ea_mermaid(ea_id, title, scope, l1_code, data, attempt=0):
    """Call 2 — raw Mermaid only, never inside JSON."""
    sys_list = ", ".join(str(s) for s in data.get("systems", [])[:12])
    prompt = ea_prompt(ea_id, title, scope, l1_code) + (
        f"\n\nSystems that must appear as nodes: {sys_list}\n"
        "Output the raw Mermaid and nothing else. No JSON, no fences, no commentary.\n"
    )
    models = [PRIMARY_MODEL, PRIMARY_MODEL, FALLBACK_MODEL]
    model = models[min(attempt, len(models) - 1)]
    try:
        return sanitise_mermaid(fix_breaks(ollama_call(prompt, model,
                                                       temperature=0.2 + 0.1 * attempt)))
    except Exception as exc:
        log(f"{ea_id}: mermaid call failed on {model} — {exc}", "WARN")
        return None


def ea_richness(mmd):
    """Floors from the airline reference repo's 149 measured diagrams."""
    if not mmd:
        return 0, {}
    # Same inline-definition problem as the process scorer: a line-anchored regex
    # undercounts. It scored EA-03 at 16 nodes when the published diagram has 27,
    # i.e. comfortably inside the 22-30 target it was being marked down for missing.
    _KW = {"subgraph", "classDef", "class", "style", "flowchart", "graph",
           "direction", "end", "linkStyle"}
    _ids = re.findall(r'\b([A-Za-z][A-Za-z0-9_]*)\s*[\[\({]', mmd)
    m = {
        "nodes":     len({i for i in _ids if i not in _KW}),
        "labelled":  mmd.count("-->|"),
        "subgraphs": mmd.count("subgraph"),
        "classdefs": mmd.count("classDef"),
    }
    score = ((m["nodes"] >= 22) + (m["labelled"] >= 12)
             + (m["subgraphs"] >= 5) + (m["classdefs"] >= 5))
    return score, m


def ea_registry_audit(ea_id, mmd, data):
    """Log every node label that does not resolve to registries/systems.json."""
    # Only node labels are checked. A subgraph title is an architecture layer
    # name ("Terminal and Yard Execution"), not a system, so scanning those
    # produced nine false "unregistered" findings on the first EA-03 run and
    # made the audit useless as a signal.
    body = "\n".join(l for l in (mmd or "").splitlines()
                     if not l.strip().startswith("subgraph"))
    labels = re.findall(r'\w+\["([^"\\]+)', body)
    # The prompt deliberately allows descriptive generics where no product
    # exists; they are not registry misses.
    ALLOWED_GENERIC = ("interline partner", "shipper", "partner railroad",
                       "customer", "regulator", "fra", "stb", "aar")
    findings = [l.strip() for l in labels
                if not in_registry(l, SYS_NAMES)
                and not any(g in l.lower() for g in ALLOWED_GENERIC)]
    for s in data.get("systems", []) or []:
        if not in_registry(s, SYS_NAMES):
            findings.append(str(s))
    findings = sorted(set(findings))
    if findings:
        log(f"  registry audit {ea_id}: {len(findings)} node/system name(s) not in "
            f"registries/systems.json", "WARN")
        for f in findings[:25]:
            log(f"    UNREGISTERED: {f}", "WARN")
    else:
        log(f"  registry audit {ea_id}: every node resolves to a sourced system")
    return findings


def render_ea(ea_id, title, scope, l1_code, data):
    """Score up to 3 drafts, keep the best, render SVG for display and PNG for download."""
    mmd_path = DIAGRAM_DIR / f"{ea_id}.mmd"
    svg_path = IMG_DIR / f"{ea_id}.svg"
    png_path = IMG_DIR / f"{ea_id}.png"

    best, best_score = None, -1
    for attempt in range(3):
        cand = generate_ea_mermaid(ea_id, title, scope, l1_code, data, attempt=attempt)
        score, metrics = ea_richness(cand)
        if cand and score > best_score:
            best, best_score = cand, score
        log(f"  {ea_id} draft {attempt+1}: score {score}/4 {metrics}")
        if score >= 3:
            break
    if not best:
        return None, None, []
    if best_score < 2:
        log(f"  {ea_id}: diagram is thin (score {best_score}/4) — publishing anyway, "
            f"re-run with --id {ea_id} --force", "WARN")

    audit = ea_registry_audit(ea_id, best, data)

    svg_ok = False
    for attempt in range(1, 4):
        svg_ok, info = render_mermaid(best, mmd_path, svg_path, EA_W, EA_H)
        if svg_ok:
            finalize_svg(svg_path)
            log(f"  {ea_id} rendered as SVG ({info})")
            break
        log(f"  {ea_id} SVG render attempt {attempt}/3 failed: {info}", "WARN")
        retry = generate_ea_mermaid(ea_id, title, scope, l1_code, data, attempt=attempt)
        if retry:
            best = retry
    if not svg_ok:
        return None, None, audit

    png_out = None
    for w, h, scale in [(EA_W, EA_H, 2), (EA_W, EA_H, 1), (2560, 1440, 2)]:
        ok, info = render_mermaid(best, mmd_path, png_path, w, h, scale=scale)
        if ok:
            log(f"  {ea_id} PNG fallback at {w}x{h} scale {scale} ({info})")
            png_out = png_path
            break
    return svg_path, png_out, audit


def build_ea_page(ea_id, title, scope, l1_code, data, has_png):
    p = prefix(DEPTH_EA)
    icon, l1_name, l1_slug, fam = L1_META[l1_code]
    dl_png = (f' &bull; <a href="{p}assets/img/{ea_id}.png" download>Download PNG</a>'
              if has_png else "")
    main = f"""      <div class="page-header">
        <div class="breadcrumb">
          <a href="{p}index.html">Home</a> &rsaquo;
          <a href="../index.html">Enterprise Architecture</a>
        </div>
        <h1><span class="pid-badge org-badge-{fam}">EA</span> {ea_id.upper()} &mdash; {esc(title)}</h1>
        <p>{esc(scope)}</p>
      </div>

      <div class="card">
        <div class="card-header">&#x1F5FA; Architecture Diagram &mdash; 4K vector, click to zoom</div>
        <div class="card-body">
          <div class="diagram-wrap">
            <a href="{p}assets/img/{ea_id}.svg" data-lightbox data-title="{ea_id.upper()} {esc(title)}">
              <img src="{p}assets/img/{ea_id}.svg" alt="{ea_id.upper()} {esc(title)}">
            </a>
            <p>Vector diagram &mdash; stays sharp at any zoom &bull; Scroll to zoom &bull; Drag to pan
               &bull; <a href="{p}assets/img/{ea_id}.svg" download>Download SVG</a>{dl_png}</p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">&#x1F4CB; Diagram Details</div>
        <div class="card-body">
          <table class="attr-table">
            <tr><th>Diagram ID</th><td>{ea_id.upper()}</td></tr>
            <tr><th>Title</th><td>{esc(title)}</td></tr>
            <tr><th>L1 Domain</th><td><a href="{p}{l1_slug}/index.html">{icon} {esc(l1_name)}</a></td></tr>
            <tr><th>Scope</th><td>{esc(scope)}</td></tr>
            <tr><th>Key Systems</th><td>{sys_tags(data.get('systems'))}</td></tr>
            <tr><th>Description</th><td>{esc(data.get('description', scope))}</td></tr>
          </table>
        </div>
      </div>
"""
    return page_shell(f"{ea_id.upper()} &mdash; {esc(title)}", "Enterprise Architecture",
                      DEPTH_EA, main, active_ea=ea_id)


def build_ea_index(tracker):
    cards = []
    for ea_id, title, scope in EA_DIAGRAMS:
        state = ("&#x2705; rendered" if tracker.get(ea_id, {}).get("status") == "Complete"
                 else '<span class="text-muted">queued</span>')
        cards.append(f"""    <div class="ea-card">
      <h4><a href="{ea_id}/index.html">{ea_id.upper()} &mdash; {esc(title)}</a></h4>
      <p>{esc(scope)}</p>
      <p class="text-sm" style="margin-top:6px">{state}</p>
    </div>""")
    main = f"""      <div class="page-header">
        <div class="breadcrumb"><a href="{prefix(DEPTH_EA_IDX)}index.html">Home</a></div>
        <h1>&#x1F5FA; Enterprise Architecture</h1>
        <p>System landscape and data flow diagrams, one per L1 domain. Every node is a
           system or facility that resolves to a sourced entry in
           <code>registries/systems.json</code>. Every diagram renders at 4K &mdash; click to zoom.</p>
      </div>
      <div class="ea-grid">
{chr(10).join(cards)}
      </div>
"""
    return page_shell("Enterprise Architecture", "Enterprise Architecture",
                      DEPTH_EA_IDX, main, active_ea="index")


def load_ea_tracker():
    if EA_TRACKER.exists():
        try:
            return json.loads(EA_TRACKER.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def main():
    ap = argparse.ArgumentParser(description="Rail wiki EA diagram generator")
    ap.add_argument("--id", help="single diagram, e.g. ea-03")
    ap.add_argument("--all", action="store_true", help="every incomplete diagram")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if the tracker says Complete")
    ap.add_argument("--index-only", action="store_true",
                    help="rebuild the EA index page only, no model calls")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    for d in (DATA_DIR, DIAGRAM_DIR, IMG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    tracker = load_ea_tracker()

    if args.index_only:
        write(f"{EA_DIR_SLUG}/index.html", build_ea_index(tracker))
        commit_and_push("Rebuild EA index", no_push=args.no_push)
        return

    targets = EA_DIAGRAMS
    if args.id:
        wanted = args.id.lower()
        targets = [d for d in EA_DIAGRAMS if d[0] == wanted]
        if not targets:
            sys.exit(f"Unknown diagram {args.id}. "
                     f"Valid: {', '.join(d[0] for d in EA_DIAGRAMS)}")
    elif not args.all:
        targets = [d for d in EA_DIAGRAMS
                   if tracker.get(d[0], {}).get("status") != "Complete"][:1]

    if not args.force:
        targets = [d for d in targets if tracker.get(d[0], {}).get("status") != "Complete"]
    if not targets:
        log("nothing to do — use --force to rebuild a completed diagram")
        return

    done, failed = [], []
    try:
        for ea_id, title, scope in targets:
            l1_code = EA_TO_L1[ea_id]
            log(f"── {ea_id.upper()} — {title}")
            data = generate_ea(ea_id, title, scope, l1_code)
            if not data:
                failed.append(ea_id)
                continue
            svg, png, audit = render_ea(ea_id, title, scope, l1_code, data)
            if not svg:
                log(f"{ea_id}: diagram failed after 3 attempts — skipped", "ERROR")
                failed.append(ea_id)
                continue
            write(f"{EA_DIR_SLUG}/{ea_id}/index.html",
                  build_ea_page(ea_id, title, scope, l1_code, data, bool(png)))
            tracker[ea_id] = {
                "status": "Complete",
                "url": f"{PAGES_BASE}/{EA_DIR_SLUG}/{ea_id}/index.html",
                "completed_at": datetime.now().isoformat(timespec="seconds"),
                "unregistered_names": audit,
            }
            done.append(ea_id)
            log(f"  wrote {EA_DIR_SLUG}/{ea_id}/index.html")
    except KeyboardInterrupt:
        log("interrupted — publishing what is done", "WARN")

    EA_TRACKER.write_text(json.dumps(tracker, indent=2), encoding="utf-8")
    write(f"{EA_DIR_SLUG}/index.html", build_ea_index(tracker))
    ok = commit_and_push(
        f"Generate {', '.join(x.upper() for x in done) or 'EA index'} under {TEMPLATE_VERSION}",
        no_push=args.no_push)

    if done and ok and not args.no_verify and not args.no_push:
        url = f"{PAGES_BASE}/{EA_DIR_SLUG}/{done[-1]}/index.html"
        log("verified live: " + url if verify_live(url) else f"could not verify {url}")

    log(f"EA run complete — {len(done)} published, {len(failed)} failed"
        + (f" ({', '.join(failed)})" if failed else ""))


if __name__ == "__main__":
    main()
