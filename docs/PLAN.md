# Project Doctor — Canonical Plan (v3.4)

> **Rename note (57B-33, 2026-07-17):** the product was renamed to **Project Analysis**; this historical plan keeps its original "Project Doctor" name unchanged as a preserved record.

**Status:** canonical. v3.2 base (2026-07-16, reviewer-approved for Phase 0) plus the
v3.4 amendments (§16) and execution decisions (§17). Linear project **Project Doctor v1**
(team `57blocks-Project-Doctor`, issues 57B-5…57B-21) tracks execution against this plan.

---

## 1. Product

A **general-purpose** Claude Code skill that acts as a project doctor: point it at any
codebase (single- or multi-repo workspace) with zero required setup, and it produces:

1. **Project overview + diagnosis** (primary) — what the project is, how it's structured
   into modules, and its problems: coupling, change friction, duplication, dead code,
   half-finished migrations, dependency risk, missing safety nets, inconsistent patterns,
   and anything else notable.
2. **Module drill-down** (secondary) — for any module the overview surfaced:
   `prd.md` (PM-facing: what the module is/does, roles, flows, rules) and `health.md`
   (dev-facing: problems with evidence, dependency picture, traced change scenarios).

**The skill is the product; WCP is only the first test fixture and benchmark.**
**Stack support:** v1 = first-class JS/TS and Go; other stacks analyzed with explicitly
disclosed reduced coverage.

### Non-goals for v1
- Executing refactors or migrations (future version).
- Domain-specific fact compilers or PRD rendering pipelines.
- Schema gates, hash contracts, verifier pipelines, formal merged-graph artifacts,
  checker scripts.
- Run-to-run comparison (`/doctor compare`) — **V2**, tracked as 57B-21.

## 2. Core principles

1. OSS tools produce repository-wide numbers; the model produces judgment. Small,
   framework-oriented discovery adapters allowed only on demonstrated failure.
2. Generalization discipline: zero target-project literals in SKILL.md/lenses/templates;
   project knowledge lives in that project's `state/` and `output/`.
3. Factual/diagnostic claims carry provenance-anchored citations; citations make claims
   inspectable, not correct — audits and benchmarks do that.
4. **Zero project-specific configuration** (the honest form of "zero setup"): auto-discovery
   replaces config files; unknowable facts become explicit assumptions. Users still need the
   supported analysis tools installed — or they accept explicitly disclosed reduced coverage.
