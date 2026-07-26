---
shard: repo
# shard: every bullet (unused Go symbols, JS/TS orphan modules, zombie
#   features) is a within-one-repo determination; staticcheck/cruiser/go-list
#   are all per-repo tool invocations and nothing here compares repos.
signals: [dependency-cruiser, go-list, staticcheck]
# signals: lenses/coverage-map.json requires only {staticcheck} for coverage
#   grading, but this file's own "Signals:" line names dependency-cruiser
#   (JS/TS orphan modules) and go-list (packages outside the import graph)
#   as PRIMARY evidence for this lens, not mere corroboration -- both are
#   added so the lens task actually receives the views its own body needs.
---
# Lens: dead-code (group A)

**Question:** what code exists but demonstrably isn't wired in — and what
merely LOOKS dead?

**Signals:** staticcheck view (Go: unused symbols, U1000-class findings),
dependency-cruiser view (JS/TS: orphan modules — files nothing imports),
go-list view (packages outside the import graph), module signals (routes —
an orphan that IS a route target is not dead).

Look for, with evidence:
- **Go unused symbols** — staticcheck unused results; group by package;
  argue impact only where the dead mass is large or misleading (an unused
  exported API invites callers).
- **JS/TS orphan modules** — cruiser orphans that are not entrypoints, not
  dynamically loaded, not route-registered (check module signals before
  claiming): each survivor is a candidate.
- **Zombie features** — code behind flags/registrations that are commented
  out or gated off: label activation `status unresolved` unless the gate's
  state is itself evidenced. Never claim "unused in production" — that is
  not knowable from the repository (scope disclaimer).

Rules:
- Dynamic loading, reflection, DI containers, and framework conventions all
  create false "dead" verdicts — every dead-code claim needs a second signal
  (orphan + no route + no dynamic-require hit = medium; add a compile-level
  unused verdict for high).
- JS dead-code tooling is limited in v1 (no dedicated tool): confidence for
  JS claims caps at medium and the coverage line must say so.
- staticcheck compile failures make its view PARTIAL: report the reduced
  coverage, not conclusions about packages that failed to compile.
