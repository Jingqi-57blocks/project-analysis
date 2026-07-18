# Toolchain reference (generic)

The validated open-source toolchain the analyzer drives, and the principles behind
how it is invoked. This document is **target-neutral**: it describes the tools and
the safe-invocation contract, not any specific analyzed project. The per-target
validation evidence (the Phase-0 spike, bake-offs, and acceptance runs on real
repositories) lives in a **private acceptance store outside this repository** and is
never shipped.

## Principle

OSS tools produce repository-wide numbers; the model produces judgment. The wrapper
invokes each tool with **safe flags** (project-owned configs disabled or replaced by
ours, no lifecycle scripts, vendored/build/generated trees excluded), redacts secrets,
bounds output, and records a per-signal manifest with the tool version, exact command,
exit code, and coverage. It never interprets findings.

The toolchain: `scc` (LOC/structure), `lizard` (complexity), `jscpd` (duplication),
`dependency-cruiser` (JS/TS import graph + cycles), `go list -deps -json` (Go package
deps), `golang.org/x/tools/cmd/callgraph` VTA (Go call graph), the pinned TypeScript
compiler (JS/TS call graph), `ast-grep` (declarative structural rules), SQLGlot (SQL
DDL), PyDriller (git history), and `osv-scanner` + `npm/yarn outdated` (dependency
risk — network lane, off by default, `--include-network` to authorize).

## §1 Version pinning & reproducibility

Node tools (dependency-cruiser, TypeScript) are pinned by lockfile in the analyzer-owned
`wrapper/node_tools`; the Go call-graph tool is pinned by version into an analyzer-owned
`GOBIN`. **`ast-grep` is intentionally NOT pinned** (its package manager cannot pin a
formula version cleanly). Reproducibility for the `ast-grep` `scan()` lanes therefore
rests on **runtime version recording**: each run probes `ast-grep --version`, records it
(and the resolved binary path) on every scan-derived signal, and discloses **drift**
against the validated version rather than failing. Code that depends on this contract
(`astgrep.py`, `tooldefs.py`, `discovery/integrations.py`) should stay in sync with this
section.

## Exclusions

Every lane excludes vendored and generated trees (`node_modules`, `vendor`, `dist`,
`build`, `coverage`, generated API docs, minified bundles, migrations where the lane is
about production source). Per-repo "tier-2" exclusions (generated/boilerplate directories)
are derived mechanically per target, never hard-coded to a specific project.

## Network lane

`osv-scanner` and `npm/yarn outdated` are the only network-capable tools. They are
**skipped unless explicitly authorized** with `--include-network`; without it the
dependency-risk signal is recorded as `skipped/unknown`, never as "no vulnerabilities".
They read lockfiles and query public vulnerability/registry endpoints; they install
nothing and never modify the target.