5. Honest coverage: a missing/failed tool → reduced-coverage disclosure, never a clean
   bill of health; absence claims only about analyzed sources ("not provided ≠ does not
   exist").
6. Conventions live in prompts/templates/wrapper — a convention becomes code only after
   a real run violates it.
7. Tool-wrapper boundary: invoke allowlisted tools, safe args, redact, bound outputs,
   record manifests — never interpret findings, validate documents, or score reports.

## 3. Invocation, language, run pointers

```
/doctor [path] [--language zh-CN|en]
/doctor module <module-id> [--from-run <run-id>] [--language zh-CN|en]
```

- One language per run (default `en`); real UI labels always verbatim from source.
- Pointers per project: `latest_completed` (automatic, inspection-only) and `current`
  (explicit user acceptance; the doctor offers acceptance when an overview completes).
- Drill-down resolution: `--from-run` → `current` → **refuse** (never implicitly use an
  unaccepted run).

## 4. Overview workflow

1. Inventory + provenance (repos via `.git` dirs AND files; stacks; run provenance block).
2. Preliminary `module_candidates.md` (marked preliminary).
3. Run tools once (signals + per-signal manifests); grouped lens agents analyze bounded
   views, returning findings in the shared shape (§7) against candidate module IDs.
4. Finalize `project_map.md` from all signals (UI→API links, endpoint→persistence,
   imports, scheduler/notification links, shared DB tables, co-change). Modules carry
   evidence + confidence; classified business / platform / shared-infra / unresolved.
5. Assign findings to finalized module IDs.
6. Write `overview.md`: executive summary; project map (Mermaid topology); top problems
   by priority; module health table; **lens coverage table**; assumptions & open questions.
7. Offer acceptance.

Wall-clock ~10–15 min is a target; quality is the gate.

## 5. Stable IDs and persistent facts

- `project-id`: deterministic from canonical workspace root (+ path-hash); `module-id`:
  stable slug preserved across runs, renames/merges recorded as aliases.
- `state/<project-id>/confirmed_facts.md`: only explicitly confirmed corrections; each
  record has scope/source/date/status (`active|superseded|conflicts_with_observation`);
  conflicts with observed code are surfaced, never silently resolved.

## 6. Lenses and toolchain

Nine lenses in 3 grouped agents (cadence-driven; fixed by Phase 0 data): **A** static
structure (scc, lizard, jscpd, dependency-cruiser, staticcheck, go list — local, <4s);
**B** history & safety (PyDriller lane + observed test evidence); **C** risk & open lens
(osv-scanner + npm/yarn outdated network lane + free observation). Canonical tool
reference: `tools/README.md` (private-until-scrubbed). Tool execution safety: never run
target code/configs; project-owned tool configs never loaded as behavior; repo-provided
interpreters refused.

## 7. Finding contract (prompt-level template)

`claim / lens / affected_modules / evidence[] / impact / priority (critical|high|medium|
low — impact, likelihood/exposure, change frequency where relevant) / confidence
(orthogonal) / limitations / suggested_direction`. Merge only on evidence overlap;
high-confidence diagnoses normally need ≥2 independent signals. Behavior-activation
labels (`active|conditional|status unresolved`) with evidence. Scope-guarded absence
claims.

## 8. Module drill-down

Immutable runs: reuse only when every repo's HEAD/clean-state/tool-versions match the
overview provenance **and the analysis identity is unchanged** — doctor/wrapper version,
tool definitions, prompt/template version, run language, and the confirmed-facts revision
(compared as recorded provenance fields, NOT a hash subsystem); any mismatch → new
overview run; drill-downs write to their own
`output/<project>/drilldown/<run-id>/` with a `source_overview_run` link. Dirty-worktree
overviews are inspection-only. `prd.md` sections included where applicable (UI entry
points verbatim, roles, flows, rules/states, notifications/integrations, open questions);
`health.md` uses up to three applicable change scenarios (UI / business-rule / data-API /
scheduler-event / external-integration).

## 9. Provenance, manifests, citations

Run provenance: per repo path, HEAD, branch, credential-redacted remote URL, HEAD
timestamp, `git describe`, dirty detail (status+path capped), submodule pins, history
completeness; run-level doctor version, model id, language, analyzed-at. Per-signal
manifests: structured argv/cwd/env, tool version (drift vs validated set disclosed),
status (§17.3), scope incl. source universe + analysis roots, exclusions (two-tier,
disclosed), network + scan date, output locations. Citations `repo@commit:path:line`;
dirty → `repo@WORKTREE:path:line`.

## 10. Read-only and secret policy

Never execute target scripts/builds/tests; env files read as variable **names** only
(values need explicit approval; endpoints derived from committed code); no raw env files
to agents; excluded: node_modules, vendor, build output, generated code, doctor's own
output/state.

**Redaction scope (raw-at-rest policy):** the redaction guarantee applies to everything
that persists in git, enters model context, or ships in a package. **Raw tool output may
contain whatever the tools emitted** (possibly incl. secrets a target repo leaks); it is
protected by containment, not sanitization — local-only, gitignored, never read by a
model, never packaged. Bounded views/samples/manifests (the persisted artifacts) are
always redacted, incl. credentials in remote URLs.

## 11. Project layout

```
project-doctor/
  SKILL.md  lenses/  templates/  tools/README.md  docs/PLAN.md
  wrapper/            # Python package (executor, tooldefs, parsers, sanitize,
                      #   manifest, git_history/) + pytest suite
  benchmark/          # private WCP grading fixtures (Phase 2)
  spike/              # frozen Phase 0 evidence (private)
  state/<project>/    # confirmed facts + run pointers
  output/<project>/overview/<run>/ | drilldown/<run>/
```

**Privacy & packaging:** spike/, benchmark/, and tools/README.md are private fixtures —
never packaged/published. Ship candidates: SKILL.md, lenses, templates, wrapper. A
sanitized public toolchain doc is a Phase 3 deliverable.

## 12. Phases (user reviews every exit; strict phase gating)

| Phase | Content | Exit |
|---|---|---|
| 0 | Toolchain spike | tools/README.md + grouping; evidence revision-anchored; external reviews absorbed |
| 1 | Wrapper (57B-10, FIRST) + scaffold ∥, discovery, lenses, synthesis, lifecycle | Full `/doctor` run on WCP; false-positive audit (zero unsupported high-confidence claims); rediscovers old project_context topology; zero WCP literals in skill files |
| 2 | Drill-down + leave benchmark | Graded vs fixed checklist incl. negative checks; PM PRD readable standalone |
| 3 | Genericity & release | Two external repos + partial-workspace run (wcp-ui alone); cross-platform install matrix + preflight; docs |

## 13. Working agreements (user-mandated)

Plan first, explicit go-ahead before implementation. Finish a phase → review → confirm →
sign-off → move; no cross-phase early starts. Work directly in the main session;
subagents only when genuinely necessary. No commits without user review of the diff.

## 14. Risks

Sampled-analysis-as-truth → coverage table + manifests + limitations + two-signal rule.
Secret leakage → §10 + tested redaction. Tool-mediated execution → §6 safety. Stale/mixed
state → immutable runs + pointers + stable IDs. Complexity creep → §2.6/§2.7 boundaries.

## 15. Amendment history (summary)

- **v1→v3.2** (2026-07-16): four external review rounds — zero-config discovery,
  multi-signal module map, finding shape, provenance, coverage-status honesty, run
  pointers/immutability, language flag, stable IDs, module-sensitive drill-down.
- **v3.4 batch**: analysis scope + referenced-but-not-analyzed + external-systems table;
  `observed|inferred|unresolved|user-confirmed` labels; behavior-activation labels;
  history completeness; runtime-limitations disclaimer; bus-factor-as-proxy wording;
  non-git = reduced coverage; provenance completion; partial-workspace test (P3.1);
  run comparison deferred to V2 (57B-21).

## 16. v3.4 amendments in force

All items in §15's v3.4 batch are requirements on the Phase 1/2 tasks (recorded per-issue
in Linear as "Additions (plan v3.4)").

