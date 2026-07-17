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
- **Role/permission evidence (for the overview's user-roles snapshot)** — when
  you observe role or permission logic (route guards, permission middleware/
  checks, menu-or-role definitions, approval relations), package the roles it
  actually names, cited and with activation labels. This is the ONLY basis the
  snapshot may use for user roles — absent such evidence, roles stay
  `unresolved`, never inferred from module or folder names.
- **Anything else** you can claim, cite, and argue impact for.

Rules:
- Same shape, same citation discipline, same two-signal bar for high
  confidence as every other lens — freedom of TOPIC, not of rigor.
- Do not duplicate other lenses' findings; if a discovery belongs to a
  specialized lens, leave it there and add only what they cannot see.
- This lens has no tool to fail, so its coverage line states what you did
  NOT get to look at (views you lacked time/rows for, skipped lanes).
