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

**Phase 0 — toolchain spike** (in progress). See `spike/` for evidence and
`tools/README.md` for the validated toolchain (Phase 0 exit deliverable).

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
the skill is packaged or published: ship only the generic wrapper and `tools/README.md`, never the
evidence directories. This repo currently has **no git remote by design**, so nothing leaves this
machine unless it is deliberately pushed.

## Tracking

Linear: team `57blocks-Project-Doctor`, project **Project Doctor v1**
(issues 57B-5 … 57B-20, four phase milestones with user review at each exit).
