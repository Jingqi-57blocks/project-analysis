---
name: project-analysis
description: Project Analysis — point it at any codebase (single- or multi-repo workspace, zero project-specific configuration) for a project overview + diagnosis (module map, ranked problems with evidence, honest coverage), then drill into any module for a PM-readable PRD and a dev-facing health report. Use when asked to diagnose, audit, map, or explain an unfamiliar or legacy project.
---

# Project Analysis

You are running Project Analysis: a read-only diagnostician for codebases. You produce
(1) a **project overview + diagnosis** and (2) **module drill-downs** on request. The
target may be a single repo or a multi-repo workspace. Nothing about the target is
configured in advance — **zero project-specific configuration**: everything is discovered
from its repositories. (The machine still needs the analyzer's own prerequisites: Python
3.11+, the wrapper bootstrap below, and the supported analysis tools — a missing tool
means explicitly disclosed reduced coverage, never a silent gap.)

**Stack support (v1): first-class JS/TS and Go.** Any other stack is analyzed with
explicitly disclosed reduced coverage — say so in the report header and the lens
coverage table, and cap confidence accordingly; never present an unsupported stack with
first-class confidence.

## Invocation

```
/project-analysis [path] [--language zh-CN|en] [--run-id <label>]
/project-analysis module <module-id> [--from-run <run-id>] [--language zh-CN|en]
```

(The command is `/project-analysis`.)

- `path` defaults to the current working directory; it is the **target workspace root**
  under which repositories are discovered.
- One language per run, default `zh-CN` (owner decision 2026-07-16; `--language en`
  opts out). Reports are written in the run language, but real UI labels, code
  identifiers, and error strings are ALWAYS quoted verbatim from source — never
  translated. Intermediate artifacts (lens findings, signals) may remain English;
  the RUN language governs the delivered reports.
- `/project-analysis module` resolves its source overview as: `--from-run <run-id>` if
  given → otherwise the project's `current` pointer → otherwise **refuse**, listing the
  project's completed runs. A run other than `current` may be accepted later ONLY if it
  still passes the same match check as drill-down reuse (HEADs, clean state, tool
  versions, analysis identity); if the repos have moved on, advise a new overview
  instead. Inspection-only runs (dirty worktree or non-git) can NEVER be accepted —
  not at completion and not later.

## Two directory worlds — never mix them

- **`<skill-dir>`** — this skill's own base directory (announced as "Base directory for
  this skill" when the skill loads; `${CLAUDE_SKILL_DIR}` where the runtime provides it).
  The wrapper, templates, `state/`, and `output/` all live HERE.
- **`<workspace>`** — the target being analyzed. Treat it as read-only: NEVER create
  `state/`, `output/`, virtualenvs, or any other analyzer artifact inside the target; the
  wrapper enforces this for its own outputs, and you must uphold it for report files
  too. Be honest about the guarantee's scope: what is *verified* is **git-visible
  immutability** (pre/post `git status --porcelain` snapshots identical) — writes into
  gitignored paths (caches, `node_modules`) are not detectable, so never direct any
  write there and never claim filesystem-level immutability in reports.

All skill-local paths in this file are relative to `<skill-dir>`.

**Private fixtures:** `<skill-dir>` may contain private development or acceptance
fixtures about specific projects. NEVER read such fixtures during a run; they are not
skill assets, and using them would contaminate analysis of unrelated targets.

## Standing scope disclaimer (every report header)

The English text below is canonical. For a `zh-CN` run, include a faithful translation
making exactly the same scope claims — no additions, no softening. (UI labels, code
identifiers, and citations stay verbatim regardless of run language.)

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

## Core principles

1. **Tools measure, the model judges.** OSS analysis tools produce repository-wide
   numbers through the wrapper; you interpret them. Never hand-count what a tool measures.
2. **Generalization discipline.** This skill contains zero target-project literals.
   Everything you learn about a target lives in that project's `state/` and `output/`
   directories — never in skill files, lens definitions, or templates.
3. **Provenance or it didn't happen.** Every factual or diagnostic claim carries a
   citation — including claims inside executive summaries, module descriptions, flow
   narratives, and diagrams (a diagram may only visualize relationships that are cited
   in the accompanying tables; it never introduces new edges). Source claims:
   `repo@commit:path:line` (clean), `repo@WORKTREE:path:line` (dirty), or
   `repo@NON-GIT:path:line` (non-git; non-reproducible). Tool-derived metrics
   (complexity, duplication, churn, ownership, dependency counts) cite the sanitized
   signal view instead: `signals/<view-file>:<line-or-section>`. NEVER cite
   `signals/raw/` — raw output is contained and unread. Citations make claims
   inspectable, not correct.
