---
shard: workspace
# shard: this lens's own first bullet below ("Size/shape imbalance -- one
#   repo or folder dwarfing the rest of the system") is a cross-repo
#   comparison of scc line counts; a per-repo task could never see a
#   sibling repo's numbers to make that call, so the whole lens stays one
#   workspace-wide task (corrected from an initial per-repo guess).
signals: [scc, dependency-cruiser, go-list]
# signals: union of lenses/coverage-map.json's required set for
#   structure-inventory (scc, dependency-cruiser, go-list -- the tested
#   catalog workspace_metrics.py uses for coverage grading) with this file's
#   own "Signals:" line below (scc, go-list). dependency-cruiser is kept
#   because coverage-map.json requires it even though the prose line omits
#   it -- it backs the "layout vs claimed structure" bullet's module-graph
#   check.
source_reads: true
# source_reads: "Layout vs claimed structure -- ... business logic in a
#   folder named like infrastructure" cannot be argued from an scc/cruiser
#   count alone -- it requires reading what is actually IN the mismatched
#   folder. The same is true for judging whether a language-sprawl or
#   generated-code-inflating-scc claim is real (vs. a trivial/vendored
#   handful of files) rather than a raw line-count artifact.
---
# Lens: structure-inventory (group A)

**Question:** what is this codebase made of, and where does its shape already
predict trouble?

**Signals:** scc views (per repo), go-list view (Go package graph),
discovery-report (analysis roots, tier-2 exclusions, module signals).

Look for, with evidence:
- **Size/shape imbalance** — one repo or folder dwarfing the rest of the
  system (scc line counts); minified/vendored code that survived exclusions.
- **Language sprawl** — stacks present beyond the declared ones (scc language
  table): a second language with real code volume is a maintenance surface.
  A large SECONDARY-language surface alongside the primary stack (e.g. a
  styling language sitting beside the main application code) is itself a
  structural observation worth a finding when the size view shows it at
  scale, not only a footnote to the primary-language count.
- **Layout vs claimed structure** — module signals (routes, folders) that
  don't line up with the folder story (e.g. route handlers living outside the
  routing tree; business logic in a folder named like infrastructure).
- **Comment/code ratio extremes** per language (scc) — only as a SUPPORTING
  signal, never a finding on its own.
- **Generated code leaking into analysis** — if a view's numbers are dominated
  by files that look generated, say so; that's a coverage problem, not a
  finding about the code. When a generated, vendored, or bulk-data file
  visibly dominates a language bucket, state the count delta it produces per
  the size view (e.g. how many files/lines that one bucket would lose if the
  dominating file were excluded) so the inflation is quantified, not just
  asserted.

Rules:
- Numbers cite the scc/go-list view rows; structural claims cite files.
- Inventory observations are findings ONLY when they carry consequence
  (impact field must say what this costs, or drop it).
