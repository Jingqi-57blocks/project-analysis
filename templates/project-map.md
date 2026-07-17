# {{project_name}} — Project Map

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

Run `{{run_id}}`; full provenance block in `technical-overview.md`. This is the reusable
topology: modules and their boundaries, how they connect, what data they share, and the
external systems on the boundary. A simplified version is embedded in `overview.md`; the
per-candidate integration disposition and all findings are in `technical-overview.md`.

## Modules

| module-id | name | classification | repos / roots | evidence | confidence | aliases |
|---|---|---|---|---|---|---|
| `{{module_id}}` | {{name}} | {{business / platform / shared-infra / unresolved}} | {{repo_relative_roots}} | {{citations}} | {{high/medium/low}} | {{prior_ids_or_none}} |

- Module IDs are stable slugs preserved across runs; renames/merges are recorded here as
  aliases, never by dropping the old ID.
- `unresolved` classification means the signals conflict or are insufficient — it is a
  work item for the user, not a soft guess.

## Relationships

Every edge carries a label: `observed` (direct evidence at the cited location),
`inferred` (multiple indirect signals, each cited), `unresolved` (suspected, evidence
insufficient), `user-confirmed` (from `confirmed_facts.md`, with record reference).

| from | to | kind | label | evidence |
|---|---|---|---|---|
| `{{module_id}}` | `{{module_id}}` | {{ui→api / endpoint→persistence / import / scheduler / notification / shared-db-table / co-change}} | {{observed/inferred/unresolved/user-confirmed}} | {{citations}} |

## Topology

```mermaid
{{full_topology — business-language labels with identifiers parenthesized; group by
repo/service; external systems on the boundary; mark unresolved edges with dashed
lines. Only edges from the relationships table above (or module→external-system
boundary edges backed by an external-systems row) — the diagram introduces no new
claims}}
```

## Shared persistence

| store / table | written by | read by | label | evidence |
|---|---|---|---|---|
| {{table_or_store}} | {{module_ids}} | {{module_ids}} | {{observed/inferred/unresolved}} | {{citations}} |

## Backend liveness (which routes the frontend actually calls)

From `discovery-report.json:route_liveness` (present only for a UI + backend
workspace). The **call ledger** is the reliable signal — it answers which
backend the frontend actually uses; the per-route rows are best-effort (leaf
routes lack their router mount prefix, so `no-direct-path-match` is NOT an
orphan list and nothing here is "dead").

| backend | UI-called routes | distinct UI call paths (ledger) | reading |
|---|---|---|---|
| `{{repo}}` | {{ui_called_count}} | {{ledger_path_count_for_its_base}} | {{one line — e.g. "still a live backend: N UI paths" or "carries the bulk of traffic"}} |

{{when parallel implementations of the same capability exist: state plainly which
domains still route to which implementation, citing the ledger. Caveat mount-prefix
limits; never call a still-referenced service dead. This is the evidence behind the
overview's system-evolution line — describe it generically (parallel implementation /
partial replacement), never assume a "migration".}}

## Co-change coupling (history signal)

| module pair | shared commits (window) | interpretation | evidence |
|---|---|---|---|
| `{{a}}` ↔ `{{b}}` | {{n}} | {{one_line — coupling vs coincidence, bulk changesets excluded}} | {{git-history signal ref}} |

## External systems

The systems on the boundary that this codebase integrates with (topology view). The full
mechanical accounting of every integration candidate — including excluded noise, with
counts that sum to the candidate total — is in `technical-overview.md`.

| system | disposition | kind | evidence |
|---|---|---|---|
| {{system}} | {{included / unresolved}} | {{storage / mail / chat / issue-tracker / directory / push / payments / …}} | {{citations}} |

`included` systems draw solid boundary edges in the topology; `unresolved` (signs present
but active use unproven) draw dashed edges. Excluded candidates are not boundary systems
and are accounted for only in `technical-overview.md`.

## Referenced but NOT analyzed

Any configured endpoint / base URL whose serving source is not among the analyzed repos.

| endpoint / base URL | referenced from | why it's flagged | status |
|---|---|---|---|
| {{endpoint}} | {{repo@commit:path:line — the config/call site that references it}} | {{why its owner cannot be confirmed from analyzed evidence}} | unresolved |
