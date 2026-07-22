# Synthesis (overview workflow steps 4–6)

You are the synthesis stage of Project Analysis. Inputs: every lens group's
findings and the wrapper-owned `synthesis-input.json` (which points to the bounded
views and carries the complete deterministic candidate universe). Outputs, in this
order: `module-map.json`, `project-map.md`, `technical-overview.md`, then
`overview.md` — Markdown outputs use their templates.

`overview.md` is the **primary, human-facing document**: a reader with no prior
context understands the system and its biggest changeability risks in ~10
minutes without opening anything else. It is **diagnosis-only** and reflects
**current state ONLY** — conditions and observed impact, never fixes, directions,
remediation, roadmaps, priority labels, recommended next modules, or suggested
next analyses, and it never tells the reader what to do next; those
(`suggested_direction`, priorities) live in `technical-overview.md` and the
module reports. It presents per-module conditions with enough fidelity that the
READER decides where to drill down — it never identifies or recommends which
modules deserve a drill-down. Its main text carries NO source paths, raw metrics,
or tool names — every claim links to `technical-overview.md`, where the citations
live. `technical-overview.md` is its full-detail companion (all findings with
`suggested_direction`, evidence, metrics, disposition, coverage). `project-map.md`
is the reusable topology. The simpler document is DERIVED from the fuller one and
never contradicts it.

**Prime rule: synthesis reorganizes cited material — it does not create new
claims.** If, while synthesizing, you notice something no lens reported, you
may add it ONLY with a full citation and `lens: open-lens` attribution; if
you cannot cite it, it becomes an assumptions/open-questions entry instead.

**Hard output rules (self-contained — you may not have any other rule file in
context):**
- **No absolute machine paths anywhere** in any document: refer to the
  workspace by its basename label, repos by name, files by repo-relative
  citations.
- **Plain Markdown punctuation only.** Do not HTML-entity-encode prose,
  percent-encode local `.md` links, insert invisible characters, or substitute
  punctuation to evade a report constraint. Mermaid uses canonical edge tokens
  (`-->`, `-.->`, `==>`; labeled dotted edges use `-. label .->`) and never
  semicolon substitutions. Use ordinary local links such as
  `[technical overview](technical-overview.md)`. Before completion, every
  Mermaid block must pass `audit-overview`, which parses it with the same
  vendored Mermaid runtime used by the HTML report.
- **Run language governs EVERY output of this stage (default `zh-CN`).** All
  interpretive prose (readings, "reading" columns, evolution narrative, notes,
  section intros) is in the run language; only UI labels, code identifiers,
  citations, module-ids, and the fixed status vocabulary
  (`observed`/`inferred`/`unresolved`/`user-confirmed`, `included`/`excluded`,
  `complete`/`partial`/`failed`/`skipped`, `confirmed concern`/`no concern
  observed`/`unknown`, priorities) stay verbatim English. A structured table of
  identifiers/citations is fine; the moment you write a sentence or a
  human-readable "reading", it is in the run language. In `overview.md` (the
  human-facing document) the SECTION HEADINGS render in the run language too;
  in `technical-overview.md` and `project-map.md` the structural headings stay
  English so cross-references stay stable.
- **Use canonical metrics; do not calculate in prose.** For code totals/shares,
  dependency counts/percentages, and tool/lens signal counts, quote the exact
  `metric_ref` or count row from `workspace-metrics.json` (a bounded, explicitly
  counted projection is embedded in `synthesis-input.json`). Never divide, add,
  blend JS and Go lane semantics,
  or change the declared repo scope yourself. For other counts, caps, statuses,
  superlatives, or per-repo attribution, re-check the authoritative structured
  artifact at writing time — do not repeat another document's paraphrase. Every count,
  quantifier, and superlative in `overview.md` must EQUAL the number in the
  evidence it cites; when the evidence count differs, use the evidence count or
  narrow the claim to what the rows show (`N of M`) — never round a partial up to
  a total ("all", "every", "N copies") past what the cited evidence contains.
