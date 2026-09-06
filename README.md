# rail-wiki

Business process reference for a **US Class I freight railroad archetype**,
instantiated with Union Pacific public examples.

Build spec: [`docs/rail-wiki-build-spec-v2.md`](docs/rail-wiki-build-spec-v2.md).
Read it before any work.

## Status

**Phase 1 — scaffold only.** No process content generated. No generator or
validator scripts written yet.

| Phase | Contents | State |
|---|---|---|
| 1 | Repo scaffold, registries, Pages placeholder | done |
| 2 | `build_registries.py`, `build_taxonomy.py`, `validate_content.py` | not started |
| 3 | `generate_rail_wiki.py`, pilot gate (spec §11), process content | not started |

## Layout

```
registries/   Sourced fact substrate — systems, regulations, roles, KPIs, facts
data/         Taxonomy + generated process data (processes.json is gitignored)
scripts/      Build, generation and validation scripts
docs/         Build spec and design notes
site/         Generated site output
index.html    GitHub Pages entry point (Pages serves from repo root)
```

## Hard rules

- **Registry-first.** No system name, CFR cite, role, KPI or figure enters a
  process unless it exists in `registries/` with a source URL.
- `validate_content.py` must exist and pass before any prose is committed.
- `data/processes.json` is gitignored. Never commit it.
- Do not generate processes outside the current phase.
- Ownership rules in §1 of the spec settle all domain overlaps.

## Registries

| File | Domain groups | Entries |
|---|---|---|
| `systems.json` | 16 | 46 |
| `roles.json` | 15 | 47 |
| `regulations.json` | 16 | 38 |
| `facts.json` | 15 | 36 |
| `kpis.json` | 16 | 20 |

Every entry carries a source. `systems[].scope` is `company_specific` or
`industry_typical` and is rendered as a visible badge on each process page —
that badge is the honesty mechanism for the whole wiki.

---

Independently compiled from public sources. Not affiliated with, sponsored by,
or endorsed by any railroad. Illustrative of US Class I practice.