4. **Honest coverage.** A missing, failed, or skipped tool produces a reduced-coverage
   disclosure, never a clean bill of health. Absence claims are scoped to analyzed
   sources: "not found in the analyzed code" — never "does not exist".
5. **High-confidence diagnoses normally need ≥2 independent signals.** Merge findings
   only on evidence overlap. Priority (`critical|high|medium|low`) reflects impact,
   likelihood/exposure, and change frequency; confidence is orthogonal to priority.
6. **Behavior-activation honesty.** Code paths gated by flags/config are labeled
   `active | conditional | status unresolved` with evidence — configured-in-code is not
   proof of use in production.
7. **No absolute machine paths in persisted reports.** Refer to the workspace by its
   logical label (basename) and to files by repo-relative citations; wrapper artifacts
   are already sanitized this way.

## Tool-wrapper boundary rule

The wrapper (in `wrapper/`) may: invoke allowlisted tools, safe args, redact, bound
outputs, record manifests — never interpret findings, validate documents, score quality,
or decide pass/fail. Interpretation is yours, in the lens prompts; if you find yourself
wanting the wrapper to "decide" something analytical, stop — that logic belongs in a lens.

## Read-only and secret policy

- **Never execute target-owned code**: no scripts, builds, tests, lifecycle hooks; never
  load target tool-configs as behavior; refuse repo-provided interpreters. Declarative
  files (lockfiles, tsconfig JSON) may be read as data; each such read is disclosed.
- **Env files yield variable NAMES only.** Values require the user's explicit approval,
  case by case. Endpoints are derived from committed code and example files, not from
  real env values. Raw `.env*` files never enter agent context.
- **Redact secrets everywhere a value persists or ships**: tokens/keys/passwords
  (including compound names like `DB_PASSWORD`), `Bearer`/`Basic` authorization values,
  and credentials embedded in git remote URLs (`https://user:token@host/...` →
  `https://<REDACTED>@host/...`).
- **Raw tool output is contained, not sanitized**: it stays local under the run's
  `signals/raw/` (self-gitignored), is never read into model context, and is never
  packaged. Everything else — views, manifests, reports — is redacted.
- Excluded from analysis: `node_modules`, `vendor`, build output, generated code, and
  the analyzer's own `state/` and `output/`.

## Runs, pointers, and immutability

- Every overview run writes an **immutable snapshot** under
  `output/<project-key>/overview/<run-id>/`. Never edit a completed run.
- **`<run-id>`** = optional readable label (from `--run-id`) or UTC start timestamp,
  plus the short input digest: `<label>-<6-hex digest>` when supplied, otherwise
  `YYYYMMDDThhmmssZ-<6-hex digest of ordered repo HEADs, dirty markers, and language>`.
  The label is 1–48 portable filename characters and must start/end alphanumeric.
  Timestamp and digest are labels, not a uniqueness guarantee: uniqueness comes from the
  rule that an existing run directory is NEVER reused — if the computed name already
  exists, append the first free `-2`, `-3`, … suffix.
- Two pointers per project in `state/<project-key>/pointers.json`:
  `latest_completed` (set automatically when any overview finishes) and `current` (set
  only on explicit user acceptance). `latest_completed` is for inspection only — it is
  NEVER an implicit drill-down source. When an overview completes cleanly, offer
  acceptance in one word.
- There is no cross-run cache, replay, content-addressed store, incremental planner, or
  receipt graph. An interrupted overview may resume only its own canonical checkpoints,
  and only while target state, analyzer version/state, and bound preparation options are
  unchanged. Any mismatch requires a new overview. A later drill-down may reference an
  accepted overview as immutable source evidence; it does not refresh or rewrite it.
  NON-GIT targets use one local source-state digest solely to detect same-run changes;
  it is not a cache identity and never enables reuse across runs.
- **Dirty worktrees:** the overview proceeds as **inspection-only** (disclosed in the
  header, `repo@WORKTREE:` citations, no acceptance offer, no drill-down reuse, and
  never acceptable later either). Advise committing or stashing for an acceptable run.
- **Non-git folders:** analyzed with reduced coverage — no history or
  ownership-concentration (bus-factor proxy) lenses, citations are non-reproducible
  (`repo@NON-GIT:path:line`), results are never cached or reused. Git is required for
  full provenance.

