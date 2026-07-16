# {{project_name}} — Project Overview & Diagnosis

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

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
evidence points to but that were absent or excluded, each with the pointing evidence}}
**Exclusions applied:** {{tier1_and_tier2_exclusions_per_repo — disclosed, with the
evidence that derived each Tier-2 entry}}

## Project map

```mermaid
{{topology_diagram — business-language labels with identifiers parenthesized;
UI→API, API→persistence, cross-service, scheduler, external systems. The diagram only
visualizes relationships cited in project_map.md — it introduces no new edges}}
```

| module | classification | one-line purpose | evidence | confidence |
|---|---|---|---|---|
| `{{module_id}}` | {{business/platform/shared-infra/unresolved}} | {{purpose}} | {{evidence_citations}} | {{high/medium/low}} |

Full map with relationship labels: [`project_map.md`](project_map.md)

## External systems (candidate disposition)

| candidate | signal kind(s) | disposition | evidence |
|---|---|---|---|
| {{system}} | {{one_or_more_of: import / client_init / outbound_endpoint / config / env / oauth_provider / ci_resource — or `dependency-only` when dependency/lockfile entries are the ONLY signals}} | {{included / unresolved / excluded}} | {{citations}} |

Dependency-only or lockfile-only signals never prove an active integration; such rows are
`unresolved` unless corroborated.

## Top problems

{{ordered by priority; each rendered from the shared finding shape:}}

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

## Lens coverage

A lens's status is the WORST status among its signals
(`failed > partial > skipped > complete`); the lens→signal mapping comes from the lens
definitions. A skipped or failed lens means **unknown**, not healthy.

| lens | signals (tool × repo) | status | summary |
|---|---|---|---|
| {{lens}} | {{signal_list}} | {{worst_signal_status}} | {{one_line — what is and is not covered}} |

### Per-signal detail

Statuses and reasons copied **verbatim** from `signals/run-summary.json` — one row per
signal, no omissions.

| signal | repo | status | reason (verbatim) |
|---|---|---|---|
| {{tool}} | {{repo_id}} | {{status}} | {{reason_or_empty}} |

## Assumptions & open questions

{{numbered; each: the assumption/question, why it matters, what evidence would resolve
it, and — where applicable — the `status unresolved` findings that hinge on it}}

---
<!-- ONLY for clean (non-inspection-only) runs, end with the acceptance offer: -->
*Accept this run as `current` (enables module drill-downs against it)? Reply "accept".*
