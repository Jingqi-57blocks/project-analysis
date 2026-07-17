# {{project_name}} — Project Overview

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

<!--
This is the PRIMARY, human-facing document: a reader with no prior context should
understand the system and its biggest changeability risks in ~10 minutes, WITHOUT
opening any other file. Keep it simple — plain sentences a PM can act on. Every claim
is grounded in code (never invented) and is verifiable from its own citation; do not
make the reader cross-check multiple documents to understand one point. The exhaustive
evidence, full findings, and metrics live in technical-overview.md — link to it, don't
inline it. Run language governs the prose AND the section headings of this document
(default zh-CN); code identifiers, UI labels, citations, and the fixed status
vocabulary (`confirmed concern`/`no concern observed`/`unknown`, `included`/`unresolved`,
`status unresolved`, priorities) stay verbatim. For a zh-CN run, render the disclaimer
above as its faithful zh-CN translation (same scope claims).
-->

## 1. Analysis basis

- **Run:** `{{run_id}}` · {{analyzed_at}} · language `{{language}}` · full detail:
  [`technical-overview.md`](technical-overview.md)
- **Analyzed:** {{repos_with_short_commit_vintage — one line per repo: name, short HEAD,
  HEAD date; NOT the full provenance table (that is in technical-overview.md)}}
- **Coverage limits that shape what follows:** {{the few coverage gaps a reader must
  keep in mind — e.g. a skipped network scan, a partially-resolved dependency graph, a
  reduced-support stack — one clause each; the complete list is section 9}}

<!-- ONLY when any target was dirty or non-git: -->
> **Inspection-only run:** one or more targets were dirty worktrees (or non-git folders)
> at analysis time; citations use `repo@WORKTREE:`/`repo@NON-GIT:` forms and this run
> cannot be accepted as `current`.

## 2. Project snapshot

- **What it is / who it's for:** {{purpose in plain product language}}
- **Business capabilities:** {{the capabilities the product delivers — business terms,
  not module IDs}}
- **Platform & shared components:** {{the shared/technical pieces that serve those
  capabilities — frontend app, auth service, schedulers, storage — kept SEPARATE from
  the business capabilities above, never listed at the same level}}
- **User roles:** {{roles ONLY when backed by evidence — permission checks, route
  guards, menu/role definitions, approval relations (cite in technical-overview.md);
  otherwise write `unresolved` — never infer a role from a module or folder name}}
- **System evolution:** {{INCLUDE ONLY when evidence detects one — parallel
  implementations of the same capability, a partial replacement, a compatibility layer;
  state plainly which side carries what, citing the evidence. OMIT this line entirely if
  no such state is observed — do not assume a migration}}

## 3. Capability & system map

{{one or two plain sentences framing the diagram}}

```mermaid
{{simplified_topology — business-language labels with identifiers parenthesized; show
capabilities, the platform/shared components that serve them, and external systems on
the boundary. NO bare status codes, route paths, or table names in labels. Only edges
backed by project-map.md relationship rows}}
```

Full topology, relationship labels, and shared data: [`project-map.md`](project-map.md).

## 4. Overall changeability diagnosis

{{the 3–5 strongest SYSTEMIC causes of change difficulty, told as ONE coherent story:
name each cause in a plain sentence, then say explicitly HOW THEY REINFORCE one another
(e.g. a still-live parallel implementation × duplicated logic × no test net compound
into a single risk). Not a bulleted list of unrelated defects — a diagnosis. Each cause
restates a finding from section 7 / technical-overview.md; no new uncited claims}}

## 5. Representative change paths

{{2–3 PROJECT-LEVEL worked examples — one business-rule change, one API/data change,
optionally one UI change — SELECTED where the evidence is strongest (co-change ∩
complexity ∩ missing safety net) and citing that evidence. For each: the components a
change crosses, the responsibilities involved, the side effects to expect, what
verification it would require, and why it is expensive today. Keep each to a short
paragraph; per-module tracing detail lives in the module drill-down, not here.}}

### {{path_name — e.g. "changing an approval rule"}}
{{crosses …; involves …; side effects …; verification …; expensive because … (cite)}}

## 6. Module changeability table

One row per business/platform module. Cells use EXACTLY this vocabulary — never
"healthy" or any wellness word inferred from the absence of a finding:
- `confirmed concern` — a cited finding says this is hard/risky to change (name the basis).
- `no concern observed` — signals for THIS cell ran and surfaced nothing (state the
  basis inline, e.g. "tests present", "low churn").
- `unknown` — the signal for THIS cell did not run or could not resolve. A gap in one
  lens makes only its own cell `unknown`; it never turns unrelated cells into concerns
  or clears them.

| module | responsibility clarity | change spread | hidden coupling | safety net | confidence |
|---|---|---|---|---|---|
| {{business_module_name (`module-id`)}} | {{...}} | {{...}} | {{...}} | {{...}} | {{high/medium/low}} |

<!-- business + platform modules; roll shared-infra modules with no findings into one
closing row, but NEVER collapse a module that has a finding into such a row -->

## 7. Prioritized findings

Ordered **systemic first, then local, then coverage gaps** — this is
**engineering-risk** priority (what makes change hard or dangerous), NOT a
business-roadmap priority. Full finding detail and all evidence are in
[`technical-overview.md`](technical-overview.md).

{{for each: a one-line claim; **impact** in plain terms; **evidence** (one citation or a
link to the technical-overview finding number); **confidence**; **direction** (a
direction, not a prescription). Systemic findings that span repos appear ONCE with their
per-repo evidence beneath — never N separate rows for one root cause.}}

### {{n}}. {{claim}}
- **Impact:** {{plain-language consequence}}
- **Evidence:** {{citation / technical-overview.md#finding-n}} · **Confidence:** {{...}}
- **Direction:** {{...}}

## 8. External systems & boundaries

{{plain-language summary of what the product relies on that it does not own — storage,
mail, chat, issue tracking, directory, etc. — noting anything with only weak signals as
"signs present, not confirmed" (`unresolved`). This is a SUMMARY; the per-candidate
disposition with evidence is in technical-overview.md, and the topology is in
project-map.md.}}

## 9. Open questions & limitations

**Questions code cannot answer** — each states WHY the repository cannot answer it. If a
re-run knob, a producer, or more evidence COULD answer it, it does not belong here (it
is a coverage gap for the technical-overview backlog, not an open question):

{{numbered; each: the question, and the reason the repository is silent on it}}

**What code cannot know (product context):** {{ownership, real-world usage, SLAs,
criticality, and roadmap are outside repository evidence — list the ones this project
raises so a human owner can supply them}}

---
<!-- ONLY for clean (non-inspection-only) runs, end with the acceptance offer: -->
*Accept this run as `current` (enables module drill-downs against it)? Reply "accept".*
