# Project Doctor

A general-purpose Claude Code skill that examines a codebase (single- or multi-repo
workspace) with zero required setup and produces:

1. **Project overview + diagnosis** — module map, ranked problems with evidence,
   honest per-lens coverage reporting.
2. **Module drill-down** — a PM-readable module PRD (`prd.md`) and a dev-facing
   health report (`health.md`) with traced change scenarios.

First-class stack support in v1: **JS/TS and Go**. Other stacks are analyzed with
explicitly disclosed reduced coverage.

## Status

**Phase 1 build complete through lifecycle.** Phase 0 (toolchain spike) is signed off —
see `spike/` for evidence and `tools/README.md` for the validated toolchain. The tool
wrapper (57B-10), discovery (57B-11), the lenses, synthesis, and the run lifecycle are
built and live-sweep validated. The v3.5 overview restructure is applied: `overview.md`
is the PM-primary document (nine sections, readable in ~10 minutes),
`technical-overview.md` is its full-detail companion, and `project-map.md` is the
reusable topology. The Phase-1 exit run is pending. The skill command is
`/project-doctor` (`/doctor` is a Claude Code built-in).

## Design

- OSS tools produce repository-wide numbers; the model produces judgment.
- Provenance-anchored citations (`repo@commit:path:line`); per-signal manifests.
- Immutable runs; explicit accepted-run pointers; project-scoped persistent state.
- No schemas, no gates, no checker scripts in v1. The tool wrapper invokes
  allowlisted tools, applies safe flags, redacts, bounds output, and records
  manifests — it never interprets findings or scores reports.
- Zero target-project literals outside `benchmark/` and `spike/` (fixture areas).

## Privacy & packaging

`spike/` and (future) `benchmark/` contain **private target-project evidence** — real author
names and emails, internal architecture, dependency versions, and vulnerability details from the
WCP repositories. They are **local development fixtures only** and must **NEVER** be included when
the skill is packaged or published.

**`tools/README.md` is ALSO private-until-scrubbed**, not a ship candidate: it embeds
WCP-derived evidence throughout (repo names, vuln/outdated counts, architecture, file names). A
**sanitized public toolchain doc is a Phase 3 release deliverable**, derived from it. The only
**ship-candidate** artifacts are the generic, evidence-free ones: `SKILL.md`, the lens
definitions, the templates, and the tool wrapper. Everything under `spike/`, `benchmark/`, and
`tools/README.md` stays in this repository until deliberately scrubbed for release. The repo is
hosted on a **private remote at the owner's direction** (added 2026-07-16); because the tracked
tree contains WCP-derived evidence, this repository must **never be made public as-is** —
publishing happens only via the scrubbed Phase-3 release artifacts.

## Python environment

Project Doctor does not require global Python packages. From `wrapper/`, run
`python3 -m doctor_wrapper.bootstrap`; it creates the gitignored `wrapper/.venv` and
installs the wrapper and the PyDriller history lane there — this is all a doctor run
needs. Developers working on the wrapper itself add `--dev` to also install test
dependencies, then run tests as `.venv/bin/python -m pytest`. The CLI is
`.venv/bin/project-doctor-wrapper`. See `wrapper/README.md` for details.

## Tracking

Linear: team `57blocks-Project-Doctor`, project **Project Doctor v1**
(issues 57B-5 … 57B-20, four phase milestones with user review at each exit).
