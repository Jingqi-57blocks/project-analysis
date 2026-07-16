# {{project_name}} — Project Map

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

Run `{{run_id}}`; full provenance block in `overview.md`.

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
lines. Only edges from the relationships table above — the diagram introduces no new
claims}}
```

## Shared persistence

| store / table | written by | read by | label | evidence |
|---|---|---|---|---|
| {{table_or_store}} | {{module_ids}} | {{module_ids}} | {{observed/inferred/unresolved}} | {{citations}} |

## Co-change coupling (history signal)

| module pair | shared commits (window) | interpretation | evidence |
|---|---|---|---|
| `{{a}}` ↔ `{{b}}` | {{n}} | {{one_line — coupling vs coincidence, bulk changesets excluded}} | {{git-history signal ref}} |
