# {{module_name}} — Module PRD

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation — for this document,
> ideally from someone who operates the product.

- **Module:** `{{module_id}}` ({{classification}}) · aliases: {{aliases_or_none}}
- **Source overview run:** `{{source_overview_run}}` · this drill-down: `{{run_id}}`
- **Repos / roots:** {{repo_relative_roots}} · language `{{language}}`

## What this module is

{{two_to_four_sentences_in_product_language — what it does for whom; no code identifiers
unless parenthesized after the business term}}

Evidence: {{citations_backing_the_description — kept out of the prose so it stays
PM-readable, but every claim above must trace to one of these}}

## UI entry points
<!-- include only if the module has user-facing surface -->

| where the user sees it | label (verbatim) | what it leads to | evidence |
|---|---|---|---|
| {{screen_or_area}} | "{{verbatim_ui_label}}" | {{outcome}} | {{citations}} |

UI labels are quoted exactly as they appear in source — never translated, never
paraphrased.

## Roles & permissions
<!-- include only if role/permission logic was observed -->

| role | can | cannot | activation | evidence |
|---|---|---|---|---|
| {{role}} | {{allowed}} | {{denied}} | {{active/conditional/status unresolved}} | {{citations}} |

## Core flows
<!-- include the 1–3 flows that define the module; diagram only where a flow warrants it -->

### {{flow_name}}

{{flow_described_in_product_language, step by step, with the rule/branch points called out}}

```mermaid
{{sequence_or_state_diagram — business-language labels, identifiers parenthesized; only
steps/transitions backed by the evidence list below}}
```

Evidence: {{citations_for_each_step_of_the_flow}}

## Rules & states
<!-- include only if business rules / state machines were observed -->

| rule / state | behavior | activation | evidence |
|---|---|---|---|
| {{name}} | {{what_happens}} | {{active / conditional (on what) / status unresolved}} | {{citations}} |

`conditional` means gated by a flag/config found in the repo; whether the gate is open in
production is not knowable from the repository.

## Notifications & integrations
<!-- include only if the module sends notifications or touches external systems -->

| trigger | channel / system | recipient / target | disposition | evidence |
|---|---|---|---|---|
| {{event}} | {{email/queue/webhook/system}} | {{who_or_what}} | {{included/unresolved}} | {{citations}} |

## Open questions

{{numbered; the product-level unknowns a PM should resolve — each with why it matters
and what would answer it (a person, a dashboard, a production config)}}
