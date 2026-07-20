# {{project_name}} — Project Overview

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

<!--
PRIMARY, human-facing document — read top-to-bottom in ~10 minutes with no prior
context. It is DIAGNOSIS-ONLY and reflects CURRENT STATE ONLY: it describes what the
system IS and how hard it is to change, with conditions and observed impact. It contains
NO fix recommendations, refactoring proposals, directions, roadmaps, remediation,
priority labels, recommended next modules, or suggested next analyses — and it never
tells the reader what to do next (those, as `suggested_direction`/priorities, live in
technical-overview.md and the module reports). It presents per-module conditions with
enough fidelity that the READER decides where to drill down; it never identifies or
recommends which modules deserve a drill-down.
Readability budget: ONE system diagram, at most 2–3 user journeys, at most 2–3
change-impact paths, at most 5–7 findings, and ~2,500 prose words (tables/Mermaid
excluded). Every required category remains represented: an inapplicable or unavailable
category is one honest line, a large inventory is summarized here and remains complete
in technical-overview.md, and a small project is never padded. Main text carries NO source paths, NO raw
metrics, and NO tool names — every claim links to technical-overview.md, where the
citations live; UI labels are quoted verbatim and module IDs may be parenthesized.
Facts come from code, never invented, never inferred from a name. Run language governs
the prose AND section headings (default zh-CN); render the disclaimer as a faithful
zh-CN translation for a zh-CN run.
Use plain Unicode and normal Markdown punctuation. Do not HTML-entity-encode prose,
percent-encode local `.md` links, insert invisible characters, or otherwise obfuscate
text to satisfy a gate. Mermaid uses standard edge tokens; never replace dots or dashes
with semicolons or encoded punctuation. Valid examples are `A -->|sync API| B` and
`A -. unresolved .-> B`; local links use literal names such as
`[technical overview](technical-overview.md)`. Every Mermaid block must parse in the
final `audit-overview` check.
-->

*How to read this: **§2 Executive diagnosis** is the complete diagnosis in summary form —
a ~2–3 minute read that stands on its own. **§3–§16** are the evidence-organized reference
to dip into per question; the ~10-minute read is layered on top of §2.*

## 1. Analysis basis

- **Run:** `{{run_id}}` · {{analyzed_at}} · language `{{language}}` · full detail &
  citations: [`technical-overview.md`](technical-overview.md)
- **Analyzed:** {{one line per repo — name, short HEAD, commit DATE, clean/dirty}}
- **Referenced but unavailable:** {{systems/endpoints configured here whose serving
  source was not among the analyzed repos — name only; detail in project-map.md. "none"
  if none}}
- **Coverage limits that shape this reading:** {{the few gaps a reader must keep in mind
  — one clause each; the full two-category list is section 16}}

<!-- ONLY when any target was dirty or non-git: -->
> **Inspection-only run:** one or more targets were dirty worktrees (or non-git folders);
> this run cannot be accepted as `current`.

## 2. Executive diagnosis

{{the read-this-and-stop section, plain prose: what the system is, who uses it, its
architectural shape at a glance, whether it is broadly easy or hard to change and why,
the systemic causes and HOW THEY REINFORCE one another, and the top remaining evidence
gaps. Conditions and observed impact ONLY — no fixes, no directions. Each claim traces
to a later section / technical-overview.md. If a shared-schema/shared-table claim first
appears here, the same sentence carries the same-name-only qualifier (the §8 ladder only
restates it).}}

## 3. Product snapshot

- **Business capabilities:** {{each with its primary users, the business outcome it
  serves, and a confidence — business terms, not module IDs}}
- **Platform & shared components:** {{frontend app, auth, schedulers, storage, etc. —
  listed SEPARATELY from capabilities, never at the same level}}
- **System evolution:** {{INCLUDE ONLY when evidence detects one (parallel
  implementations / partial replacement / compatibility layer), stating which side
  carries what. A `v2`/`legacy`/`new`/`old` NAME is never proof of a migration state —
  cite behavior, not naming. OMIT this line if no such state is evidenced}}

## 4. Users, roles & access model

- **Who the users are:** {{distinguish static roles (a fixed catalog) · external user
  types · contextual identities earned per record (owner / leader / approver / …)}}
- **Where defined & catalog drift:** {{where roles/permissions are declared, and any
  differences in the role catalog across repos}}
- **Enforcement layers (presence + location class, not raw paths):** {{frontend menus/
  routes · backend middleware · policy engines · inline checks — which layers exist}}
- **Observed authorization gaps / unresolved boundaries:** {{gaps actually seen;
  boundaries that could not be resolved}}

Distinguish throughout: **frontend visibility vs backend authorization**, **contextual
vs static** identity, and **discovery vs verification** (what was found ≠ what was
proven enforced). Per-role permission matrices belong in the module PRDs, not here.

## 5. Representative user journeys

{{2–3 journeys — one normal, one approval-or-rule-heavy, one background-or-integration.
Each traced: actor → UI entry (label quoted VERBATIM from source) → action → API/service
→ rule applied → data touched (persisted store named from the handler's own model /
TableName evidence, never inferred from the domain name) → notification / final state.
A capability with no
independent UI is labeled `embedded` / `background` / `API-only`. Verbatim labels come
from bounded reads of only the 2–3 entry files.}}

### {{journey_name}}
{{actor → "verbatim UI label" → … → final state}}

## 6. Runtime & system topology

