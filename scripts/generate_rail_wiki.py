#!/usr/bin/env python3
"""Phase C — constrained generation (BUILD-SPEC-v2 §4).

    python3 scripts/generate_rail_wiki.py --pid RR-06-02-01 [--mock]

The model fills slots. It does not invent entities.

How that is enforced
--------------------
The prompt offers a menu of registry entries for the process's own domain,
each with an id. The model answers with **ids only** — it never writes a
system name, job title, citation or metric as free text, and any name it did
write would be discarded rather than trusted. The entity fields are then
*constructed* from the registry:

    systems[]        <- registry name, scope and source_id for each chosen id
    actors[]         <- registry role text
    regulatory_hook  <- registry cite text
    kpi_moved        <- registry KPI text
    sources[]        <- source_url of the entries actually used
    pain_points[]    <- the closed §7 list, chosen by index
    steps[]          <- same registry ids, resolved per step (§3.1)

So those fields ground by construction. What the model genuinely authors is
prose: the description, the inputs/outputs, and each step's name/input/output.
That prose is exactly where a fabricated figure or date can still appear, and
it is what validate_content.py scans before anything is written.

An id the model invents does not resolve, and the process is assembled with
the unresolved id left visible so the gate rejects it rather than the
generator quietly dropping it. Nothing is written on a rejection.

Description length and step-list shape are enforced here, not in
validate_content.py. A rejection from the validator means exactly one thing —
the registries do not ground something — and that single meaning is what
makes it trustworthy as the §4 gate. Length and structure are generation
quality conditions, so they live in Phase C with a bounded, self-describing
retry.

Five-field description
-----------------------
A single free-text "write 120-200 words" instruction undershot badly in
practice (57 words on the first live run) — a length target stated as a range
inside a JSON template value reads to a small model as a format hint, not a
binding constraint. The model generates five short fields instead of one long
one — trigger, sequence, judgement, handoff, done — each with its own word
floor. assemble() concatenates them into a single "description" string before
anything downstream ever sees it; validate_content.py, data/processes.json
and any page renderer see one description field, exactly as before.

Step list (§3.1, Phase 4b)
---------------------------
A flat, ordered `steps[]` list — 4-8 steps, at least one decision/exception
gate, a branch object required on every decision_point:"Y" step. Deliberately
smaller than the Air Canada wiki's 16-30-step/5-8-phase model: a rail-wiki PID
is already narrow (the taxonomy splits each L2 cluster into 2-3 PIDs), so one
process here is closer to one AC *phase* than a whole AC process — see spec
§3.1 for the full reasoning. Each step's role/system/kpi is chosen by id from
the same per-domain menu as the process-level fields, and resolved by
assemble() the same way.

Model
-----
Ollama REST at http://localhost:11434, model qwen2.5:14b-instruct, falling
back to qwen2.5:latest if the preferred tag is not pulled. --mock replaces the
call entirely with a canned response and never opens a socket. --show-prompt
prints the prompt and exits before either path is reached.

A live run observed the backend process (llama-server) get killed and
respawned by Ollama's own supervisor mid-session -- on this machine, by
macOS's wakeups-limit power management, which will kill any process that
wakes the CPU too aggressively, and GPU inference does exactly that. The
symptom was a script that sat silently for tens of minutes: a bare
urllib.request.urlopen(timeout=180) does apply a socket timeout, but nothing
distinguished "the model is thinking" from "the backend died and Ollama is
still routing the request somewhere." call_ollama() now retries a connection
-level failure on its own, a bounded and VISIBLE number of times (see
CONNECT_RETRY_DELAYS), separately from and prior to the content-quality retry
loop in generate_one() -- a dead backend should cost a retry-with-backoff,
not the whole PID, and it should never again look identical to silence.

Reuse
-----
generate_one() is the whole single-PID pipeline as a plain function returning
a result dict, not a CLI wrapper around sys.exit -- scripts/run_batch.py calls
it directly, in-process, for many PIDs in one run, rather than shelling out.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_content as vc  # noqa: E402
from registry_loader import REGISTRY_DIR, Entry, RegistryError, load  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "docs" / "rail-wiki-build-spec-v2.md"
TAXONOMY = REPO / "data" / "taxonomy.json"
PROCESSES = REPO / "data" / "processes.json"

OLLAMA_HOST = "http://localhost:11434"
PREFERRED_MODEL = "qwen2.5:14b-instruct"
FALLBACK_MODEL = "qwen2.5:latest"
TIMEOUT = 180
# Bounded, visible retries for a connection-level failure (the backend
# process died and is being respawned) -- NOT for a slow-but-alive model.
# Delays sit under the ~4-5 min respawn cycle observed live, so two full
# cycles fit inside the total backoff budget before giving up.
CONNECT_RETRY_DELAYS = [10, 30, 90]
CONNECT_ERRORS = (urllib.error.URLError, ConnectionError, TimeoutError,
                  socket.timeout, http.client.RemoteDisconnected)

# The five description beats, in writing/concatenation order, each checked
# independently. Floors sum to DESC_FLOOR_WORDS, which stays in place as a
# backstop on the assembled whole — if the five pass individually the backstop
# should pass automatically; it only fires if assembly itself misbehaves.
FIELD_KEYS = ["trigger", "sequence", "judgement", "handoff", "done"]
FIELD_LABELS = {k: k.upper() for k in FIELD_KEYS}
FIELD_PROMPTS = {
    "trigger": "What starts this work, and how the need becomes visible to "
               "the people who act on it.",
    "sequence": "Who does what, in order, from trigger through to completion.",
    "judgement": "The decision or exception that makes this process "
                 "non-trivial — the point where experience matters — and "
                 "what happens on each branch.",
    "handoff": "What leaves this process, and who picks it up next.",
    "done": "The condition that means the work is complete and can be "
            "closed out.",
}
FIELD_FLOOR_WORDS = 20
FIELD_MAX_WORDS = 45

# Backstop on the assembled whole (sum of the five floors). The band is
# advisory, matching §3's "120-200 words" for the process record.
DESC_FLOOR_WORDS = len(FIELD_KEYS) * FIELD_FLOOR_WORDS
DESC_BAND = (120, 200)
DEFAULT_RETRIES = 2

# §3.1 step list. Smaller than AC's 16-30/6-gate model — see module docstring.
STEP_MIN = 4
STEP_MAX = 8
# Was 1; a real process with only 1 gate produces a visibly sparse diagram
# once every gate gets a labelled branch (rendering can label what exists,
# it cannot invent gates the model never generated). 3 gives the diagram
# enough real branch points to read as a workflow rather than a checklist.
STEP_MIN_GATES = 3

MENU_REGISTRIES = ("systems", "roles", "regulations", "kpis")


class GenerationError(RuntimeError):
    pass


# --- inputs -------------------------------------------------------------------
def load_taxonomy_full() -> dict:
    if not TAXONOMY.exists():
        raise GenerationError(
            f"{TAXONOMY.relative_to(REPO)} not found — run scripts/build_taxonomy.py first")
    return json.loads(TAXONOMY.read_text(encoding="utf-8"))


def load_taxonomy(pid: str) -> dict:
    doc = load_taxonomy_full()
    for p in doc["processes"]:
        if p["pid"] == pid:
            return p
    raise GenerationError(
        f"{pid} is not in the taxonomy. It is not a PID this build emits — "
        f"check scripts/build_taxonomy.py --domain {pid.split('-')[1] if '-' in pid else '??'}")


def spec_pain_points() -> list[str]:
    """The closed §7 list. The spec forbids model-invented pain points."""
    text = SPEC.read_text(encoding="utf-8")
    i = text.find("## 7. Pain points")
    j = text.find("## 8.", i)
    if i < 0:
        raise GenerationError("could not locate §7 in the spec")
    return [m.group(1).strip()
            for m in re.finditer(r"^-\s+(.+)$", text[i:j], re.M)]


def domain_group(pid: str, r) -> str | None:
    m = re.match(r"^RR-(\d{2})-", pid)
    if not m:
        return None
    want = f"domain_{m.group(1)}_"
    for reg in MENU_REGISTRIES + ("facts",):
        for e in r.all(reg):
            if e.domain.startswith(want):
                return e.domain
    return None


def build_menu(r, group: str) -> dict[str, list[Entry]]:
    """Registry entries available to this domain, keyed by registry."""
    return {reg: [e for e in r.all(reg) if e.domain == group] for reg in MENU_REGISTRIES}


# --- prompt -------------------------------------------------------------------
def render_menu(menu: dict[str, list[Entry]], pains: list[str]) -> str:
    lines: list[str] = []
    labels = {"systems": "SYSTEMS", "roles": "ROLES (actors)",
              "regulations": "REGULATORY HOOKS", "kpis": "KPIs"}
    for reg in MENU_REGISTRIES:
        lines.append(f"{labels[reg]}:")
        if not menu[reg]:
            lines.append("  (none registered for this domain — select none)")
        for e in menu[reg]:
            extra = f" [scope: {e.scope}]" if reg == "systems" and e.scope else ""
            lines.append(f"  {e.entry_id} = {e.key}{extra}")
        lines.append("")
    lines.append("PAIN POINTS (choose by number, spec §7 — this list is closed):")
    for i, p in enumerate(pains, start=1):
        lines.append(f"  P{i:02d} = {p}")
    return "\n".join(lines)


def _process_header(node: dict) -> str:
    return f"""PROCESS
  PID : {node['pid']}
  L1  : {node['l1']}
  L2  : {node['l2']}"""


def _entities_block(menu: dict[str, list[Entry]], pains: list[str]) -> str:
    return f"""ENTITIES
