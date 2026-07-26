# Compatibility (upgrade safety)

**v1 detects, guides, and preserves — it does not auto-migrate.** Upgrading Project
Analysis's code must never lose data, never silently mix artifacts produced under
incompatible schemas, and never block a brand-new overview because an old run happens
to sit on disk.

## Compat identity

Compatibility is decided from three things, together:

- **code version** — `compat.ARTIFACT_CONTRACT_VERSION`: the artifact contract THIS
  CODE actually reads and writes, not the skill's own release `VERSION`. The two move
  on independent schedules (a code release can ship with no artifact-contract change
  at all, or vice versa), so only the contract this code was built against may decide
  whether it can resume another run's artifacts — see "Version stamping" below.
- **artifact-schema version** — the `compat.artifact_contract_version` stamped into a
  run's `run-provenance.json` at mint time (see "Version stamping" below).
- **runtime contract** — `analysis_wrapper.paths.RUNTIME_CONTRACT`, which already
  versions the generated-runtime tree (venv, `node_tools/`, the Go `callgraph` binary).

This is explicitly **not** the run's or the analyzer's absolute install path. The same
code checked out at a different path — a fresh `git clone`, a CLI-managed symlink, a new
version directory — is exactly as compatible as it was at the old path.
`run_provenance.analyzer_staleness()` already only compares `version`/`git_head`/
`dirty_detail`/`source_state_sha256`, never the recorded `root`; `compat.py` follows the
same rule and never keys a decision on an absolute path either.

## Outcome vocabulary

Every object one of these functions reasons about gets exactly one outcome:

| Outcome      | Meaning                                                              |
|--------------|-----------------------------------------------------------------------|
| `readable`   | The artifact can be opened/read as-is; nothing about it is rewritten.  |
| `resumable`  | An incomplete run may safely continue from its next pending stage.    |
| `migratable` | Reserved for a future version once an actual migration path exists — v1 never returns this. |
| `unsupported`| The artifact cannot be used by this code; the remedy is always "mint a new run", never an automatic rewrite. |

Applied per object:

- A **completed run's reports** → always `readable`. This is a filesystem invariant —
  nothing in this codebase ever rewrites or deletes a finished run — not a
  matrix-conditioned decision.
- An **incomplete (resumable-shaped) run whose schema is incompatible** with the
  current code → `unsupported`, i.e. **resume is refused**. This is the real hazard:
  resuming would combine artifacts produced under two different schemas in one run
  directory. The only remedy is a new run.
- A **new overview** → never blocked by any existing run, compatible or not. Minting one
  never even inspects another run's schema first.
- **The runtime** (venv / `node_tools/` / Go tool versions vs. `tools/manifest.json`
  pins) → `reconcile` (run `setup`) or an explicit, opt-in `accept-as-degraded`
  (`PROJECT_ANALYSIS_ACCEPT_DEGRADED_RUNTIME=1`) — never silently degraded on its own.
- **Old schema data** in general → `readable`, `migratable`, or `unsupported` per the
  compat matrix below.

## The compat matrix

`analysis_wrapper/compat.py`'s `COMPAT_MATRIX` is the single declarative table. Each row
is one `(code family, artifact-schema family)` combination:

| Code family  | Schema family | completed run | incomplete run | new overview |
|--------------|----------------|----------------|-----------------|--------------|
| pre-3.0.0    | pre-3.0.0      | readable       | resumable       | readable     |
| post-3.0.0   | pre-3.0.0      | readable       | **unsupported** (refuse resume) | readable |
| post-3.0.0   | post-3.0.0     | readable       | resumable       | readable     |
| pre-3.0.0    | post-3.0.0     | readable       | unsupported     | readable     |

Every row's **completed run** column is `readable`: a completed run's files are never
rewritten or deleted regardless of which code family produced them, so "readable" is a
filesystem invariant, not something the matrix conditionally grants. (An earlier
version of this row read `unsupported` here while `compat.completed_run_outcome()`
unconditionally returned `readable` anyway — outcome and doc contradicted each other;
`readable` is the corrected, single source of truth. "Forward compatibility is not
promised" for the last row describes RESUMING or re-processing that run with older
code, never reading its already-written files.)

