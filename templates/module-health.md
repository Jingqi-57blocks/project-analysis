# {{module_name}} — Module Health

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

Coverage limits for every lens are listed at the end — a lens that did not run means
**unknown**, not healthy.

- **Module:** `{{module_id}}` ({{classification}}) · aliases: {{aliases_or_none}}
- **Source overview run:** `{{source_overview_run}}` · this drill-down: `{{run_id}}`
- **Repos / roots:** {{repo_relative_roots}}

## Health summary

{{three_to_five_sentences: overall condition, the dominant risk, and where the next
change to this module is most likely to hurt — every claim restates a cited finding or
metric from below (source or `signals/<view>:<row>` citations); no new uncited claims}}

## Findings

{{ordered by priority; every finding in the shared shape:}}

### {{n}}. {{claim}} — `{{priority}}`
- **Lens:** {{lens}} · **Confidence:** {{confidence}}
- **Affected modules:** {{module_ids}}
- **Evidence:** {{citations_with_one_line_each — ≥2 independent signals for
  high-confidence claims}}
- **Impact:** {{concrete_consequence_for_change_or_operation}}
- **Limitations:** {{what_this_finding_cannot_see}}
- **Suggested direction:** {{direction_not_prescription}}

## Dependency picture

- **Inbound (who depends on this module):** {{module_ids_with_edge_labels}}
- **Outbound (what this module depends on):** {{module_ids_with_edge_labels}}
- **External:** {{external_systems_with_disposition_and_evidence}}

```mermaid
{{local_dependency_diagram — this module in the middle, labeled edges; only edges cited
in the dependency picture above — the diagram introduces no new claims}}
```

## Change scenarios

{{up to 3 APPLICABLE scenarios from: UI change · business-rule change · data/API change ·
scheduler/event change · external-integration change. Skip inapplicable ones and say why.}}

### Scenario: {{scenario_name}}

{{traced end-to-end: the files/layers a developer touches, in order, with citations;
the coupling points where the change leaks into other modules; the safety nets (tests,
types, migrations) that would or would not catch a mistake}}

## Test & safety-net evidence

| area | observed evidence | assessment | citations |
|---|---|---|---|
| {{tests/types/migrations/ci-gates}} | {{what_exists}} | {{present/thin/absent — scoped to analyzed sources}} | {{citations}} |

## Coverage & limitations

A lens's status is the WORST status among its signals
(`failed > partial > skipped > complete`), same as the overview.

| lens | status (aggregate) | signals affecting this module — reason (verbatim) |
|---|---|---|
| {{lens}} | {{worst_signal_status}} | {{each_non-complete_signal_touching_this_module: tool × repo — reason copied verbatim from run-summary.json; "all complete" otherwise}} |

{{plus module-specific blind spots: excluded paths inside this module, generated code,
reduced-support stacks}}
