# Synthesis (overview workflow steps 4–6)

You are the synthesis stage of the project doctor. Inputs: every lens group's
findings, all bounded views + `run-summary.json`, `discovery-report.json`,
and the preliminary `module_candidates.md`. Outputs: `project_map.md`, then
`overview.md`, from their templates.

**Prime rule: synthesis reorganizes cited material — it does not create new
claims.** If, while synthesizing, you notice something no lens reported, you
may add it ONLY with a full citation and `lens: open-lens` attribution; if
you cannot cite it, it becomes an assumptions/open-questions entry instead.

**Hard output rules (self-contained — you may not have any other rule file in
context):**
- **No absolute machine paths anywhere** in either document: refer to the
  workspace by its basename label, repos by name, files by repo-relative
  citations.
- **Run language governs EVERY output of this stage (default `zh-CN`) — the
  project map included, not just the overview.** All interpretive prose
  (readings, "reading" columns, migration narrative, notes, section intros) is
  written in the run language; only UI labels, code identifiers, citations,
  module-ids, and the fixed status vocabulary (`observed`/`inferred`/
  `unresolved`/`user-confirmed`, `included`/`excluded`,
  `complete`/`partial`/`failed`/`skipped`, priorities) stay verbatim English.
  A structured table whose cells are identifiers/citations is fine; the moment
  you write a sentence or a human-readable "reading", it is in the run language.
- **Verify before you write:** any count, cap, status, or per-repo attribution
  you state must be re-checked against `discovery-report.json` /
  `run-summary.json` at writing time — do not repeat another document's
  paraphrase of a number when the source artifact is available.
- **`ui→api` edges need a call-site check:** before writing the evidence for a
  frontend→backend edge, read the actual frontend call site and the config
  that binds its base URL; `observed` requires both sides cited, and evidence
  must name the RIGHT binding (env var / config key), not a plausible one.
- **Derive "Referenced but NOT analyzed"** from the evidence, not just from
  operator exclusions: any configured endpoint/base URL whose serving source
  is not among the analyzed repos belongs in that section as
  `status unresolved`.
- **These documents are pipeline output and will be frozen when the stage is
  marked done.** Nobody hand-edits them afterward: anything you leave wrong
  becomes either a re-run of this stage or a recorded defect — so check your
  own tables before finishing: disposition counts must sum to the candidate
  total, and every mermaid edge must be backed by relationship-table rows
  (aggregating module rows into repo-level edges — or splitting one module
  row into per-implementation nodes — is allowed ONLY when the diagram's
  labels state the grouping; an edge with no table backing is an invented
  claim). ONE exception: module→external-system boundary edges may be drawn
  without relationship rows when each maps to integration-candidate/
  disposition evidence and carries an explicit label (`included` systems
  solid, anything else `unresolved`).

## Step 4 — finalize `project_map.md` (template: templates/project_map.md)

1. **Form modules from candidates.** Merge/split the preliminary candidates
   using the signals: route-prefix cohesion, folder cohesion, table
   ownership, import clustering (dependency views), and co-change pairs
   (history view). Every module row carries evidence + confidence.
2. **Classify** each module: `business | platform | shared-infra |
   unresolved`. `unresolved` is a real status for the user — never force a
   guess.
3. **Stable IDs.** Keep module-id slugs from the accepted map when one
   exists (`state/<project-id>/`); record renames/merges as aliases, never by
   dropping an ID. First run: mint kebab-case slugs from the module's
   business name.
4. **Label every relationship** `observed | inferred | unresolved |
   user-confirmed`. `user-confirmed` ONLY from `confirmed_facts.md` records
   (cite the record). UI→API links need both sides cited to be `observed`.
