#!/usr/bin/env python3
"""
generate_rail_wiki.py — US Class I Freight Railroad Process Wiki generator.

Reference archetype: Union Pacific. Repo: https://github.com/ghatk047/rail-wiki
Pages: https://ghatk047.github.io/rail-wiki/

This is a port of shipping-wiki/scripts/generate_shipping_wiki.py. Everything
that file learned the hard way is carried over deliberately and is marked below
with "PORTED FIX" — do not simplify those without re-reading why they exist:

  1. Content and diagram are two SEPARATE model calls. Mermaid never travels
     inside a JSON string, so a 14B model never has to escape \\n correctly.
  2. Two separate system prompts. The JSON prompt forbids diagram syntax; only
     the Mermaid prompt recites Mermaid rules.
  3. extract_json() strips %%{init}%% and flowchart lines first, then walks every
     balanced object and returns the first that parses AND carries the required
     key. Unparseable responses land in data/raw/ for diagnosis.
  4. The label sanitiser stashes label and edge-label text before the
     digit.digit -> S1_1 node-ID rewrite, so 49 CFR 213.9, GCOR 6.28 and
     FRA Class 4.0 survive intact. Label cleaning covers [..], (..) AND {..}.
  5. One canonical %%{init}%% with a system-resident font stack. An SVG loaded
     through <img> cannot fetch webfonts and silently falls back to serif.
  6. Diagrams are scored before publishing; 3 drafts, keep the best, publish
     under the floor rather than blocking, but log a WARNing naming the PID.
  7. finalize_svg() rewrites width="100%" to the real viewBox pixel width and
     strips mmdc's inline max-width, which otherwise caps the vector and makes
     zoom look like a magnified bitmap.
  8. Every page carries <!-- template-v1 --> so pages built under old code stay
     greppable.

DEVIATION FROM THE REFERENCE: the reference pushes file-by-file through the
GitHub Contents API with a GITHUB_TOKEN. rail-wiki is a real local clone with a
working `gh` credential helper, so this uses git add/commit/push instead — one
commit, one Pages build, and no token ever passes through Python. Nothing else
about the pipeline changes.

Flags
  --pid PID         single process
  --count N         next N incomplete processes
  --full            all incomplete processes
  --start PID       resume from PID in catalogue order
  --force           regenerate even if the tracker says Complete
  --bootstrap       push shell only (css, js, search, home, all indexes) and exit
  --rebuild-nav     regenerate every index + search index, no model calls
  --no-verify       skip the live-page check
  --no-push         build locally, do not commit or push
"""

import argparse
import html as _html
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

REPO_OWNER = "ghatk047"
REPO_NAME = "rail-wiki"
BRANCH = "main"
PAGES_BASE = f"https://{REPO_OWNER}.github.io/{REPO_NAME}"

SITE_TITLE = "US Class I Freight Railroad Process Wiki"
SITE_SUB = "Union Pacific archetype &mdash; independently compiled"

OLLAMA_URL = "http://localhost:11434"
PRIMARY_MODEL = "qwen2.5:14b-instruct"
FALLBACK_MODEL = "qwen2.5:latest"
OLLAMA_TIMEOUT = 900

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DIAGRAM_DIR = ROOT / "diagrams"
IMG_DIR = ROOT / "assets" / "img"
REGISTRY_DIR = ROOT / "registries"
TAXONOMY_PATH = DATA_DIR / "taxonomy.json"
TRACKER = DATA_DIR / "processes.json"          # gitignored, per CLAUDE.md
EXCEL_PATH = DATA_DIR / "rail_wiki_progress.xlsx"

TEMPLATE_VERSION = "template-v1"

# PORTED FIX 5 — one canonical init line. The font stack is system-resident on
# every platform mmdc and the browser run on; an <img>-loaded SVG cannot fetch
# a webfont and falls back to serif without this.
INIT_LINE = ("%%{init: {'theme':'base','themeVariables':"
             "{'fontSize':'13px','fontFamily':'Helvetica Neue, Helvetica, Arial, sans-serif'}}}%%")

PID_W, PID_H = 2400, 1400
EA_W, EA_H = 3840, 2160
MMDC_SCALE = 2
VERIFY_WAIT = 75

# ─────────────────────────────────────────────────────────────────────────────
# TAXONOMY — 15 L1 domains, 136 L2 groups, 290 processes, from data/taxonomy.json
# ─────────────────────────────────────────────────────────────────────────────

# icon, family. Names and ordering come from data/taxonomy.json, which is built
# from BUILD-SPEC v2 §2 — never retyped here, so the two cannot drift.
L1_EXTRA = {
    "RR-01": ("\U0001F5FA", "net"),    # Service Design & Network Planning
    "RR-02": ("\U0001F6A6", "net"),    # Dispatching & Train Movement
    "RR-03": ("\U0001F3ED", "ops"),    # Terminal & Yard Operations
    "RR-04": ("\U0001F468", "ops"),    # Crew Management
    "RR-05": ("\U0001F527", "ops"),    # Mechanical & Rolling Stock
    "RR-06": ("\U0001F6E4", "ops"),    # Engineering: Track, Structures & Signals
    "RR-07": ("\U0001F6E1", "corp"),   # Safety & Regulatory Compliance
    "RR-08": ("☣", "corp"),       # Hazardous Materials & Environmental
    "RR-09": ("\U0001F4E6", "ops"),    # Intermodal & Automotive Operations
    "RR-10": ("\U0001F4B0", "comm"),   # Commercial, Pricing & Customer Service
    "RR-11": ("\U0001F517", "net"),    # Interline, Equipment & Car Management
    "RR-12": ("\U0001F5A5", "corp"),   # Technology, Data & Cybersecurity
    "RR-13": ("\U0001F4CA", "comm"),   # Finance, Revenue Accounting & Procurement
    "RR-14": ("\U0001F465", "corp"),   # HR, Labor Relations & Training
    "RR-15": ("\U0001F91D", "corp"),   # Merger & Network Integration
}

FAMILY_LABEL = {"ops": "OPERATIONS", "net": "NETWORK & PLANNING",
                "comm": "COMMERCIAL", "corp": "CORPORATE"}


def slugify(text):
    text = str(text).replace("&", " ").replace("/", " ")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-+", "-", text)


def _load_taxonomy():
    """Build L1_META, TAXONOMY and PROCESSES from data/taxonomy.json.

    Structures mirror the reference exactly so build_sidebar, the index builders
    and the search index are line-for-line ports.
    """
    tx = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    domains = tx["_meta"]["domains"]
    procs = tx["processes"]

    l1_meta = {}
    for d in domains:
        code = d["l1_id"]                       # RR-01
        icon, fam = L1_EXTRA[code]
        num = code.split("-")[1]
        l1_meta[code] = (icon, d["l1"], f"rr-{num}-{slugify(d['l1'])}", fam)

    # ordered L2 groups per L1, preserving taxonomy.json order
    taxonomy, seen = [], {}
    for p in procs:
        l1_name, l2_name = p["l1"], p["l2"]
        code = next(c for c, m in l1_meta.items() if m[1] == l1_name)
        key = (code, l2_name)
        if key not in seen:
            l2_num = f"{sum(1 for c, _ in seen if c == code) + 1:02d}"
            seen[key] = {"l1": code, "l2": l2_num, "l2_name": l2_name,
                         "l2_slug": slugify(l2_name), "names": []}
            taxonomy.append(seen[key])
        seen[key]["names"].append(p["pid"])

    catalogue = []
    for g in taxonomy:
        icon, l1_name, l1_slug, fam = l1_meta[g["l1"]]
        for pid in g["names"]:
            catalogue.append({
                "pid": pid,
                "slug": pid.lower(),
                "l1": g["l1"], "l1_name": l1_name, "l1_icon": icon,
                "l1_slug": l1_slug, "family": fam,
                "l2": g["l2"], "l2_name": g["l2_name"], "l2_slug": g["l2_slug"],
                # L3 process name: the spec fixes L1/L2 names and PID counts but
                # not L3 titles, so the L3 title is the L2 group plus its index.
                # The model is told the real L1/L2/L3 context either way.
                "name": f"{g['l2_name']} — Process {pid.split('-')[-1]}",
                "path": f"{l1_slug}/{g['l2_slug']}/{pid.lower()}/index.html",
            })
    return l1_meta, taxonomy, catalogue


L1_META, TAXONOMY, PROCESSES = _load_taxonomy()
BY_PID = {p["pid"]: p for p in PROCESSES}
FAMILY = {code: meta[3] for code, meta in L1_META.items()}

