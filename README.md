# Project Analysis

A general-purpose Claude Code skill that examines a codebase (single- or multi-repo
workspace) with zero required setup and produces:

1. **Project overview + diagnosis** — module map, ranked problems with evidence,
   honest per-lens coverage reporting.
2. **Module drill-down** — a PM-readable module PRD (`prd.md`) and a dev-facing
   health report (`health.md`) with traced change scenarios.

First-class stack support in v1: **JS/TS and Go**. Other stacks are analyzed with
explicitly disclosed reduced coverage.

## Status

**Phase 1 complete.** The static-analysis foundation (call graphs for JS/TS + Go,
dependency edges, a deterministic `system-model.json`), the tool wrapper, discovery,
the lenses, synthesis, and the run lifecycle are built and accepted. `tools/README.md`
documents the validated toolchain (generic). `overview.md` is the PM-primary document,
`technical-overview.md` its full-detail companion, and `project-map.md` the reusable
topology. The skill command is `/project-analysis` (see [Skill registration](#skill-registration)).

## Design

- OSS tools produce repository-wide numbers; the model produces judgment.
- Provenance-anchored citations (`repo@commit:path:line`); per-signal manifests.
- Immutable runs; explicit accepted-run pointers; project-scoped persistent state.
- No schemas, no gates, no checker scripts in v1. The tool wrapper invokes
  allowlisted tools, applies safe flags, redacts, bounds output, and records
  manifests — it never interprets findings or scores reports.
- Zero target-project literals in tracked files (the analyzer is general-purpose).

## Privacy & packaging

Per-target analysis output is never committed: runs write to gitignored `output/` and
`state/`. The tracked tree is **target-neutral** — `SKILL.md`, the lens definitions, the
templates, the tool wrapper, and the generic `tools/README.md`.

Per-target **acceptance evidence** (spike bake-offs, benchmark checklists, and validation
runs against real repositories — which contain real author names, internal architecture,
and vulnerability details) is kept in a **private acceptance store outside this repository**,
reachable for our own reproducibility but never shipped. Git history is likewise clean of
that evidence: it was removed with `git filter-repo`. (Commit messages and tags may still
reference a target project by name — that was an explicit scope choice; the requirement is
that no target's evidence *content* is tracked or retrievable from history.)

## Skill registration

Claude Code discovers skills by directory name under `~/.claude/skills/<name>/SKILL.md`;
the invocation command is the directory name. Register one symlink:

```
ln -s /path/to/project-analysis ~/.claude/skills/project-analysis
```

That registers the `/project-analysis` command.

## Python environment

Project Analysis does not require global Python packages. From `wrapper/`, run
`python3 -m analysis_wrapper.bootstrap`; it creates the gitignored `wrapper/.venv` and
installs the wrapper and the PyDriller history lane there — this is all an analysis run
needs. Developers working on the wrapper itself add `--dev` to also install test
dependencies, then run tests as `.venv/bin/python -m pytest`. The CLI is
`.venv/bin/project-analysis-wrapper`. See `wrapper/README.md` for details.

## Tracking

Linear: team `57blocks-Project-Analysis`, project **Project Analysis**
(issues 57B-5 … 57B-20, four phase milestones with user review at each exit).
The team key stays `57B` and existing `57B-*` issue identifiers are unchanged.