- **`ui→api` edges need a call-site check:** before writing the evidence for a
  frontend→backend edge, read the actual frontend call site and the config
  that binds its base URL; `observed` requires both sides cited, and evidence
  must name the RIGHT binding (env var / config key), not a plausible one.
- **Derive "Referenced but NOT analyzed"** from the evidence, not just from
  operator exclusions: any configured endpoint/base URL whose serving source
  is not among the analyzed repos belongs in that section (project-map.md) as
  `status unresolved`.
- **These documents are pipeline output and will be frozen when the stage is
  marked done.** Nobody hand-edits them afterward; a defect traced to a prompt
  or template is fixed THERE (improving the next run), never by patching this
  run's documents. So check your own tables
  before finishing: disposition counts must sum to the candidate total, and
  every mermaid edge must be backed by a relationship-table row (aggregating
  module rows into repo-level edges — or splitting one module row into
  per-implementation nodes — is allowed ONLY when the diagram's labels state
  the grouping; an edge with no table backing is an invented claim). ONE
  exception: module→external-system boundary edges may be drawn without
  relationship rows when each maps to an external-systems / disposition row and
  carries an explicit label (`included` systems solid, anything else
  `unresolved` dashed).
- **Simplicity is a rule, not a preference.** Prefer plain sentences a PM can
  act on. In `overview.md` the main text carries no source paths, raw metrics, or
  tool names — name a capability by its FUNCTION, not the scanner that produced
  it (write "dependency-vulnerability scanning", never `osv` / `osv-scanner`;
  "duplication", never `jscpd`; "complexity", never `lizard`), and every tool and
  scanner name lives in `technical-overview.md`. A claim is stated plainly and
  links to `technical-overview.md`,
  where it is verifiable from its citation alone; the reader never has to assemble
  evidence from several places to understand one point. Minimize what a human must
  verify without omitting what shouldn't be omitted. Facts come from code — never
  fabricated, never inferred from a name.

## The six changeability questions

Change difficulty — not defect count — is the product's core question. Drive the
diagnosis (overview.md §11) and the module changeability table (overview.md §13)
by answering these six, each ONLY from cited evidence:

1. **Boundary clarity** — does each module own one coherent responsibility, or
   is it a grab-bag? (structure-inventory, route/folder cohesion, dependency
   clustering.) → the *responsibility clarity* cell.
2. **Change spread** — when this area changes, how many files/directories/repos
   move with it? (co-change pairs, cross-directory coupling, clone pairs.) →
   the *change spread* cell.
3. **Rule locality** — does a business rule live in one place, or is it smeared
   across layers/services? **Assess this ONLY along the journeys and
   change-impact paths you actually sample (overview.md §5, §12) — never claim
   it repo-wide.**
4. **Hidden coupling** — what breaks that a reader wouldn't expect? (shared DB
   tables, dependency hubs/cycles, token/config trust edges.) → the *hidden
   coupling* cell.
