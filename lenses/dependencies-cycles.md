---
shard: workspace
# shard: the "Cross-stack contracts" bullet below explicitly needs
#   endpoints/paths shared between a UI repo and an API repo in the SAME
#   task -- a per-repo shard could only ever see one side of that
#   correlation, so this lens stays one workspace-wide task.
signals: [dependency-cruiser, go-list, staticcheck]
# signals: matches lenses/coverage-map.json's required set for
#   dependencies-cycles exactly ({dependency-cruiser, go-list, staticcheck}).
#   This file's own "Signals:" line does not mention staticcheck, but
#   coverage-map.json is workspace_metrics.py's tested, authoritative
#   catalog (test_skill_hygiene.py keeps it in lockstep with the installed
#   lens set), so it is kept rather than dropped.
---
# Lens: dependencies-cycles (group A)

**Question:** where does the dependency structure make change expensive —
cycles, hub modules, boundary violations, unresolvable imports?

**Signals:** dependency-cruiser view (JS/TS module graph, cycles, orphans,
external import partitions), go-list view (internal package graph + external
imports), discovery-report (analysis roots — what the cruiser actually saw).

Look for, with evidence:
- **Cycles** — every cycle the cruiser reports is a candidate finding; argue
  impact by what the cycle couples (two business modules ≫ two helpers).
- **Hub/god modules** — modules with extreme fan-in AND fan-out; changes
  there ripple everywhere.
- **Boundary violations** — UI importing persistence directly, module A
  reaching into module B's internals (cite the specific import edges).
- **Unclassified/unresolved imports** — the view's unresolved percentage is a
  COVERAGE fact: >15% means the graph itself is partial; cap confidence of
  every graph-shaped claim and say why in limitations.
- **Cross-stack contracts** — endpoints/paths shared between a UI repo and an
  API repo (corroborate with module signals routes) — label the relationship
  `observed` only when both sides are cited; otherwise `inferred`.

Rules:
- Fan-in/fan-out and cycle claims cite view rows; boundary claims also cite
  the source import line.
- The cruiser runs with `--no-config`, so path aliases may be unresolved —
  never report an alias-induced "missing module" as a defect; it is coverage.
