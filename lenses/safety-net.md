---
shard: repo
# shard: every bullet (coverage asymmetry, test wiring, assertion-quality
#   sampling, type/migration nets, operational-state signals) is a per-repo
#   observation. The Rules section's "Non-git targets: this lens is
#   unavailable" is itself a PER-REPO fact -- some workspace repos may be
#   non-git while siblings are git -- which a repo shard represents
#   naturally (one shard reports unavailable, siblings report their own
#   findings), corrected from an initial workspace guess inherited from the
#   old README grouping (shared history-lane invocation with
#   hotspots-change-friction, not a shared need for cross-repo comparison).
signals: [git-history, scc]
# signals: lenses/coverage-map.json requires only {scc}; this file's own
#   "Signals:" line additionally names git-history ("do tests change
#   alongside the code they cover?"), so it is added.
source_reads: true
# source_reads: this lens's own body requires reading actual file content no
#   signal view carries -- "Assertion quality sampling -- read a SAMPLE of
#   tests in the hottest modules... cite the specific test files sampled"
#   and "Type/migration nets... (read tsconfig/migration files as data)"
#   both name a real file read, not a tool-summarized count.
---
# Lens: safety-net (group B)

**Question:** when someone changes this code, what actually catches mistakes —
observed, not assumed?

**Signals:** the source tree (test files, CI config — read as data), scc view
(test-code volume per language), git-history view (do tests change alongside
the code they cover?), discovery-report (CI resource signals).

This lens reports **observed test evidence, never a file census**. A folder
full of `*_test.go` files is not a safety net until something shows they run
and assert meaningfully.

Look for, with evidence:
- **Coverage asymmetry** — modules with real churn and zero co-changing test
  files (history view pairs); the risky combination is hot + untested.
- **Test wiring** — CI config that runs (or conspicuously does not run) the
  test suites; a test folder that no pipeline step executes is `status
  unresolved`, not protection.
- **Assertion quality sampling** — read a SAMPLE of tests in the hottest
  modules: smoke-only tests, snapshot-everything tests, or mocked-to-nothing
  tests are weaker nets; cite the specific test files sampled and say the
  sample size.
- **Type/migration nets** — TypeScript strictness settings, DB migration
  discipline (read tsconfig/migration files as data) — supporting evidence
  for or against safety, always cited.
- **Operational-state signals (for the overview's operational-state section)** —
  while reading CI and config, record the PRESENCE/ABSENCE and LOCATION only (never
  a reliability verdict) of: health-check endpoints, logging config, DB migration
  directories, rollback/deploy hints, metrics/tracing/alert wiring. Absence is
  `unknown`, not "unreliable".

Rules:
- NEVER report a coverage percentage — no coverage tool runs in v1. Claims
  are about presence, wiring, and co-change of tests, with citations.
- "No tests found for X" is an absence claim: scope-guard it to the analyzed
  sources and pair it with where you looked.
- Distinguish "tests exist but their execution is unverified" (`status
  unresolved`) from "no test files touch this module" (observed absence).