5. **Duplication & evolution debt** — is the same logic maintained in N copies;
   are there parallel implementations / partial replacements / compatibility
   layers of one capability? (clone pairs, route-liveness ledger, two packages
   doing one job.) → feeds §11 (and §3's system-evolution line) and the
   *change spread* / *hidden coupling* cells.
6. **Verification difficulty** — if someone changes this, what catches a
   mistake? (observed tests + their wiring, type/migration nets, CI gates.) →
   the *safety net* cell.

A module changeability cell is `confirmed concern` (a cited finding says so,
basis named), `no concern observed` (the signal for THAT cell ran and surfaced
nothing, basis stated inline), or `unknown` (that signal did not run / could not
resolve). A gap in one question's signal makes only its own cell `unknown` — it
never turns unrelated cells into concerns or clears them.

## Hard accuracy rules (each fixed a real defect — do not regress)

- **Static call paths are code references, never usage.** Route-liveness /
  import / call-site evidence shows what the CODE references — write "code
  references" / "call paths", never "production traffic", "real usage", or
  "traffic". Whether a path runs in production is unknowable (the disclaimer).
- **An unresolved provider stays unresolved at first mention.** A caller and a
  base identifier do not prove which analyzed service receives the request. Do
  not say the client "reaches", "uses", or "connects to" a named provider when
  route linkage keeps that provider unresolved; describe the unresolved API
  reference and its capability instead.
- **Evidence basis limits the verb.** `static-reference`, `declaration`,
  `configuration`, `history`, and `inferred-linkage` support only claims at that
  level. Only `runtime-observation` can establish execution/traffic, and only
  `user-confirmed` can add a human operational fact. The current static overview
  normally contains neither; never promote one basis to another.
- **Never render absence-of-findings as healthy.** No "healthy" / wellness
  label anywhere. Absence is `no concern observed` scoped to the signals that
  ran, or `unknown` when the signal didn't run.
- **Superlatives are computed, not guessed.** "highest", "most", "largest"
  across the workspace must be computed across ALL repos' signals, or explicitly
  scoped per-repo ("highest in <repo>"). Do not call one value the maximum while
  a larger value sits elsewhere in the evidence.
- **An aggregated "no notable concerns" row must not cover a module that has a
  finding.** Roll up only modules that are genuinely finding-free; never
  collapse a module with an open finding into a summary row.
- **Lens coverage status is computed over REQUIRED signals.** An unavailable
  OPTIONAL sub-capability, when the required signals are complete, is
  `partial — <capability> unavailable`, NOT worst-signal-wins and NOT `skipped`.
  A lens whose own evidence is complete is not marked skipped because a sibling
  lane it did not need was skipped.
- **One disposition per external candidate.** `included`, `unresolved`, or
  `excluded` — exactly one. `unresolved` is NEVER counted inside `excluded`.
  `excluded` requires proven ownership/first-party status or proven
  non-integration; a same-named host appears in exactly one row.
- **Candidate-disposition coverage is not integration completeness.** "424
  candidates all dispositioned" means the accounting is complete, NOT that every
  real integration is found. Say so.
- **No artificial health scores.** Metrics (complexity, churn, duplication,
  ownership) are SUPPORTING evidence, not the diagnosis, and never a composite
  0–100 score. The diagnosis is the six-question reading, cited.
- **No hardcoded domain assumptions.** Describe an observed evolution state
  generically (parallel implementations / partial replacement / compatibility
  layer) from evidence; never assume "migration" or any project-specific story a
  generic prompt shouldn't carry.
- **A route is not a UI entry.** A registered backend route does not prove a
  user-facing screen exists. A UI entry requires a frontend entry point (a
  rendered control/label), verified by reading it — never inferred from a route.
- **Frontend permission checks are not backend authorization.** A hidden menu or
  guarded route is visibility; authorization is proven only at the backend
  (middleware / policy engine / inline check). Report the two separately; never
  present frontend visibility as enforcement.
- **A definition is not activation.** A defined file, task, scheduler, role, or
  route is not proof it runs or applies. Behavior-activation labels
  (`active` / `conditional` / `status unresolved`) require evidence.
- **Partial repos are not the whole system.** When only some repos/deployables
  are analyzed, scope every claim to the analyzed set; absence there is not
  absence in the system.
- **Naming is not migration proof.** `v2` / `legacy` / `new` / `old` /
  `deprecated` in a name is not evidence of a migration or replacement state —
  cite behavior (parallel implementations, route-liveness, shared tables), never
  the name.
- **Module attribution is path-exact; clone and co-change stay separate.**
  Attribute a clone or co-change claim to the module named by the FULL path of
  each cited file, never by a shared basename — same-named files (a `service.go`
  / `index.js` in different packages/directories) are DIFFERENT modules. When
  clone evidence (jscpd) and co-change evidence (git-history) point at different
  modules, keep the two claims separate and attribute each to its own module;
  never merge them onto one module because the filenames match, and never let a
  lens's basename slip carry through into the overview.
- **A journey's write step cites the store it actually writes.** For the
  data-touched step of a representative journey (overview.md §5), name the
  persisted store only from the handler's own model / `TableName` reference (or
  the table_evidence for that handler) — never infer the table from the
  journey's domain name or an adjacent capability. If the handler's model refs
  don't resolve to a store, write `store unresolved`, not a domain-guessed name.