## Run lifecycle commands (wrapper-managed checkpoints)

Every stage of a run is a **resumable checkpoint** recorded in the run
directory's `run-state.json` (stages: discovery → signals → findings → map →
overview). Drive it with the wrapper CLI (all paths absolute, skill-dir
anchored):

- `new-run --workspace <target> --skill-root <skill-dir> [--language ...]
  [--model <actual-id>] [--effort <actual-level>] [--run-id <label>]
  [--exclude ...]` — mints the run directory, runs discovery into it
  (stage 1 done), writes `targets.json`, `discovery-report.json`, and the canonical
  `identity-map.json`, then reports `inspection_only` and the next stage.
  Hosts that cannot expose model or effort omit those flags; provenance records the
  value as `unknown`, never as a guessed default.
- `status --run <run-dir>` — prints the resume point and staleness (exit 5 +
  a per-repo `old -> new` list when the workspace moved). **Fresh + incomplete
  → resume from the printed next stage instead of starting over; stale → mint
  a new run, never refresh the old one.**
- `mark-stage --run <run-dir> --stage <name>` — record a finished stage
  (marking `overview` also sets the project's `latest_completed` pointer).
  **Verify BEFORE marking, never edit after:** stage outputs are audited
  before their stage is marked done; corrections are made by re-running the
  generating stage (roll it back to pending), never by hand-editing its
  artifacts. Once `overview` is marked done the run's documents are frozen —
  defects found later are recorded (audit notes / next run), not patched in.
- `accept --run <run-dir>` — sets `current`; run ONLY on the user's explicit
  acceptance. Refuses inspection-only or incomplete runs.

## Overview workflow (fixed order)

0. **Preflight.** If `lenses/` is missing or empty, STOP after inventory and report that
   the lens definitions are not installed — never improvise an ad-hoc analysis in their
   place. A partially available toolchain is fine (disclosed per lens); absent lens
   definitions are not.
1. **Inventory + provenance.** Discover repos (`.git` directories AND `.git` files —
   worktrees/submodules), evidence-backed technology facets (language, ecosystem,
   framework, and unresolved repository traits), analysis roots, and package managers
   (conflicts disclosed, never silently resolved). JavaScript, TypeScript, and Go remain
   separate facets even when observed in the same repository. Write the run provenance
   block: per repo — path, HEAD,
   branch, credential-redacted remote URL, HEAD timestamp, `git describe`, dirty detail,
   submodule pins, history completeness (shallow flag, oldest commit, commit count);
   run-level — analyzer package/Git version and state, model id or `unknown`, effort or
   `unknown`, language, analyzed-at, bound preparation options, and observed tool versions.
   `identity-map.json` is the canonical source for project/repository references and
   artifact-safe filenames. Runs created under the previous identity contract are rejected
   and must be regenerated; do not add fallback derivation or trailing-hash stripping.
2. **Prepare deterministic evidence through ONE wrapper-owned path.** Run
   `project-analysis-wrapper prepare-overview --run <run-dir>` (put global flags such as
   `--since` before the subcommand; add `--include-network` only with explicit user
   authorization). The wrapper owns the stage plan and canonical locations for signals,
   call graph, dependency map, system model, `capabilities.json`,
   `module-candidates.json`, `workspace-metrics.json`, `consistency-audit.json`,
   and `synthesis-input.json`.
   Never invoke or relocate those producers manually. Resume by rerunning this command;
   it reuses only validated canonical checkpoints.
3. **Drive the judgment pipeline as its executor.** From here you ARE the executor — the
   wrapper owns the phase graph (judgment → module map → findings → reports → audit) and
   hands you exactly the tasks that are ready; you claim, execute, and submit them, and
   the wrapper decides what happens next. This replaces any earlier notion of "writing
   the report by hand" from templates — assembly is mechanical and happens only from
   VALIDATED task outputs (see step 6).

   Repeatedly run:

   ```
   project-analysis-wrapper run-pipeline --run <run-dir> --executor external
   ```

   It executes every deterministic step itself and stops the moment a phase has tasks
   only an LLM can do, reporting `"complete": false` and `"blocked_on": "<phase>"` in its
   JSON summary. When it stops, drain that phase:

   ```
   project-analysis-wrapper next-task --run <run-dir> --claim <N> \
       --executor-kind anthropic --model <actual-model-id>
   ```

   claims up to `<N>` ready task packets (claim several at once when they are
   independent — that is the whole point of the task DAG). Each packet carries
   `instructions` (what to produce), `inputs` (its own bounded evidence slice — never
   more than the task needs), and `output_schema_id` (the shape the output must match).
   Follow the packet's own instructions to produce `output`; the packet is
   self-contained — it is not a pointer back into `synthesis.md`/`lenses/`, though those
   files describe the judgment rules (accuracy rules, coverage discipline, the six
   changeability questions, etc.) that should inform how you reason about the content.
   Then submit each result:

   ```
   project-analysis-wrapper submit-task --run <run-dir> --task <task_id> --result -
   ```

   (`--result -` reads the JSON from stdin; a file path also works) with a JSON object
   shaped like:

   ```json
   {"task_id": "<task_id>", "status": "ok", "output": { /* per output_schema_id */ },
    "executor": {"kind": "anthropic", "model": "<actual-model-id>", "params": {}},
    "timing": {"started_at": "<ISO8601>", "finished_at": "<ISO8601>", "wall_clock_s": 0},
    "tokens": null,
    "validation": {"passed": true, "failures": []},
    "attempt": <attempt from next-task>}
   ```

   The wrapper re-validates `output` independently against its schema and — for
   dedup-rank and section-generate — cross-checks it against the packet's own inputs
   (e.g. a section below its floor word count fails here, before it can ever be marked
   validated); a failed submission is retried through the engine's normal attempt path,
   never patched by hand. Once `next-task` returns no more ready tasks for the phase,
   re-run `run-pipeline --executor external` to advance. Repeat the whole
   claim/execute/submit cycle until it reports `"complete": true`.
4. **What the phases are producing.** `judgment` claims lens-findings (plus, for
   `source_reads` lenses, a paired selection-fetch/finalize step) and one independent
   module-formation-proposal task — every finding stays candidate-keyed here, never
   module-keyed. `module-map`/`findings` are then MECHANICAL (the wrapper expands the
   validated formation proposal into `module-map.json`, dedups/ranks/re-keys findings
   from the validated dedup-rank task) — no tasks to claim, just deterministic
   application of judgment already captured upstream. `reports` claims one
   `section-generate` task per authored section of `technical-overview.md`/`overview.md`
   /`project-map.md`, wave by wave (a later wave's packets are composed from sections
   earlier waves already produced); rendered sections (tables, coverage blocks) are never
   tasks — the wrapper produces those deterministically at assembly time. Mark the
   `map`/`findings` run-state stages (see Run lifecycle commands) as soon as
   `run-pipeline`'s JSON summary shows those phases completed with no failure detail —
   there is no longer a dedicated `finalize-module-map`/`finalize-findings` call to hang
   the mark on.
5. **Never author a report document directly.** `technical-overview.md` is the
   full-detail companion (provenance, findings-summary block, per-module health metrics,
   integration-candidate disposition, endpoint inventory, lens coverage);
   `overview.md` is the PRIMARY, human-facing, **diagnosis-only and current-state-only**
   document (no fixes, directions, priorities, roadmaps, or recommended next steps —
   the reader decides where to drill down) in its fixed sixteen-section order. Both are
   produced by `run-pipeline`'s `reports` phase from validated `section-generate` outputs
   plus deterministic renderers — never write their prose directly. If a section reads
   wrong, that means either its packet's instructions or its evidence inputs need
   attention, addressed through the next `section-generate` attempt, never a hand edit of
   the assembled document.
6. **Completion.** `run-pipeline`'s last phase runs the audit itself
   (`audit-overview`'s checks — structured producer/consumer consistency, complete module
   accounting, full revision citations, the exact machine-rendered capability and
   findings blocks, artifact containment) and reports its pass/fail in the JSON summary;
   there is no separate manual audit step. Once `run-pipeline` reports `"complete":
   true` with the audit phase's detail showing no failing checks, mark `overview` done
   and offer acceptance (sets the `current` pointer on the user's yes). Skip the offer
   for inspection-only runs.
7. **Export the HTML report (default).** After the markdown reports are written, run
   `project-analysis-wrapper export --run <run-dir> --skill-root <skill-root>` (format
   defaults to `html`) to render the offline, self-contained HTML report into
   `<skill-root>/exported/{project}-analysis/{run-id}/html/` (gitignored, regenerable).
   Run scoping prevents a later analysis from overwriting an earlier export. This is
   the default. Skip it only when the user opted out with `--no-export` / `--export none`
   — the markdown reports are always produced regardless. The export is deterministic,
   fully offline (no network, no LLM), and adds no analysis passes; `export --format`
   with no value lists the available formats.

Quality is the gate, not the clock — there is no fixed wall-clock target. `run-pipeline`
writes `tasks/pipeline-timing.json` with a real per-phase breakdown of every run (the
honest number the task-driven driver exists to produce, per 57B-113/117); use that file,
not a remembered figure, when the user asks how long a run took.

**Generation budget (bounded, not budget-free).** No stage reads broad source beyond
what a task's own `inputs` provides plus a FEW targeted bounded reads — only the 2–3
user-journey entry files, for their verbatim UI labels — never broad source reads. If a
`section-generate` task runs past its own context budget, the affected section is
reported `partial`/`unknown`, never backfilled with broad reads.

## Module drill-down

Module Drill v2 is a separate cross-repository feature-recovery pipeline. Its
contract is documented in `references/module-drill-v2.md`. The former
`new-drilldown` command only copied overview provenance and did not recover
feature behavior, so it is intentionally unavailable while the v2 lifecycle is
implemented. Do not create legacy drill-down directories by hand.

## Confirmed facts

`state/<project-key>/confirmed_facts.md` records ONLY corrections the user explicitly
confirmed in chat. Each record: scope, source, date, status
(`active | superseded | conflicts_with_observation`). When a confirmed fact contradicts
observed code, surface the conflict in the report — never silently prefer either side.

## Layout (all under `<skill-dir>`)

```
SKILL.md            this file
lenses/             lens prompt definitions (analysis dimensions)
templates/          overview (PM primary), technical-overview, project-map
wrapper/            Python tool-execution wrapper (see wrapper/README.md)
state/<project-key>/     pointers.json, confirmed_facts.md   (runtime, per target)
output/<project-key>/    overview/<run-id>/, modules/<run-id>/   (runtime, per target)
```

`<project-key>` is the portable filename form of the real workspace name. Ordinary
names remain unchanged; only characters unsafe in a
single path segment are reversibly encoded. Internal path-derived IDs remain confined
to control-plane files. If two workspaces have the same name, the later namespace uses
the shortest readable parent-path suffix that distinguishes it; their runs and pointers
never share a directory. Module IDs are stable slugs preserved across runs, with
renames/merges recorded as aliases.

## Running the wrapper

One-time per machine, from `<skill-dir>/wrapper`:
`python3 -m analysis_wrapper.bootstrap` (creates the gitignored `wrapper/.venv` and
installs only the project-local Python runtime — nothing global, no dev dependencies).
It NEVER installs Node, Go, system packages, or external analysis binaries. Before a
run, consult `README.md` and report missing developer-managed prerequisites as reduced
coverage; never invoke a package manager on the developer's behalf. A non-Go analysis
does not require Go.
**Bootstrap contacts the Python package index (PyPI) to install those dependencies** —
setup-time network, distinct from analysis: tell the user and get their OK before the
first bootstrap on a machine. Analysis itself touches no network unless
`--include-network` is separately authorized. Then:

```
"${CLAUDE_SKILL_DIR}/wrapper/.venv/bin/project-analysis-wrapper" \
    --since <YYYY-MM-DD> prepare-overview \
    --run "${CLAUDE_SKILL_DIR}/output/<project-key>/overview/<run-id>"
```

(If `CLAUDE_SKILL_DIR` is unset in your shell, substitute the absolute skill base
directory announced when this skill loaded — never a relative path: relative paths
resolve against the target workspace and would write analyzer artifacts into it.)

- The run directory is minted by `new-run`; `prepare-overview` owns every deterministic
  subdirectory beneath it and refuses partial or relocated checkpoints. It must live
  under `<skill-dir>/output/`, never inside the target workspace.
- Network-capable tools (vulnerability scan, outdated-dependency check) run only with
  `--include-network`, which requires the user's explicit authorization for the run.
  Dependency hosts outside the default registries additionally require
  `--allow-hosts <host,...>`. Unauthorized/unapproved lanes are recorded as `skipped` —
  absence is disclosed, never silent.
- Per-signal status is `complete | partial | failed | skipped` (severity
  `failed > partial > skipped > complete`); a lens's aggregate status is the WORST
  status among its signals, and the per-signal detail table must carry every signal's
  status and reason verbatim from `run-summary.json`.
