# Project Analysis

A portable Agent Skill that examines a codebase (single- or multi-repo workspace) with
zero target-specific configuration and produces:

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
topology.

## Quick start

Project Analysis is not a server and has no daemon to start. Set up its isolated Python
environment, register the checkout as a skill, start a new agent session, and invoke it.

### 1. Clone and initialize the Python environment

```bash
git clone <repository-url> project-analysis
cd project-analysis
cd wrapper
python3 -m analysis_wrapper.bootstrap
cd ..
```

Bootstrap requires Python 3.11+ and installs only this project's Python packages into
the gitignored `wrapper/.venv`. It never installs or changes Node, Go, Homebrew, nvm,
asdf, mise, or standalone analysis tools.

Confirm the wrapper is available:

```bash
wrapper/.venv/bin/project-analysis-wrapper --help
```

### 2. Prepare only the language lanes you need

For a JS/TS target, select Node with your normal version manager. The committed
dependency-cruiser version supports Node `22.x`, `24.x`, or `26+`. Then prepare the
analyzer-owned packages yourself:

```bash
pnpm install --dir wrapper/node_tools --frozen-lockfile --ignore-scripts
```

For a Go target, provide a Go runtime compatible with both the target and callgraph
`v0.48.0` (Go 1.25+), plus `staticcheck`. If callgraph coverage is required, install it
yourself from the skill root:

```bash
mkdir -p wrapper/go_tools/bin
GOBIN="$PWD/wrapper/go_tools/bin" \
  go install golang.org/x/tools/cmd/callgraph@v0.48.0
```

Skip both sections when the target contains neither JS/TS nor Go.

### 3. Register the checkout

Choose one or more clients. These commands link the checkout; they do not copy it or
install software globally.

```bash
# Codex
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD" "${CODEX_HOME:-$HOME/.codex}/skills/project-analysis"

# Claude Code
mkdir -p "$HOME/.claude/skills"
ln -s "$PWD" "$HOME/.claude/skills/project-analysis"

# Cursor
mkdir -p "$HOME/.cursor/skills"
ln -s "$PWD" "$HOME/.cursor/skills/project-analysis"
```

Do not use `ln -sf`: if a destination already exists, inspect it and decide manually
whether it should be removed or retained. Start a new client session after registration.

### 4. Run an overview

Use the syntax supported by the client:

```text
# Codex
$project-analysis Analyze /absolute/path/to/project --language zh-CN

# Claude Code or Cursor
/project-analysis /absolute/path/to/project --language zh-CN
```

Use `--language en` for English output and `--run-id <label>` for a readable run label.
The skill writes Markdown under `output/` and an offline HTML export under `exported/`.

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

## Environment and coverage

Project Analysis reports missing tools as reduced coverage; it never changes a
developer's language runtime or global toolchain. Install only the lanes you need, using
your preferred manager (`nvm`, `asdf`, `mise`, Homebrew, system packages, and so on):

- Python 3.11+: wrapper, PyDriller history analysis, SQL parsing, and HTML rendering.
- Git: history, ownership/co-change evidence, and reproducible revision citations;
  non-git folders remain supported with disclosed reduced coverage.
- `scc`: repository-wide size and language inventory.
- `lizard`: complexity metrics.
- `jscpd`: within-repository and same-language cross-repository duplication.
- `ast-grep`: structural route, integration, table, and access-model discovery.
- Node + pnpm + analyzer-owned dependency-cruiser/TypeScript: JS/TS dependency and call
  graphs. Node may be supplied by nvm, asdf, mise, or another developer-selected manager.
- Go + `staticcheck` + callgraph: Go dependency, quality, and call-graph lanes. These are
  unnecessary for non-Go targets.
- `osv-scanner`: optional vulnerability evidence; the network lane remains disabled
  unless the user explicitly authorizes it for the analysis run.

The project intentionally does not prescribe how Node, Go, or standalone binaries are
installed. Validated tool versions and invocation details are listed in
[`tools/README.md`](tools/README.md); actual versions and resulting coverage are recorded
in every run's manifests. Developers working on the wrapper add `--dev` to bootstrap and
run tests with `wrapper/.venv/bin/python -m pytest`.

## Tracking

Linear: team `57blocks-Project-Analysis`, project **Project Analysis**
(issues 57B-5 … 57B-20, four phase milestones with user review at each exit).
The team key stays `57B` and existing `57B-*` issue identifiers are unchanged.