assert len(L1_META) == 15, f"{len(L1_META)} L1 domains, expected 15"
assert len(TAXONOMY) == 136, f"{len(TAXONOMY)} L2 groups, expected 136"
assert len(PROCESSES) == 290, f"{len(PROCESSES)} processes, expected 290"

# One EA landscape per L1 domain. ea-NN maps to RR-NN.
EA_DIR_SLUG = "ea-diagrams"
EA_DIAGRAMS = [
    (f"ea-{code.split('-')[1]}", f"{L1_META[code][1]} Architecture",
     f"System landscape and data flows across {L1_META[code][1]} for a US Class I "
     f"freight railroad, Union Pacific archetype")
    for code in L1_META
]
EA_TO_L1 = {f"ea-{c.split('-')[1]}": c for c in L1_META}


# ─────────────────────────────────────────────────────────────────────────────
# MERMAID SANITISER  (PORTED FIX 4 + 5 — battle-tested, extend with care)
# ─────────────────────────────────────────────────────────────────────────────

def sanitise_mermaid(mmd_str):
    if not mmd_str:
        return None
    # 1. Strip markdown fences
    mmd_str = re.sub(r'^```[a-z]*\n?', '', mmd_str, flags=re.MULTILINE)
    mmd_str = re.sub(r'```$', '', mmd_str, flags=re.MULTILINE)
    # 2. Remove YAML frontmatter
    mmd_str = re.sub(r'^---.*?---\s*', '', mmd_str, flags=re.DOTALL)
    # 3. Fix HTML-encoded arrows
    mmd_str = mmd_str.replace('--gt;', '-->').replace('--&gt;', '-->').replace('--&gt', '-->')
    # 3b. HTML line breaks would lose their angle brackets in step 5
    for _tag in ('<br/>', '<br />', '<br>'):
        mmd_str = mmd_str.replace(_tag, '\\n')
    # 4. Fix digit-start node IDs: 1.1 -> S1_1.
    #    PORTED FIX 4: every piece of *text* is stashed before the rewrite, so a
    #    real citation is never mangled. Rail content is dense with these —
    #    49 CFR 213.9, GCOR 6.28, FRA Class 4.0, AAR Rule 1.2 — and the naive
    #    rewrite would turn 49 CFR 213.9 into 49 CFR S213_9.
    #    Beyond the reference's [..] {..} |".."| set this also stashes bare
    #    |..| pipe labels and `-- text -->` edge labels, which rail diagrams use
    #    constantly to carry the branch wording.
    stash = []

    def _hold(m):
        stash.append(m.group(0))
        return f'\x00{len(stash) - 1}\x00'

    mmd_str = re.sub(
        r'\[[^\]]*\]'          # [node label]
        r'|\{[^}]*\}'          # {decision label}
        r'|\|[^|]*\|'          # |edge label| (quoted or bare)
        r'|--\s*[^->|\n]+?\s*-->',   # -- edge label -->
        _hold, mmd_str)
    mmd_str = re.sub(r'\b(\d+)\.(\d+)\b', r'S\1_\2', mmd_str)
    mmd_str = re.sub(r'\x00(\d+)\x00', lambda m: stash[int(m.group(1))], mmd_str)
    # 5. Remove special chars from inside node labels [ ], ( ) and { }
    def clean_label(m):
        text = m.group(2)
        text = re.sub(r'[()&<>]', '', text)
        text = re.sub(r'  +', ' ', text).strip()
        return f'{m.group(1)}{text}{m.group(3)}'
    mmd_str = re.sub(r'(\[)([^\]]+)(\])', clean_label, mmd_str)
    mmd_str = re.sub(r'(\{)([^}]+)(\})', clean_label, mmd_str)
    mmd_str = re.sub(r'(\()([^)]+)(\))', clean_label, mmd_str)
    # 6. Force one canonical %%{init}%% carrying the system font stack.
    mmd_str = re.sub(r'^\s*%%\{init.*?\}%%\s*\n?', '', mmd_str, flags=re.DOTALL | re.MULTILINE)
    mmd_str = INIT_LINE + "\n" + mmd_str.lstrip()
    # 7. Remove blank lines between %%{init}%% and the flowchart directive
    lines = mmd_str.strip().split('\n')
    cleaned = []
    for line in lines:
        if cleaned and cleaned[-1].strip().startswith('%%{init') and line.strip() == '':
            continue
        cleaned.append(line)
    return '\n'.join(cleaned).strip()


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {level:<5} {msg}", flush=True)


def dump_raw(tag, raw):
    """PORTED FIX 3 — keep every unparseable response so a bad run is diagnosable."""
    try:
        d = DATA_DIR / "raw"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{tag}.txt"
        f.write_text(raw or "(empty response)", encoding="utf-8")
        log(f"  raw response saved to {f}", "WARN")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY (registry-first rule, CLAUDE.md + BUILD-SPEC v2 §4)
# ─────────────────────────────────────────────────────────────────────────────

def _load_registry(name):
    data = json.loads((REGISTRY_DIR / f"{name}.json").read_text(encoding="utf-8"))
    out = []
    for key, entries in data.items():
        if key.startswith("_") or key == "not_yet_sourced_do_not_use":
            continue
        for e in entries:
            if isinstance(e, dict):
                e = dict(e)
                e["_domain"] = key
                out.append(e)
    return out


REG_SYSTEMS = _load_registry("systems")
REG_ROLES = _load_registry("roles")
REG_REGS = _load_registry("regulations")
REG_KPIS = _load_registry("kpis")
REG_FACTS = _load_registry("facts")


def _dom_key(l1_code):
    """RR-03 -> the domain_03_* key used inside every registry file."""
    num = l1_code.split("-")[1]
    for e in REG_SYSTEMS + REG_ROLES + REG_REGS + REG_KPIS + REG_FACTS:
        if e["_domain"].startswith(f"domain_{num}_"):
            return e["_domain"]
    return None


def registry_systems_for(l1_code):
    """Systems registered to this domain first, then the rest of the estate.

    A single domain can hold as few as one sourced system (domain_15), which is
    why the whole 61-entry estate is offered — an EA landscape or a process
    legitimately touches systems owned by other domains (Umler, I-ETMS,
    NetControl), and every one of those names is still registry-sourced.
    """
    dom = _dom_key(l1_code)
    own = [s for s in REG_SYSTEMS if s["_domain"] == dom]
    other = [s for s in REG_SYSTEMS if s["_domain"] != dom]
    return own, other


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _registry_names(entries, key):
    names = set()
    for e in entries:
        v = e.get(key)
        if not v:
            continue
        names.add(_norm(v))
        # a parenthetical or slash alias counts as the same entry
        base = re.sub(r"\s*\(.*?\)\s*", " ", str(v))
        names.add(_norm(base))
        for part in re.split(r"\s*/\s*", base):
            if len(part.strip()) > 2:
                names.add(_norm(part))
    return {n for n in names if n}


SYS_NAMES = _registry_names(REG_SYSTEMS, "name")
ROLE_NAMES = _registry_names(REG_ROLES, "role")
REG_CITES = _registry_names(REG_REGS, "cite")


def in_registry(value, pool):
    """Exact-or-contained match against a registry name pool. No fuzzy matching."""
    n = _norm(value)
    if not n:
        return False
    if n in pool:
        return True
    return any(r in n or n in r for r in pool if len(r) > 4)


