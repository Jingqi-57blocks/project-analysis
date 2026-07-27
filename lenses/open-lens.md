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

Rules:
- Same shape, same citation discipline, same two-signal bar for high
  confidence as every other lens — freedom of TOPIC, not of rigor.
- Do not duplicate other lenses' findings; if a discovery belongs to a
  specialized lens, leave it there and add only what they cannot see.
- This lens has no tool to fail, so its coverage line states what you did
  NOT get to look at (views you lacked time/rows for, skipped lanes).