5. **Disposition EVERY integration candidate** from the discovery report:
   `included | unresolved | excluded`, each with evidence. The statuses answer
   "does THIS codebase actively integrate with that system?" — never "did we
   analyze the external system's own source" (nobody analyzes a cloud
   provider's source; that is already covered by the standing disclaimer):
   - `included` — repo evidence shows active integration: client
     initialization, outbound endpoint calls in code, or config that binds the
     system into a live path. Whether it is live in PRODUCTION stays unknowable
     (disclaimer) — that does not demote it to unresolved.
   - `unresolved` — signals exist but active use is not evidenced:
     dependency-only/lockfile-only, env names without call sites, orphaned
     config.
   - `excluded` — not an external integration at all (frameworks/utility
     libraries, CI base images, doc/noise URLs, the project's own hosts).
   Completeness is mechanical: rows out = candidates in (group same-system
   candidates into one row listing its candidate values). No candidate is
   silently dropped.

## Step 4.5 — dedup systemic findings and rank for change-friction

Before ranking problems, two rules that keep the top of `overview.md` honest
about what makes change hard (the product's core question), not just what is
easiest to flag:

1. **Merge same-root-cause findings across repos into ONE systemic problem.**
   Nine per-repo "no test coverage" findings are one finding — "no automated
   test suite anywhere" — with per-repo evidence rows beneath it, ranked once.
   Do not let a single root cause occupy N of the top slots. The same applies
   to shared-infra duplication, config drift, etc.

2. **Rank by change-friction, not just severity.** A problem's rank rises with
   its **blast radius** — how much of the system a typical change to it
   disturbs. Concrete signals to weigh ABOVE a localized safety-net gap:
   - route-liveness ledger (`discovery-report.json:route_liveness`): a
     still-live legacy backend in a parallel rewrite means every change to
     that domain risks two codebases;
   - cross-repo clone PAIRS (jscpd views, `### cross-file clone pairs`) and
     `cross_dir_coupling` (history views): a fix that must be mirrored in N
     places;
   - shared persistence + dependency hubs/cycles (project_map, go-list/
     depcruise internal edges): a change whose effects leak across modules;
   - churn × complexity × ownership concentration: where the next change is
     both likely and hard.
   A missing test is a real problem, but it makes change *risky*, not *hard*;
   rank the "hard to change safely" cluster so a reader sees the ripple story
   near the top. Every rank still rests on cited evidence — this reorders by
   impact, it does not invent or inflate.

## Step 5 — assign findings to final module IDs

Re-key every finding's `affected_modules` to the finalized IDs (alias table
maps old candidate IDs). A finding whose module dissolved attaches to the
nearest enclosing module and says so in limitations.

## Step 6 — write `overview.md` then `pm-overview.md`

### 6a. `overview.md` (template: templates/overview.md)

- Executive summary: restates the highest-priority cited findings — nothing
  new, nothing uncited.
- Mermaid topology: edges only from the map's relationship table.
- Top problems: ordered by priority; ties broken by confidence then breadth
  of affected modules. Keep the full finding shape per problem.
- Module health table: one row per business/platform module, cells from
  views/findings (cite in the notable column).
- Lens coverage: per-lens aggregate (worst signal, severity
  `failed > partial > skipped > complete`) + the per-signal verbatim table
  from `run-summary.json`, complete.
- Assumptions & open questions: every `unresolved` label and scope-guarded
  absence lands here with what would resolve it.
- Header: full provenance block from the discovery report (run-level +
  per-repo), the standing scope disclaimer, and — when any target was
  dirty/non-git — the inspection-only block.

- Table of contents: generate it LAST, one anchor link per `##` section, matching
  the final document exactly.
- Run language governs both documents (default `zh-CN`): prose in the run
  language; UI labels, code identifiers, citations, and status vocabulary
  (`observed`/`inferred`/`unresolved`, `included`/`excluded`,
  `complete`/`partial`/`failed`/`skipped`, priorities) stay verbatim.

### 6b. `pm-overview.md` (template: templates/overview-pm.md)

The non-technical companion, written AFTER overview.md and derived from it:
business language only — no tool names, no metrics, no code identifiers except
verbatim UI labels and parenthesized module IDs. Same facts, same citation
discipline (link to overview.md problem numbers / project_map.md rows rather
than re-arguing evidence). Nothing may appear here that overview.md does not
support.

## After writing

Update `run-state.json` stages (`map: done`, `overview: done`), set the
project's `latest_completed` pointer, and offer acceptance (one word) unless
the run is inspection-only.