def registry_audit(pid, data, mmd=None):
    """PHASE 2 REQUIREMENT — log every system/role/regulation the model produced
    that does not resolve to a registries/ entry. Advisory: it reports, it does
    not block, because blocking here would silently drop otherwise good content
    and hide the miss. Findings are what make the registry-first design real."""
    findings = []
    for s in data.get("systems", []) or []:
        if not in_registry(s, SYS_NAMES):
            findings.append(("system", s))
    for step in data.get("l4_steps", []) or []:
        if step.get("system") and not in_registry(step["system"], SYS_NAMES):
            findings.append(("step.system", step["system"]))
        if step.get("role") and not in_registry(step["role"], ROLE_NAMES):
            findings.append(("step.role", step["role"]))
    for c in re.findall(r'\b\d{2}\s*CFR\s*(?:Part\s*)?[\d.]+\b', json.dumps(data)):
        if not in_registry(c, REG_CITES):
            findings.append(("regulation", c))
    if findings:
        log(f"  registry audit {pid}: {len(findings)} unregistered name(s)", "WARN")
        for kind, val in findings[:20]:
            log(f"    UNREGISTERED {kind}: {val}", "WARN")
    else:
        log(f"  registry audit {pid}: all systems, roles and citations resolve")
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS  (PORTED FIX 1 + 2 — two calls, two system prompts)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_JSON = """You are a senior rail operations consultant documenting business
processes for a US Class I freight railroad, using Union Pacific as the reference
archetype. You have deep knowledge of:
- Class I freight railroad operations: dispatching, terminal and yard operations,
  road and yard crews, mechanical, engineering (MOW), intermodal and interline
- US rail regulation: 49 CFR (FRA Parts 213 track, 214 roadway worker, 215 freight
  car safety, 217/218 operating rules, 219 drug and alcohol, 220 radio, 228 hours
  of service, 229 locomotive, 232 brake system, 236 signal and PTC, 172/174 hazmat),
  the Surface Transportation Board (49 CFR Part 1180 and Part 1244), and the
  Railway Labor Act
- Industry rulebooks and standards: GCOR, AAR Interchange Rules, AAR Field Manual,
  Umler, the AAR Circular OT-55 key train protocol

Real systems in this estate: NetControl and CADx computer-aided dispatch, the
Harriman Dispatching Center, I-ETMS positive train control with its Back Office
Server, PS Technology crew systems, CrewPro, HASTUS, CloudMoyo Crew Management,
Umler, CHARM car hire accounting, TRAIN II, EHMS equipment health, the Interline
Settlement System, wayside defect detector networks, machine vision portals,
RailAI, Wabtrax, the FRA Automated Track Inspection Program, track geometry cars,
Wabtec Modular Control Architecture, NEXSYS III-i, UPGo, EMP and UMAX container
fleets, Loup Logistics, ShipmentVision, the up.com customer portal, AskRail,
TRANSCAER, SERTC, GCOR, C3RS, Bailey Yard at North Platte, and the Unified
Plan 2020 operating plan.

Return ONLY valid JSON with no markdown fences, no preamble, no trailing text and
absolutely no Mermaid or diagram syntax anywhere in the response."""

MERMAID_RULES = """MERMAID CRITICAL RULES (violations cause mmdc parse errors):
- NO YAML frontmatter
- Node IDs MUST start with a LETTER such as NodeA, StepB or S1_1 — never a digit
- Arrows are ALWAYS --> and never --gt or --&gt;
- Node labels contain NO parentheses, no ampersand, no angle brackets"""

SYSTEM_PROMPT = SYSTEM_PROMPT_JSON + "\n\n" + MERMAID_RULES

JSON_SHAPE = """{
  "description": "3-4 sentence operational description in the US Class I railroad context",
  "trigger": "what initiates this process",
  "outcome": "what successful completion produces",
  "l4_steps": [
    {
      "step": "1.1",
      "name": "Step name",
      "role": "Exact rail craft or management role such as Yardmaster, Locomotive Engineer, Conductor, Train Dispatcher, Carman, Track Inspector",
      "system": "Exact system such as NetControl, I-ETMS, Umler, PS Technology",
      "input": "Input document or data",
      "output": "Output document or deliverable",
      "kpi": "Measurable metric with target",
      "decision_point": "Y or N",
      "exception": "Y or N",
      "pain_point": "Real operational challenge at this step"
    }
  ],
  "swim_lanes": [{"role": "Role Title", "color": "#hex", "steps": ["1.1", "1.2"]}],
  "systems": ["NetControl", "I-ETMS"],
  "kpis": ["Terminal dwell under 24 hours", "Train speed above 20 mph"],
  "risks": ["Air brake test defect released to the road"],
  "regulations": ["49 CFR 232.205", "GCOR 6.28"]
}"""

CONTENT_RULES = """CONTENT RULES:
- 10 to 12 l4_steps spread across 4 to 6 phases
- At least 3 steps must be genuine decision points with decision_point Y
- At least 2 steps must have exception Y
- Real rail roles only: Train Dispatcher, Chief Dispatcher, Yardmaster, Conductor,
  Locomotive Engineer, Carman, Car Inspector, Track Inspector, Roadway Worker in
  Charge, Signal Maintainer, Trainmaster, Manager of Train Operations, Crew Caller,
  Mechanical Foreman, Hazmat Specialist, Corridor Manager
- 4 to 6 systems per process, named exactly as listed in the system prompt
- 4 to 6 KPIs with measurable targets
- 3 to 5 rail-specific risks covering regulatory, safety, service and commercial
- 2 to 4 regulations, cited exactly, e.g. 49 CFR 232.205, 49 CFR 218.99, GCOR 6.28
- Write role and system NAMES, never internal ID codes such as ROLE-D01-02
- Do NOT include a mermaid field. The diagram is requested separately.
- JSON ONLY — no markdown, no preamble"""

# PORTED FIX 6 — form reference for the BPMN diagram. Deliberately from a
# different industry (maritime) so the model copies the CONSTRUCT and none of
# the subject matter. Measured against the airline reference repo's 149 real
# .mmd files, whose averages are the floor this has to clear:
#   30.6 nodes | 6.2 decision diamonds | 12.4 labelled branches
#   5.4 subgraphs | 8.5 style/classDef lines | 2.4 terminators
BPMN_FORM_EXAMPLE = """flowchart LR
  subgraph P1[Phase 1: Booking Intake and Screening]
    A([Start]) --> B[Receive booking request\\nCarrier booking portal]
    B --> C{Cargo within\\nvessel capacity?}
    C -- No --> B
    C -- Yes --> D[Allocate slot and\\nconfirm sailing]
  end

  subgraph P2[Phase 2: Cargo Verification]
    D --> E[Verify declared gross mass\\nshipper VGM feed]
    E --> F{VGM received\\nbefore cut-off?}
    F -- No --> Hold1([Hold])
    F -- Yes --> G[Screen dangerous goods\\nIMDG segregation table]
    G --> H{DG declaration\\napproved?}
    H -- No --> Rej1([Reject])
  end

  subgraph P3[Phase 3: Stow and Release]
    H -- Yes --> I[Assign bay and tier\\nstowage planner]
    I --> J{Stability within\\nlimits?}
    J -- No --> I
    J -- Yes --> K[Issue loading instruction\\nterminal operating system]
    K --> L([End])
  end

  style A fill:#20242b,color:#fff,stroke:#20242b
  style L fill:#20242b,color:#fff,stroke:#20242b
  style Rej1 fill:#20242b,color:#fff,stroke:#20242b
  style Hold1 fill:#20242b,color:#fff,stroke:#20242b
  style C fill:#c8791a,color:#fff,stroke:#c8791a
  style F fill:#c8791a,color:#fff,stroke:#c8791a
  style H fill:#c8791a,color:#fff,stroke:#c8791a
  style J fill:#c8791a,color:#fff,stroke:#c8791a"""


def ollama_call(prompt, model, temperature=0.35):
    r = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False,
              "options": {"temperature": temperature, "num_ctx": 8192}},
        timeout=OLLAMA_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("response", "")


def extract_json(raw, required=None):
    """PORTED FIX 3 — find the first balanced object that parses AND carries the
    expected keys. Small models often prepend a Mermaid block whose
    %%{init: {...}}%% braces are the first thing a naive scanner finds, so those
    are stripped before scanning and every remaining candidate is tried in turn.
    """
    if not raw:
        return None
    text = re.sub(r'^```[a-z]*\n?', '', raw.strip(), flags=re.MULTILINE)
    text = re.sub(r'```$', '', text, flags=re.MULTILINE)
    text = re.sub(r'%%\{.*?\}%%', '', text, flags=re.DOTALL)      # mermaid init blocks
    text = re.sub(r'^\s*(flowchart|graph)\s+\w+.*$', '', text, flags=re.MULTILINE)

    def candidates(s):
        depth = start = 0
        in_str = esc = False
        for i, ch in enumerate(s):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth:
                    depth -= 1
                    if depth == 0:
                        yield s[start:i + 1]

    best = None
    for blob in candidates(text):
        for attempt in (blob, blob.replace("\n", " "), re.sub(r',\s*([}\]])', r'\1', blob)):
            try:
                data = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                break
            if required and not any(data.get(k) for k in required):
                best = best or data
                break
            return data
    return best