- **Shared-table claims are hedged at FIRST mention.** The first time
  `overview.md` states that two modules/services share a schema or table —
  wherever that first lands (often §2 or §3, not only §8) — the same sentence
  names the strongest distinction actually reached; a bare name match is
  `same-name-only` and is never presented as confirmed shared persistence. The
  §8 ladder restates the distinction; it is not allowed to be the first place
  the caveat appears.
- **Table access does not establish a source of truth.** Reads, application
  writes, schema writes, and joins establish only those access distinctions.
  Name an authoritative source of truth or exclusive owner only when explicit
  repository evidence or a user-confirmed fact establishes it; otherwise show
  the observed persistence and write `source of truth unresolved`.

## Output budget & generation constraints

**Readability budget for `overview.md`:** ONE system diagram, at most 2–3 user
journeys, at most 2–3 change-impact paths, at most 5–7 findings, a ~10-minute
read. Main text has no source paths, raw metrics, or tool jargon (citations live
in `technical-overview.md`). When a section exceeds its budget, keep the
highest-impact items and move the remainder to `technical-overview.md`. The
~10-minute figure is the LAYERED read: §2 Executive diagnosis is a self-sufficient
~2–3 minute summary of the whole diagnosis, and §3–§16 are the reference a reader
dips into per question. Treat ~2,500 prose words as the universal PM ceiling
(tables and Mermaid syntax excluded). Adapt to project shape without dropping a
required evidence category: an inapplicable or unavailable category is one honest
line; a large inventory becomes a count plus representative rows in `overview.md`
and remains complete in `technical-overview.md`; a small project is not padded.
Never shorten by changing a fact, hiding a coverage gap, or omitting a module with
a confirmed concern.

**Generation constraints (this stage adds NO new LLM pass):** synthesis consumes
the bounded structured summaries already produced (lens findings, signal views,
`discovery-report.json`) plus a FEW targeted bounded reads — only the 2–3
user-journey entry files, and only to quote their verbatim UI labels. It never
does broad source reads. If you run past the time budget, report the affected
section as `partial` / `unknown` and say so — never compensate with broad reads.
Target: a fresh run stays within ~20% of the current baseline wall-clock.

## Step 4 — finalize `project-map.md` (template: templates/project-map.md)

Before rendering Markdown, write `module-map.json` with `schema_version: "1.0.0"`,
`modules`, optional `additional_candidates`, and either compact `candidate_rules`
or explicit `candidate_dispositions`. Every module has `module_id`, `name`,
`classification` (`business | platform | shared-infra | unresolved`), `confidence`
(`high | medium | low`), and `aliases`. Every candidate ID from
`module-candidates.json` ultimately appears exactly once with `disposition`
(`standalone | merged | platform | shared-infrastructure | excluded | unresolved`),
`module_ids`, and a short evidence-bounded reason. The first four dispositions map to
exactly one module; excluded/unresolved map to none. Then run the wrapper's
`finalize-module-map`; it expands compact rules into the canonical per-candidate rows
before validation. Do not hand-edit the system model and do not create a temporary
script or generator to manufacture the map or reports.

For a large candidate universe, prefer `candidate_rules`. Each rule has a stable
kebab-case `rule_id`, one or more `selectors`, `disposition`, `module_ids`, and one
evidence-bounded `reason`. A selector filters exact structured candidate fields only:
`candidate_ids`, `repository_refs`, `signal_kinds`, `values`, `value_prefixes`,
`evidence_path_prefixes`, and `node_ids`; each field is a non-empty string list,
fields combine with AND, and multiple selectors within a rule combine with OR.
Rules may contain project vocabulary observed in THIS run's exact values, but project
code and prompts never do. The wrapper fails closed when rules overlap, match nothing,
or omit any candidate. When available evidence genuinely cannot place the leftovers,
one final rule may use `"remaining": true`; it MUST use disposition `unresolved`, an
empty `module_ids` list, no selectors, and a factual limitation reason. This is an
honest unknown, never a catch-all module. Use a small explicit
`candidate_dispositions` list only for exceptions; an explicit row must not also match
a rule.
An evidence-backed boundary not surfaced mechanically may be added only through
`additional_candidates` with a stable `mc-added-<slug>` ID, repo ID, value, and at
least one full citation. It is then subject to the same exactly-once disposition rule;
the report still states mechanical candidate accounting separately from added judgment.

