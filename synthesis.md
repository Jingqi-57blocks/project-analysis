# Synthesis (overview workflow steps 4–6)

You are the synthesis stage of the project doctor. Inputs: every lens group's
findings, all bounded views + `run-summary.json`, `discovery-report.json`,
and the preliminary `module_candidates.md`. Outputs, in this order:
`project-map.md`, then `technical-overview.md`, then `overview.md` — each from
its template.

`overview.md` is the **primary, human-facing document**: a reader with no prior
context understands the system and its biggest changeability risks in ~10
minutes without opening anything else. `technical-overview.md` is its
full-detail companion (all findings, evidence, metrics, disposition, coverage).
`project-map.md` is the reusable topology. The simpler document is DERIVED from
the fuller one and never contradicts it.

**Prime rule: synthesis reorganizes cited material — it does not create new
claims.** If, while synthesizing, you notice something no lens reported, you
may add it ONLY with a full citation and `lens: open-lens` attribution; if
you cannot cite it, it becomes an assumptions/open-questions entry instead.

**Hard output rules (self-contained — you may not have any other rule file in
context):**
- **No absolute machine paths anywhere** in any document: refer to the
  workspace by its basename label, repos by name, files by repo-relative
  citations.
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
- **Verify before you write:** any count, cap, status, superlative, or per-repo
  attribution you state must be re-checked against `discovery-report.json` /
  `run-summary.json` at writing time — do not repeat another document's
  paraphrase of a number when the source artifact is available.
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
  act on. Each claim must be verifiable from its own citation alone; never make
  the reader cross-check two documents to understand one point. Minimize what a
  human must verify without omitting what shouldn't be omitted. Facts come from
  code — never fabricated, never inferred from a name.

## The six changeability questions

Change difficulty — not defect count — is the product's core question. Drive the
diagnosis (overview.md §4) and the module changeability table (overview.md §6)
by answering these six, each ONLY from cited evidence:

1. **Boundary clarity** — does each module own one coherent responsibility, or
   is it a grab-bag? (structure-inventory, route/folder cohesion, dependency
   clustering.) → the *responsibility clarity* cell.
2. **Change spread** — when this area changes, how many files/directories/repos
   move with it? (co-change pairs, cross-directory coupling, clone pairs.) →
   the *change spread* cell.
3. **Rule locality** — does a business rule live in one place, or is it smeared
   across layers/services? **Assess this ONLY along the representative change
   paths you actually sample (§5) — never claim it repo-wide.**
4. **Hidden coupling** — what breaks that a reader wouldn't expect? (shared DB
   tables, dependency hubs/cycles, token/config trust edges.) → the *hidden
   coupling* cell.
5. **Duplication & evolution debt** — is the same logic maintained in N copies;
   are there parallel implementations / partial replacements / compatibility
   layers of one capability? (clone pairs, route-liveness ledger, two packages
   doing one job.) → feeds §4 and the *change spread* / *hidden coupling* cells.
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

## Step 4 — finalize `project-map.md` (template: templates/project-map.md)

1. **Form modules from candidates.** Merge/split the preliminary candidates
   using the signals: route-prefix cohesion, folder cohesion, table
   ownership, import clustering (dependency views), and co-change pairs
   (history view). Every module row carries evidence + confidence.
2. **Classify** each module: `business | platform | shared-infra |
   unresolved`. `unresolved` is a real status for the user — never force a
   guess. Business capabilities and technical/platform components are DISTINCT
   classes; keep them so (they are never mixed at one level in overview.md §3).
3. **Stable IDs.** Keep module-id slugs from the accepted map when one
   exists (`state/<project-id>/`); record renames/merges as aliases, never by
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

## Step 6 — write `technical-overview.md` then `overview.md`

### 6a. `technical-overview.md` (template: templates/technical-overview.md)

The full-detail companion. Carries: the complete provenance block (run-level +
per-repo) from the discovery report; the standing scope disclaimer (and the
inspection-only block when any target was dirty/non-git); complete analysis
scope; EVERY finding in the shared shape ordered by priority (ties broken by
confidence then breadth), systemic findings once with per-repo evidence rows;
the per-module health table with metric columns (absence = `no concern
observed` scoped to signals that ran, never "healthy"); the full
integration-candidate disposition table (counts sum to total); the lens
coverage table (status over REQUIRED signals, optional-capability gaps as
`partial — <capability> unavailable`) plus the verbatim per-signal detail from
`run-summary.json`; and assumptions/open-questions. Generate its table of
contents LAST. Topology lives in `project-map.md` — link, don't duplicate.

### 6b. `overview.md` (template: templates/overview.md)

The PRIMARY human-facing document, written AFTER technical-overview.md and
derived from it — nothing here may appear that technical-overview.md does not
support. Nine sections, independently readable in ~10 minutes:

1. **Analysis basis** — compact header: run date, per-repo commit vintage
   (short), the few coverage limits that shape the reading, link to
   technical-overview.md, and the standing scope disclaimer. NOT the full
   provenance table.
2. **Project snapshot** — purpose; business capabilities; platform/shared
   components (kept separate from capabilities); **evidence-backed user roles**
   (only from permission checks / route guards / menu-or-role definitions /
   approval relations — otherwise `unresolved`, never inferred from a module
   name); a **system-evolution** line ONLY when evidence detects one (parallel
   implementations, partial replacement, compatibility layer — generic, no
   assumed "migration").
3. **Capability & system map** — business capabilities NEVER mixed with
   technical/platform components at one level; a SIMPLIFIED mermaid
   (business-labeled, identifiers parenthesized, no bare status codes / route
   paths / table names), edges backed by project-map.md.
4. **Overall changeability diagnosis** — the 3–5 strongest SYSTEMIC causes and
   explicitly HOW THEY REINFORCE one another (one coherent story, not a list).
5. **Representative change paths** — 2–3 project-level examples (one
   business-rule change, one API/data change, optionally one UI change) SELECTED
   where evidence is strongest (co-change ∩ complexity ∩ missing safety net) and
   citing it; each shows components crossed, responsibilities involved, side
   effects, verification required, why expensive. Rule locality is assessed here
   (sampled), not repo-wide. Per-module tracing stays in the module drill-down.
6. **Module changeability table** — columns responsibility clarity / change
   spread / hidden coupling / safety net / confidence; cells EXACTLY `confirmed
   concern` · `no concern observed` (basis inline) · `unknown`. Per-gap unknown
   mapping (§ six questions). Never "healthy".
7. **Prioritized findings** — systemic first, local second, coverage gaps
   third; each with impact / evidence (link to the technical-overview finding) /
   confidence / direction. Note explicitly: this is ENGINEERING-RISK priority,
   not a business-roadmap priority.
8. **External systems & boundaries** — plain summary of what the product relies
   on; disposition detail stays in technical-overview.md.
9. **Open questions & limitations** — ONLY questions code cannot answer, each
   stating WHY (if a re-run knob or producer could answer it, it is a coverage
   gap for the technical-overview backlog, not a question). Product context
   (ownership, usage, SLA, criticality, roadmap) listed as what code cannot
   know.

## After writing

Update `run-state.json` stages (`map: done`, `overview: done`), set the
project's `latest_completed` pointer, and offer acceptance (one word) unless
the run is inspection-only.
