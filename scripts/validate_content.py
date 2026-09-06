#!/usr/bin/env python3
"""Phase D — the hard gate (BUILD-SPEC-v2 §4).

Takes one process object matching the §3 schema, loads all five registries,
and rejects the process if it names anything the registries do not ground.

Checks
  1. structure        required §3 fields present, well-typed; confidence in
                      {high, medium, low}; sources[] non-empty http(s) URLs
  2. systems[]        every name resolves in systems.json AND its source_id
                      resolves AND the two refer to the same entry; scope
                      agrees with the registry
  3. regulatory_hook  every cite resolves in regulations.json
  4. actors[]         every role resolves in roles.json
  5. kpi_moved[]      every metric resolves in kpis.json
  6. prose figures    every digit-bearing span and every date in free text is
                      either inside a resolved registry surface form or is
                      present in facts.json

Denylist: entries under `not_yet_sourced_do_not_use` in any registry are
explicit denials, not grounding. Naming one is a rejection, and the denial
text is quoted back.

Matching is on normalised text with a documented alias set: a registry name
"I-ETMS (Interoperable Electronic Train Management System)" grounds both
"I-ETMS" and the expansion; " / "-separated roles and KPIs ground each side;
regulation cites ground with and without the word "Part".

Known limits, stated rather than hidden:
  - Check 6 scans digits, month names and ISO/US dates. A spelled-out figure
    ("twelve hours") is not caught by default; --words adds spelled-out
    cardinals, at the cost of noise.
  - Grounding is token-level, not claim-level: it proves the number appears in
    a sourced fact, not that the process uses it to say the same thing. To
    narrow that gap a figure must ground against a fact in its OWN domain
    group; grounding only on another domain's fact is reported, not accepted
    (--any-domain relaxes this). Run --explain to see what grounded what. A
    right-number/wrong-claim error inside one domain is still the human
    reviewer's job under §11.

Exit code: 0 = pass, 1 = rejected, 2 = usage/load error.

Usage:
    python3 scripts/validate_content.py path/to/process.json
    python3 scripts/validate_content.py process.json --json
    python3 scripts/validate_content.py process.json --words
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY_DIR = REPO / "registries"
DENY_GROUP = "not_yet_sourced_do_not_use"

REQUIRED_FIELDS = [
    "pid", "name", "l1", "l2", "description", "actors", "inputs", "outputs",
    "systems", "regulatory_hook", "kpi_moved", "confidence", "sources",
]
LIST_FIELDS = ["actors", "inputs", "outputs", "systems", "regulatory_hook",
               "kpi_moved", "pain_points", "sources"]
PROSE_FIELDS = ["name", "description", "inputs", "outputs", "pain_points",
                "operating_rule_ref"]
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_SCOPE = {"company_specific", "industry_typical"}

# --- span detection -----------------------------------------------------------
MONTHS = ("january|february|march|april|may|june|july|august|september|"
          "october|november|december")
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


def norm(s: str) -> str:
    """Lowercase, strip punctuation noise, collapse whitespace."""
    s = s.lower().replace("—", " ").replace("–", " ").replace("’", "'")
    s = re.sub(r"[,\.\;\:]+$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def norm_cite(s: str) -> str:
    """Normalise a regulatory citation so §-forms and Part-forms unify."""
    s = norm(s)
    s = s.replace("§", " ").replace("u.s.c.", "usc").replace("c.f.r.", "cfr")
    s = re.sub(r"\bparts?\b", " ", s)
    s = re.sub(r"[,\(\)]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


MONTH_NUM = {m: i for i, m in enumerate(
    "january february march april may june july august september october "
    "november december".split(), start=1)}


def canon_num(span: str) -> str:
    """Canonical key for a figure: digits and decimal point only."""
    return re.sub(r"[^\d.]", "", span).strip(".")


def date_key(span: str) -> str | None:
    """Canonical YYYY[-MM[-DD]] key for a date span, most precise first."""
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
    """(figure keys, date keys) present in a block of text."""
    nums: set[str] = set()
    dates: set[str] = set()
    consumed: list[tuple[int, int]] = []
    for m in DATE_RE.finditer(text):
        consumed.append((m.start(), m.end()))
        k = date_key(m.group(0))
        if k:
            dates.add(k)
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


# --- registry loading ---------------------------------------------------------
class Registries:
    def __init__(self, path: Path):
        self.path = path
        self.raw: dict[str, dict] = {}
        self.deny: dict[str, list[str]] = {}
        for name in ("systems", "regulations", "roles", "kpis", "facts"):
            f = path / f"{name}.json"
            if not f.exists():
                sys.exit(f"FATAL: registry not found: {f}")
            try:
                self.raw[name] = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                sys.exit(f"FATAL: {f} is not valid JSON: {e}")
            self.deny[name] = [
                e for e in self.raw[name].get(DENY_GROUP, []) if isinstance(e, str)
            ]

        self.systems = list(self._entries("systems"))
        self.regulations = list(self._entries("regulations"))
        self.roles = list(self._entries("roles"))
        self.kpis = list(self._entries("kpis"))
        self.facts = list(self._entries("facts"))

        self.sys_by_name: dict[str, dict] = {}
        self.sys_by_id: dict[str, dict] = {}
        for _, e in self.systems:
            self.sys_by_id[e["source_id"].upper()] = e
            for a in self._sys_aliases(e["name"]):
                self.sys_by_name.setdefault(a, e)

        self.reg_by_cite: dict[str, dict] = {}
        for _, e in self.regulations:
            for a in self._cite_aliases(e["cite"]):
                self.reg_by_cite.setdefault(a, e)

        self.role_by_name: dict[str, dict] = {}
        for _, e in self.roles:
            for a in self._slash_aliases(e["role"]):
                self.role_by_name.setdefault(a, e)

        self.kpi_by_name: dict[str, dict] = {}
        for _, e in self.kpis:
            for a in self._slash_aliases(e["kpi"]):
                self.kpi_by_name.setdefault(a, e)

        self.fact_texts = [(e.get("fact", ""), e.get("as_of", "")) for _, e in self.facts]
        self.fact_domain = [g for g, _ in self.facts]
        # Token-level index of every figure and date that facts.json actually
        # grounds. Substring matching over concatenated digits is NOT safe:
        # it lets "34" ride on a fact that only ever said "2034".
        self.fact_numbers: dict[str, list[int]] = {}
        self.fact_dates: dict[str, list[int]] = {}
        for i, (fact, as_of) in enumerate(self.fact_texts):
            nums, dates = extract_keys(fact + " " + (as_of or ""))
            for k in nums:
                self.fact_numbers.setdefault(k, []).append(i)
            for k in dates:
                self.fact_dates.setdefault(k, []).append(i)

        # Every surface form that carries a digit — used to exempt grounded
        # entity mentions from the prose figure scan.
        self.digit_surfaces: list[str] = sorted(
            {a for a in list(self.sys_by_name) + list(self.reg_by_cite)
             + list(self.role_by_name) + list(self.kpi_by_name) if re.search(r"\d", a)},
            key=len, reverse=True,
        )

    def _entries(self, name: str):
        for group, items in self.raw[name].items():
            if group.startswith("_") or group == DENY_GROUP or not isinstance(items, list):
                continue
            for e in items:
                if isinstance(e, dict):
                    yield group, e

    @staticmethod
    def _sys_aliases(name: str) -> list[str]:
        out = {norm(name)}
        m = re.match(r"^(.*?)\s*\((.+)\)\s*$", name)
        if m:
            out.add(norm(m.group(1)))
            out.add(norm(m.group(2)))
        return [a for a in out if a]

    @staticmethod
    def _slash_aliases(name: str) -> list[str]:
        out = {norm(name)}
        for part in re.split(r"\s+/\s+", name):
            out.add(norm(part))
        m = re.match(r"^(.*?)\s*\((.+)\)\s*$", name)
        if m:
            out.add(norm(m.group(1)))
            out.add(norm(m.group(2)))
        return [a for a in out if a]

    @staticmethod
    def _cite_aliases(cite: str) -> list[str]:
        out = {norm_cite(cite), norm(cite)}
        m = re.match(r"^(.*?)\s*\((.+)\)\s*$", cite)
        if m:
            out.add(norm_cite(m.group(1)))
        return [a for a in out if a]

    def deny_hit(self, registry: str, text: str) -> str | None:
        t = norm(text)
        for d in self.deny.get(registry, []):
            if t and t in norm(d):
                return d
        return None


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


def near(term: str, pool, n: int = 3) -> list[str]:
    return difflib.get_close_matches(norm(term), list(pool), n=n, cutoff=0.55)


def context_of(text: str, start: int, end: int, pad: int = 45) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    return (("…" if a else "") + text[a:start] + "[[" + text[start:end] + "]]"
            + text[end:b] + ("…" if b < len(text) else ""))


# --- checks -------------------------------------------------------------------
def check_structure(p: dict, f: list[Finding]) -> None:
    for key in REQUIRED_FIELDS:
        if key not in p:
            f.append(Finding("structure", key, "<missing>",
                             "required §3 field is absent"))
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
        f.append(Finding("structure", "pid", pid,
                         "must match RR-{L1}-{L2}-{NN}"))


def check_systems(p: dict, r: Registries, f: list[Finding]) -> None:
    for i, s in enumerate(p.get("systems") or []):
        fld = f"systems[{i}]"
        if not isinstance(s, dict):
            f.append(Finding("systems", fld, repr(s)[:60],
                             "must be an object with name, scope, source_id"))
            continue
        name, sid, scope = s.get("name"), s.get("source_id"), s.get("scope")

        if not name:
            f.append(Finding("systems", fld + ".name", "<missing>", "required"))
            continue

        denied = r.deny_hit("systems", name)
        if denied:
            f.append(Finding("systems", fld + ".name", name,
                             "named on the systems.json DO-NOT-USE list: " + denied))
            continue

        by_name = r.sys_by_name.get(norm(name))
        by_id = r.sys_by_id.get(str(sid).upper()) if sid else None

        if by_name is None:
            f.append(Finding(
                "systems", fld + ".name", name,
                "not found in registries/systems.json — no registry entry grounds "
                "this system name, so it cannot be written into a process",
                suggestions=near(name, r.sys_by_name)))
        if sid and by_id is None:
            f.append(Finding("systems", fld + ".source_id", str(sid),
                             "no entry in systems.json carries this source_id",
                             suggestions=near(str(sid), r.sys_by_id)))
        if not sid:
            f.append(Finding("systems", fld + ".source_id", "<missing>",
                             "required — a system name must be bound to a registry id"))
        if by_name is not None and by_id is not None and by_name is not by_id:
            f.append(Finding(
                "systems", fld, f"{name} / {sid}",
                f"name and source_id disagree: name resolves to "
                f"{by_name['source_id']} ({by_name['name']}), "
                f"source_id resolves to {by_id['name']}"))
        if scope is not None and scope not in VALID_SCOPE:
            f.append(Finding("systems", fld + ".scope", str(scope),
                             f"must be one of {sorted(VALID_SCOPE)}"))
        elif by_name is not None and scope and scope != by_name.get("scope"):
            f.append(Finding(
                "systems", fld + ".scope", str(scope),
                f"registry records this system as {by_name.get('scope')!r}; "
                f"the scope badge is the honesty mechanism and must agree"))


def _check_list(p: dict, key: str, index: dict, registry: str, label: str,
                r: Registries, f: list[Finding], normaliser=norm) -> None:
    for i, v in enumerate(p.get(key) or []):
        fld = f"{key}[{i}]"
        if not isinstance(v, str):
            f.append(Finding(label, fld, repr(v)[:60], "must be a string"))
            continue
        denied = r.deny_hit(registry, v)
        if denied:
            f.append(Finding(label, fld, v,
                             f"named on the {registry}.json DO-NOT-USE list: {denied}"))
            continue
        if normaliser(v) not in index:
            f.append(Finding(label, fld, v,
                             f"not found in registries/{registry}.json",
                             suggestions=near(v, index)))


def home_domain(pid: str, groups: list[str]) -> str | None:
    """Map RR-06-… to the facts.json group name for domain 06."""
    m = re.match(r"^RR-(\d{2})-", pid or "")
    if not m:
        return None
    want = f"domain_{m.group(1)}_"
    return next((g for g in groups if g.startswith(want)), None)


def check_prose_figures(p: dict, r: Registries, f: list[Finding],
                        words: bool = False,
                        grounded: list | None = None,
                        any_domain: bool = False) -> None:
    grounded = grounded if grounded is not None else []
    home = home_domain(p.get("pid", ""), r.fact_domain)
    for key in PROSE_FIELDS:
        if key not in p:
            continue
        val = p[key]
        chunks = [(key, val)] if isinstance(val, str) else [
            (f"{key}[{i}]", v) for i, v in enumerate(val) if isinstance(v, str)
        ] if isinstance(val, list) else []

        for fld, text in chunks:
            low = text.lower()
            # Ranges covered by a grounded registry surface form are exempt.
            covered: list[tuple[int, int]] = []
            for surf in r.digit_surfaces:
                st = 0
                while (k := low.find(surf, st)) >= 0:
                    covered.append((k, k + len(surf)))
                    st = k + 1
            # Also exempt any citation the process legitimately declares.
            for cite in p.get("regulatory_hook") or []:
                if isinstance(cite, str) and norm_cite(cite) in r.reg_by_cite:
                    for pat in {norm(cite), norm_cite(cite)}:
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
                table = r.fact_dates if kind == "date" else r.fact_numbers
                cands = list(table.get(k, [])) if k else []
                if not cands and kind != "date" and k and re.fullmatch(r"(19|20)\d{2}", k):
                    cands = list(r.fact_dates.get(k, []))

                own = [i for i in cands if r.fact_domain[i] == home]
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
                        f"{r.fact_domain[i]}, not under this process's own domain "
                        f"({home or 'unknown'}) — the number is real but it is "
                        f"grounding on an unrelated claim: "
                        f"\"{r.fact_texts[i][0][:110]}…\". Source it for this domain "
                        f"or drop the span (--any-domain to allow).",
                        offset=a, context=context_of(text, a, b)))
                    continue
                f.append(Finding(
                    "figures", fld, span_r,
                    f"ungrounded {kind} — not inside any registry entity and not "
                    f"present in registries/facts.json",
                    offset=a, context=context_of(text, a, b)))


def validate(p: dict, r: Registries, words: bool = False,
             grounded: list | None = None,
             any_domain: bool = False) -> list[Finding]:
    f: list[Finding] = []
    check_structure(p, f)
    check_systems(p, r, f)
    _check_list(p, "regulatory_hook", r.reg_by_cite, "regulations",
                "regulations", r, f, normaliser=norm_cite)
    _check_list(p, "actors", r.role_by_name, "roles", "roles", r, f)
    _check_list(p, "kpi_moved", r.kpi_by_name, "kpis", "kpis", r, f)
    check_prose_figures(p, r, f, words=words, grounded=grounded,
                        any_domain=any_domain)
    return f


# --- reporting ----------------------------------------------------------------
ORDER = ["structure", "systems", "regulations", "roles", "kpis", "figures"]
TITLES = {
    "structure": "SCHEMA",
    "systems": "UNGROUNDED SYSTEM",
    "regulations": "UNGROUNDED REGULATORY HOOK",
    "roles": "UNGROUNDED ROLE",
    "kpis": "UNGROUNDED KPI",
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
    ap.add_argument("--explain", action="store_true",
                    help="show which fact grounded each figure that passed")
    ap.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
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

    r = Registries(Path(args.registries))
    results, rejected = [], False
    for p in procs:
        grounded: list = []
        findings = validate(p, r, words=args.words, grounded=grounded,
                            any_domain=args.any_domain)
        rejected = rejected or bool(findings)
        results.append({"pid": p.get("pid", "<no pid>"),
                        "passed": not findings,
                        "findings": [f.as_dict() for f in findings]})
        results[-1]["grounded_figures"] = [
            {"field": g[0], "span": g[1], "fact": r.fact_texts[g[2]][0]} for g in grounded
        ]
        if not args.as_json:
            report(p.get("pid", "<no pid>"), findings)
            if args.explain and grounded:
                print()
                print("  ── FIGURES GROUNDED (token match — verify the claim, not just the number) ──")
                for fld, span, idx in grounded:
                    fact = r.fact_texts[idx][0]
                    print(f"    {fld}: {span!r}")
                    print(f"      grounded by: {fact[:150]}{'…' if len(fact) > 150 else ''}")

    if args.as_json:
        print(json.dumps({"rejected": rejected, "results": results},
                         indent=2, ensure_ascii=False))
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