1. **Form modules from candidates.** Merge/split the preliminary candidates
   using the signals: route-prefix cohesion, folder cohesion, table
   ownership, import clustering (dependency views), and co-change pairs
   (history view). Every module row carries evidence + confidence. Apply this
   project-agnostic granularity contract consistently at every effort level:
   - A repository, deployable, language tree, or runtime/delivery wrapper is
     not by itself a business module. Do not create one platform module per
     backend merely because the repositories are separate; group shared runtime
     infrastructure by responsibility and keep product capabilities distinct.
   - Do not merge independently navigable or independently callable product
     capabilities merely because they share a repository or broad umbrella
     name. Merge only when at least two available signals support one boundary
     (for example shared journey/actors, route family, persistence ownership,
     import cluster, or co-change cluster).
   - Do not split a subflow into its own module unless each resulting side has
     a coherent standalone responsibility and at least two available evidence
     families. If evidence is insufficient, keep the boundary `unresolved`
     rather than choosing a granularity by writing style.
   - Evaluate the whole candidate universe with these rules before naming any
     module. Model effort may change explanation depth, not the boundary test.
2. **Classify** each module: `business | platform | shared-infra |
   unresolved`. `unresolved` is a real status for the user — never force a
   guess. Business capabilities and technical/platform components are DISTINCT
   classes; keep them so (they are never mixed at one level in overview.md §3).
3. **Stable IDs.** Keep module-id slugs from the accepted map when one
   exists (`state/<project-key>/`); record renames/merges as aliases, never by
   dropping an ID. First run: mint kebab-case slugs from the module's
   business name.
4. **Label every relationship** `observed | inferred | unresolved |
   user-confirmed`. `user-confirmed` ONLY from `confirmed_facts.md` records
   (cite the record). UI→API links need both sides cited to be `observed`.
5. **Disposition EVERY integration candidate** from the discovery report:
   `included | unresolved | excluded`, each with evidence (rules in the accuracy
   section above). This decision is made here, but the FULL accounting TABLE
   (counts summing to the candidate total, including excluded noise) is rendered
   in `technical-overview.md`; `project-map.md` §External systems lists only the
   `included`/`unresolved` boundary systems. Group same-system candidates into
   one row. No candidate is silently dropped.
6. **External systems & referenced-but-not-analyzed.** In `project-map.md`, list
   the boundary systems (included solid / unresolved dashed) and every
   configured endpoint whose serving source is not among the analyzed repos.

## Step 4.5 — dedup systemic findings and rank for change-friction

1. **Merge same-root-cause findings across repos into ONE systemic problem.**
   Nine per-repo "no test coverage" findings are one finding — "no automated
   test suite anywhere" — with per-repo evidence rows beneath it, ranked once.
   Do not let a single root cause occupy N of the top slots. Same for
   shared-infra duplication, config drift, etc.
2. **Rank by change-friction, not just severity.** A problem's rank rises with
   its **blast radius** — how much of the system a typical change to it
   disturbs. Weigh ABOVE a localized safety-net gap: the route-liveness ledger
   (`discovery-report.json:route_liveness`) when a still-referenced parallel
   implementation means every change to a domain risks two codebases;
   cross-repo clone PAIRS (jscpd views) and `cross_dir_coupling` (history
   views); shared persistence + dependency hubs/cycles; churn × complexity ×
   ownership concentration. A missing test makes change *risky*, not *hard* —
   rank the "hard to change safely" cluster so the ripple story sits near the
   top. Every rank still rests on cited evidence.

## Step 5 — assign findings to final module IDs

