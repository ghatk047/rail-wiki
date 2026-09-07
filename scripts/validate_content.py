#!/usr/bin/env python3
"""Phase D — the hard gate (BUILD-SPEC-v2 §4).

Takes one process object matching the §3 schema and rejects it if it names
anything the registries do not ground. All registry access goes through
registry_loader; this module holds no registry-reading code of its own and
never reads registries/*.json directly.

Checks
  1. structure        required §3 fields present, well-typed; confidence in
                      {high, medium, low}; sources[] non-empty http(s) URLs
  2. systems[]        every name resolves AND its source_id resolves AND the
                      two are the same registry entry; scope agrees
  3. regulatory_hook  every cite resolves in regulations.json
  4. actors[]         every role resolves in roles.json
  5. kpi_moved[]      every metric resolves in kpis.json
  6. prose figures    every digit-bearing span and every date in free text --
                      including every steps[].name/input/output -- is either
                      inside a resolved registry surface form or is present
                      in facts.json
  7. steps[]          (§3.1) each step's role/system/kpi resolves exactly like
                      the process-level equivalents; a decision_point:"Y"
                      step must carry a branch object

Grounding is the loader's answer, unmodified. A None from the loader is a
rejection; this module never softens one into a pass. The nearest-match
suggestions printed on a rejection are for the human reading the failure and
are never consulted when deciding pass or fail.

Known limits, stated rather than hidden:
  - Check 6 scans digits, month names and ISO/US dates. A spelled-out figure
    ("twelve hours") is not caught by default; --words adds spelled-out
    cardinals, at the cost of noise.
  - Grounding is token-level, not claim-level: it proves the number appears in
    a sourced fact, not that the process uses it to say the same thing. To
    narrow that gap a figure must ground against a fact in its OWN domain
    group; grounding only on another domain is reported, not accepted
    (--any-domain relaxes this). Run --explain to see what grounded what. A
    right-number/wrong-claim error inside one domain is still the human
    reviewer's job under §11.

Exit code: 0 = pass, 1 = rejected, 2 = usage/load error.

Usage:
    python3 scripts/validate_content.py path/to/process.json
    python3 scripts/validate_content.py process.json --explain
    python3 scripts/validate_content.py process.json --json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registry_loader import (  # noqa: E402
    REGISTRY_DIR,
    Entry,
    RegistryError,
    RegistryLoader,
    canon,
    canon_cite,
    load,
)

REQUIRED_FIELDS = [
    "pid", "name", "l1", "l2", "description", "actors", "inputs", "outputs",
    "systems", "regulatory_hook", "kpi_moved", "confidence", "sources",
]
LIST_FIELDS = ["actors", "inputs", "outputs", "systems", "regulatory_hook",
               "kpi_moved", "pain_points", "sources", "steps"]
VALID_YN = {"Y", "N"}
PROSE_FIELDS = ["name", "description", "inputs", "outputs", "pain_points",
                "operating_rule_ref"]
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_SCOPE = {"company_specific", "industry_typical"}

MONTHS = ("january|february|march|april|may|june|july|august|september|"
          "october|november|december")
MONTH_NUM = {m: i for i, m in enumerate(MONTHS.split("|"), start=1)}
DATE_RE = re.compile(
    rf"\b(?:{MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}\b"
    rf"|\b(?:{MONTHS})\s+\d{{4}}\b"
    rf"|\b\d{{4}}-\d{{2}}-\d{{2}}\b"
    rf"|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b",
    re.I,
)
NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|billion|million|bn|b\b|m\b)?", re.I)
WORD_NUM_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|twenty|thirty|forty|fifty|sixty|hundred|"
    r"thousand|million|billion)\b", re.I)


# --- figure and date keys -----------------------------------------------------
def canon_num(span: str) -> str:
    return re.sub(r"[^\d.]", "", span).strip(".")


def date_key(span: str) -> str | None:
    t = span.strip().lower().replace(",", "")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.fullmatch(rf"({MONTHS})\s+(\d{{1,2}})\s+(\d{{4}})", t)
    if m:
        return f"{m.group(3)}-{MONTH_NUM[m.group(1)]:02d}-{int(m.group(2)):02d}"
    m = re.fullmatch(rf"(\d{{1,2}})\s+({MONTHS})\s+(\d{{4}})", t)
    if m:
        return f"{m.group(3)}-{MONTH_NUM[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.fullmatch(rf"({MONTHS})\s+(\d{{4}})", t)
    if m:
        return f"{m.group(2)}-{MONTH_NUM[m.group(1)]:02d}"
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t)
    if m:
        y = m.group(3)
        y = ("20" + y) if len(y) == 2 else y
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def extract_keys(text: str) -> tuple[set[str], set[str]]:
    nums: set[str] = set()
    dates: set[str] = set()
    consumed: list[tuple[int, int]] = []
    for m in DATE_RE.finditer(text):
        consumed.append((m.start(), m.end()))
        k = date_key(m.group(0))
        if k:
            parts = k.split("-")
            for i in range(1, len(parts) + 1):
                dates.add("-".join(parts[:i]))
    for m in NUM_RE.finditer(text):
        if any(a <= m.start() and m.end() <= b for a, b in consumed):
            continue
        k = canon_num(m.group(0))
        if k:
            nums.add(k)
            if re.fullmatch(r"(19|20)\d{2}", k):
                dates.add(k)
    return nums, dates


class FactIndex:
    """Token-level view of facts.json, built from the loader's entries.

    Substring matching over concatenated digits is NOT safe: it lets "34" ride
    on a fact that only ever said "2034". Tokens are indexed individually and
    carry the domain group they came from.
    """

    def __init__(self, r: RegistryLoader):
        self.facts: tuple[Entry, ...] = r.all("facts")
        self.numbers: dict[str, list[int]] = {}
        self.dates: dict[str, list[int]] = {}
        for i, e in enumerate(self.facts):
            text = f"{e.key} {e.as_of or ''}"
            nums, dates = extract_keys(text)
            for k in nums:
                self.numbers.setdefault(k, []).append(i)
            for k in dates:
                self.dates.setdefault(k, []).append(i)

    def domain(self, i: int) -> str:
        return self.facts[i].domain

    def text(self, i: int) -> str:
        return self.facts[i].key


# --- findings -----------------------------------------------------------------
class Finding:
    def __init__(self, check: str, field: str, span: str, reason: str,
                 offset: int | None = None, context: str | None = None,
                 suggestions: list[str] | None = None):
        self.check, self.field, self.span, self.reason = check, field, span, reason
        self.offset, self.context, self.suggestions = offset, context, suggestions or []

    def as_dict(self) -> dict:
        return {"check": self.check, "field": self.field, "span": self.span,
                "reason": self.reason, "offset": self.offset,
                "context": self.context, "suggestions": self.suggestions}


def near(term: str, r: RegistryLoader, registry: str, n: int = 3) -> list[str]:
    """Human-facing hints only. Never consulted when deciding pass or fail."""
    return difflib.get_close_matches(canon(term), list(r.aliases(registry)), n=n, cutoff=0.55)


def context_of(text: str, start: int, end: int, pad: int = 45) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    return (("…" if a else "") + text[a:start] + "[[" + text[start:end] + "]]"
            + text[end:b] + ("…" if b < len(text) else ""))


# --- checks -------------------------------------------------------------------
def check_structure(p: dict, f: list[Finding]) -> None:
    for key in REQUIRED_FIELDS:
        if key not in p:
            f.append(Finding("structure", key, "<missing>", "required §3 field is absent"))
    for key in LIST_FIELDS:
        if key in p and not isinstance(p[key], list):
            f.append(Finding("structure", key, repr(p[key])[:60],
                             f"must be a list, got {type(p[key]).__name__}"))
    c = p.get("confidence")
    if c is not None and c not in VALID_CONFIDENCE:
        f.append(Finding("structure", "confidence", str(c),
                         f"must be one of {sorted(VALID_CONFIDENCE)}"))
    srcs = p.get("sources")
    if isinstance(srcs, list):
        if not srcs:
            f.append(Finding("structure", "sources", "[]",
                             "at least one source URL is required"))
        for i, s in enumerate(srcs):
            if not (isinstance(s, str) and s.startswith(("http://", "https://"))):
                f.append(Finding("structure", f"sources[{i}]", str(s),
                                 "must be an http(s) URL"))
    pid = p.get("pid")
    if isinstance(pid, str) and not re.fullmatch(r"RR-\d{2}-\d{2}-\d{2}", pid):
        f.append(Finding("structure", "pid", pid, "must match RR-{L1}-{L2}-{NN}"))


def _check_one_system(fld: str, s, r: RegistryLoader, f: list[Finding],
                      check: str = "systems") -> None:
    """One systems[] entry OR one steps[].system entry -- same object shape,
    same rules, so both call sites share this."""
    if not isinstance(s, dict):
        f.append(Finding(check, fld, repr(s)[:60],
                         "must be an object with name, scope, source_id"))
        return
    name, sid, scope = s.get("name"), s.get("source_id"), s.get("scope")

    if not name:
        f.append(Finding(check, fld + ".name", "<missing>", "required"))
        return

    denied = r.denial_for("systems", name)
    if denied:
        f.append(Finding(check, fld + ".name", name,
                         "named on the systems.json DO-NOT-USE list: " + denied))
        return

    by_name = r.get_system_by_name(name)
    by_id = r.get_system(sid) if sid else None

    if by_name is None:
        f.append(Finding(
            check, fld + ".name", name,
            "not found in registries/systems.json — no registry entry grounds "
            "this system name, so it cannot be written into a process",
            suggestions=near(name, r, "systems")))
    if sid and by_id is None:
        f.append(Finding(check, fld + ".source_id", str(sid),
                         "no entry in systems.json carries this source_id"))
    if not sid:
        f.append(Finding(check, fld + ".source_id", "<missing>",
                         "required — a system name must be bound to a registry id"))
    if by_name is not None and by_id is not None and by_name.entry_id != by_id.entry_id:
        # The name may be cross-registered; accept any candidate matching the id.
        if not any(c.entry_id == by_id.entry_id
                   for c in r.find_all("systems", name)):
            f.append(Finding(
                check, fld, f"{name} / {sid}",
                f"name and source_id disagree: name resolves to "
                f"{by_name.entry_id} ({by_name.key}), "
                f"source_id resolves to {by_id.key}"))
    resolved = by_id if by_id is not None else by_name
    if scope is not None and scope not in VALID_SCOPE:
        f.append(Finding(check, fld + ".scope", str(scope),
                         f"must be one of {sorted(VALID_SCOPE)}"))
    elif resolved is not None and scope and scope != resolved.scope:
        f.append(Finding(
            check, fld + ".scope", str(scope),
            f"registry records this system as {resolved.scope!r}; "
            f"the scope badge is the honesty mechanism and must agree"))


def check_systems(p: dict, r: RegistryLoader, f: list[Finding]) -> None:
    for i, s in enumerate(p.get("systems") or []):
        _check_one_system(f"systems[{i}]", s, r, f)


def check_steps(p: dict, r: RegistryLoader, f: list[Finding]) -> None:
    """§3.1: each step's role/system/kpi resolves exactly like the
    process-level equivalents; a decision_point:"Y" step must carry a branch.
    pain_point is not independently re-checked here, matching the top-level
    pain_points[] policy -- it is trusted by construction (assemble() only
    ever writes verbatim §7 text into it)."""
    for i, s in enumerate(p.get("steps") or []):
        fld = f"steps[{i}]"
        if not isinstance(s, dict):
            f.append(Finding("steps", fld, repr(s)[:60], "must be an object"))
            continue

        for req in ("step", "name", "role", "input", "output", "decision_point", "exception"):
            if req not in s or s.get(req) in (None, ""):
                f.append(Finding("steps", f"{fld}.{req}", "<missing>",
                                 "required step field is absent"))

        role = s.get("role")
        if isinstance(role, str) and role:
            denied = r.denial_for("roles", role)
            if denied:
                f.append(Finding("steps", f"{fld}.role", role,
                                 "named on the roles.json DO-NOT-USE list: " + denied))
            elif r.get_role(role) is None:
                f.append(Finding("steps", f"{fld}.role", role,
                                 "not found in registries/roles.json",
                                 suggestions=near(role, r, "roles")))
        elif role is not None and not isinstance(role, str):
            f.append(Finding("steps", f"{fld}.role", repr(role)[:60], "must be a string"))

        system = s.get("system")
        if system is not None:
            _check_one_system(f"{fld}.system", system, r, f, check="steps")

        kpi = s.get("kpi")
        if isinstance(kpi, str) and kpi:
            denied = r.denial_for("kpis", kpi)
            if denied:
                f.append(Finding("steps", f"{fld}.kpi", kpi,
                                 "named on the kpis.json DO-NOT-USE list: " + denied))
            elif r.get_kpi(kpi) is None:
                f.append(Finding("steps", f"{fld}.kpi", kpi,
                                 "not found in registries/kpis.json",
                                 suggestions=near(kpi, r, "kpis")))
        elif kpi is not None and not isinstance(kpi, str):
            f.append(Finding("steps", f"{fld}.kpi", repr(kpi)[:60], "must be a string"))

        dp = str(s.get("decision_point", "")).strip().upper()
        exc = str(s.get("exception", "")).strip().upper()
        if s.get("decision_point") is not None and dp not in VALID_YN:
            f.append(Finding("steps", f"{fld}.decision_point", str(s.get("decision_point")),
                             'must be "Y" or "N"'))
        if s.get("exception") is not None and exc not in VALID_YN:
            f.append(Finding("steps", f"{fld}.exception", str(s.get("exception")),
                             'must be "Y" or "N"'))

        branch = s.get("branch")
        if dp == "Y" and not (isinstance(branch, dict) and branch.get("label") and branch.get("to")):
            f.append(Finding(
                "steps", f"{fld}.branch", repr(branch)[:60] if branch else "<missing>",
                'decision_point:"Y" requires a branch object with "label" and '
                '"to" -- a diamond with no alternate path is not a decision'))


def _check_list(p: dict, key: str, registry: str, label: str, getter,
                r: RegistryLoader, f: list[Finding]) -> None:
    for i, v in enumerate(p.get(key) or []):
        fld = f"{key}[{i}]"
        if not isinstance(v, str):
            f.append(Finding(label, fld, repr(v)[:60], "must be a string"))
            continue
        denied = r.denial_for(registry, v)
        if denied:
            f.append(Finding(label, fld, v,
                             f"named on the {registry}.json DO-NOT-USE list: {denied}"))
            continue
        if getter(v) is None:
            f.append(Finding(label, fld, v,
                             f"not found in registries/{registry}.json",
                             suggestions=near(v, r, registry)))


def home_domain(pid: str, facts: FactIndex) -> str | None:
    m = re.match(r"^RR-(\d{2})-", pid or "")
    if not m:
        return None
    want = f"domain_{m.group(1)}_"
    return next((e.domain for e in facts.facts if e.domain.startswith(want)), None)


def check_prose_figures(p: dict, r: RegistryLoader, facts: FactIndex,
                        f: list[Finding], words: bool = False,
                        grounded: list | None = None,
                        any_domain: bool = False) -> None:
    grounded = grounded if grounded is not None else []
    home = home_domain(p.get("pid", ""), facts)

    surfaces = sorted(
        {k for reg in ("systems", "regulations", "roles", "kpis")
         for k in r.aliases(reg) if re.search(r"\d", k)},
        key=len, reverse=True,
    )

    chunk_sources: list[tuple[str, str]] = []
    for key in PROSE_FIELDS:
        if key not in p:
            continue
        val = p[key]
        chunk_sources += [(key, val)] if isinstance(val, str) else [
            (f"{key}[{i}]", v) for i, v in enumerate(val) if isinstance(v, str)
        ] if isinstance(val, list) else []
    # steps[].name / .input / .output are free prose too -- a fabricated
    # figure can hide in a step exactly as easily as in the description.
    for i, s in enumerate(p.get("steps") or []):
        if not isinstance(s, dict):
            continue
        for sub in ("name", "input", "output"):
            v = s.get(sub)
            if isinstance(v, str) and v:
                chunk_sources.append((f"steps[{i}].{sub}", v))

    for fld, text in chunk_sources:
            low = text.lower()
            covered: list[tuple[int, int]] = []
            for surf in surfaces:
                st = 0
                while (k := low.find(surf, st)) >= 0:
                    covered.append((k, k + len(surf)))
                    st = k + 1
            for cite in p.get("regulatory_hook") or []:
                if isinstance(cite, str) and r.get_regulation(cite) is not None:
                    for pat in {canon(cite), canon_cite(cite)}:
                        st = 0
                        while (k := low.find(pat, st)) >= 0:
                            covered.append((k, k + len(pat)))
                            st = k + 1

            def is_covered(a: int, b: int) -> bool:
                return any(ca <= a and b <= cb for ca, cb in covered)

            spans: list[tuple[int, int, str, str]] = []
            for m in DATE_RE.finditer(text):
                spans.append((m.start(), m.end(), m.group(0), "date"))
            for m in NUM_RE.finditer(text):
                if not any(a <= m.start() and m.end() <= b for a, b, _, _ in spans):
                    spans.append((m.start(), m.end(), m.group(0), "figure"))
            if words:
                for m in WORD_NUM_RE.finditer(text):
                    spans.append((m.start(), m.end(), m.group(0), "figure (spelled out)"))

            for a, b, span, kind in sorted(spans):
                if is_covered(a, b):
                    continue
                k = date_key(span) if kind == "date" else canon_num(span)
                table = facts.dates if kind == "date" else facts.numbers
                cands = list(table.get(k, [])) if k else []
                if not cands and kind != "date" and k and re.fullmatch(r"(19|20)\d{2}", k):
                    cands = list(facts.dates.get(k, []))

                own = [i for i in cands if facts.domain(i) == home]
                span_r = span.strip()

                if own:
                    grounded.append((fld, span_r, own[0]))
                    continue
                if cands and any_domain:
                    grounded.append((fld, span_r, cands[0]))
                    continue
                if cands:
                    i = cands[0]
                    f.append(Finding(
                        "figures", fld, span_r,
                        f"{kind} appears in facts.json only under "
                        f"{facts.domain(i)}, not under this process's own domain "
                        f"({home or 'unknown'}) — the number is real but it is "
                        f"grounding on an unrelated claim: "
                        f"\"{facts.text(i)[:110]}…\". Source it for this domain "
                        f"or drop the span (--any-domain to allow).",
                        offset=a, context=context_of(text, a, b)))
                    continue
                f.append(Finding(
                    "figures", fld, span_r,
                    f"ungrounded {kind} — not inside any registry entity and not "
                    f"present in registries/facts.json",
                    offset=a, context=context_of(text, a, b)))


def validate(p: dict, r: RegistryLoader, facts: FactIndex, words: bool = False,
             grounded: list | None = None, any_domain: bool = False) -> list[Finding]:
    f: list[Finding] = []
    check_structure(p, f)
    check_systems(p, r, f)
    _check_list(p, "regulatory_hook", "regulations", "regulations", r.get_regulation, r, f)
    _check_list(p, "actors", "roles", "roles", r.get_role, r, f)
    _check_list(p, "kpi_moved", "kpis", "kpis", r.get_kpi, r, f)
    check_steps(p, r, f)
    check_prose_figures(p, r, facts, f, words=words, grounded=grounded,
                        any_domain=any_domain)
    return f


# --- reporting ----------------------------------------------------------------
ORDER = ["structure", "systems", "regulations", "roles", "kpis", "steps", "figures"]
TITLES = {
    "structure": "SCHEMA",
    "systems": "UNGROUNDED SYSTEM",
    "regulations": "UNGROUNDED REGULATORY HOOK",
    "roles": "UNGROUNDED ROLE",
    "kpis": "UNGROUNDED KPI",
    "steps": "STEP PROBLEM",
    "figures": "UNGROUNDED FIGURE OR DATE",
}


def report(pid: str, findings: list[Finding]) -> None:
    if not findings:
        print(f"PASS  {pid}")
        print("      every system, citation, role, KPI and figure resolves to a registry entry.")
        return
    print(f"REJECT  {pid}")
    print(f"        {len(findings)} ungrounded item(s). Nothing is written until these resolve.")
    for check in ORDER:
        group = [f for f in findings if f.check == check]
        if not group:
            continue
        print()
        print(f"  ── {TITLES[check]} ──")
        for f in group:
            loc = f"{f.field}" + (f" @ char {f.offset}" if f.offset is not None else "")
            print(f"    {loc}")
            print(f"      span   : {f.span!r}")
            print(f"      why    : {f.reason}")
            if f.context:
                print(f"      in     : {f.context}")
            if f.suggestions:
                print(f"      nearest: {', '.join(repr(s) for s in f.suggestions)}")
    print()
    print("  Registry-first: add a sourced registry entry, or remove the span. "
          "Do not soften the prose to slip past the gate.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Reject any process containing ungrounded entities.")
    ap.add_argument("process", help="JSON file: one process object (or a list of them)")
    ap.add_argument("--registries", default=str(REGISTRY_DIR), help="registry directory")
    ap.add_argument("--words", action="store_true",
                    help="also flag spelled-out cardinals (noisier)")
    ap.add_argument("--any-domain", action="store_true", dest="any_domain",
                    help="allow a figure to ground on a fact from any domain")
    ap.add_argument("--strict-literal", action="store_true", dest="strict_literal",
                    help="disable the loader's alias derivation (rejects 'I-ETMS')")
    ap.add_argument("--explain", action="store_true",
                    help="show which fact grounded each figure that passed")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output")
    args = ap.parse_args()

    path = Path(args.process)
    if not path.exists():
        print(f"FATAL: no such file: {path}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FATAL: {path} is not valid JSON: {e}", file=sys.stderr)
        return 2

    procs = doc if isinstance(doc, list) else [doc]
    if not all(isinstance(p, dict) for p in procs):
        print("FATAL: expected a process object or a list of them", file=sys.stderr)
        return 2

    try:
        r = load(args.registries, strict_literal=args.strict_literal)
    except RegistryError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    facts = FactIndex(r)

    results, rejected = [], False
    for p in procs:
        grounded: list = []
        findings = validate(p, r, facts, words=args.words, grounded=grounded,
                            any_domain=args.any_domain)
        rejected = rejected or bool(findings)
        results.append({"pid": p.get("pid", "<no pid>"),
                        "passed": not findings,
                        "findings": [f.as_dict() for f in findings],
                        "grounded_figures": [
                            {"field": g[0], "span": g[1], "fact": facts.text(g[2])}
                            for g in grounded]})
        if not args.as_json:
            report(p.get("pid", "<no pid>"), findings)
            if args.explain and grounded:
                print()
                print("  ── FIGURES GROUNDED (token match — verify the claim, not just the number) ──")
                for fld, span, idx in grounded:
                    t = facts.text(idx)
                    print(f"    {fld}: {span!r}")
                    print(f"      grounded by: {t[:150]}{'…' if len(t) > 150 else ''}")

    if args.as_json:
        print(json.dumps({"rejected": rejected, "results": results},
                         indent=2, ensure_ascii=False))
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
