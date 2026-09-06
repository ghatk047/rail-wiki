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

So those fields ground by construction. What the model genuinely authors is
prose: description, inputs and outputs. That prose is exactly where a
fabricated figure or date can still appear, and it is what validate_content.py
scans before anything is written.

An id the model invents does not resolve, and the process is assembled with
the unresolved id left visible so the gate rejects it rather than the
generator quietly dropping it. Nothing is written on a rejection.

Description length is enforced here, not in validate_content.py. A rejection
from the validator means exactly one thing — the registries do not ground
something — and that single meaning is what makes it trustworthy as the §4
gate. Length is a prose-quality condition, so it lives in Phase C with a
bounded retry that tells the model what was wrong.

Model
-----
Ollama REST at http://localhost:11434, model qwen2.5:14b-instruct, falling
back to qwen2.5:latest if the preferred tag is not pulled. --mock replaces the
call entirely with a canned response and never opens a socket. --show-prompt
prints the prompt and exits before either path is reached.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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

# Description length. The floor is a hard generator-side condition; the band is
# advisory. Deliberately a floor and not the full band: a gate that demands 120
# words gets 120 words, filler included, and padded prose is worse than short
# prose for a wiki whose value is being recognisable to a rail person.
DESC_FLOOR_WORDS = 100
DESC_BAND = (120, 200)
DEFAULT_RETRIES = 2

MENU_REGISTRIES = ("systems", "roles", "regulations", "kpis")


class GenerationError(RuntimeError):
    pass


# --- inputs -------------------------------------------------------------------
def load_taxonomy(pid: str) -> dict:
    if not TAXONOMY.exists():
        raise GenerationError(
            f"{TAXONOMY.relative_to(REPO)} not found — run scripts/build_taxonomy.py first")
    doc = json.loads(TAXONOMY.read_text(encoding="utf-8"))
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


