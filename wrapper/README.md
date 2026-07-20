# Project Analysis wrapper

The wrapper executes the allowlisted Phase-0 toolchain from a discovery-produced
`TargetSpec`. It invokes tools, classifies their execution status, writes provenance
manifests, and produces sanitized bounded views. It does not interpret findings or
validate reports.

Create the project-local virtual environment first. The host Python is used only
to create the environment; all packages are installed into `wrapper/.venv`.

```bash
cd wrapper
python3 -m analysis_wrapper.bootstrap          # runtime + PyDriller
python3 -m analysis_wrapper.bootstrap --dev    # also install pytest

# One tool against one stable repository ID (the output path must be new)
.venv/bin/project-analysis-wrapper --targets targets.json --out output/run/signals \
  run --repo api-11112222 --tool scc

# All applicable local tools; add --include-network only with explicit approval
.venv/bin/project-analysis-wrapper --targets targets.json --out output/run/signals sweep

# Canonical overview preparation (run dir must first be minted by `new-run`)
.venv/bin/project-analysis-wrapper prepare-overview --run output/project/overview/run-id

# Tests also use the isolated interpreter; shell activation is unnecessary
.venv/bin/python -m pytest
```

Set `--venv <path>` to keep the environment elsewhere. Re-running bootstrap is
safe and updates the same environment. `wrapper/.venv` is gitignored. Do not run
`pip install` with the host Python.

Network-capable definitions (`staticcheck`, `go list`, `osv-scanner`, and npm/yarn
outdated) are skipped unless `--include-network` is explicitly supplied, including
for the single-tool `run` command. Go tools may contact `GOPROXY` on a cold module
cache; the wrapper pins those requests to `proxy.golang.org` and
`sum.golang.org`, disables workspace/toolchain auto-dispatch, and never falls
back directly to dependency hosts. OSV sends dependency coordinates to
`api.osv.dev`; outdated checks send
package names and versions only to the fixed public npm/yarn registry. Target-owned
registry configuration and remote dependency URLs are refused rather than followed.
The orchestrator must obtain approval before this flag is used on private code.

The output directory must not already exist and must be outside every target repository.
Raw stdout/stderr stays under the self-gitignoring `signals/raw/` containment directory.
Only `*.view.txt`, manifests, and the run summary may be read by an agent. The normalized
manifest excludes volatile fields and supports byte-for-byte deterministic comparison.

`run` and `sweep` are low-level diagnostic commands. A real overview must use
`prepare-overview`, which owns the deterministic producer order and canonical artifact
locations; invoking the individual producers manually is not an alternate overview path.

PyDriller is primary for history analysis and bootstrap installs the pinned 2.10
release into the virtual environment. If the wrapper is deliberately run outside
that environment, set `PROJECT_ANALYSIS_PYDRILLER_PYTHON` to an isolated Python
containing PyDriller 2.10; otherwise the lane uses the disclosed plain-Git fallback
and reports `partial`. Commit traversal and per-file modification data come from
PyDriller; the co-change / ownership / author-roster / sampling AGGREGATION on top
of it is a tested, thin analyzer-owned layer (`git_history/worker.py`,
`git_history/identity.py`) — not a re-implementation of history parsing.

## Analyzer-owned Node toolchain, ast-grep, and SQLGlot

`bootstrap` (the one approved network step) additionally sets up a pinned,
lockfile-frozen Node toolchain under `node_tools/` via pnpm —
**dependency-cruiser 18.1.0 + typescript 5.9.3**, committed `package.json` +
`pnpm-lock.yaml`, `node_modules/` gitignored. The dependency-cruiser signal uses
only this env binary (`node_env.py`), never a global or target-resolved one; if
the env lacks `.tsx` support a TypeScript target's dependency signal is recorded
`unavailable` (fail-closed). `bootstrap --skip-node-tools` opts out.

For TS/Vite repos the depcruise lane (`depcruise_lane.py`) runs a per-run
preparation step: `node_helpers/resolve-ts-config.mjs` reads tsconfig
(baseUrl/paths/references) with the official TypeScript compiler API and
statically extracts vite `resolve.alias` from the config AST (literal mappings
only; dynamic ones reported unresolved — target config is never executed), and
`resolvers/ts_aliases.py` writes an analyzer-owned depcruise + tsconfig config
UNDER THE RUN OUTPUT DIR. The manifest records depcruise + typescript versions,
the config inputs used, and BOTH total and internal edge-resolution metrics; the
bounded view lists distinct dependency-cycle member files.

`ast-grep` (brew; validated 0.44.1) drives declarative structural rules under
`rules/` (route registration, HTTP call sites, client construction, host-fragment
constants, ORM/table usage), each shipping domain-neutral positive/negative
fixtures under `rules/fixtures/`. `astgrep.py` is the thin runner; import edges
are NEVER read from ast-grep (that is dependency-cruiser / `go list`). When
ast-grep is absent the route lane falls back to the transparent regex and the
integration/table/access-model producers fail closed (disclosed). New discovery
producers: `discovery/integrations.py` (assembled-URL host fragments + integration
packages); `discovery/tables.py` (table access-type ladder — declaration / write /
read / join-ref / same-name / unresolved — with a Go typed-constant registry and an
exact-identifier structural join for `.Table(constant.X)` accesses); an
`discovery/access_model.py` locate-and-count view (role catalogs, authz checks,
middleware, route guards, casbin policy files, identity comparisons, plus a
cross-repo role-catalog summary); and `discovery/deploy_units.py` (deployable-unit
candidates — Dockerfile / compose services / Go `package main` / CI deploy —
status `inferred` or `unknown`, never claiming completeness). `SQLGlot 30.12.0`
(bootstrap `[sql]` extra) parses raw SQL DDL for the table lane; SQL coverage
(dialect, parse failures, unparsed files) is explicit and never reported complete
on failure.

The offline Go lane records GOOS/GOARCH/CGO_ENABLED and build-tag scope in every
manifest; a cold cache / missing dep / load failure fails loudly. Warm the module
cache first under approval: `python3 -m analysis_wrapper.bootstrap --warm-go <repo>`
(`go list -deps -json` with network allowed, still `-mod=readonly`). The
git-history co-change pass takes an optional `--coupling-sample-cap` (0 = no cap,
unchanged; when exceeded, an evenly-spaced, disclosed sample is used).
