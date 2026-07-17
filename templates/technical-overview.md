# {{project_name}} — Technical Overview & Diagnosis

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

<!--
This is the full-detail companion to overview.md (the primary, human-facing document).
It carries the complete evidence: full provenance, every finding in the shared shape,
per-module health metrics, the complete integration-candidate disposition, and the lens
coverage tables. overview.md links here for detail; nothing here may contradict it.
Run language governs prose (default zh-CN); code identifiers, UI labels, citations, and
status vocabulary (`observed`/`inferred`/`unresolved`, `included`/`excluded`,
`complete`/`partial`/`failed`/`skipped`, priorities) stay verbatim. Structural section
headings stay English here so cross-references (`technical-overview.md#top-problems`)
remain stable.
-->

## Contents

{{table_of_contents — one line per `##` section below, in order, as markdown anchor
links; generate it LAST so it matches the final document exactly}}

<!-- ONLY when any target was dirty or non-git, add this second blockquote: -->
> **Inspection-only run:** one or more targets were dirty worktrees (or non-git folders)
> at analysis time. Citations use `repo@WORKTREE:`/`repo@NON-GIT:` forms, this run cannot
> be accepted as `current`, and drill-downs will not reuse it.

## Run provenance

- **Run:** `{{run_id}}` · analyzed at {{analyzed_at_utc}} · language `{{language}}`
- **Doctor:** version {{doctor_version}} · wrapper {{wrapper_version}} · model `{{model_id}}`
- **Workspace:** `{{workspace_label — basename/logical name ONLY; absolute machine paths
  must never appear in persisted reports}}` → project-id `{{project_id}}`

| repo | HEAD | branch | describe | HEAD date | remote (redacted) | dirty | history |
|---|---|---|---|---|---|---|---|
| {{repo_id}} | {{head_short}} | {{branch}} | {{git_describe}} | {{head_timestamp}} | {{remote_redacted}} | {{dirty_detail}} | {{history_completeness}} |

Submodule pins: {{submodule_pins_or_none}}

## Executive summary

{{three_to_six_sentences: what this project is, its shape, and the 2–4 problems that
matter most — written for someone deciding where to invest engineering time. Every
factual claim carries a citation (source or `signals/<view>:<row>`), or restates a
cited finding/table row below — no uncited claims}}

## Analysis scope

**Analyzed:** {{analyzed_repos_roots_and_source_universe}}
**Referenced but NOT analyzed:** {{referenced_not_analyzed — systems/repos/paths that
evidence points to but that were absent or excluded, each with the pointing evidence;
the topology-level detail lives in project-map.md}}
**Exclusions applied:** {{tier1_and_tier2_exclusions_per_repo — disclosed, with the
evidence that derived each Tier-2 entry}}

Topology, module classification, relationships, and shared persistence are in
[`project-map.md`](project-map.md).

## Top problems

{{ordered by priority, then confidence, then breadth of affected modules; systemic
findings (one root cause across repos) appear ONCE with per-repo evidence rows beneath.
Each rendered from the shared finding shape:}}

### {{n}}. {{claim}} — `{{priority}}`
- **Lens:** {{lens}} · **Confidence:** {{confidence}}
- **Affected modules:** {{module_ids}}
- **Evidence:** {{citations_with_one_line_each}}
- **Impact:** {{why_this_matters}}
- **Limitations:** {{what_this_finding_cannot_see}}
- **Suggested direction:** {{direction_not_prescription}}

## Module health table

| module | complexity | duplication | churn×complexity | ownership concentration | dependency risk | notable |
|---|---|---|---|---|---|---|
| `{{module_id}}` | {{...}} | {{...}} | {{...}} | {{...}} | {{...}} | {{one_liner}} |

Absence of a finding in a cell is `no concern observed` scoped to the signals that ran —
never "healthy". A cell whose signal did not run or resolve is `unknown`.

## External systems (candidate disposition)

The mechanical accounting of EVERY integration candidate: rows out = candidates in
(group same-system candidates into one row). Disposition answers "does THIS codebase
actively integrate with that system?" — not "did we analyze the external system's own
source". Counts must sum to the candidate total.

| candidate | signal kind(s) | disposition | evidence |
|---|---|---|---|
| {{system}} | {{one_or_more_of: import / client_init / outbound_endpoint / config / env / oauth_provider / ci_resource — or `dependency-only` when dependency/lockfile entries are the ONLY signals}} | {{included / unresolved / excluded}} | {{citations}} |

- `included` — client init, outbound calls, or config binding it into a live path
  (production reachability stays unknowable per the disclaimer; that does NOT demote it).
- `unresolved` — signals exist but active use is unproven (dependency-only, env name
  without a call site, orphaned config). Never counted in `excluded`.
- `excluded` — not an external integration (frameworks/libraries, CI base images,
  doc/noise URLs, the project's own hosts); exclusion requires proven ownership.

Dependency-only or lockfile-only signals never prove an active integration; such rows are
`unresolved` unless corroborated. One disposition per candidate — no double-counting.

## Lens coverage

A lens's status is the WORST status among its REQUIRED signals
(`failed > partial > skipped > complete`); the lens→signal mapping comes from the lens
definitions. Where an OPTIONAL sub-capability is unavailable but the lens's required
signals are complete, the status is `partial — <capability> unavailable`, not the worst
signal. A skipped or failed lens means **unknown**, not healthy.

| lens | signals (tool × repo) | status | summary |
|---|---|---|---|
| {{lens}} | {{signal_list}} | {{status}} | {{one_line — what is and is not covered}} |

### Per-signal detail

Statuses and reasons copied **verbatim** from `signals/run-summary.json` — one row per
signal, no omissions.

| signal | repo | status | reason (verbatim) |
|---|---|---|---|
| {{tool}} | {{repo_id}} | {{status}} | {{reason_or_empty}} |

## Assumptions & open questions

{{numbered; each: the assumption/question, why it matters, what evidence would resolve
it, and — where applicable — the `status unresolved` findings that hinge on it.
Questions a re-run or a producer COULD answer are coverage gaps (name the knob), not
open questions}}
