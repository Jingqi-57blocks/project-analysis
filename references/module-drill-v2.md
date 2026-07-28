# Module Drill v2 contract

Module Drill recovers one evidence-bounded **feature slice** across all
repositories that implement it. A module is a broader organisational,
business, platform, or technical boundary that can help discovery; it is not
automatically the unit of analysis.

## Evidence boundary

The result may follow only evidence-backed, feature-relevant relationships:

```text
UI action → client/interface → route/handler/service → rule or state
→ data effect → job/event/notification/integration
```

Generic framework code, generated code, logging, telemetry, external systems,
and shared utilities without feature semantics are terminal boundaries. Static
reachability never proves production activation.

`source-manifest.json` is the source index for every Module Drill run. It
records snapshot identity, source mode, preparation options, provider and tool
identity, complete canonical artifacts/fragments, integrity, and two-axis
Coverage. Bounded views, report projections, and model packets can be indexes
but are never the complete fact universe.

## Contracts

All contracts are technology-neutral and versioned:

- `source-manifest/v1`: normalized overview-backed or standalone evidence
  source.
- `module-scope/v2`: deterministic candidate set and its selection, feature seeds, initial
  frontiers, and closure state.
- `module-model/v2`: nodes, edges, claims, flows, frontier dispositions,
  feature-specific Coverage, and closure.
- `module-audit/v1` and `module-run-state/v2`: audit result and a derived
  high-level projection over the authoritative ledger and validated artifacts.

Coverage always has two independent axes:

- applicability: `applicable`, `not-applicable`, or `unknown`;
- execution status: `complete`, `partial`, `unavailable`, `skipped`, or
  `failed`.

Feature closure is separate: `closed`, `open`, or `blocked`. A
`not-applicable` result requires positive evidence; an extractor returning
zero matches is insufficient.

The UI/action dimension is applicable only when a canonical UI seed or a
verified UI linkage exists. A backend-only, worker-only, or data-only feature
may therefore state `not-applicable` only with positive evidence for that
shape; otherwise it remains `unknown`. The same rule applies to every feature
dimension.

## Task protocol

Module Drill reuses the generic task packet, engine, ledger, claim/submit
protocol, retries, redaction, and safety boundary. It owns these task types:

1. `module-candidate-ranking`
2. `module-frontier-expansion`
3. `module-sync-recovery`
4. `module-async-recovery`
5. `module-model-merge`
6. `module-claim-verification`
7. `module-section-generate`

Each task has an independent output schema and packet cross-check. The later
phase owning a task defines its semantic cross-check; this contract establishes
the stable envelope and IDs first.

## Run selection and layout

The public command defined by this contract is:

```text
/project-analysis module "<feature-or-module>" [workspace]
  [--from-run <overview-run-id> | --standalone]
  [--language zh-CN|en]
  [--run-id <label>]
```

An explicit `--from-run` must match the current source snapshot or fail. An
automatically chosen accepted overview may fall back to standalone preparation
only when it is incompatible, with that fallback recorded in the source
manifest. The run layout is:

```text
output/<project-key>/modules/<run-id>/
  run-state.json
  provenance.json
  source-manifest.json
  scope-candidates.json       # only when resolution is ambiguous
  module-scope.json
  module-model.json
  coverage.json
  module.md
  details/
  tasks/
```

The task ledger and validated artifacts are authoritative. `run-state.json`
is derived. A failed final audit, stale source, unresolved mandatory frontier,
or failed mandatory lane must yield `complete=false` and a non-zero command
exit; non-mandatory gaps remain visible as partial Coverage.

## Delivery loop

Every child task starts from the shared Module Recovery feat branch, works in
an isolated worktree, and opens a PR back to that feat branch. A task proceeds
only after focused tests, three review passes, full regression, and PR checks
are green. A material scope change becomes a new dependency rather than being
silently added to the current PR. Linear state is controlled by the linked PR;
comments record only necessary acceptance evidence.

The generic three-repository fixture under
`wrapper/tests/fixtures/module_drill_v2/` is the common acceptance baseline.
It contains a UI action, API route/rule/data effect, event consumer, and
external boundary. Removing a critical linkage is specified to yield
`open`/`blocked` closure and reduced Coverage. Later tracing and semantic
recovery phases execute that expectation against real provider output.

## Current availability

The old `new-drilldown` lifecycle was a provenance-only stub and has been
retired. The new public command and run driver are introduced by the lifecycle
task after this contract is accepted; no legacy run format is supported.