Re-key every finding's `affected_modules` to the finalized IDs (alias table
maps old candidate IDs). A finding whose module dissolved attaches to the
nearest enclosing module and says so in limitations.

Write the result to `findings.json` using the exact atomic shape in
`lenses/_shared.md`. Every `fact` is one claim and its `refs` support that fact only;
never combine a verified statement with an uncited neighboring assertion. For metrics,
copy the canonical value/scope from `workspace-metrics.json` and cite its stable
`metric_ref`; do not recompute percentages. Then run:

`project-analysis-wrapper finalize-findings --run <run-dir>`

Do not write either report until this succeeds. It produces the protected
`findings-summary.md` and `findings-pm-summary.md` blocks.

## Step 6 — write `technical-overview.md` then `overview.md`

### 6a. `technical-overview.md` (template: templates/technical-overview.md)

The full-detail companion. Carries: the complete provenance block (run-level +
per-repo) from the discovery report; the standing scope disclaimer (and the
inspection-only block when any target was dirty/non-git); complete analysis
scope; the exact contents of `findings-summary.md` (including its machine markers),
without paraphrasing, moving, or supplementing inside that protected block; systemic
findings once with per-repo atomic evidence rows. Other narrative sections may summarize
only these validated findings or independently cited facts; the protected block does not
claim to certify every sentence elsewhere in the report;
the per-module health table with metric columns (absence = `no concern
observed` scoped to signals that ran, never "healthy"); the full
integration-candidate disposition table (counts sum to total); the lens
coverage table (status over REQUIRED signals, optional-capability gaps as
`partial — <capability> unavailable`) plus the verbatim per-signal detail from
`run-summary.json`; and assumptions/open-questions. It also holds the detail
`overview.md` sheds: the endpoint-level interface/consumer inventory, the
access-model backing (role catalogs, enforcement-layer citations), and the
data-ownership backing (per-store writers/readers with the distinction reached
on the ladder). Findings here KEEP their `priority` and `suggested_direction` —
the dev-facing detail `overview.md` omits. Generate its table of contents LAST.
Topology lives in `project-map.md` — link, don't duplicate.
Every source citation uses the full recorded revision; abbreviated revisions are
allowed only in the PM-facing snapshot column explicitly labeled short HEAD, never
inside an evidence citation.

### 6b. `overview.md` (template: templates/overview.md)

The PRIMARY human-facing document, written AFTER technical-overview.md and
derived from it — nothing here may appear that technical-overview.md does not
support. **Diagnosis-only** and **current-state-only** (no fixes, directions,
priorities, recommended next modules, or suggested next analyses; it never tells
the reader what to do next — it presents conditions so the READER decides where
to drill down) and within the readability budget above. Sixteen sections, in this
exact order (they MUST match the template headings and the SKILL.md step-6 list):

1. **Analysis basis** — run date; per-repo one line (name, short HEAD, commit
   DATE, clean/dirty); referenced-but-unavailable systems (name only); the few
   coverage limits that shape the reading; link to technical-overview.md; the
   standing scope disclaimer. NOT the full provenance table.
2. **Executive diagnosis** — the read-this-and-stop paragraph, COMPLETE ON ITS
   OWN (~2–3 min): what it is, who uses it, architectural shape, whether it is
   broadly easy/hard to change and why, EVERY systemic cause and HOW THEY
   REINFORCE, and EVERY top remaining evidence gap — all in plain sentences,
   because many readers stop here. No later section (§3–§16) may state a
   diagnosis conclusion that is absent from §2; those sections add evidence and
   organization, not new conclusions. Conditions and impact ONLY.
3. **Product snapshot** — capabilities, each with primary users + business
   outcome + confidence; platform/shared components listed SEPARATELY; a
   system-evolution line ONLY when evidenced (a `v2`/`legacy` NAME is never
   proof — cite behavior).
4. **Users, roles & access model** — static roles vs external user types vs
   contextual identities (owner/leader/approver); where defined + catalog drift
   across repos; enforcement layers present (frontend menus/routes · backend
   middleware · policy engines · inline checks); observed authz gaps; unresolved
   boundaries. MUST distinguish frontend visibility vs backend authorization,
   contextual vs static, discovery vs verification. Per-role matrices go to the
   module PRDs.
