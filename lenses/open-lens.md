---
shard: workspace
# shard: this file's own "Signals:" line says "ALL views + manifests +
#   discovery-report + module candidates -- this is free observation,
#   bounded by evidence discipline, not by a tool"; it is also the sole
#   basis for cross-repo catalog-drift and UI/backend contract-mismatch
#   findings ("Contract mismatches -- UI calling endpoints no analyzed
#   service exposes"), both inherently workspace-wide questions.
signals: []
# signals: lenses/coverage-map.json records "tools": [] for open-lens
#   deliberately -- no single required tool. templates.py's resolver must
#   treat an EMPTY signals list here as "every tool this run recorded",
#   never as "no tool" -- documented explicitly so a future maintainer does
#   not read the empty list as "nothing".
source_reads: true
# source_reads: this lens's richest catches -- "Half-finished migrations...
#   (cite both generations)", "Configuration drift risk -- the same logical
#   setting duplicated across services/env templates", and the
#   role/permission enforcement-layer evidence its own body requires -- are
#   none of them visible in any tool-summarized view; they exist only in
#   actual source/config file content, which is exactly what this lens's
#   "free observation, bounded by evidence discipline, not by a tool" own
#   line already claims to do.
max_selections: 24
# max_selections: this is the ONLY lens whose own "Signals:" line is "ALL
#   views + manifests... workspace-wide", making it the sole carrier of
#   cross-repo systemic conditions (background jobs, dead integration
#   surfaces, dependency-declaration drift, per-route enforcement
#   consistency, client-layer duplication -- see the systemic-condition
#   checklist below). A run spanning several repositories, each contributing
#   its own instances of several of these concern families, structurally
#   needs more than the flat per-lens default to name enough locations to
#   verify them all -- round-2 spot-check evidence showed the flat 12-cap
#   silently starving this lens specifically. 24 keeps a real, disclosed
#   ceiling (never unbounded) while giving this one workspace-wide lens
#   roughly double the default headroom.
---
# Lens: open-lens (group C)

**Question:** what matters about this codebase that no other lens was built
to see?

**Signals:** ALL views + manifests + discovery-report + module candidates —
this is free observation, bounded by evidence discipline, not by a tool.

Typical catches (non-exhaustive — that is the point of this lens):
- **Half-finished migrations** — two frameworks/ORMs/HTTP clients doing the
  same job side by side; "v2" folders that never absorbed v1; TODO-dated
  transitional shims (cite both generations).
- **Configuration drift risk** — the same logical setting duplicated across
  services/env templates with different shapes (names only, never values).
- **Contract mismatches** — UI calling endpoints no analyzed service exposes
  (label `unresolved` — the backend may be un-analyzed; scope-guard it).
- **Operational sharp edges** — schedulers/cron jobs with no visible failure
  handling; startup scripts that mutate state; committed credentials paths
  (report the FILE as a finding; the redactor keeps values out of views).
- **Inconsistent patterns** — three different error-handling or auth styles
  across one codebase; each style cited once.
- **Role/permission & access-model evidence (for the overview's roles snapshot
  and access model)** — when you observe role or permission logic (route guards,
  permission middleware/checks, menu-or-role definitions, policy-engine rules,
  approval relations), package the roles it names AND the enforcement LAYER and
  location of each check: frontend menu/route (visibility) versus backend
  middleware / policy engine / inline check (authorization). Cite each, label
  activation, and keep frontend visibility distinct from backend authorization.
  This is the ONLY basis the snapshot and access model may use — absent such
  evidence, roles stay `unresolved`, never inferred from module or folder names.
- **Anything else** you can claim, cite, and argue impact for.

**Systemic-condition checklist** — work through each of these explicitly for
this run, not only as catches you happen to stumble on:
- Background jobs/schedulers — which framework(s) run them, whether more
  than one generation coexists side by side, and whether error/failure
  handling is present or absent per implementation.
- Provisioned-but-dead integration surfaces — env vars, config entries, or
  dependencies present with no live call path ever reaching them.
- Dependency declaration vs. use — imports with no matching manifest entry,
  and manifest entries with no matching import anywhere in the source.
- Per-route-group enforcement consistency — the same middleware/guard
  applied, commented out, or simply absent across different registration
  sites; verify at the actual registration lines, never by assumption from
  one example.
- Client-layer duplication — more than one HTTP or data-access layer
  performing the same kind of call, with DIFFERING cross-cutting behavior
  (session handling, error handling) between them.

Rules:
- Same shape, same citation discipline, same two-signal bar for high
  confidence as every other lens — freedom of TOPIC, not of rigor.
- Do not restate a specialized-lens finding when this packet itself contains
  the comparison evidence. If no such comparison set is supplied, state the
  boundary of this lens's own evidence rather than claiming a hidden
  cross-lens de-duplication judgment.
- This lens has no tool to fail, so its coverage line states what you did
  NOT get to look at (views you lacked time/rows for, skipped lanes).