**Family granularity note:** today the matrix only knows about ONE break boundary
(`3.0.0`), so every version `>= 3.0.0` collapses into the single `post-3.0.0` family —
there is no way yet to distinguish a hypothetical `4.0.0` break from `3.0.0` itself. A
future SECOND artifact-contract break needs its own new family boundary (another row,
following the same "add rows, never rewrite existing ones" discipline `_BREAK_VERSION`
already documents) — it must not be shoehorned into the existing two-family table.

### The 3.0.0 artifact-contract break

A separate workstream is landing a deliberate artifact-contract break at `3.0.0`, **with
no migration by design** — this is the second matrix row above:

- Completed reports made under the old (`pre-3.0.0`) contract **remain readable as
  files** — open them directly, they are never rewritten.
- An **incomplete** `pre-3.0.0` run refuses to resume once the code has moved to
  `post-3.0.0` — resuming would silently combine two incompatible schemas in one run
  directory.
- A **new overview is never blocked** by the presence of an old, now-incompatible run.
  Re-run the analysis; old runs are re-run, not migrated.

## Version stamping

Every fresh run's `run-provenance.json` carries a `compat` block (written by
`run_provenance.create_document`, sourced from `compat.compat_stamp()`):

```json
"compat": {
  "skill_version": "1.0.0-dev",
  "artifact_contract_version": "2.0.0",
  "runtime_contract": "1"
}
```

This is additive — no `run_provenance.SCHEMA_VERSION` bump — so a run minted before this
stamping existed remains loadable; `compat.run_schema_family()` treats a missing/absent
`compat` block as the `pre-3.0.0` family, exactly like every other pre-stamp run.

## Runtime drift and the entry-point guard

`doctor` already probes every manifest tool and computes drift against
`validated_version`/`accepted_range`. `compat.runtime_reconciliation()` reuses that
exact probing/drift logic (never duplicates it) and narrows it to **pinned** tools —
the ones a code upgrade can silently outpace: the analyzer package itself,
`dependency-cruiser`, `typescript`, `go-callgraph`, and the pip-installed history/SQL/
report extras. It also asks `doctor.build_report()` to probe ONLY those pinned tool
ids (`tool_ids=...`) rather than the full manifest, so every gated CLI invocation
spawns roughly half as many version-probe subprocesses as calling the unrestricted
`doctor` command would.

A tool that was **never installed at all** is a first-run/setup situation, not an
upgrade-compat hazard, so it does not count on its own. But **some pinned tools
present while others are absent** (a *partial install*) DOES count as drift, even
though no single tool's own detected version disagrees with its pin: new code needing
a pinned tool this checkout's `setup` has never provisioned is exactly the
half-reconciled-runtime hazard this guard exists to catch. Only the "every pinned tool
is absent" shape — the ordinary pre-`setup` state — is exempt.

`compat.guard_entry(command)` is called once, early, in every wrapper invocation
(`cli.main`). Every subcommand is explicitly classified in
`compat.COMMAND_CLASSIFICATION` as gated or not-gated — an unclassified command is
unreachable in practice because `test_every_subcommand_is_classified` fails the build
the moment one is added without a classification, and (fail closed, belt-and-braces)
`guard_entry` treats anything still missing from the table as gated by default.

**The diagnosis-and-remedy path always stays open**, precisely because these commands
are never gated:

| Command | Why it must never be gated |
|---|---|
| `doctor` | Diagnoses the very drift this guard reports. |
| `setup` | **The remedy** the guard's own refusal message points at — gating it would make a drifted runtime unrecoverable. |
| `migrate` | One-time data-root relocation, not an analysis run. |
| `status` | Read-only inspection of an existing run's stage/staleness; never mutates. |
| `export` | Renders an *existing* (possibly completed) run's artifacts to files — the compat matrix promises a completed run's reports stay `readable`, which this guard must not contradict. |
| `compare-runs` | Read-only diff between two existing runs' artifacts. |
| `list`, `help` | Informational; not registered subcommands yet, but pre-classified so they stay exempt the moment a later phase adds them. |
| `--version` | N/A to this guard — argparse's `action="version"` exits inside `parser().parse_args()`, before `cli.main` (and therefore `guard_entry`) ever runs. |