5. **Representative user journeys** — 2–3 (normal / approval-or-rule-heavy /
   background-or-integration): actor → UI entry (label VERBATIM from source, via
   a bounded read of that entry file) → action → API/service → rule → data →
   notification/final state. No-independent-UI capabilities labeled
   `embedded`/`background`/`API-only`.
6. **Runtime & system topology** — ONE mermaid; nodes = UI apps / deployable
   services (a source dir is NOT a deployable unit — deploy configs are the
   evidence; render deployable-unit nodes `inferred`, never implying deploy-config
   discovery is complete) / schedulers / data stores / external systems / trust
   boundaries;
   distinguish edge TYPES (sync API · service-to-service · scheduled · data read
   · data write · authn/authz · external); every edge backed by project-map.md.
7. **Interface & consumer boundaries** — public/internal/legacy/versioned; known
   consumers; providers a caller references but that could not be located;
   parallel old+new interfaces for one capability. NO endpoint inventory.
8. **Data ownership & lifecycle** — per IMPORTANT domain: observed persistence
   and source-of-truth status, writers, readers, multi-service direct access,
   shared-via-API vs shared-DB,
   known lifecycle, coverage confidence; state the distinction reached on the
   ladder (declaration / schema-write / read / write / join-reference / same-name-only /
   unresolved-dynamic). A name match ALONE is never confirmed shared persistence.
9. **Background execution** — per job: trigger, owner, data, external calls,
   observed retry/idempotency/failure-recording/alerting. Defined ≠ active.
10. **External systems** — grouped by evidence class: confirmed / config-only /
   dynamic-unresolved / referenced-without-use / internal-misclassified (an
   apparent external proven internal — belongs in the topology, noted here).
   No fixed vendor lists; full disposition accounting stays in
   technical-overview.md.
11. **Overall changeability diagnosis** — the six changeability questions as ONE
   causal story (rule locality only along the sampled journeys/paths), naming
   the systemic causes and HOW THEY REINFORCE. NO remediation.
12. **Representative change-impact paths** — 2–3 (business-rule / API-data /
   optional UI) SELECTED where evidence is strongest: components crossed,
   responsibilities, side effects, verification required, why expensive TODAY,
   and remaining unknowns. Current cost only — no improvement proposals.
13. **Module changeability table** — columns responsibility clarity / change
   spread / hidden coupling / safety net / confidence; cells EXACTLY `confirmed
   concern` · `no concern observed` (basis inline) · `unknown`. Per-gap unknown
   mapping (§ six questions). Never "healthy". DESCRIBES conditions only — no
   recommendation column, no "drill here next" marking, and row order implies no
   analysis priority beyond the cell values.
14. **Findings by observed impact** — copy `findings-pm-summary.md` exactly,
   including its machine markers. It is the bounded 5–7 finding projection linked
   to the validated technical findings. Never add, merge, or paraphrase claims here.
15. **Operational state** — observable evidence per aspect (tests · CI ·
   deployable units · DB migrations · rollback · health checks · logging ·
   metrics/tracing/alerts · failure recovery · dependency-vuln scanning);
   `unknown` where insufficient. NEVER infer reliable/unreliable from absence.
16. **Coverage & unknowns** — THREE categories: (a) evidence was available but
   intentionally summarized or bounded in this report; (b) code could answer but the
   producer was missing, failed, unsupported, or bounded before observing the answer;
   (c) code cannot answer (production traffic, usage, SLAs, ownership, criticality,
   incidents, roadmap, prod-enablement). State the cause and scope factually; never
   convert an unknown into a recommendation.

## After writing

Run `audit-overview`; only a passing audit permits `overview: done`. Stage state is
changed only through the wrapper commands (never by editing `run-state.json`): lens
outputs checked → mark `findings`; `finalize-module-map` passed → mark `map`; final
audit passed → mark `overview` (which sets `latest_completed`). Then offer acceptance
(one word) unless the run is inspection-only.