```mermaid
{{ONE diagram. Nodes: UI apps · deployable services (a source directory is NOT a
deployable unit — deploy configs are the evidence; render deployable-unit nodes with an
`inferred` label and never imply deploy-config discovery is complete) · schedulers · data
stores · external systems · trust boundaries. Distinguish edge TYPES: sync API ·
service-to-service · scheduled · data read · data write · authn/authz · external. Every
edge is evidence-backed (backing in project-map.md); edges show code references,
never traffic; the diagram introduces no new edge}}
```

Relationship labels, shared data, and evidence: [`project-map.md`](project-map.md).

## 7. Interface & consumer boundaries

{{which interfaces are public / internal / legacy / versioned; the known consumers of
each; any provider that a caller references but that could not be located; parallel
old+new interfaces serving one capability. NO endpoint inventory — that is in
technical-overview.md.}}

## 8. Data ownership & lifecycle

{{per IMPORTANT domain only: source of truth · writers · readers · any multi-service
direct access · shared-via-API vs shared-DB · known lifecycle (create → archive/delete
where visible) · coverage confidence. State the strongest distinction reachable on the
ladder: declaration / schema-write / read / write / join-reference / same-name-only / unresolved-dynamic.
Schema-write evidence changes table structure; it does NOT make a service an application
data writer.
A name match ALONE is never confirmed shared persistence.}}

| domain | source of truth | writers | readers | sharing | distinction reached | confidence |
|---|---|---|---|---|---|---|
| {{domain}} | {{owner}} | {{...}} | {{...}} | {{via-API / shared-DB / none}} | {{ladder level}} | {{high/medium/low}} |

## 9. Background execution

{{scheduled/async work, per job: trigger · owning component · data touched · external
calls · observed retry / idempotency / failure-recording / alerting. A job that is
DEFINED is not proven ACTIVE — label accordingly. "none observed" if none.}}

## 10. External systems

{{what the product relies on that it does not own, grouped by evidence class:
**confirmed integration** · **config-only** · **dynamic/unresolved** ·
**referenced-without-use** · **internal-misclassified** (looked external, proven
internal — moved to the topology, noted here). Derive from evidence — no fixed
vendor lists. The full per-candidate disposition accounting is in
technical-overview.md.}}

## 11. Overall changeability diagnosis

{{the six changeability questions told as ONE causal story — boundary clarity, change
spread, rule locality (only along the sampled journeys/paths), hidden coupling,
duplication & evolution debt, verification difficulty — naming the systemic causes and
HOW THEY REINFORCE one another. Conditions only; NO remediation.}}

## 12. Representative change-impact paths

{{2–3 worked change examples (one business-rule, one API/data, optionally one UI),
SELECTED where evidence is strongest (co-change ∩ complexity ∩ missing safety net). Each:
components crossed · responsibilities involved · side effects · verification required ·
why it is expensive TODAY · **remaining unknowns**. Current cost only — no improvement
proposals.}}

### {{path_name}}
{{crosses …; side effects …; verification …; expensive today because …; remaining unknowns …}}

## 13. Module changeability table

One row per business/platform module. Cells use EXACTLY this vocabulary — never
"healthy" or any wellness word inferred from the absence of a finding:
- `confirmed concern` — a finding says this is hard/risky to change (name the basis).
- `no concern observed` — the signal for THIS cell ran and surfaced nothing (basis inline).
- `unknown` — the signal for THIS cell did not run or could not resolve. A gap in one
  lens makes only its own cell `unknown`; it never turns unrelated cells into concerns.

The table DESCRIBES conditions only — no recommendation column, no "drill here next"
marking, and the row order implies no analysis priority beyond the stated cell values.
The reader draws their own drill-down conclusions from the conditions.

| module | responsibility clarity | change spread | hidden coupling | safety net | confidence |
|---|---|---|---|---|---|
| {{business_module_name (`module-id`)}} | {{...}} | {{...}} | {{...}} | {{...}} | {{high/medium/low}} |

<!-- business + platform modules; roll shared-infra modules with no findings into one
closing row, but NEVER collapse a module that has a finding into such a row -->

## 14. Findings by observed impact

At most 5–7, system-level impact only, ordered by **observed engineering impact — NOT
product priority**. Each carries claim / affected modules / observed impact / evidence /
confidence / limitations. NO direction, NO remediation, NO priority label (those live in
technical-overview.md). Systemic findings appear ONCE, never N rows for one root cause.

### {{n}}. {{claim}}
- **Affected modules:** {{module_ids}}
- **Observed impact:** {{what it costs when someone changes/operates this}}
- **Evidence:** [`technical-overview.md#finding-{{n}}`](technical-overview.md) · **Confidence:** {{...}}
- **Limitations:** {{what this finding cannot see}}

## 15. Operational state

Observable evidence only — one line per aspect, `unknown` where evidence is insufficient.
NEVER infer reliable/unreliable from absence; absence is `unknown`, not a verdict.

| aspect | observed | reading |
|---|---|---|
| {{aspect — one row EACH for: automated tests · CI · deployable units · DB migrations · rollback · health checks · logging · metrics/tracing/alerts · failure recovery · dependency-vuln scanning}} | {{present/absent/partial + where}} | {{one line, or `unknown`}} |

## 16. Coverage & unknowns

**(a) Code could answer, this run did not** — analysis-coverage gaps, stated factually:
{{each gap and the signal/knob that WOULD hold the answer — a statement of what is
missing, never a request or recommendation to run it}}

**(b) Code cannot answer** — outside repository evidence: production traffic, real usage,
SLAs, ownership, criticality, incident history, roadmap, whether a configured integration
is enabled in production. {{list the ones this project raises, for a human owner. These
are never turned into recommendations.}}

---
<!-- ONLY for clean (non-inspection-only) runs, end with the acceptance offer: -->
*Accept this run as `current` (enables module drill-downs against it)? Reply "accept".*