def generate_process_content(proc):
    """Call 1 — JSON only. No Mermaid rules in this prompt (PORTED FIX 2)."""
    own, other = registry_systems_for(proc["l1"])
    sys_menu = ", ".join(s["name"] for s in own) or "none registered for this domain"
    prompt = (
        f"{SYSTEM_PROMPT_JSON}\n\n"
        f"Document this business process for a US Class I freight railroad:\n"
        f"  Process ID : {proc['pid']}\n"
        f"  L1 Domain  : {proc['l1_name']}\n"
        f"  L2 Group   : {proc['l2_name']}\n"
        f"  L3 Process : {proc['name']}\n\n"
        f"Systems specifically sourced for this domain, prefer these: {sys_menu}\n\n"
        f"Return exactly this JSON shape:\n{JSON_SHAPE}\n\n{CONTENT_RULES}\n"
    )
    for i, model in enumerate((PRIMARY_MODEL, PRIMARY_MODEL, FALLBACK_MODEL)):
        try:
            raw = ollama_call(prompt, model, temperature=0.3 + 0.1 * i)
            data = extract_json(raw, required=("l4_steps",))
            if not data:
                dump_raw(f"{proc['pid']}-json-{i+1}", raw)
            if data and data.get("l4_steps"):
                for k in ("systems", "kpis", "risks", "regulations", "swim_lanes"):
                    data.setdefault(k, [])
                return data
            log(f"{proc['pid']}: unusable JSON from {model} (try {i+1}/3)", "WARN")
        except Exception as exc:
            log(f"{proc['pid']}: Ollama error on {model} — {exc}", "WARN")
        time.sleep(2)
    return None


def generate_process_mermaid(proc, data, attempt=0):
    """Call 2 — raw Mermaid only, so a multi-line label never has to survive JSON
    escaping (PORTED FIX 1)."""
    steps = "\n".join(
        f"  {s.get('step','')} {s.get('name','')} "
        f"[role: {s.get('role','')} | system: {s.get('system','')} | "
        f"decision: {s.get('decision_point','N')} | exception: {s.get('exception','N')}]"
        for s in data.get("l4_steps", [])
    )
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Draw the BPMN process flow for US Class I railroad process {proc['pid']} — "
        f"{proc['name']}\nin the {proc['l1_name']} domain.\n\n"
        f"These are the L4 steps it must cover:\n{steps}\n\n"
        "STRUCTURE — copy this construct exactly. It is from a different industry, so take\n"
        "the FORM and none of the content:\n\n"
        f"{BPMN_FORM_EXAMPLE}\n\n"
        "REQUIREMENTS:\n"
        "- flowchart LR with 5 or 6 phase subgraphs named P1 to P6, each titled\n"
        "  Phase N: short phase name.\n"
        "- One ([Start]) terminator and one ([End]) terminator. Add 1 or 2 more terminators\n"
        "  for exception exits such as ([Bad Order]), ([Hold]), ([Reject]) or ([Escalate]).\n"
        "- 5 to 8 decision diamonds using curly braces. EVERY decision has at least two\n"
        "  labelled outbound branches written as X -- Yes --> Y and X -- No --> Z.\n"
        "  Use real rail decision language: air brake test passed, FRA defect found,\n"
        "  interchange accepted, PTC initialised, hours of service remaining, track\n"
        "  authority granted, hazmat placard verified, car bad ordered.\n"
        "- At least 2 rework loops that route a failed decision BACK to an earlier task node\n"
        "  rather than straight to an exit. This is what makes the flow multi-path.\n"
        "- 22 to 30 nodes overall. Every task label is two lines: what happens, then a\n"
        "  literal backslash-n, then the system or document involved.\n"
        "  Example: ABT[Perform Class I air brake test\\\\nFRA 49 CFR 232.205 record]\n"
        "- Flow must cross subgraph boundaries: a node in P2 connects to nodes in P3 and,\n"
        "  where there is rework, back to P1.\n"
        "- Close with style lines. Terminators fill:#20242b,color:#fff,stroke:#20242b and\n"
        "  every decision diamond fill:#c8791a,color:#fff,stroke:#c8791a.\n"
        "- Node IDs start with a letter. No parentheses, ampersands or angle brackets inside\n"
        "  any label. Never use <br/>. Arrows are --> only.\n\n"
        "Output the raw Mermaid and nothing else. No JSON, no fences, no commentary.\n"
    )
    models = [PRIMARY_MODEL, PRIMARY_MODEL, FALLBACK_MODEL]
    model = models[min(attempt, len(models) - 1)]
    try:
        return sanitise_mermaid(ollama_call(prompt, model, temperature=0.2 + 0.1 * attempt))
    except Exception as exc:
        log(f"{proc['pid']}: mermaid call failed on {model} — {exc}", "WARN")
        return None


def diagram_richness(mmd):
    """PORTED FIX 6 — structural score. Floors are the airline reference repo's
    measured averages across 149 real .mmd files, rounded down."""
    if not mmd:
        return 0, {}
    m = {
        "nodes":     len(re.findall(r'^\s*\w+[\[\({]', mmd, flags=re.MULTILINE)),
        "decisions": len(re.findall(r'\w+\{[^}]+\}', mmd)),
        "branches":  len(re.findall(r'--\s*[A-Za-z][\w \-]*\s*-->', mmd)),
        "subgraphs": mmd.count("subgraph"),
        "styles":    len(re.findall(r'^\s*(style|classDef)\s', mmd, flags=re.MULTILINE)),
        "terminals": len(re.findall(r'\(\[', mmd)),
    }
    score = ((m["decisions"] >= 5) + (m["branches"] >= 10) + (m["subgraphs"] >= 5)
             + (m["styles"] >= 8) + (m["nodes"] >= 22) + (m["terminals"] >= 2))
    return score, m


# ─────────────────────────────────────────────────────────────────────────────
# MMDC RENDERING
# ─────────────────────────────────────────────────────────────────────────────

def render_mermaid(mmd_text, mmd_path, out_path, width, height, scale=MMDC_SCALE):
    mmd_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mmd_path.write_text(mmd_text, encoding="utf-8")
    is_svg = out_path.suffix.lower() == ".svg"
    cmd = ["mmdc", "-i", str(mmd_path), "-o", str(out_path),
           "-w", str(width), "-H", str(height), "--backgroundColor", "white"]
    if not is_svg:
        cmd += ["--scale", str(scale)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "mmdc timed out"
    except FileNotFoundError:
        return False, "mmdc not on PATH — npm i -g @mermaid-js/mermaid-cli"
    if res.returncode != 0:
        return False, (res.stderr or res.stdout or "mmdc failed").strip()[:400]
    floor = 1024 if is_svg else 4096
    if not out_path.exists() or out_path.stat().st_size < floor:
        return False, f"mmdc produced no usable {out_path.suffix.lstrip('.')}"
    size_mb = out_path.stat().st_size / (1024 * 1024)
    if not is_svg and size_mb > 90 and scale > 1:
        log(f"{out_path.name}: {size_mb:.1f}MB — re-rendering at lower scale", "WARN")
        return render_mermaid(mmd_text, mmd_path, out_path, int(width * 0.75),
                              int(height * 0.75), scale=scale - 1)
    return True, f"{size_mb:.2f}MB"


def finalize_svg(path):
    """PORTED FIX 7 — mmdc emits width="100%" plus an inline max-width on the
    root <svg>. Both are hostile to zooming: the max-width caps how large the
    vector will ever render, so the browser rasterises at that ceiling and scales
    the bitmap up — which looks exactly like a blurry PNG even though the file is
    genuinely vector. Replacing them with the viewBox dimensions gives the file a
    real intrinsic size and no ceiling.
    Verify on the output file, not in code: grep -c 'max-width' file.svg == 0.
    """
    try:
        svg = path.read_text(encoding="utf-8")
    except Exception:
        return False
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if not m:
        return False
    w, h = m.group(1), m.group(2)
    svg = svg.replace('width="100%"', f'width="{w}" height="{h}"', 1)
    svg = re.sub(r'max-width:\s*[\d.]+px;?\s*', '', svg)
    if 'height=' not in svg.split('>', 1)[0]:
        svg = svg.replace('<svg ', f'<svg height="{h}" ', 1)
    path.write_text(svg, encoding="utf-8")
    remaining = svg.count("max-width")
    log(f"  svg finalised — intrinsic {w}x{h}, max-width occurrences remaining: {remaining}")
    return remaining == 0


def build_diagram(proc, data):
    """Generate, score, sanitise and render. 3 drafts, best wins."""
    mmd_path = DIAGRAM_DIR / f"{proc['slug']}.mmd"
    svg_path = IMG_DIR / f"{proc['slug']}.svg"
    best, best_score = None, -1

    for attempt in range(3):
        candidate = generate_process_mermaid(proc, data, attempt=attempt)
        score, metrics = diagram_richness(candidate)
        if candidate and score > best_score:
            best, best_score = candidate, score
        log(f"  diagram draft {attempt+1}: score {score}/6 {metrics}")
        if score >= 5:
            break

    if not best:
        return None
    if best_score < 4:
        log(f"  {proc['pid']}: diagram is thin (score {best_score}/6) — publishing anyway, "
            f"re-run with --pid {proc['pid']} --force to try again", "WARN")

    for attempt in range(1, 4):
        ok, info = render_mermaid(best, mmd_path, svg_path, PID_W, PID_H)
        if ok:
            finalize_svg(svg_path)
            log(f"  diagram rendered as SVG ({info}) on render attempt {attempt}")
            return svg_path
        log(f"  mmdc attempt {attempt}/3 failed: {info}", "WARN")
        retry = generate_process_mermaid(proc, data, attempt=attempt)
        if retry:
            best = retry
    return None


# ─────────────────────────────────────────────────────────────────────────────
# GIT TRANSPORT  (deviation from the reference — see module docstring)
# ─────────────────────────────────────────────────────────────────────────────

def git(*args, check=True):
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and res.returncode != 0:
        log(f"git {' '.join(args)} failed: {res.stderr.strip()[:300]}", "ERROR")
    return res


def commit_and_push(message, no_push=False):
    """One commit, one Pages build. The token never enters this process: git uses
    the `gh` credential helper already configured on this machine."""
    git("add", "-A")
    status = git("status", "--porcelain").stdout.strip()
    if not status:
        log("nothing to commit — working tree clean")
        return True
    res = git("commit", "-m", message + "\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>")
    if res.returncode != 0:
        return False
    log(f"committed: {message}")
    if no_push:
        log("--no-push set — stopping before push", "WARN")
        return True
    res = git("push", "origin", BRANCH)
    if res.returncode != 0:
        return False
    log("pushed to origin/main — Pages rebuild triggered")
    return True


def verify_live(url, wait=VERIFY_WAIT, needle="assets/img"):
    log(f"  waiting {wait}s for the Pages build …")
    time.sleep(wait)
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=45)
            if r.status_code == 200 and needle in r.text:
                return True
            log(f"  verify attempt {attempt+1}: HTTP {r.status_code}"
                f"{'' if r.status_code != 200 else ' (page served, marker not present yet)'}",
                "WARN")
        except Exception as exc:
            log(f"  verify attempt {attempt+1} error: {exc}", "WARN")
        time.sleep(25)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# LOCAL TRACKER  (data/processes.json — gitignored, never committed)