Select entities below ONLY by their id, and put that id ONLY in an id field
of the OUTPUT JSON (system_ids, role_ids, regulation_ids, kpi_ids,
pain_point_ids, or a step's role_id/system_id/kpi_id/pain_point_id) -- never
inside prose. Do not invent ids. If nothing in a list fits, return an empty
list.

You may still describe roles and systems in prose using ordinary words --
"the Yardmaster", "the inspection system" -- that is normal and expected. The
one thing that must never appear inside prose is the id CODE ITSELF, things
like "SYS-D06-01" or "ROLE-D06-02" or "P04" typed as text in a sentence. If
you need to refer to an entity in prose, use its plain name or role, never
its id string.

{render_menu(menu, pains)}"""


def build_description_prompt(node: dict, menu: dict[str, list[Entry]], pains: list[str],
                             retry_note: str | None = None) -> str:
    """Phase A: the five description fields, name, inputs/outputs and the
    process-level entity ids. No steps here.

    This used to be one prompt asking for the description AND the step list
    in a single JSON object. Live testing found that combination genuinely
    hard for a 14B model to converge on: fixing a step problem on retry would
    regress a description field back below its floor, and vice versa,
    because each retry regenerates the entire response from scratch rather
    than patching just the flagged part. Splitting description and steps
    into two independent generate-and-retry phases means a steps retry can
    no longer disturb fields that already passed, and the reverse.
    """
    retry = f"\n{retry_note}\n" if retry_note else ""
    field_instructions = "\n".join(
        f"  {FIELD_LABELS[k]} ({FIELD_FLOOR_WORDS}-{FIELD_MAX_WORDS} words) — {FIELD_PROMPTS[k]}"
        for k in FIELD_KEYS
    )
    field_template = ",\n".join(
        f'  "{k}": "{FIELD_FLOOR_WORDS}-{FIELD_MAX_WORDS} words covering {FIELD_LABELS[k]} only"'
        for k in FIELD_KEYS
    )
    return f"""You are documenting one business process for a US Class I freight railroad.

{_process_header(node)}

{_entities_block(menu, pains)}

WRITING THE DESCRIPTION
This is the main body of work in this task. The description is five separate
fields, each covering one beat of the process, in this order:

{field_instructions}

Each field is checked on its own and must independently meet its word count.
Do not write labels or headings inside the field text — the JSON key is the
label. Write plain declarative sentences, one or two per field. No consulting
register, no filler, no restating the process name.

Write about mechanism rather than measurement: explain how the work is
carried out and what governs it. Figures, percentages and dates are not what
makes these fields good, and they are better without them unless one is
genuinely central to the process.

If a field is coming in short, that means you have under-described that one
beat specifically — expand what that beat covers, don't pad with filler, and
don't borrow content that belongs in a different field.

OTHER FIELDS
"name": a specific process name, 4-12 words, no numbers.
"inputs" / "outputs": 2-4 short noun phrases each — what feeds this process
and what it produces, not a restatement of the five description fields.
{retry}
OUTPUT
Return ONE JSON object, nothing else. Do not include a "steps" field — that
is a separate step, not part of this one.

{{
  "name": "specific process name, 4-12 words, no numbers",
{field_template},
  "inputs": ["2-4 short noun phrases"],
  "outputs": ["2-4 short noun phrases"],
  "system_ids": ["SYS-..."],
  "role_ids": ["ROLE-..."],
  "regulation_ids": ["REG-..."],
  "kpi_ids": ["KPI-..."],
  "pain_point_ids": ["P01"],
  "confidence": "high | medium | low"
}}"""


def build_steps_prompt(node: dict, menu: dict[str, list[Entry]], pains: list[str],
                       retry_note: str | None = None) -> str:
    """Phase B: just the step list, run after the description phase has
    already passed. Repeats the ENTITIES menu (steps need their own
    role_id/system_id/kpi_id picks) but asks for nothing else."""
    retry = f"\n{retry_note}\n" if retry_note else ""
    return f"""You are documenting the step-by-step breakdown of one business process for
a US Class I freight railroad. The process itself is already written; this is
only the step list.

{_process_header(node)}

{_entities_block(menu, pains)}

WRITING THE STEPS
Break the process into {STEP_MIN}-{STEP_MAX} concrete steps, in order, as a
flat list — not grouped into phases, because a process this specific is
already one coherent stretch of work. Each step is:

  {{"step": "1", "name": "short action phrase", "role_id": "ROLE-...",
   "system_id": "SYS-... or null", "input": "...", "output": "...",
   "kpi_id": "KPI-... or null", "decision_point": "Y or N",
   "exception": "Y or N", "pain_point_id": "P.. or null"}}

Rules:
  - "step" ids are "1", "2", "3", ... in order, unique.
  - "role_id", "system_id" and "kpi_id" are each a SINGLE id string from the
    ENTITIES lists above (e.g. "ROLE-D06-01"), never a name, never an
    invented id, and never a list even if more than one seems to fit — pick
    the one that fits best. Use null (not an empty string) if none applies.
  - Exactly one of "decision_point" / "exception" may be "Y" on a given step;
    most steps are "N"/"N". At least {STEP_MIN_GATES} steps across the whole
    list must be "Y" on one of them, spread across different points in the
    sequence rather than clustered together — a process with no real decision or
    exception in it is being under-described, not genuinely simple.
  - A step with "decision_point": "Y" MUST also carry:
      "branch": {{"label": "short label", "to": "<step id or early-exit phrase>"}}
    naming the alternate path. A decision with no branch is not a decision.
    A "branch" on an "exception": "Y" step is welcome but not required.
{retry}
OUTPUT
Return ONE JSON object, nothing else:

{{
  "steps": [
    {{"step": "1", "name": "...", "role_id": "ROLE-...", "system_id": "SYS-... or null",
     "input": "...", "output": "...", "kpi_id": "KPI-... or null",
     "decision_point": "Y or N", "exception": "Y or N", "pain_point_id": "P.. or null"}}
  ]
}}"""


# --- model call ---------------------------------------------------------------
def _http_json(url: str, payload: dict | None = None, timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def pick_model(host: str) -> str:
    try:
        tags = _http_json(f"{host}/api/tags", timeout=10)
    except (urllib.error.URLError, OSError) as e:
        raise GenerationError(
            f"cannot reach Ollama at {host} ({e}). Start it, or use --mock.") from e
    have = {m.get("name", "") for m in tags.get("models", [])}
    if PREFERRED_MODEL in have:
        return PREFERRED_MODEL
    if FALLBACK_MODEL in have:
        print(f"  note: {PREFERRED_MODEL} not pulled, falling back to {FALLBACK_MODEL}")
        return FALLBACK_MODEL
    raise GenerationError(
        f"neither {PREFERRED_MODEL} nor {FALLBACK_MODEL} is pulled on {host}. "
        f"Available: {sorted(have) or 'none'}")


def call_ollama(prompt: str, host: str) -> tuple[dict, str]:
    """Returns (parsed reply, raw response text) -- the raw text is what --debug shows.

    A connection-level failure (backend killed and respawning) retries here,
    bounded and printed at every step, rather than the caller having no way
    to tell "the model is thinking" from "the process under it just died."
    """
    model = pick_model(host)
    print(f"  model: {model} @ {host}")

    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2},
    }
    last_err: Exception | None = None
    out = None
    for i, delay in enumerate([0] + CONNECT_RETRY_DELAYS):
        if delay:
            print(f"  connection problem ({last_err}); retrying in {delay}s "
                  f"(reconnect {i}/{len(CONNECT_RETRY_DELAYS)})")
            time.sleep(delay)
        try:
            out = _http_json(f"{host}/api/generate", payload)
            break
        except CONNECT_ERRORS as e:
            last_err = e
    if out is None:
        raise GenerationError(
            f"lost connection to Ollama at {host} and it did not come back "
            f"within {sum(CONNECT_RETRY_DELAYS)}s of retrying ({last_err}). "
            f"This usually means the backend process died mid-request and is "
            f"slow to restart. Check it is actually up (curl {host}/api/tags) "
            f"before retrying this PID.")

    raw = out.get("response", "")
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError as e:
        raise GenerationError(f"model did not return JSON: {e}\n---\n{raw[:800]}") from e


# --- mock ---------------------------------------------------------------------
# Each profile supplies the five fields directly (dict keyed by FIELD_KEYS),
# rather than one flat description, so the mock exercises the same shape the
# real model now returns.
MOCK_FIELDS = {
    "good": {
        "trigger": "A planned inspection cycle is published for each subdivision on a "
                   "fixed schedule rather than in response to a specific complaint or "
                   "event, so the need becomes visible through the calendar, not through "
                   "an alert.",
        "sequence": "The field team executes the published cycle in order, and every "
                    "finding is scored and logged as it is made. Results are then routed "
                    "for disposition before the next cycle opens, so nothing carries over "
                    "unresolved and unassigned.",
        "judgement": "Findings are classified by severity: the most serious generate an "
                     "immediate operating restriction, which is placed into effect before "
                     "any further movement is authorised over the affected segment, while "
                     "less serious findings are queued into the planned maintenance "
                     "programme instead of acted on immediately.",
        "handoff": "Every restriction imposed, changed or lifted is recorded so that the "
                   "field condition, the dispatching system and the onboard enforcement "
                   "data stay consistent with one another, and downstream data files "
                   "carrying the railroad's physical characteristics are updated to match.",
        "done": "The cycle is closed out once every finding from it has either been "
                "resolved or formally queued, and any disagreement between what the "
                "field recorded and what the system holds has been reconciled rather "
                "than left standing.",
    },
    "short": {
        "trigger": "A train is ready for departure.",
        "sequence": "The crew performs the required test.",
        "judgement": "A defect is repaired or the car is set out.",
        "handoff": "The train departs once cleared.",
        "done": "The record is closed.",
    },
    # Deliberately over the per-field floor, so the attempt reaches the
    # validator and exercises the grounding path rather than being turned
    # back on length.
    "ungrounded-figure": {
        "trigger": "A planned inspection cycle is published for each subdivision on a "
                   "fixed schedule, so the need becomes visible through the calendar "
                   "rather than through a specific alert or complaint.",
        "sequence": "The field team executes the cycle in order. Automated classification "
                    "cut triage time by 34% and clears 1,200 exceptions per week across "
                    "the region, and results are routed for disposition before the next "
                    "cycle opens.",
        "judgement": "Findings are classified by severity: the most serious generate an "
                     "immediate operating restriction, placed into effect before any "
                     "further movement is authorised, while less serious findings are "
                     "queued into the planned maintenance programme instead.",
        "handoff": "Every restriction imposed, changed or lifted is recorded so the field "
                   "condition and the systems that govern movement stay consistent with "
                   "one another, and the change is carried into the relevant data files.",
        "done": "Since March 2019 the programme has run continuously on all main line "
                "track, and the cycle is closed out once any disagreement between the "
                "field record and the system has been reconciled.",
    },
}

MOCK_STEP_NAMES = [
    "Work item is received and logged",
    "Field or desk check is performed",
    "Result is classified for disposition",
    "Exception path is escalated if needed",
    "Outcome is recorded against the record",
]


def _mock_steps(menu: dict[str, list[Entry]], quality: str = "good") -> list[dict]:
    """Deterministic step list for mocks, built from whatever this domain's
    menu actually contains (some domains have zero systems/kpis)."""
    sys_ids = [e.entry_id for e in menu["systems"]]
    role_ids = [e.entry_id for e in menu["roles"]] or [None]
    kpi_ids = [e.entry_id for e in menu["kpis"]]

    def cyc(lst: list, i: int):
        return lst[i % len(lst)] if lst else None

    if quality == "bad":
        # Too few steps (violates STEP_MIN) AND a decision step with no
        # branch, to exercise both structural checks in one profile.
        return [
            {"step": "1", "name": "First action", "role_id": cyc(role_ids, 0),
             "system_id": None, "input": "Input A", "output": "Output A",
             "kpi_id": None, "decision_point": "Y", "exception": "N"},
            {"step": "2", "name": "Second action", "role_id": cyc(role_ids, 1),
             "system_id": None, "input": "Output A", "output": "Output B",
             "kpi_id": None, "decision_point": "N", "exception": "N"},
        ]

    steps = []
    for i in range(5):
        # 3 gates spread across the sequence (index 0, 2, 4), matching
        # STEP_MIN_GATES=3 and its "spread across different points" rule --
        # not clustered together, same as real generated output should be.
        dp = i == 2
        exc = i in (0, 4)
        step = {
            "step": str(i + 1),
            "name": MOCK_STEP_NAMES[i],
            "role_id": cyc(role_ids, i),
            "system_id": cyc(sys_ids, i),
            "input": "Initiating input" if i == 0 else "Prior step output",
            "output": "Final record" if i == 4 else "Next step input",
            "kpi_id": cyc(kpi_ids, i),
            "decision_point": "Y" if dp else "N",
            "exception": "Y" if exc else "N",
        }
        if dp:
            step["branch"] = {"label": "No", "to": str(i + 2)}
        steps.append(step)
    return steps


def mock_description_response(menu: dict[str, list[Entry]], pains: list[str], profile: str,
                              attempt: int = 1) -> tuple[dict, str]:
    """Phase A canned reply -- fields, name, inputs/outputs, process-level
    entity ids. No steps. Opens no socket."""
    sys_ids = [e.entry_id for e in menu["systems"][:2]]
    role_ids = [e.entry_id for e in menu["roles"][:3]]
    reg_ids = [e.entry_id for e in menu["regulations"][:1]]
    kpi_ids = [e.entry_id for e in menu["kpis"][:1]]

    if profile == "invented-system":
        sys_ids = ["SYS-D06-09"] + sys_ids[:1]
    if profile == "invented-role":
        role_ids = ["ROLE-D06-99"] + role_ids[:2]

    fields_key = profile
    if profile == "short-then-good":
        fields_key = "short" if attempt == 1 else "good"
    fields = MOCK_FIELDS.get(fields_key, MOCK_FIELDS["good"])

    reply = {
        "name": "Scheduled Inspection Cycle and Exception Disposition",
        **fields,
        "inputs": ["Published inspection cycle plan", "Prior exception history"],
        "outputs": ["Scored exception list", "Operating restriction request"],
        "system_ids": sys_ids,
        "role_ids": role_ids,
        "regulation_ids": reg_ids,
        "kpi_ids": kpi_ids,
        "pain_point_ids": ["P07"] if len(pains) >= 7 else ([f"P{len(pains):02d}"] if pains else []),
        "confidence": "medium",
    }
    return reply, json.dumps(reply, indent=2, ensure_ascii=False)


def mock_steps_response(menu: dict[str, list[Entry]], profile: str,
                        attempt: int = 1) -> tuple[dict, str]:
    """Phase B canned reply -- just steps. Opens no socket."""
    steps_quality = "good"
    if profile == "bad-steps":
        steps_quality = "bad"
    if profile == "bad-steps-then-good":
        steps_quality = "bad" if attempt == 1 else "good"
    steps = _mock_steps(menu, steps_quality)
    if profile == "invented-system" and steps:
        steps[0]["system_id"] = "SYS-D06-09"
    if profile == "invented-role" and steps:
        steps[0]["role_id"] = "ROLE-D06-99"

    reply = {"steps": steps}
    return reply, json.dumps(reply, indent=2, ensure_ascii=False)


# --- field-length gate ---------------------------------------------------------
def field_word_counts(reply: dict) -> dict[str, int]:
    return {k: len(str(reply.get(k) or "").split()) for k in FIELD_KEYS}


def check_field_lengths(reply: dict) -> tuple[bool, dict[str, int], list[str], list[str]]:
    """(all_pass, counts, weak_fields, over_max_fields). weak_fields is a hard
    gate; over_max_fields is advisory only."""
    counts = field_word_counts(reply)
    weak = [k for k in FIELD_KEYS if counts[k] < FIELD_FLOOR_WORDS]
    over = [k for k in FIELD_KEYS if counts[k] > FIELD_MAX_WORDS]
    return (len(weak) == 0), counts, weak, over


def field_retry_note(counts: dict[str, int], weak: list[str]) -> str:
    """Names only the deficient fields, and tells the model to leave the rest
    untouched -- a generic nudge is what the single-field version tried, and
    it made every field shorter on every retry instead of longer."""
    lines = ["PREVIOUS ATTEMPT REJECTED — DESCRIPTION FIELDS"]
    for k in FIELD_KEYS:
        if k in weak:
            lines.append(
                f"Your {FIELD_LABELS[k]} was {counts[k]} words, need at least "
                f"{FIELD_FLOOR_WORDS} — expand only that beat.")
    good = [FIELD_LABELS[k] for k in FIELD_KEYS if k not in weak]
    if good:
        verb = "meets" if len(good) == 1 else "meet"
        lines.append(f"Leave {', '.join(good)} exactly as they are — "
                     f"{'it' if len(good) == 1 else 'they'} already {verb} the requirement.")
    return "\n".join(lines)


# --- step-structure gate --------------------------------------------------------
def check_steps_structure(reply: dict) -> tuple[bool, list[str], int]:
    """(ok, problems, gate_count). Structural checks only, before assembly --
    registry grounding of role_id/system_id/kpi_id happens later, in
    validate_content.py, exactly like the process-level fields."""
    steps = reply.get("steps") if isinstance(reply, dict) else None
    problems: list[str] = []
    if not isinstance(steps, list):
        got = type(steps).__name__ if steps is not None else "missing"
        return False, [f'"steps" must be a list, got {got}'], 0

    n = len(steps)
    if not (STEP_MIN <= n <= STEP_MAX):
        problems.append(f"{n} steps returned, need {STEP_MIN}-{STEP_MAX}")

    seen_ids: set[str] = set()
    gate_count = 0
    for i, s in enumerate(steps):
        loc = f"step {i + 1}"
        if not isinstance(s, dict):
            problems.append(f"{loc}: not an object")
            continue
        sid = s.get("step")
        label = sid if isinstance(sid, str) and sid else str(i + 1)
        if not sid or not isinstance(sid, str):
            problems.append(f"{loc}: missing \"step\" id")
        elif sid in seen_ids:
            problems.append(f"{loc} ({label}): duplicate step id")
        else:
            seen_ids.add(sid)
        for req in ("name", "role_id", "input", "output", "decision_point", "exception"):
            if not s.get(req):
                problems.append(f"{loc} ({label}): missing \"{req}\"")
        # id fields are a single id or null -- a live run returned a LIST of
        # kpi ids for one step ('want both' read as 'give me a list'), which
        # would otherwise sail through this structural gate and only fail
        # later at validate_content.py, by which point Phase B has already
        # "passed" and there is no retry left to fix it in.
        for id_field in ("role_id", "system_id", "kpi_id", "pain_point_id"):
            v = s.get(id_field)
            if v is not None and not isinstance(v, str):
                problems.append(
                    f"{loc} ({label}): \"{id_field}\" must be a single id string or "
                    f"null, got {v!r} -- pick ONE id, not a list")

        dp = str(s.get("decision_point", "")).strip().upper()
        exc = str(s.get("exception", "")).strip().upper()
        if s.get("decision_point") is not None and dp not in ("Y", "N"):
            problems.append(f"{loc} ({label}): decision_point must be \"Y\" or \"N\"")
        if s.get("exception") is not None and exc not in ("Y", "N"):
            problems.append(f"{loc} ({label}): exception must be \"Y\" or \"N\"")
        if dp == "Y" or exc == "Y":
            gate_count += 1
        if dp == "Y":
            br = s.get("branch")
            if not (isinstance(br, dict) and br.get("label") and br.get("to")):
                problems.append(
                    f"{loc} ({label}): decision_point is \"Y\" but has no branch "
                    f"object with \"label\" and \"to\"")

    if n >= STEP_MIN and gate_count < STEP_MIN_GATES:
        problems.append(f"only {gate_count} decision/exception gate(s) across "
                         f"{n} steps, need at least {STEP_MIN_GATES}")

    return (len(problems) == 0), problems, gate_count


def step_retry_note(problems: list[str]) -> str:
    lines = ["PREVIOUS ATTEMPT REJECTED — STEP LIST"]
    for p in problems:
        lines.append(f"  - {p}")
    lines.append("Fix only the problem(s) listed above. Leave every step not "
                 "mentioned exactly as it is.")
    return "\n".join(lines)


# --- assembly -----------------------------------------------------------------
def _terminate(sentence: str) -> str:
    """Ensure a field's text ends with terminal punctuation before it is
    concatenated with the next one, so two beats don't run together."""
    s = sentence.strip()
    if s and not s.endswith((".", "!", "?")):
        s += "."
    return s


def _by_id(r, registry: str, entry_id: str) -> Entry | None:
    if not isinstance(entry_id, str):
        return None
    want = entry_id.strip().upper()
    return next((e for e in r.all(registry) if e.entry_id.upper() == want), None)


def assemble_steps(reply: dict, r, pains: list[str], notes: list[str]) -> list[dict]:
    """§3.1: resolve each step's role_id/system_id/kpi_id/pain_point_id from
    the registry, exactly like the process-level fields. An id that does not
    resolve is left in place (as the raw id string, or a bare system stub) so
    validate_content.py rejects it visibly instead of this function hiding
    the gap."""
    out: list[dict] = []
    for i, s in enumerate(reply.get("steps") or []):
        if not isinstance(s, dict):
            continue

        role_id = s.get("role_id")
        role_e = _by_id(r, "roles", role_id) if role_id else None
        if role_id and role_e is None:
            notes.append(f"steps[{i}].role: model selected id {role_id!r}, which "
                         f"is not in the registry — left unresolved for the gate")
        role_text = role_e.key if role_e else (role_id or "")

        sys_id = s.get("system_id")
        system_obj = None
        if sys_id:
            sys_e = r.get_system(sys_id)
            if sys_e is None:
                notes.append(f"steps[{i}].system: model selected id {sys_id!r}, "
                             f"which is not in the registry — left unresolved "
                             f"for the gate")
                system_obj = {"name": sys_id, "scope": "company_specific", "source_id": sys_id}
            else:
                system_obj = {"name": sys_e.key, "scope": sys_e.scope, "source_id": sys_e.entry_id}

        kpi_id = s.get("kpi_id")
        kpi_e = _by_id(r, "kpis", kpi_id) if kpi_id else None
        if kpi_id and kpi_e is None:
            notes.append(f"steps[{i}].kpi: model selected id {kpi_id!r}, which "
                         f"is not in the registry — left unresolved for the gate")
        kpi_text = kpi_e.key if kpi_e else (kpi_id or "")

        pain_text = ""
        pt = s.get("pain_point_id")
        if pt:
            m = re.fullmatch(r"P(\d{1,2})", str(pt).strip(), re.I)
            if m and 1 <= int(m.group(1)) <= len(pains):
                pain_text = pains[int(m.group(1)) - 1]
            else:
                notes.append(f"steps[{i}].pain_point: {pt!r} is not in the "
                             f"closed §7 list — dropped")

        step_obj = {
            "step": str(s.get("step") or str(i + 1)),
            "name": str(s.get("name") or "").strip(),
            "role": role_text,
            "system": system_obj,
            "input": str(s.get("input") or "").strip(),
            "output": str(s.get("output") or "").strip(),
            "kpi": kpi_text,
            "decision_point": str(s.get("decision_point") or "N").strip().upper(),
            "exception": str(s.get("exception") or "N").strip().upper(),
            "pain_point": pain_text,
        }
        branch = s.get("branch")
        if isinstance(branch, dict) and branch.get("label") and branch.get("to"):
            step_obj["branch"] = {"label": str(branch["label"]), "to": str(branch["to"])}
        out.append(step_obj)
    return out


def assemble(node: dict, reply: dict, r, pains: list[str]) -> tuple[dict, list[str]]:
    """Build the §3 process object from registry entries, not from model text.

    The five description fields are concatenated here into one "description"
    string with no labels or field boundaries visible. Everything downstream
    of this function -- validate_content.py, data/processes.json, any future
    page renderer -- sees exactly the shape it saw before the five-field
    split existed, plus the new steps[] array (§3.1).
    """
    notes: list[str] = []

    def resolve(ids, getter, registry: str):
        out: list[Entry | str] = []
        for i in ids or []:
            if not isinstance(i, str):
                notes.append(f"{registry}: non-string id {i!r} discarded")
                continue
            e = getter(i)
            if e is None:
                notes.append(f"{registry}: model selected id {i!r}, which is not "
                             f"in the registry — left unresolved for the gate")
                out.append(i)
            else:
                out.append(e)
        return out

    systems = resolve(reply.get("system_ids"), r.get_system, "systems")
    roles = resolve(reply.get("role_ids"), lambda i: _by_id(r, "roles", i), "roles")
    regs = resolve(reply.get("regulation_ids"), lambda i: _by_id(r, "regulations", i), "regulations")
    kpis = resolve(reply.get("kpi_ids"), lambda i: _by_id(r, "kpis", i), "kpis")

    sources: list[str] = []
    for e in systems + roles + regs + kpis:
        if isinstance(e, Entry) and e.source_url and e.source_url not in sources:
            sources.append(e.source_url)

    chosen_pains: list[str] = []
    for tag in reply.get("pain_point_ids") or []:
        m = re.fullmatch(r"P(\d{1,2})", str(tag).strip(), re.I)
        if m and 1 <= int(m.group(1)) <= len(pains):
            chosen_pains.append(pains[int(m.group(1)) - 1])
        else:
            notes.append(f"pain_points: {tag!r} is not in the closed §7 list — dropped")

    # Any entity name the model wrote in prose is ignored; only ids were read.
    for key in ("systems", "actors", "regulatory_hook", "kpi_moved", "pain_points"):
        if key in reply:
            notes.append(f"model returned a free-text {key!r} field — ignored, "
                         f"entity fields are built from the registry")

    description = " ".join(
        _terminate(str(reply.get(k) or "")) for k in FIELD_KEYS if str(reply.get(k) or "").strip()
    )

    proc = {
        "pid": node["pid"],
        "name": str(reply.get("name") or node["l2"]).strip(),
        "l1": node["l1"],
        "l2": node["l2"],
        "description": description,
        "actors": [e.key if isinstance(e, Entry) else e for e in roles],
        "inputs": [str(x) for x in (reply.get("inputs") or [])],
        "outputs": [str(x) for x in (reply.get("outputs") or [])],
        "systems": [
            {"name": e.key, "scope": e.scope, "source_id": e.entry_id}
            if isinstance(e, Entry) else
            {"name": e, "scope": "company_specific", "source_id": e}
            for e in systems
        ],
        "regulatory_hook": [e.key if isinstance(e, Entry) else e for e in regs],
        "operating_rule_ref": "GCOR (general reference)",
        "kpi_moved": [e.key if isinstance(e, Entry) else e for e in kpis],
        "pain_points": chosen_pains,
        "confidence": str(reply.get("confidence") or "low").strip().lower(),
        "sources": sources,
        "last_reviewed": date.today().isoformat(),
        "steps": assemble_steps(reply, r, pains, notes),
    }

    return proc, notes


def check_description_length(proc: dict) -> tuple[bool, int, str | None]:
    """(meets_floor, words, advisory) on the ASSEMBLED whole. This is a
    backstop, not the primary gate -- the primary gate is check_field_lengths,
    run on the five fields before assembly. If the five fields individually
    clear their floors this should pass automatically; it exists to catch an
    assembly bug, not to catch the model."""
    words = len((proc.get("description") or "").split())
    lo, hi = DESC_BAND
    if words < DESC_FLOOR_WORDS:
        return False, words, None
    if words < lo:
        return True, words, f"description is {words} words, under the §3 band of {lo}-{hi}"
    if words > hi:
        return True, words, f"description is {words} words, over the §3 band of {lo}-{hi}"
    return True, words, None


# --- output -------------------------------------------------------------------
def write_process(proc: dict, path: Path) -> str:
    doc = {"_meta": {}, "processes": {}}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("processes"), dict):
                doc = existing
        except json.JSONDecodeError:
            raise GenerationError(f"{path} exists but is not valid JSON — refusing to overwrite")
    action = "updated" if proc["pid"] in doc["processes"] else "written"
    doc["processes"][proc["pid"]] = proc
    doc["_meta"] = {
        "generated_by": "scripts/generate_rail_wiki.py",
        "note": "gitignored per spec §8 — never commit this file",
        "process_count": len(doc["processes"]),
        "last_write": date.today().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return action


# --- reusable pipeline ----------------------------------------------------------
def generate_one(
    pid: str, *,
    mock: bool = False,
    mock_profile: str = "good",
    host: str = OLLAMA_HOST,
    registries: str = str(REGISTRY_DIR),
    out: str = str(PROCESSES),
    max_retries: int = DEFAULT_RETRIES,
    dry_run: bool = False,
    debug: bool = False,
) -> dict:
    """The whole single-PID pipeline as a function, not a CLI wrapper.

    Two independent generate-and-retry phases, not one combined call: Phase A
    (the five description fields + name/inputs/outputs + process-level
    entity ids) must pass before Phase B (the step list) is even attempted.
    Live testing found the combined version genuinely hard for a 14B model to
    converge on -- a retry aimed at fixing the steps would regress a
    description field that had already passed, and vice versa, because each
    retry regenerates the whole response from scratch rather than patching
    the flagged part. Splitting them means a Phase B retry cannot touch
    fields Phase A already locked in, and each phase gets its own full
    `max_retries` budget rather than splitting one budget across two
    unrelated kinds of failure.

    Returns a result dict rather than exiting, so scripts/run_batch.py can
    call this directly for many PIDs in one process instead of shelling out:

        {"pid", "status", "reason", "name", "confidence", "word_count",
         "step_count", "gate_count", "attempts", "sources"}

    attempts is the sum of both phases' attempt counts. status is one of:
    written, updated, rejected_generation (a phase exhausted its retries),
    rejected_validation (registry grounding failed), error (setup/model/IO
    failure), dry_run.
    """
    def result(status: str, **extra) -> dict:
        return {"pid": pid, "status": status, **extra}

    try:
        r = load(registries)
        node = load_taxonomy(pid)
        pains = spec_pain_points()
        group = domain_group(pid, r)
        if group is None:
            raise GenerationError(f"no registry domain group for {pid}")
        menu = build_menu(r, group)
    except (GenerationError, RegistryError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return result("error", reason=str(e))

    counts = ", ".join(f"{len(menu[k])} {k}" for k in MENU_REGISTRIES)
    print(f"{pid}  {node['l1']}")
    print(f"          {node['l2']}")
    print(f"  registry menu ({group}): {counts}, {len(pains)} §7 pain points")

    attempts = 1 + max(0, max_retries)

    # --- Phase A: description ---------------------------------------------
    print("  Phase A: description")
    note: str | None = None
    desc_reply: dict | None = None
    phase_a_attempts = 0
    for attempt in range(1, attempts + 1):
        phase_a_attempts = attempt
        prompt = build_description_prompt(node, menu, pains, retry_note=note)
        try:
            if mock:
                print(f"    MOCK: canned response, profile {mock_profile!r} "
                      f"(attempt {attempt}/{attempts}, no network call)")
                reply, raw = mock_description_response(menu, pains, mock_profile, attempt=attempt)
            else:
                print(f"    attempt {attempt}/{attempts}")
                reply, raw = call_ollama(prompt, host)
        except GenerationError as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return result("error", reason=str(e))

        reply_d = reply if isinstance(reply, dict) else {}
        ok_fields, counts_, weak, over = check_field_lengths(reply_d)

        if debug:
            print()
            print(f"  {'=' * 66}")
            print(f"  DEBUG: Phase A attempt {attempt}/{attempts} raw model response")
            print(f"  {'=' * 66}")
            print(raw)
            print(f"  {'-' * 66}")
            print("  per-field word counts:")
            for k in FIELD_KEYS:
                flag = " <- BELOW FLOOR" if k in weak else (" <- over advisory max" if k in over else "")
                print(f"    {FIELD_LABELS[k]:10s} {counts_[k]:3d} words{flag}")
                print(f"      {reply_d.get(k, '')}")
            print(f"  {'=' * 66}")
            print()

        if not ok_fields:
            print(f"    REJECTED (length): {len(weak)} field(s) below the "
                  f"{FIELD_FLOOR_WORDS}-word floor: "
                  f"{', '.join(FIELD_LABELS[k] for k in weak)}")
            if attempt < attempts:
                note = field_retry_note(counts_, weak)
                print("    retrying with a corrective note")
                continue
            reason = (f"{', '.join(FIELD_LABELS[k] for k in weak)} below the "
                     f"{FIELD_FLOOR_WORDS}-word floor")
            print()
            print(f"REJECT  {pid}  (Phase A: description)")
            print(f"        {reason}, after {attempts} attempt(s). Surfacing for human "
                  f"review rather than looping — the prompt or the registry coverage "
                  f"for this domain is the thing to look at, not the retry count.")
            print(f"\n  NOT WRITTEN. {Path(out).name} is unchanged.")
            return result("rejected_generation", reason=reason, attempts=attempt)

        if over:
            print(f"    note: {', '.join(FIELD_LABELS[k] for k in over)} over the "
                  f"{FIELD_MAX_WORDS}-word advisory max (not a gate condition)")
        desc_reply = reply_d
        print(f"    passed on attempt {attempt}/{attempts} "
              f"(all 5 fields >= {FIELD_FLOOR_WORDS} words)")
        break

    assert desc_reply is not None

    # --- Phase B: steps ------------------------------------------------------
    print("  Phase B: steps")
    note = None
    steps_reply: dict | None = None
    phase_b_attempts = 0
    gate_count = 0
    for attempt in range(1, attempts + 1):
        phase_b_attempts = attempt
        prompt = build_steps_prompt(node, menu, pains, retry_note=note)
        try:
            if mock:
                print(f"    MOCK: canned response, profile {mock_profile!r} "
                      f"(attempt {attempt}/{attempts}, no network call)")
                reply, raw = mock_steps_response(menu, mock_profile, attempt=attempt)
            else:
                print(f"    attempt {attempt}/{attempts}")
                reply, raw = call_ollama(prompt, host)
        except GenerationError as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return result("error", reason=str(e))

        reply_d = reply if isinstance(reply, dict) else {}
        ok_steps, step_problems, gate_count = check_steps_structure(reply_d)

        if debug:
            steps_raw = reply_d.get("steps")
            n_steps = len(steps_raw) if isinstance(steps_raw, list) else 0
            print()
            print(f"  {'=' * 66}")
            print(f"  DEBUG: Phase B attempt {attempt}/{attempts} raw model response")
            print(f"  {'=' * 66}")
            print(raw)
            print(f"  {'-' * 66}")
            print(f"  steps: {n_steps} returned, {gate_count} decision/exception gate(s)")
            for prob in step_problems:
                print(f"    <- {prob}")
            print(f"  {'=' * 66}")
            print()

        if not ok_steps:
            print(f"    REJECTED (steps): {len(step_problems)} problem(s)")
            for prob in step_problems:
                print(f"      - {prob}")
            if attempt < attempts:
                note = step_retry_note(step_problems)
                print("    retrying with a corrective note")
                continue
            reason = f"{len(step_problems)} step problem(s)"
            print()
            print(f"REJECT  {pid}  (Phase B: steps — description already passed, "
                  f"{phase_a_attempts} attempt(s), and is unaffected)")
            print(f"        {reason}, after {attempts} attempt(s). Surfacing for human "
                  f"review rather than looping — the prompt or the registry coverage "
                  f"for this domain is the thing to look at, not the retry count.")
            print(f"\n  NOT WRITTEN. {Path(out).name} is unchanged.")
            return result("rejected_generation", reason=reason,
                          attempts=phase_a_attempts + attempt)

        steps_reply = reply_d
        print(f"    passed on attempt {attempt}/{attempts} "
              f"({len(reply_d.get('steps', []))} steps, {gate_count} gate(s))")
        break

    assert steps_reply is not None

    proc, notes = assemble(node, {**desc_reply, "steps": steps_reply.get("steps", [])}, r, pains)
    for n in notes:
        print(f"  note: {n}")

    backstop_ok, words, advisory = check_description_length(proc)
    if not backstop_ok:
        msg = (f"assembled description is {words} words, under the "
              f"{DESC_FLOOR_WORDS}-word backstop, despite every field clearing "
              f"its own floor. This indicates a bug in assemble(), not a model "
              f"problem — do not retry.")
        print(f"  FATAL: {msg}")
        return result("error", reason=msg)
    if advisory:
        print(f"  note: {advisory} (advisory)")
    else:
        print(f"  description: {words} words (5 fields, all >= {FIELD_FLOOR_WORDS})")

    print()
    facts = vc.FactIndex(r)
    findings = vc.validate(proc, r, facts)
    vc.report(pid, findings)

    common = dict(
        name=proc["name"], confidence=proc["confidence"],
        word_count=len(proc["description"].split()),
        step_count=len(proc["steps"]), gate_count=gate_count,
        sources=len(proc["sources"]), attempts=phase_a_attempts + phase_b_attempts,
    )

    if findings:
        print()
        print(f"  NOT WRITTEN. {Path(out).name} is unchanged.")
        return result("rejected_validation",
                      reason=f"{len(findings)} ungrounded item(s)", **common)

    if dry_run:
        print("\n  --dry-run: validation passed, nothing written.")
        return result("dry_run", **common)

    try:
        action = write_process(proc, Path(out))
    except GenerationError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return result("error", reason=str(e), **common)
    print(f"\n  {action}: {pid} -> {Path(out).relative_to(REPO)}")
    return result(action, **common)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate one registry-constrained process.")
    ap.add_argument("--pid", required=True, help="e.g. RR-06-02-01")
    ap.add_argument("--mock", action="store_true",
                    help="use a canned response instead of calling Ollama (opens no socket)")
    ap.add_argument("--mock-profile", default="good",
                    choices=["good", "ungrounded-figure", "invented-system",
                             "invented-role", "short", "short-then-good",
                             "bad-steps", "bad-steps-then-good"],
                    help="which canned response to use with --mock")
    ap.add_argument("--host", default=OLLAMA_HOST)
    ap.add_argument("--registries", default=str(REGISTRY_DIR))
    ap.add_argument("--out", default=str(PROCESSES))
    ap.add_argument("--max-retries", type=int, default=DEFAULT_RETRIES,
                    dest="max_retries",
                    help=f"retries when a description field or the step list "
                         f"misses its requirement (default {DEFAULT_RETRIES})")
    ap.add_argument("--dry-run", action="store_true", help="validate but never write")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the prompt for this PID and exit — no model "
                         "call, no write. Ignores --mock.")
    ap.add_argument("--debug", action="store_true",
                    help="dump the full raw model response, per-field word "
                         "counts, the step-list summary, and the assembled "
                         "description for every attempt, mock or live")
    args = ap.parse_args()

    if args.show_prompt:
        try:
            r = load(args.registries)
            node = load_taxonomy(args.pid)
            pains = spec_pain_points()
            group = domain_group(args.pid, r)
            if group is None:
                raise GenerationError(f"no registry domain group for {args.pid}")
            menu = build_menu(r, group)
        except (GenerationError, RegistryError) as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 2
        counts = ", ".join(f"{len(menu[k])} {k}" for k in MENU_REGISTRIES)
        print(f"{args.pid}  {node['l1']}")
        print(f"          {node['l2']}")
        print(f"  registry menu ({group}): {counts}, {len(pains)} §7 pain points")
        desc_prompt = build_description_prompt(node, menu, pains)
        steps_prompt = build_steps_prompt(node, menu, pains)
        print("\n" + "-" * 70 + "\nPHASE A: DESCRIPTION\n" + "-" * 70)
        print(desc_prompt)
        print("\n" + "-" * 70 + "\nPHASE B: STEPS\n" + "-" * 70)
        print(steps_prompt)
        print("-" * 70)
        print(f"\n  --show-prompt: preview only (both phases). No model call, "
              f"{Path(args.out).name} untouched.")
        return 0

    res = generate_one(
        args.pid, mock=args.mock, mock_profile=args.mock_profile, host=args.host,
        registries=args.registries, out=args.out, max_retries=args.max_retries,
        dry_run=args.dry_run, debug=args.debug,
    )
    return 0 if res["status"] in ("written", "updated", "dry_run") else \
        (2 if res["status"] == "error" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
