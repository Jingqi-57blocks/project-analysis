---
shard: workspace
# shard: this is the network lane, authorized or skipped for the WHOLE run
#   at once (never per repo). When unauthorized -- which README calls
#   "often skipped" -- every repo's shard would independently produce the
#   identical trivial "coverage: skipped" output, multiplying LLM calls for
#   zero additional information; one workspace task states the same skip
#   once. When authorized, the composer's own deterministic sharding
#   (composer.py) already splits an oversized workspace packet by its
#   largest input if needed, so per-repo semantic sharding would only buy
#   parallelism at the cost of duplicated skip-state calls on the common
#   path -- a bad trade for this lane. Kept as one workspace task.
signals: [dependency-cruiser, go-list, osv-scanner, outdated]
# signals: lenses/coverage-map.json requires {osv-scanner, outdated}; this
#   file's own "Signals:" line additionally names dependency-cruiser
#   ("external import partitions (prod vs dev vs unclassified)") and
#   go-list ("external imports") as corroborating signals its own Rules
#   section requires ("production-exposure claims add the cruiser
#   partition citation"), so both are added.
---
# Lens: dependency-risk (group C)

**Question:** which third-party dependencies expose this project to known
vulnerabilities, abandonment, or upgrade cliffs?

**Signals:** osv-scanner views (known vulnerabilities per lockfile), outdated
views (npm/yarn version lag), dependency-cruiser external import partitions
(prod vs dev vs unclassified), go-list external imports, discovery-report
candidates (dependency-only markers).

**This is the network lane.** When the run was not authorized for network,
these views are SKIPPED: your entire lens output is then the coverage
statement — dependency risk is UNKNOWN, and no absence-of-vulnerabilities
impression may leak into the overview.

Look for, with evidence:
- **Known vulnerabilities** (osv view): group by dependency, quote IDs and
  severity as the view renders them; a vulnerable PROD dependency (cruiser
  partition) outranks a dev one. Do not re-score severities.
- **Version cliffs** (outdated view): major-version lag on core framework
  dependencies (corroborate "core" with import counts); EOL runtimes.
- **Abandonment smells** — a dependency that is BOTH years behind AND
  vulnerable; or a fork/vendored copy pinned outside the registry (candidate
  evidence shows non-registry hosts — quote the guard disclosures).
- **Unclassified imports** — imports that resolve to no declared dependency
  (cruiser `unclassified`): phantom dependencies that break on clean
  installs.

Rules:
- Vulnerability claims cite the osv view rows; upgrade claims cite outdated
  view rows; production-exposure claims add the cruiser partition citation.
- The osv scan is lockfile-based: runtime-substituted or vendored versions
  are invisible — say so in limitations when the repo vendors dependencies.
- PM-fallback approximations (view manifest notes) cap confidence at medium.