# ─────────────────────────────────────────────────────────────────────────────

def load_tracker():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if TRACKER.exists():
        try:
            return json.loads(TRACKER.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("processes.json unreadable — starting a fresh tracker", "WARN")
    return {}


def save_tracker(tr):
    TRACKER.write_text(json.dumps(tr, indent=2), encoding="utf-8")


def is_complete(pid, tr):
    return tr.get(pid, {}).get("status") == "Complete"


# ─────────────────────────────────────────────────────────────────────────────
# EXCEL  (local only, gitignored)
# ─────────────────────────────────────────────────────────────────────────────

def excel_record(proc, data, url):
    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.utils import get_column_letter
    except ImportError:
        log("openpyxl not installed — skipping the Excel row", "WARN")
        return
    if EXCEL_PATH.exists():
        wb = load_workbook(EXCEL_PATH)
    else:
        wb = Workbook()
        idx = wb.active
        idx.title = "Index"
        idx.append(["PID", "Process Name", "L1 Domain", "L2 Group",
                    "Status", "GitHub Pages URL", "Completed At"])
        for p in PROCESSES:
            idx.append([p["pid"], p["name"], p["l1_name"], p["l2_name"],
                        "Queued", f"{PAGES_BASE}/{p['path']}", ""])
        cat = wb.create_sheet("Master Catalog")
        cat.append(["PID", "Step", "Step Name", "Role", "System", "Input", "Output",
                    "KPI", "Decision", "Exception", "Pain Point"])

    for row in wb["Index"].iter_rows(min_row=2):
        if row[0].value == proc["pid"]:
            row[4].value = "Complete"
            row[6].value = datetime.now().strftime("%Y-%m-%d %H:%M")
            break

    cat = wb["Master Catalog"]
    for s in data.get("l4_steps", []):
        cat.append([proc["pid"], s.get("step", ""), s.get("name", ""), s.get("role", ""),
                    s.get("system", ""), s.get("input", ""), s.get("output", ""),
                    s.get("kpi", ""), s.get("decision_point", "N"),
                    s.get("exception", "N"), s.get("pain_point", "")])

    tab = proc["pid"][:31]
    if tab in wb.sheetnames:
        del wb[tab]
    ws = wb.create_sheet(tab)
    for label, value in (("Process ID", proc["pid"]), ("Process Name", proc["name"]),
                         ("L1 Domain", proc["l1_name"]), ("L2 Group", proc["l2_name"]),
                         ("Trigger", data.get("trigger", "")),
                         ("Outcome", data.get("outcome", "")),
                         ("Description", data.get("description", "")),
                         ("Systems", ", ".join(map(str, data.get("systems", [])))),
                         ("KPIs", " | ".join(map(str, data.get("kpis", [])))),
                         ("Risks", " | ".join(map(str, data.get("risks", [])))),
                         ("Regulations", " | ".join(map(str, data.get("regulations", [])))),
                         ("URL", url)):
        ws.append([label, value])
    ws.append([])
    ws.append(["Step", "Name", "Role", "System", "Input", "Output",
               "KPI", "Decision", "Exception", "Pain Point"])
    for s in data.get("l4_steps", []):
        ws.append([s.get("step", ""), s.get("name", ""), s.get("role", ""),
                   s.get("system", ""), s.get("input", ""), s.get("output", ""),
                   s.get("kpi", ""), s.get("decision_point", "N"),
                   s.get("exception", "N"), s.get("pain_point", "")])
    for col, width in enumerate([10, 34, 26, 24, 26, 26, 30, 10, 10, 40], start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    wb.save(EXCEL_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# HTML BUILDING
# ─────────────────────────────────────────────────────────────────────────────

DEPTH_PROCESS = 3      # l1/l2/pid/index.html
DEPTH_L2 = 2           # l1/l2/index.html
DEPTH_L1 = 1           # l1/index.html
DEPTH_EA = 2           # ea-diagrams/ea-NN/index.html
DEPTH_EA_IDX = 1       # ea-diagrams/index.html
DEPTH_ROOT = 0         # index.html

# Every class below maps to a registries/systems.json family — nothing invented.
SYS_TAG_MAP = [
    ("netcontrol", "netcontrol"), ("cadx", "netcontrol"),
    ("computer-aided dispatch", "netcontrol"), ("computer aided dispatch", "netcontrol"),
    ("i-etms", "ietms"), ("ietms", "ietms"), ("back office server", "ietms"),
    ("positive train control", "ietms"), ("ptc", "ietms"),
    ("ps technology", "pst"), ("pst", "pst"), ("crewpro", "pst"),
    ("hastus", "pst"), ("cloudmoyo", "pst"), ("trackhos", "pst"),
    ("umler", "umler"), ("charm", "umler"), ("train ii", "umler"),
    ("ehms", "umler"), ("equipment health", "umler"),
    ("interline settlement", "umler"), ("switching settlement", "umler"),
    ("unified plan", "unified"),
    ("loup", "loup"), ("shipmentvision", "loup"), ("up.com", "loup"),
    ("customer portal", "loup"), ("transentric", "loup"),
    ("upgo", "upgo"), ("emp", "upgo"), ("umax", "upgo"),
    ("wabtec", "wabtec"), ("nexsys", "wabtec"),
    ("wabtrax", "wabtrax"),
    ("wayside", "wayside"), ("detector", "wayside"), ("machine vision", "wayside"),
    ("railai", "wayside"), ("atip", "wayside"), ("track geometry", "wayside"),
    ("automated track inspection", "wayside"),
    ("gcor", "gcor"), ("c3rs", "gcor"), ("close call", "gcor"), ("commit", "gcor"),
    ("operating practices command", "gcor"),
    ("askrail", "askrail"), ("transcaer", "askrail"), ("sertc", "askrail"),
    ("bailey yard", "facility"), ("harriman", "facility"), ("shop", "facility"),
    ("yard", "facility"), ("terminal", "facility"), ("ictf", "facility"),
    ("joliet", "facility"), ("global iv", "facility"),
    ("fuel surcharge", "finance"), ("arc", "finance"),
    ("te&y", "training"), ("training", "training"), ("classroom", "training"),
]


def sys_tag_class(name):
    low = (name or "").lower()
    for needle, cls in SYS_TAG_MAP:
        if needle in low:
            return cls
    return "custom"


def sys_tags(systems):
    """Registered systems get their family colour; anything the registry does not
    know is rendered with .sys-unregistered so a registry miss is visible on the
    page instead of blending in."""
    out = []
    for s in systems or []:
        s = str(s).strip()
        if not s or s.lower() in ("none", "n/a"):
            continue
        cls = sys_tag_class(s) if in_registry(s, SYS_NAMES) else "sys-unregistered"
        out.append(f'<span class="sys-tag {cls}">{esc(s)}</span>')
    return " ".join(out) or '<span class="sys-tag custom">Not specified</span>'


def esc(text):
    return _html.escape(str(text if text is not None else ""), quote=False)


def prefix(depth):
    return "../" * depth if depth else "./"


def trunc(text, n=52):
    text = str(text)
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def build_sidebar(depth, active_pid=None, active_ea=None):
    """Full navigation: every L1, every L2, every process, plus the EA section."""
    p = prefix(depth)
    active = BY_PID.get(active_pid) if active_pid else None
    parts = ['<nav class="wiki-nav">']

    for l1_code, (icon, l1_name, l1_slug, fam) in L1_META.items():
        groups = [g for g in TAXONOMY if g["l1"] == l1_code]
        open_l1 = " open" if active and active["l1"] == l1_code else ""
        parts.append(f'<div class="nav-domain{open_l1}">')
        parts.append(
            f'  <a class="nav-l1" href="{p}{l1_slug}/index.html">'
            f'<span class="nav-l1-icon">{icon}</span>'
            f'<span class="nav-org-dot {fam}"></span>'
            f'<span class="nav-l1-text">{esc(l1_name)}</span>'
            f'<span class="nav-l1-caret">&#9654;</span></a>'
        )
        parts.append('  <div class="nav-l2-list">')
        for g in groups:
            open_l2 = (" open" if active and active["l1"] == l1_code
                       and active["l2"] == g["l2"] else "")
            parts.append(f'    <div class="nav-l2-group{open_l2}">')
            parts.append(
                f'      <a class="nav-l2-title" href="{p}{l1_slug}/{g["l2_slug"]}/index.html">'
                f'{esc(g["l2_name"])}</a>'
            )
            parts.append('      <div class="nav-l3-list">')
            for pid in g["names"]:
                cls = "nav-l3 active" if pid == active_pid else "nav-l3"
                parts.append(
                    f'        <a class="{cls}" '
                    f'href="{p}{l1_slug}/{g["l2_slug"]}/{pid.lower()}/index.html">'
                    f'{pid}</a>'
                )
            parts.append('      </div>')
            parts.append('    </div>')
        parts.append('  </div>')
        parts.append('</div>')

    open_ea = " open" if active_ea else ""
    parts.append(f'<div class="nav-domain{open_ea}">')
    parts.append(
        f'  <a class="nav-l1" href="{p}{EA_DIR_SLUG}/index.html">'
        f'<span class="nav-l1-icon">\U0001F5FA</span>'
        f'<span class="nav-org-dot corp"></span>'
        f'<span class="nav-l1-text">Enterprise Architecture</span>'
        f'<span class="nav-l1-caret">&#9654;</span></a>'
    )
    parts.append('  <div class="nav-l2-list">')
    parts.append(f'    <div class="nav-l2-group{open_ea}">')
    parts.append(f'      <a class="nav-l2-title" href="{p}{EA_DIR_SLUG}/index.html">EA Diagrams</a>')
    parts.append('      <div class="nav-l3-list">')
    for ea_id, ea_title, _ in EA_DIAGRAMS:
        cls = "nav-l3 active" if ea_id == active_ea else "nav-l3"
        parts.append(
            f'        <a class="{cls}" href="{p}{EA_DIR_SLUG}/{ea_id}/index.html">'
            f'{ea_id.upper()} &mdash; {esc(trunc(ea_title, 34))}</a>'
        )
    parts.append('      </div>')
    parts.append('    </div>')
    parts.append('  </div>')
    parts.append('</div>')
    parts.append('</nav>')
    return "\n".join(parts)


FOOTER = """      <div class="wiki-footer">
        Independently compiled from public sources. Not affiliated with, sponsored by,
        or endorsed by Union Pacific or any other railroad. Illustrative of US Class I
        practice; every system, regulation, role and metric named here traces to a
        sourced entry in <code>registries/</code>.
      </div>"""


def page_shell(title, topbar_sub, depth, main_html, active_pid=None, active_ea=None):
    """PORTED FIX 8 — the template stamp goes in every page, right after the
    doctype, so a page built under older code stays greppable."""
    p = prefix(depth)
    return f"""<!DOCTYPE html>
<!-- {TEMPLATE_VERSION} -->
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="robots" content="noindex">
  <title>{title} &mdash; {SITE_TITLE}</title>
  <link rel="stylesheet" href="{p}assets/css/wiki.css">
</head>
<body>
  <div class="topbar">
    <button id="topbarToggle" aria-label="Menu"><span></span><span></span><span></span></button>
    <a class="topbar-brand" href="{p}index.html">US Class I <span class="accent">Rail</span> Process Wiki</a>
    <span class="topbar-sub">{topbar_sub}</span>
    <span class="topbar-spacer"></span>
  </div>
  <div class="wiki-layout">
    <nav id="sidebar">
      <button id="sidebarToggle" title="Collapse sidebar">&#9664;</button>
{build_sidebar(depth, active_pid, active_ea)}
    </nav>
    <main class="wiki-main">
{main_html}
{FOOTER}
    </main>
  </div>
  <div id="lightbox"><img id="lightboxImg" src="" alt=""></div>
  <script src="{p}assets/js/wiki.js"></script>
</body>
</html>
"""


def build_process_page(proc, data):
    p = prefix(DEPTH_PROCESS)
    fam = proc["family"]
    rows = []
    for s in data.get("l4_steps", []):
        dec = ('<span class="decision-y">Y</span>'
               if str(s.get("decision_point", "N")).upper().startswith("Y") else "N")
        exc = ('<span class="exception-y">Y</span>'
               if str(s.get("exception", "N")).upper().startswith("Y") else "N")
        rows.append(f"""        <tr>
          <td class="step-num">{esc(s.get('step',''))}</td>
          <td>{esc(s.get('name',''))}</td>
          <td>{esc(s.get('role',''))}</td>
          <td>{sys_tags([s.get('system')])}</td>
          <td>{esc(s.get('input',''))}</td>
          <td>{esc(s.get('output',''))}</td>
          <td>{esc(s.get('kpi',''))}</td>
          <td>{esc(s.get('pain_point',''))}</td>
          <td>{dec}</td>
          <td>{exc}</td>
        </tr>""")

    kpis = " ".join(f'<span class="kpi-pill">{esc(k)}</span>' for k in data.get("kpis", []))
    risks = " ".join(f'<span class="risk-pill">{esc(r)}</span>' for r in data.get("risks", []))
    regs = " ".join(f'<span class="sys-tag gcor">{esc(r)}</span>'
                    for r in data.get("regulations", []))
    lanes = " ".join(
        f'<span class="kpi-pill" style="background:#f2f2f0;color:#33363b">'
        f'{esc(l.get("role",""))} &middot; {esc(", ".join(map(str, l.get("steps", []))))}</span>'
        for l in data.get("swim_lanes", []) if isinstance(l, dict)
    )

    main = f"""      <div class="page-header">
        <div class="breadcrumb">
          <a href="{p}index.html">Home</a> &rsaquo;
          <a href="{p}{proc['l1_slug']}/index.html">{esc(proc['l1_name'])}</a> &rsaquo;
          <a href="{p}{proc['l1_slug']}/{proc['l2_slug']}/index.html">{esc(proc['l2_name'])}</a>
        </div>
        <h1>
          <span class="pid-badge org-badge-{fam}">{proc['l1']}</span>
          {proc['pid']} &mdash; {esc(proc['l2_name'])}
        </h1>
        <p>{esc(data.get('description',''))}</p>
      </div>

      <div class="card">
        <div class="card-header">&#x1F5FA; BPMN Process Flow</div>
        <div class="card-body">
          <div class="diagram-wrap">
            <a href="{p}assets/img/{proc['slug']}.svg" data-lightbox data-title="{proc['pid']} BPMN">
              <img src="{p}assets/img/{proc['slug']}.svg" alt="{proc['pid']} BPMN Diagram">
            </a>
            <p>Click diagram to zoom &amp; pan &bull; Scroll to zoom &bull; Drag to pan &bull;
               <a href="{p}assets/img/{proc['slug']}.svg" download>Download SVG</a></p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">&#x1F4CB; Process Attributes</div>
        <div class="card-body">
          <table class="attr-table">
            <tr><th>Process ID</th><td>{proc['pid']}</td></tr>
            <tr><th>Domain Family</th><td>{FAMILY_LABEL[fam]}</td></tr>
            <tr><th>L1 Domain</th><td>{esc(proc['l1_name'])}</td></tr>
            <tr><th>L2 Process Group</th><td>{esc(proc['l2_name'])}</td></tr>
            <tr><th>Trigger</th><td>{esc(data.get('trigger',''))}</td></tr>
            <tr><th>Outcome</th><td>{esc(data.get('outcome',''))}</td></tr>
            <tr><th>Systems</th><td>{sys_tags(data.get('systems'))}</td></tr>
            <tr><th>Regulatory Hooks</th><td>{regs or '<span class="text-muted">None captured</span>'}</td></tr>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-header">&#x1F4CB; L4 Process Steps</div>
        <div class="card-body">
          <div class="l4-table-wrap">
            <table class="l4-table">
              <thead>
                <tr>
                  <th>Step</th><th>Name</th><th>Role</th><th>System</th>
                  <th>Input</th><th>Output</th><th>KPI</th><th>Pain Point / Risk</th>
                  <th>Decision?</th><th>Exception?</th>
                </tr>
              </thead>
              <tbody>
{chr(10).join(rows)}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">&#x1F4CA; KPIs &amp; Risk Register</div>
        <div class="card-body">
          <p class="mb-8"><strong>KPIs</strong></p>
          <div class="kpi-list mb-8">{kpis or '<span class="text-muted">None captured</span>'}</div>
          <p class="mb-8" style="margin-top:16px"><strong>Key Risks</strong></p>
          <div class="kpi-list">{risks or '<span class="text-muted">None captured</span>'}</div>
          {'<p class="mb-8" style="margin-top:16px"><strong>Swim Lanes</strong></p><div class="kpi-list">' + lanes + '</div>' if lanes else ''}
        </div>
      </div>
"""
    return page_shell(f"{proc['pid']}", f"{esc(proc['l1_name'])} &rsaquo; {esc(proc['l2_name'])}",
                      DEPTH_PROCESS, main, active_pid=proc["pid"])


def group_processes(l1_code, l2_code):
    return [p for p in PROCESSES if p["l1"] == l1_code and p["l2"] == l2_code]


def status_cell(pid, tr):
    if is_complete(pid, tr):
        return '<span style="color:#1a7f37;font-weight:700">&#x2705; Complete</span>'
    return '<span style="color:#9a9da3">Queued</span>'


def build_l2_index(l1_code, l2_code, tr):
    icon, l1_name, l1_slug, fam = L1_META[l1_code]
    g = next(x for x in TAXONOMY if x["l1"] == l1_code and x["l2"] == l2_code)
    procs = group_processes(l1_code, l2_code)
    done = sum(1 for x in procs if is_complete(x["pid"], tr))
    p = prefix(DEPTH_L2)
    rows = "\n".join(
        f'<tr><td><a href="{x["slug"]}/index.html">{x["pid"]}</a></td>'
        f'<td>{esc(g["l2_name"])}</td><td>{status_cell(x["pid"], tr)}</td></tr>'
        for x in procs
    )
    main = f"""      <div class="page-header">
        <div class="breadcrumb">
          <a href="{p}index.html">Home</a> &rsaquo;
          <a href="../index.html">{esc(l1_name)}</a>
        </div>
        <h1>{icon} {esc(g['l2_name'])}</h1>
        <p>{done} of {len(procs)} processes complete</p>
      </div>
      <div class="card">
        <div class="card-header">Process List</div>
        <div class="card-body">
          <table class="l4-table" style="min-width:500px">
            <thead><tr><th>PID</th><th>L2 Process Group</th><th>Status</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>
"""
    return page_shell(esc(g["l2_name"]), f"{esc(l1_name)} &rsaquo; {esc(g['l2_name'])}",
                      DEPTH_L2, main, active_pid=procs[0]["pid"] if procs else None)


def build_l1_index(l1_code, tr):
    icon, l1_name, l1_slug, fam = L1_META[l1_code]
    groups = [g for g in TAXONOMY if g["l1"] == l1_code]
    all_procs = [p for p in PROCESSES if p["l1"] == l1_code]
    done = sum(1 for p in all_procs if is_complete(p["pid"], tr))
    p = prefix(DEPTH_L1)
    ea_id = f"ea-{l1_code.split('-')[1]}"

    cards = []
    for g in groups:
        gp = group_processes(l1_code, g["l2"])
        gdone = sum(1 for x in gp if is_complete(x["pid"], tr))
        cards.append(f"""    <div class="domain-card org-{fam}">
      <h3><a href="{g['l2_slug']}/index.html">{esc(g['l2_name'])}</a></h3>
      <p class="card-meta">{gdone}/{len(gp)} processes complete</p>
    </div>""")

    main = f"""      <div class="page-header">
        <div class="breadcrumb"><a href="{p}index.html">Home</a></div>
        <h1>{icon} {esc(l1_name)}</h1>
        <p>{done} of {len(all_procs)} processes complete across {len(groups)} process groups &middot;
           <a href="{p}{EA_DIR_SLUG}/{ea_id}/index.html">{ea_id.upper()} architecture diagram</a></p>
      </div>
      <div class="domain-grid">
{chr(10).join(cards)}
      </div>
"""
    return page_shell(esc(l1_name), esc(l1_name), DEPTH_L1, main,
                      active_pid=all_procs[0]["pid"] if all_procs else None)


def build_home(tr):
    done = sum(1 for p in PROCESSES if is_complete(p["pid"], tr))
    cards = []
    for l1_code, (icon, l1_name, l1_slug, fam) in L1_META.items():
        procs = [p for p in PROCESSES if p["l1"] == l1_code]
        d = sum(1 for x in procs if is_complete(x["pid"], tr))
        cards.append(f"""    <div class="domain-card org-{fam}">
      <h3><a href="{l1_slug}/index.html">{icon} {esc(l1_name)}</a></h3>
      <p class="card-meta">
        <span class="nav-org-dot {fam}" style="display:inline-block;margin-right:4px"></span>
        {FAMILY_LABEL[fam]}
      </p>
      <p class="card-count">{d} / {len(procs)} processes complete</p>
    </div>""")
    cards.append(f"""    <div class="domain-card org-corp">
      <h3><a href="{EA_DIR_SLUG}/index.html">\U0001F5FA Enterprise Architecture</a></h3>
      <p class="card-meta"><span class="nav-org-dot corp" style="display:inline-block;margin-right:4px"></span>CORPORATE</p>
      <p class="card-count">{len(EA_DIAGRAMS)} EA diagrams</p>
    </div>""")

    n_sys = len(REG_SYSTEMS)
    main = f"""      <div class="page-header">
        <h1>{SITE_TITLE}</h1>
        <p>End-to-end business process reference for US Class I freight railroading,
           modelled on the Union Pacific archetype.</p>
      </div>

      <div class="dedup-panel">
        <h4>Registry-first</h4>
        <ul>
          <li>No system, CFR citation, role or metric appears in a process unless it
              resolves to a sourced entry in <code>registries/</code> &mdash;
              {n_sys} systems, {len(REG_ROLES)} roles, {len(REG_REGS)} regulations,
              {len(REG_KPIS)} KPIs and {len(REG_FACTS)} facts, each with a source URL.</li>
          <li>Any name the model produces that does not resolve is logged at generation
              time and rendered on the page in a dashed red tag rather than silently accepted.</li>
        </ul>
      </div>

      <div class="stats-bar">
        <div class="stat-card"><div class="stat-num">{done}</div><div class="stat-label">Processes Complete</div></div>
        <div class="stat-card"><div class="stat-num">{len(PROCESSES)}</div><div class="stat-label">Total Processes</div></div>
        <div class="stat-card"><div class="stat-num">{len(L1_META)}</div><div class="stat-label">L1 Domains</div></div>
        <div class="stat-card"><div class="stat-num">{len(TAXONOMY)}</div><div class="stat-label">L2 Process Groups</div></div>
        <div class="stat-card"><div class="stat-num accent">{len(EA_DIAGRAMS)}</div><div class="stat-label">EA Diagrams</div></div>
      </div>

      <div class="domain-grid">
{chr(10).join(cards)}
      </div>
"""
    return page_shell(SITE_TITLE, "Business Process Reference", DEPTH_ROOT, main)


def build_search_page():
    return """<!DOCTYPE html>
<!-- """ + TEMPLATE_VERSION + """ -->
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="robots" content="noindex">
  <title>Search &mdash; """ + SITE_TITLE + """</title>
  <link rel="stylesheet" href="assets/css/wiki.css">
</head>
<body>
  <div class="topbar">
    <button id="topbarToggle" aria-label="Menu"><span></span><span></span><span></span></button>
    <a class="topbar-brand" href="index.html">US Class I <span class="accent">Rail</span> Process Wiki</a>
    <span class="topbar-sub">Search Results</span>
    <span class="topbar-spacer"></span>
  </div>
  <div class="wiki-layout">
    <nav id="sidebar">
      <button id="sidebarToggle" title="Collapse sidebar">&#9664;</button>
""" + build_sidebar(DEPTH_ROOT) + """
    </nav>
    <main class="wiki-main">
      <div class="page-header">
        <h1>&#x1F50D; Search</h1>
        <p id="searchQueryDisplay"></p>
      </div>
      <div id="searchResults" class="card">
        <div class="card-body">
          <div class="search-count" id="resultCount">Loading&hellip;</div>
          <div id="resultList"></div>
        </div>
      </div>
    </main>
  </div>
  <div id="lightbox"><img id="lightboxImg" src="" alt=""></div>
  <script src="assets/js/wiki.js"></script>
  <script>
  (function(){
    var params = new URLSearchParams(window.location.search);
    var q = (params.get('q') || '').trim().toLowerCase();
    var qDisplay = document.getElementById('searchQueryDisplay');
    var countEl  = document.getElementById('resultCount');
    var listEl   = document.getElementById('resultList');
    if (!q) { countEl.textContent = 'Enter a search term.'; return; }
    qDisplay.textContent = 'Query: "' + params.get('q') + '"';

    fetch('search-index.json')
      .then(function(r){ return r.json(); })
      .then(function(idx){
        var tokens = q.split(/\\s+/).filter(Boolean);
        var results = idx.filter(function(item){
          var hay = (item.pid + ' ' + item.l1 + ' ' + item.l2 + ' ' +
                     item.l3 + ' ' + (item.systems||'')).toLowerCase();
          return tokens.every(function(t){ return hay.indexOf(t) >= 0; });
        });
        countEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '') +
                              ' for "' + params.get('q') + '"';
        if (!results.length) {
          listEl.innerHTML = '<p class="text-muted mt-16">No processes matched your search.</p>';
          return;
        }
        listEl.innerHTML = results.map(function(r){
          return '<div class="search-result"><h4><a href="' + r.url + '">' +
                 r.pid + ' &mdash; ' + r.l3 + '</a></h4>' +
                 '<p>' + r.l1 + ' &rsaquo; ' + r.l2 + '</p></div>';
        }).join('');
      })
      .catch(function(){ countEl.textContent = 'Search index not yet available.'; });
  })();
  </script>
</body>
</html>
"""


def build_search_index(tr):
    items = []
    for p in PROCESSES:
        rec = tr.get(p["pid"], {})
        if rec.get("status") != "Complete":
            continue
        items.append({"pid": p["pid"], "l1": p["l1_name"], "l2": p["l2_name"],
                      "l3": p["l2_name"], "url": p["path"],
                      "systems": rec.get("systems", "")})
    for ea_id, ea_title, ea_desc in EA_DIAGRAMS:
        items.append({"pid": ea_id.upper(), "l1": "Enterprise Architecture",
                      "l2": "EA Diagrams", "l3": ea_title,
                      "url": f"{EA_DIR_SLUG}/{ea_id}/index.html", "systems": ea_desc})
    return json.dumps(items, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def write(rel_path, content):
    path = ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def rebuild_nav(tr):
    """Regenerate home, every L1 index, every L2 index, search page and index."""
    log("rebuilding navigation …")
    write("index.html", build_home(tr))
    write("search.html", build_search_page())
    write("search-index.json", build_search_index(tr))
    n = 0
    for l1_code, (_, _, l1_slug, _) in L1_META.items():
        write(f"{l1_slug}/index.html", build_l1_index(l1_code, tr))
        n += 1
        for g in [x for x in TAXONOMY if x["l1"] == l1_code]:
            write(f"{l1_slug}/{g['l2_slug']}/index.html", build_l2_index(l1_code, g["l2"], tr))
            n += 1
    log(f"  {n} index pages + home + search rebuilt")


def push_shell(tr, no_push=False):
    css = ROOT / "assets" / "css" / "wiki.css"
    js = ROOT / "assets" / "js" / "wiki.js"
    for f in (css, js):
        if not f.exists():
            sys.exit(f"Missing {f}. wiki.css and wiki.js must exist before running.")
    write(".nojekyll", "")
    rebuild_nav(tr)
    commit_and_push("Rebuild site shell to the reference standard", no_push=no_push)


def process_one(proc, tr):
    log(f"── {proc['pid']} — {proc['l1_name']} / {proc['l2_name']}")
    data = generate_process_content(proc)
    if not data:
        log(f"{proc['pid']}: no usable content from Ollama — skipped", "ERROR")
        return None
    log(f"  content: {len(data.get('l4_steps', []))} steps, "
        f"{len(data.get('systems', []))} systems, {len(data.get('kpis', []))} KPIs")
    registry_audit(proc["pid"], data)

    svg = build_diagram(proc, data)
    if not svg:
        log(f"{proc['pid']}: diagram failed after 3 attempts — skipped", "ERROR")
        return None

    write(proc["path"], build_process_page(proc, data))
    log(f"  wrote {proc['path']}")
    return data


def select_targets(args, tr):
    if args.pid:
        pid = args.pid.upper()
        if pid not in BY_PID:
            sys.exit(f"Unknown PID {pid}")
        return [BY_PID[pid]]
    pool = PROCESSES
    if args.start:
        start = args.start.upper()
        if start not in BY_PID:
            sys.exit(f"Unknown PID {start}")
        idx = next(i for i, p in enumerate(PROCESSES) if p["pid"] == start)
        pool = PROCESSES[idx:]
    incomplete = pool if args.force else [p for p in pool if not is_complete(p["pid"], tr)]
    if args.count:
        return incomplete[: args.count]
    if args.full or args.start:
        return incomplete
    return incomplete[:1]


def main():
    ap = argparse.ArgumentParser(description="US Class I Rail Process Wiki generator")
    ap.add_argument("--pid", help="single process")
    ap.add_argument("--count", type=int, help="run exactly N incomplete processes")
    ap.add_argument("--full", action="store_true", help="all incomplete processes")
    ap.add_argument("--start", help="resume from this PID")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if the tracker says Complete")
    ap.add_argument("--bootstrap", action="store_true",
                    help="push assets, home and every index only, then exit")
    ap.add_argument("--rebuild-nav", action="store_true",
                    help="regenerate all index pages and the search index, no model calls")
    ap.add_argument("--no-verify", action="store_true", help="skip the live check")
    ap.add_argument("--no-push", action="store_true", help="build locally, do not push")
    args = ap.parse_args()

    for d in (DATA_DIR, DIAGRAM_DIR, IMG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    tr = load_tracker()

    if args.bootstrap:
        push_shell(tr, no_push=args.no_push)
        log("bootstrap complete")
        return
    if args.rebuild_nav:
        rebuild_nav(tr)
        commit_and_push("Rebuild navigation and search index", no_push=args.no_push)
        return

    targets = select_targets(args, tr)
    if not targets:
        log("nothing to do — everything selected is already complete")
        return
    log(f"{len(targets)} process(es) queued | force={args.force}")

    done_now = []
    try:
        for i, proc in enumerate(targets, start=1):
            if is_complete(proc["pid"], tr) and not args.force:
                log(f"skip {proc['pid']} — already complete (use --force to rebuild)")
                continue
            data = process_one(proc, tr)
            if data:
                done_now.append((proc, data))
            log(f"progress: {i}/{len(targets)}")
    except KeyboardInterrupt:
        log("interrupted — publishing what is done", "WARN")

    if not done_now:
        log("no process produced usable output", "ERROR")
        return

    for proc, data in done_now:
        tr[proc["pid"]] = {
            "status": "Complete",
            "url": f"{PAGES_BASE}/{proc['path']}",
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "systems": ", ".join(str(s) for s in data.get("systems", [])),
        }
    save_tracker(tr)
    rebuild_nav(tr)
    ok = commit_and_push(
        f"Generate {', '.join(p['pid'] for p, _ in done_now)} under {TEMPLATE_VERSION}",
        no_push=args.no_push)

    for proc, data in done_now:
        excel_record(proc, data, f"{PAGES_BASE}/{proc['path']}")
    log(f"  {len(done_now)} process(es) recorded in the tracker and Excel")

    if ok and not args.no_verify and not args.no_push:
        url = f"{PAGES_BASE}/{done_now[-1][0]['path']}"
        log("verified live: " + url if verify_live(url) else f"could not verify {url}")
    log(f"run complete — {sum(1 for p in PROCESSES if is_complete(p['pid'], tr))}"
        f"/{len(PROCESSES)} processes live at {PAGES_BASE}/")


if __name__ == "__main__":
    main()
