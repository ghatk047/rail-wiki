#!/usr/bin/env python3
"""Single point of access to the five registries (BUILD-SPEC-v2 §4, Phase A).

The registries are reviewed source of truth. This module reads them, indexes
them, and answers exact lookups. It does not correct, complete, or second-guess
them, and it never writes to them.

Lookup contract
---------------
Every get_* returns an Entry, or None. None means "this registry does not
ground that term" — nothing more. There is no fallback chain, no fuzzy or
approximate matching, and no nearest-neighbour rescue. A caller that wants to
suggest alternatives to a human must do that itself, and must not treat a
suggestion as grounding.

Canonicalisation vs. matching (read this before changing it)
------------------------------------------------------------
Lookups compare canonical forms, not raw strings: case, surrounding
whitespace, trailing punctuation, and — for citations — the noise words and
symbols that carry no meaning in a CFR reference ("Part", "§", commas). A
registry entry also answers to deterministic aliases derived from its own
text, and to nothing else:

    "I-ETMS (Interoperable Electronic Train Management System)"
        -> "i-etms" and the expansion, because a parenthetical gloss names
           the same system
    "PTC Field Engineer / PTC Technician"
        -> each side of the slash, because the registry wrote one entry for
           two interchangeable titles
    "49 CFR Part 236, Subpart I"
        -> "49 cfr 236 subpart i", because the spec's own §3 example cites it
           that way

This is canonicalisation, not similarity: each alias is derived by rule from
the entry's own text and is auditable via aliases().

A key claimed by more than one entry is a cross-registration, not a conflict:
the registries deliberately list NetControl under both Engineering and
Technology, and 49 CFR Part 1180 under Engineering, Safety and Merger, each
noting its primary home. Such a key resolves — refusing to resolve it would
reject processes that cite a perfectly grounded system. get_* returns the
first entry in document order; find_all() returns every candidate, which is
what a caller needs when the domain matters. Duplicates are listed in
.duplicates for audit.

If you want the strictest possible reading, construct with
strict_literal=True: aliases are not derived at all and only the entry's own
canonicalised primary key resolves. Be aware that this rejects "I-ETMS", which
is how the spec itself writes that system in §3.

Denials
-------
Entries under `not_yet_sourced_do_not_use` are explicit refusals, not data.
They are never returned by a get_*. denial_for() reports whether a term is
named in one, so a caller can quote the refusal back instead of reporting a
bland miss.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

REPO = Path(__file__).resolve().parent.parent
REGISTRY_DIR = REPO / "registries"
DENY_GROUP = "not_yet_sourced_do_not_use"

REGISTRY_FILES = ("systems", "regulations", "roles", "kpis", "facts")

# The field that carries each registry's primary human-readable key.
PRIMARY_KEY = {
    "systems": "name",
    "regulations": "cite",
    "roles": "role",
    "kpis": "kpi",
    "facts": "fact",
}
# Prefix for the synthetic, stable ids minted for registries that ship none.
ID_PREFIX = {"regulations": "REG", "roles": "ROLE", "kpis": "KPI", "facts": "FACT"}


class RegistryError(RuntimeError):
    """The registries could not be loaded or are internally inconsistent."""


@dataclass(frozen=True)
class Entry:
    """One registry record, with its provenance attached.

    `data` is the raw object exactly as it appears on disk. The loader never
    mutates it, so a caller reading entry.data is reading the reviewed file.
    """

    registry: str
    domain: str
    entry_id: str
    key: str
    data: dict = field(repr=False)

    @property
    def source_url(self) -> str | None:
        return self.data.get("source_url")

    @property
    def scope(self) -> str | None:
        return self.data.get("scope")

    @property
    def as_of(self) -> str | None:
        return self.data.get("as_of")


# --- canonical forms ----------------------------------------------------------
def canon(s: str) -> str:
    """Case, dash and whitespace normalisation. Meaning-preserving only."""
    s = s.lower().replace("—", " ").replace("–", " ").replace("’", "'")
    s = re.sub(r"[,\.\;\:]+$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def canon_cite(s: str) -> str:
    """Canonical citation form: '49 CFR Part 236, Subpart I' == '49 CFR 236 Subpart I'."""
    s = canon(s)
    s = s.replace("§", " ").replace("u.s.c.", "usc").replace("c.f.r.", "cfr")
    s = re.sub(r"\bparts?\b", " ", s)
    s = re.sub(r"[,\(\)]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _paren_aliases(text: str) -> set[str]:
    """'X (Y)' also answers to 'X' and to 'Y'."""
    out: set[str] = set()
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", text.strip())
    if m:
        out.add(canon(m.group(1)))
        out.add(canon(m.group(2)))
    return {a for a in out if a}


def _slash_aliases(text: str) -> set[str]:
    """'A / B' is one entry naming two interchangeable titles."""
    return {canon(p) for p in re.split(r"\s+/\s+", text) if canon(p)}


# --- loader -------------------------------------------------------------------
class RegistryLoader:
    """Loads the five registries once and answers exact lookups against them."""

    def __init__(self, path: Path | str = REGISTRY_DIR, strict_literal: bool = False):
        self.path = Path(path)
        self.strict_literal = strict_literal

        self.raw: dict[str, dict] = {}
        self.entries: dict[str, tuple[Entry, ...]] = {}
        self.denials: dict[str, tuple[str, ...]] = {}
        self._index: dict[str, dict[str, Entry]] = {}
        self._by_id: dict[str, dict[str, Entry]] = {}
        self._all_index: dict[str, dict[str, list[Entry]]] = {}
        self.duplicates: dict[str, dict[str, list[str]]] = {r: {} for r in REGISTRY_FILES}

        for name in REGISTRY_FILES:
            self._load_file(name)
        for name in REGISTRY_FILES:
            self._build_index(name)

    # -- loading
    def _load_file(self, name: str) -> None:
        f = self.path / f"{name}.json"
        if not f.exists():
            raise RegistryError(f"registry not found: {f}")
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RegistryError(f"{f} is not valid JSON: {e}") from e
        if not isinstance(doc, dict):
            raise RegistryError(f"{f}: expected an object of domain groups")

        self.raw[name] = doc
        self.denials[name] = tuple(
            d for d in doc.get(DENY_GROUP, []) if isinstance(d, str)
        )

        pk = PRIMARY_KEY[name]
        out: list[Entry] = []
        for group, items in doc.items():
            if group.startswith("_") or group == DENY_GROUP:
                continue
            if not isinstance(items, list):
                raise RegistryError(f"{f}: group {group!r} is not a list")
            for i, obj in enumerate(items, start=1):
                if not isinstance(obj, dict):
                    # Non-dict entries outside the deny group are malformed.
                    raise RegistryError(
                        f"{f}: {group}[{i - 1}] is a {type(obj).__name__}, expected an object"
                    )
                key = obj.get(pk)
                if not isinstance(key, str) or not key.strip():
                    raise RegistryError(f"{f}: {group}[{i - 1}] has no usable {pk!r}")
                out.append(
                    Entry(
                        registry=name,
                        domain=group,
                        entry_id=self._entry_id(name, group, i, obj),
                        key=key,
                        data=obj,
                    )
                )
        self.entries[name] = tuple(out)

    @staticmethod
    def _entry_id(registry: str, group: str, ordinal: int, obj: dict) -> str:
        """Registry-supplied id where one exists, else a stable synthetic one."""
        if registry == "systems":
            sid = obj.get("source_id")
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
        m = re.match(r"domain_(\d{2})_", group)
        dom = f"D{m.group(1)}" if m else "DXX"
        return f"{ID_PREFIX.get(registry, 'ENT')}-{dom}-{ordinal:02d}"

    # -- indexing
    def _keys_for(self, e: Entry) -> set[str]:
        """Every canonical form that identifies this entry, and no others."""
        if e.registry == "regulations":
            keys = {canon_cite(e.key), canon(e.key)}
            if not self.strict_literal:
                for a in _paren_aliases(e.key):
                    keys.add(canon_cite(a))
            return {k for k in keys if k}
        keys = {canon(e.key)}
        if not self.strict_literal:
            if e.registry == "systems":
                keys |= _paren_aliases(e.key)
            elif e.registry in ("roles", "kpis"):
                keys |= _slash_aliases(e.key)
                keys |= _paren_aliases(e.key)
        return {k for k in keys if k}

    def _build_index(self, name: str) -> None:
        index: dict[str, Entry] = {}
        all_index: dict[str, list[Entry]] = {}
        for e in self.entries[name]:
            for k in self._keys_for(e):
                bucket = all_index.setdefault(k, [])
                if not any(x.entry_id == e.entry_id for x in bucket):
                    bucket.append(e)
                index.setdefault(k, e)
        # More than one entry on a key means the registries cross-register the
        # same thing across domains. That resolves; it is not an error.
        for k, bucket in all_index.items():
            if len(bucket) > 1:
                self.duplicates[name][k] = [e.entry_id for e in bucket]
        self._index[name] = index
        self._all_index[name] = all_index

        by_id: dict[str, Entry] = {}
        for e in self.entries[name]:
            eid = e.entry_id.upper()
            if eid in by_id and by_id[eid].key != e.key:
                raise RegistryError(
                    f"{name}.json: duplicate id {e.entry_id!r} on "
                    f"{by_id[eid].key!r} and {e.key!r}"
                )
            by_id[eid] = e
        self._by_id[name] = by_id

    # -- lookups (exact; None means not grounded)
    def get_system(self, source_id: str) -> Entry | None:
        """By registry source_id, e.g. 'SYS-D06-01'."""
        if not isinstance(source_id, str):
            return None
        return self._by_id["systems"].get(source_id.strip().upper())

    def get_system_by_name(self, name: str) -> Entry | None:
        """By system name. Needed to check a name and its source_id agree."""
        return self._lookup("systems", name)

    def get_regulation(self, cite: str) -> Entry | None:
        if not isinstance(cite, str):
            return None
        return self._index["regulations"].get(canon_cite(cite))

    def get_role(self, name: str) -> Entry | None:
        return self._lookup("roles", name)

    def get_kpi(self, name: str) -> Entry | None:
        return self._lookup("kpis", name)

    def get_fact(self, text_or_id: str) -> Entry | None:
        """By synthetic id ('FACT-D06-04') or by exact fact text."""
        if not isinstance(text_or_id, str):
            return None
        hit = self._by_id["facts"].get(text_or_id.strip().upper())
        return hit if hit is not None else self._lookup("facts", text_or_id)

    def _lookup(self, registry: str, term: str) -> Entry | None:
        if not isinstance(term, str):
            return None
        return self._index[registry].get(canon(term))

    # -- iteration and audit
    def find_all(self, registry: str, term: str) -> tuple[Entry, ...]:
        """Every entry a term resolves to, across domain groups. Empty if none."""
        if not isinstance(term, str):
            return ()
        key = canon_cite(term) if registry == "regulations" else canon(term)
        return tuple(self._all_index[registry].get(key, ()))

    def all(self, registry: str) -> tuple[Entry, ...]:
        if registry not in self.entries:
            raise KeyError(f"no such registry: {registry!r}")
        return self.entries[registry]

    def __iter__(self) -> Iterator[Entry]:
        for name in REGISTRY_FILES:
            yield from self.entries[name]

    def aliases(self, registry: str) -> dict[str, Entry]:
        """Every canonical key that resolves, for audit and human suggestions."""
        return dict(self._index[registry])

    def denial_for(self, registry: str, term: str) -> str | None:
        """The DO-NOT-USE text naming this term, if any.

        Substring containment against the refusal prose, so that a denial
        reading "do not write 'Hexagon' or 'INFOR'" is found by either name.
        This grounds nothing; it only makes a refusal quotable.
        """
        if not isinstance(term, str):
            return None
        t = canon(term)
        if not t:
            return None
        return next((d for d in self.denials.get(registry, ()) if t in canon(d)), None)

    def summary(self) -> dict:
        return {
            "path": str(self.path),
            "strict_literal": self.strict_literal,
            "counts": {n: len(self.entries[n]) for n in REGISTRY_FILES},
            "denials": {n: len(self.denials[n]) for n in REGISTRY_FILES},
            "duplicates": {n: self.duplicates[n] for n in REGISTRY_FILES
                           if self.duplicates[n]},
        }


# --- module-level access (loaded once, cached per path) -----------------------
_CACHE: dict[tuple[str, bool], RegistryLoader] = {}


def load(path: Path | str = REGISTRY_DIR, strict_literal: bool = False) -> RegistryLoader:
    """Return the loader for `path`, reading from disk only on first call."""
    key = (str(Path(path).resolve()), strict_literal)
    if key not in _CACHE:
        _CACHE[key] = RegistryLoader(path, strict_literal=strict_literal)
    return _CACHE[key]


def get_system(source_id: str) -> Entry | None:
    return load().get_system(source_id)


def get_system_by_name(name: str) -> Entry | None:
    return load().get_system_by_name(name)


def get_regulation(cite: str) -> Entry | None:
    return load().get_regulation(cite)


def get_role(name: str) -> Entry | None:
    return load().get_role(name)


def get_kpi(name: str) -> Entry | None:
    return load().get_kpi(name)


def get_fact(text_or_id: str) -> Entry | None:
    return load().get_fact(text_or_id)


if __name__ == "__main__":
    import sys

    r = load(strict_literal="--strict-literal" in sys.argv)
    s = r.summary()
    print(f"registries: {s['path']}  (strict_literal={s['strict_literal']})")
    for name in REGISTRY_FILES:
        print(f"  {name:12s} {s['counts'][name]:3d} entries  "
              f"{s['denials'][name]:2d} denials  "
              f"{len(r.aliases(name)):3d} resolvable keys")
    if s["duplicates"]:
        print("\n  cross-registered keys (resolve to the first; all via find_all):")
        for name, keys in s["duplicates"].items():
            for k, ids in sorted(keys.items()):
                print(f"    {name}: {k!r} -> {', '.join(ids)}")