def build_prompt(node: dict, menu: dict[str, list[Entry]], pains: list[str],
                 retry_note: str | None = None) -> str:
    """The prompt sent to the model.

    The description requirement lives in its own section, not as a value in
    the output template: a template value reads as a format hint and gets
    ignored. It is also kept apart from the "leave statistics out" guidance,
    because a length target and a suppression instruction in one sentence
    fight each other and the suppression wins.
    """
    retry = f"\n{retry_note}\n" if retry_note else ""
    return f"""You are documenting one business process for a US Class I freight railroad.

PROCESS
  PID : {node['pid']}
  L1  : {node['l1']}
  L2  : {node['l2']}

ENTITIES
You may reference the entities below ONLY, and ONLY by their id. Do not write
a system name, job title, regulation citation or metric name anywhere in your
prose. Do not invent ids. If nothing in a list fits, return an empty list.

{render_menu(menu, pains)}

WRITING THE DESCRIPTION
This is the main body of work in this task. Write 120-200 words, covering all
five of the following, in this order, at roughly 25-40 words each:

  1. TRIGGER    What starts this work, and how the need becomes visible to
                the people who act on it.
  2. SEQUENCE   Who does what, in order, from trigger through to completion.
  3. JUDGEMENT  The decision or exception that makes this process non-trivial
                — the point where experience matters — and what happens on
                each branch.
  4. HANDOFF    What leaves this process, and who picks it up next.
  5. DONE       The condition that means the work is complete and can be
                closed out.

Write about mechanism rather than measurement: explain how the work is
carried out and what governs it. Figures, percentages and dates are not what
makes a description good here, and a description is better without them
unless one is genuinely central to the process.

Plain declarative sentences. No consulting register, no filler, no restating
the process name back. If you find yourself short of 120 words, you have
under-described one of the five elements above — go back and expand it rather
than padding.
{retry}
OUTPUT
Return ONE JSON object, nothing else:

{{
  "name": "specific process name, 4-12 words, no numbers",
  "description": "the 120-200 word description specified above",
  "inputs": ["2-4 short noun phrases"],
  "outputs": ["2-4 short noun phrases"],
  "system_ids": ["SYS-..."],
  "role_ids": ["ROLE-..."],
  "regulation_ids": ["REG-..."],
  "kpi_ids": ["KPI-..."],
  "pain_point_ids": ["P01"],
  "confidence": "high | medium | low"
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


def call_ollama(prompt: str, host: str) -> dict:
    model = pick_model(host)
    print(f"  model: {model} @ {host}")
    out = _http_json(f"{host}/api/generate", {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2},
    })
    raw = out.get("response", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise GenerationError(f"model did not return JSON: {e}\n---\n{raw[:800]}") from e


# --- mock ---------------------------------------------------------------------
MOCK_PROSE = {
    "good": (
        "The work runs on a scheduled cycle rather than on demand. A planned "
        "cycle is published for each subdivision, the field team executes "
        "against it, and the results are scored and routed for disposition "
        "before the next cycle opens. Findings are classified by severity: the "
        "most serious generate an immediate operating restriction, which is "
        "placed into effect before any further movement is authorised over the "
        "affected segment. Less serious findings are queued into the planned "
        "maintenance programme and tracked to closure. Every restriction "
        "imposed, changed or lifted is recorded so that the field condition, "
        "the dispatching system and the onboard enforcement data stay "
        "consistent with one another. Where a finding touches the physical "
        "characteristics of the track, the change is carried through into the "
        "downstream data files so that enforcement matches the railroad as "
        "built. Disagreement between what the field recorded and what the "
        "system holds is treated as a defect in its own right and is reconciled "
        "before the cycle is closed out."
    ),
    "short": (
        "The crew performs the required test before departure and records the "
        "result. Any defect found is repaired or the equipment is set out."
    ),
    # Deliberately over the length floor, so that it reaches the validator and
    # exercises the grounding path rather than being turned back on length.
    "ungrounded-figure": (
        "The work runs on a scheduled cycle rather than on demand. A planned "
        "cycle is published for each subdivision, the field team executes "
        "against it, and the results are scored and routed for disposition "
        "before the next cycle opens. Automated classification cut triage time "
        "by 34% and clears 1,200 exceptions per week across the region. Since "
        "March 2019 the programme has run continuously on all main line track. "
        "Findings are classified by severity: the most serious generate an "
        "immediate operating restriction, which is placed into effect before "
        "any further movement is authorised over the affected segment. Less "
        "serious findings are queued into the planned maintenance programme and "
        "tracked to closure. Every restriction imposed, changed or lifted is "
        "recorded so that the field condition and the systems that govern "
        "movement stay consistent with one another, and any disagreement "
        "between them is reconciled before the cycle is closed out."
    ),
}


def mock_response(menu: dict[str, list[Entry]], pains: list[str], profile: str,
                  attempt: int = 1) -> dict:
    """A canned model reply. Opens no socket."""
    sys_ids = [e.entry_id for e in menu["systems"][:2]]
    role_ids = [e.entry_id for e in menu["roles"][:3]]
    reg_ids = [e.entry_id for e in menu["regulations"][:1]]
    kpi_ids = [e.entry_id for e in menu["kpis"][:1]]

    if profile == "invented-system":
        sys_ids = ["SYS-D06-09"] + sys_ids[:1]
    if profile == "invented-role":
        role_ids = ["ROLE-D06-99"] + role_ids[:2]

    prose_key = profile
    if profile == "short-then-good":
        prose_key = "short" if attempt == 1 else "good"
    return {
        "name": "Scheduled Inspection Cycle and Exception Disposition",
        "description": MOCK_PROSE.get(prose_key, MOCK_PROSE["good"]),
        "inputs": ["Published inspection cycle plan", "Prior exception history"],
        "outputs": ["Scored exception list", "Operating restriction request"],
        "system_ids": sys_ids,
        "role_ids": role_ids,
        "regulation_ids": reg_ids,
        "kpi_ids": kpi_ids,
        "pain_point_ids": ["P07"] if len(pains) >= 7 else ([f"P{len(pains):02d}"] if pains else []),
        "confidence": "medium",
    }


# --- assembly -----------------------------------------------------------------
def assemble(node: dict, reply: dict, r, pains: list[str]) -> tuple[dict, list[str]]:
    """Build the §3 process object from registry entries, not from model text."""
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

    proc = {
        "pid": node["pid"],
        "name": str(reply.get("name") or node["l2"]).strip(),
        "l1": node["l1"],
        "l2": node["l2"],
        "description": str(reply.get("description") or "").strip(),
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
    }

    return proc, notes


def check_description_length(proc: dict) -> tuple[bool, int, str | None]:
    """(meets_floor, words, advisory). Below the floor is not writable."""
    words = len((proc.get("description") or "").split())
    lo, hi = DESC_BAND
    if words < DESC_FLOOR_WORDS:
        return False, words, None
    if words < lo:
        return True, words, f"description is {words} words, under the §3 band of {lo}-{hi}"
    if words > hi:
        return True, words, f"description is {words} words, over the §3 band of {lo}-{hi}"
    return True, words, None


def retry_note(words: int) -> str:
    lo, hi = DESC_BAND
    return (
        f"PREVIOUS ATTEMPT REJECTED\n"
        f"Your last description was {words} words. The requirement is {lo}-{hi}.\n"
        f"It was rejected for length alone, not for content. Do not pad it. Work\n"
        f"through TRIGGER, SEQUENCE, JUDGEMENT, HANDOFF and DONE in turn and give\n"
        f"each of the five 25-40 words of real detail — at least one of them was\n"
        f"left thin or skipped entirely last time."
    )


def _by_id(r, registry: str, entry_id: str) -> Entry | None:
    if not isinstance(entry_id, str):
        return None
    want = entry_id.strip().upper()
    return next((e for e in r.all(registry) if e.entry_id.upper() == want), None)


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


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate one registry-constrained process.")
    ap.add_argument("--pid", required=True, help="e.g. RR-06-02-01")
    ap.add_argument("--mock", action="store_true",
                    help="use a canned response instead of calling Ollama (opens no socket)")
    ap.add_argument("--mock-profile", default="good",
                    choices=["good", "ungrounded-figure", "invented-system",
                             "invented-role", "short", "short-then-good"],
                    help="which canned response to use with --mock")
    ap.add_argument("--host", default=OLLAMA_HOST)
    ap.add_argument("--registries", default=str(REGISTRY_DIR))
    ap.add_argument("--out", default=str(PROCESSES))
    ap.add_argument("--max-retries", type=int, default=DEFAULT_RETRIES,
                    dest="max_retries",
                    help=f"retries when the description misses the "
                         f"{DESC_FLOOR_WORDS}-word floor (default {DEFAULT_RETRIES})")
    ap.add_argument("--dry-run", action="store_true", help="validate but never write")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the prompt for this PID and exit — no model "
                         "call, no write. Ignores --mock.")
    args = ap.parse_args()

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

    prompt = build_prompt(node, menu, pains)
    if args.show_prompt:
        # Preview only. Exits before any model call and before any write, so
        # a prompt can be inspected for any PID at zero cost.
        print("\n" + "-" * 70 + "\n" + prompt + "\n" + "-" * 70)
        print(f"\n  --show-prompt: preview only. No model call, "
              f"{Path(args.out).name} untouched.")
        return 0

    attempts = 1 + max(0, args.max_retries)
    note: str | None = None
    proc = None
    for attempt in range(1, attempts + 1):
        prompt = build_prompt(node, menu, pains, retry_note=note)
        try:
            if args.mock:
                print(f"  MOCK: canned response, profile {args.mock_profile!r} "
                      f"(attempt {attempt}/{attempts}, no network call)")
                reply = mock_response(menu, pains, args.mock_profile, attempt=attempt)
            else:
                print(f"  attempt {attempt}/{attempts}")
                reply = call_ollama(prompt, args.host)
        except GenerationError as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 2

        proc, notes = assemble(node, reply, r, pains)
        for n in notes:
            print(f"  note: {n}")

        ok, words, advisory = check_description_length(proc)
        if ok:
            if advisory:
                print(f"  note: {advisory} (advisory)")
            else:
                print(f"  description: {words} words")
            break
        print(f"  REJECTED (length): description is {words} words, "
              f"floor is {DESC_FLOOR_WORDS}")
        if attempt < attempts:
            note = retry_note(words)
            print("  retrying with a corrective note")
        else:
            print()
            print(f"REJECT  {node['pid']}")
            print(f"        description did not reach {DESC_FLOOR_WORDS} words in "
                  f"{attempts} attempt(s). Surfacing for human review rather than "
                  f"looping — the prompt or the registry coverage for this domain "
                  f"is the thing to look at, not the retry count.")
            print(f"\n  NOT WRITTEN. {Path(args.out).name} is unchanged.")
            return 1

    print()
    facts = vc.FactIndex(r)
    findings = vc.validate(proc, r, facts)
    vc.report(proc["pid"], findings)

    if findings:
        print()
        print(f"  NOT WRITTEN. {Path(args.out).name} is unchanged.")
        return 1

    if args.dry_run:
        print("\n  --dry-run: validation passed, nothing written.")
        return 0

    try:
        action = write_process(proc, Path(args.out))
    except GenerationError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    print(f"\n  {action}: {args.pid} -> {Path(args.out).relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