So a user who hits the refusal can always run `doctor` to see the drift, `setup` to
reconcile it, and `status` / `export` / (later) `list` to keep working with results a
prior, still-compatible run already produced.

Everything that **executes** analysis or **creates/advances/mutates** a run stays
gated: `new-run`, `new-drilldown`, `prepare-overview`, `discover`, `callgraph`,
`dependency-map`, `system-model`, `finalize-findings`, `finalize-module-map`,
`audit-overview`, `mark-stage`, `rollback`, `accept`, `run`, `sweep`.

`guard_entry`:

- does nothing for a not-gated command (see table above);
- otherwise refuses the command with an actionable message — naming exactly which
  tool(s) drifted and that `setup` reconciles it — if the runtime is out of sync;
  the same message also names the `PROJECT_ANALYSIS_ACCEPT_DEGRADED_RUNTIME=1` escape
  hatch for a user who wants to proceed anyway;
- is cheap and fully offline (no network, no heavy work of its own) — it only reuses
  `doctor`'s already-offline probes.

`PROJECT_ANALYSIS_ACCEPT_DEGRADED_RUNTIME=1` is only ever consulted AFTER a drift is
actually detected — an inherited-but-unneeded env var (a shell profile, CI) is
therefore never silently mistaken for "nothing to check". When it does suppress a
real, detected drift, `guard_entry` prints a one-line `wrapper warning:` to stderr so
the bypass is visible in the terminal, and the detail is threaded into whichever run
this invocation mints (`new-run`/`new-drilldown`): the fresh run's `compat` stamp gains
a `degraded_runtime_accepted` key recording exactly what was accepted, so that run is
never forensically indistinguishable from one minted under a clean runtime.

## The run-level guard: resuming an EXISTING run

`guard_entry` only ever checks the installed **runtime** against this code's manifest
pins — it has no notion of which run directory a gated command is about to advance, so
it cannot by itself catch the "resuming an old-schema run under new code would mix two
artifact contracts in one run directory" hazard the outcome vocabulary above describes.
That is `compat.guard_run(run_dir)`'s job: it calls `refuse_incompatible_resume` and is
wired into `cli.main()` at one choke point for every command that takes an **existing**
run via `--run` and writes more into it — `mark-stage`, `rollback`, `system-model`,
`prepare-overview`, `finalize-module-map`, `finalize-findings`, `audit-overview` — plus
a second call site for the top-level `callgraph`/`dependency-map` subcommands, which
layer into an existing run directory via `--out` instead of `--run`
(`executor.use_existing_run_directory`). Plain `run`/`sweep` need no such call: their
`--out` always demands a brand-new directory (`executor.prepare_output_directory`
refuses an existing path outright), so they can never reach an incompatible existing
run in the first place.

`status`, `export`, `compare-runs`, and `accept` are deliberately **not** covered:
`status`/`export`/`compare-runs` only ever read a run (the matrix's `readable`
guarantee for a completed run must hold even mid-refusal), and `accept` only ever
applies to an ALREADY complete run (`lifecycle.Pointers.accept` enforces this) and
writes only an external pointer file, never into the run directory itself. Minting a
brand-new run (`new-run`/`new-drilldown`) is never gated by `guard_run` either — see
`new_overview_outcome`.

## Upgrading, per channel

1. **git clone / checkout** — `git pull` (or check out the new tag/commit) inside
   `<skill-dir>`. This never touches `<data-root>` (persistent output/state/exported,
   or the generated runtime) — see `paths.py`'s "three directory worlds".
2. **Skills CLI–managed install** — use its `update` verb.
3. Either way, **then run `setup`** to reconcile the runtime (venv / `node_tools/` /
   go tools) with the new code's manifest pins. `doctor` (before or after) reports
   exactly what, if anything, is out of sync.

Old runs are never migrated by any of the above. Completed ones stay readable; an
incomplete one either resumes (same schema family) or refuses resume with a pointer to
mint a new overview instead.