## 17. Execution decisions (binding, decided during Phase 0)

1. **Wrapper language/architecture:** Python + pytest; modular package (no monolith);
   data-driven tool definitions (plain data, not a plugin system); shell at most thin
   launchers. No TS/Go without demonstrated need.
2. **Contract ownership:** 57B-10 (wrapper) **defines** the targets input contract and
   ships its fixture; 57B-11 (discovery) **produces** conforming output. This resolves
   build ordering: wrapper first, against the fixture.
3. **Status contract:** per signal `complete | partial | failed | skipped` — skipped =
   never invoked (guard refusal with no fallback, preflight-offline, tool missing);
   failed = invoked without a valid result (error exits, malformed output, mid-run
   network/auth errors — an *attempted* run that hits a network error is failed, never
   skipped — and timeouts); partial = ran but materially incomplete (incl. PM-fallback
   approximations). **Severity order: `failed > partial > skipped > complete`**; the
   aggregate is the worst status present; wrapper exit: failed → nonzero, else 0.
4. **Determinism criterion:** normalized outputs are byte-deterministic **under
   identical inputs** — same HEADs, clean worktrees, same tool versions, network lanes
   excluded or snapshot-pinned — with volatile fields (wall time, timestamps) excluded.
5. **Target immutability (git-visible):** pre-run vs post-run `git status --porcelain`
   snapshots must be identical (valid for already-dirty inspection-only targets). This
   guarantees **git-visible state only** — writes into ignored paths (caches,
   node_modules) are not detectable this way; stronger enforcement (sandboxing) is out
   of v1 scope and the guarantee is worded accordingly.
6. **TargetSpec ownership:** 57B-10 (wrapper) defines **TargetSpec** (the targets
   contract), its fixtures, and the executor. 57B-11 (discovery) **implements** the
   producers — package-manager, stack, analysis-root, generated-file (Tier-2 exclusion),
   and external-system candidate discovery — and emits TargetSpec. One Python package;
   two tasks' modules; the executor never re-derives targets.
7. **External systems — candidate-disposition workflow (not name lists):** discovery
   mechanically produces integration *candidates* from direct imports, SDK/client
   initialization, outbound endpoints, committed config/env names, OAuth providers, and
   CI/deployment resources. Every candidate is dispositioned by the lens as
   `included | unresolved | excluded` **with evidence**; dependency-only or
   lockfile-only signals never prove an active integration. The WCP integration list
   (AWS/S3, Jira, Slack, Gmail/Google OAuth, LDAP, …) survives only as a **private
   false-negative benchmark**; benchmark names never enter shipped skill code.
8. **Toolchain (Phase 0 verdicts):** scc, lizard, jscpd (same-language only),
   dependency-cruiser (TS undercount disclosed; >15% unresolved ⇒ partial), staticcheck,
   PyDriller (primary history; plain git + `--full-history` fallback), go list -deps
   -json (package level sufficient), osv-scanner + npm/yarn outdated (PM-aware, corepack
   guards, yarn execution vectors refused). knip DROPPED; code-maat REJECTED.
9. **Report language & PM companion (owner decision 2026-07-16, supersedes §3's
   `en` default):** default run language is **zh-CN** (`--language en` opts out);
   UI labels/identifiers/citations/status vocabulary stay verbatim; intermediate
   artifacts (lens findings) may remain English. `overview.md` opens with a table
   of contents. The overview stage additionally produces **`pm-overview.md`** — a
   non-technical, business-language companion for PMs derived strictly from
   overview.md (same facts, linked citations, no new claims).
10. **Invocation command (decided during 57B-9):** `/project-doctor`, not `/doctor`.
   The name `/doctor` is taken by Claude Code's bundled doctor skill (installation
   health check); personal/project skills CAN override bundled skills, but shadowing a
   diagnostic command is deliberately avoided. Skill directory/frontmatter name:
   `project-doctor`; §3's `/doctor` grammar is read with this substitution (57B-9's
   Linear acceptance is amended accordingly). Run-id format (also 57B-9):
   `YYYYMMDDThhmmssZ-<6-hex input digest>` (ordered repo HEADs, dirty markers,
   language) — labels for recognizability; uniqueness comes from never reusing an
   existing run directory (first free `-N` suffix on collision).
