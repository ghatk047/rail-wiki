# rail-wiki

Read [`docs/rail-wiki-build-spec-v2.md`](docs/rail-wiki-build-spec-v2.md) before any work.

## Hard rules

- Registry-first. No system name, CFR cite, role, or figure enters a
  process unless it exists in `registries/` with a source URL.
- `validate_content.py` must exist and pass before any prose is committed.
- `data/processes.json` is gitignored. Never commit it.
- Do not generate processes outside the current phase.
- Ownership rules in §1 of the spec settle all domain overlaps.

## Repo

https://github.com/ghatk047/rail-wiki
