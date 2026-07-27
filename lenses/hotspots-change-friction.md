---
shard: repo
# shard: git-history is fundamentally per-repo -- a commit history cannot
#   span two repos, and unlike duplication's jscpd-cross there is no
#   cross-repo git-history tool at all. Every bullet below (hotspots,
#   co-change, bus-factor, change friction, change-spread packaging) is
#   phrased in terms of one repo's own files/directories/commits and never
#   compares across repos, so this lens is safely repo-sharded (corrected
#   from an initial workspace guess inherited from the old README grouping,
#   which batched it with safety-net for an unrelated reason -- shared
#   history-lane invocation, not a shared need for cross-repo comparison).
signals: [git-history, jscpd, lizard]
# signals: lenses/coverage-map.json requires only {git-history}; this
#   file's own "Signals:" line additionally names lizard (complexity
#   corroboration) and jscpd (clone corroboration -- the per-repo variant,
#   never jscpd-cross, since this lens shards per repo and jscpd-cross is
#   not attributable to a single repository_ref; see duplication.md).
---
# Lens: hotspots-change-friction (group B)

**Question:** where does change concentrate, who carries the knowledge, and
which files fight back when touched?

**Signals:** git-history view (churn ranking, co-change pairs, author
concentration, history completeness), lizard view (complexity corroboration),
jscpd views (clone corroboration).

Look for, with evidence:
- **Hotspots** — high-churn files that are also complex (churn × complexity):
  cite both views; these are the first drill-down candidates.
- **Churn concentration by directory** — when a large share of the repo's
  own top-churn files cluster in one directory, that CONCENTRATION is itself
  a finding, distinct from and in addition to naming the individual hotspot
  files: state the share (e.g. N of the top-churn files) directly from the
  history view, not an eyeballed estimate.
- **Co-change coupling** — file pairs that keep changing together across
  commits, especially ACROSS module boundaries (bulk changesets are already
  excluded by the wrapper; say so when quoting pair counts).
- **Knowledge concentration (bus-factor proxy)** — files/modules where one
  author owns the dominant share of commits/churn. REQUIRED framing: this is
  an ownership-concentration PROXY from git identities (deduped, bots
  excluded), not a measure of who can maintain the code. Report the
  identity-resolution caveats from the view (uncertain name matches stay
  unmerged).
- **Change friction** — files whose changes cluster with reverts/fix-ups
  (subject lines in the view), or hotspots that keep reappearing.
- **Change-spread packaging (for the overview's representative change paths)** —
  where the evidence intersects (a hot file that is also complex and has no
  co-changing test, or a cross-directory co-change cluster spanning several top-
  level dirs), say so plainly and cite all sides. Synthesis SELECTS the strongest
  such intersections as worked change-path examples, so name the directories a
  change crosses rather than only ranking single files.

Rules:
- **Attribute each co-change pair to the module of its FULL path, never a
  shared basename.** Files that share a basename (a per-type `handler.go`, a
  per-feature `index.ts`) across different packages/directories belong to
  different modules — read the whole path and name the module each cited file
  actually sits in before calling a pair "lockstep" or "co-changing". A pair
  split across two modules is cross-module coupling, reported as such.
- History window and completeness (shallow flag, oldest commit vs window)
  come from the view; when the window is truncated or the repo is shallow,
  CAP confidence and state it in limitations.
- Ownership claims never name-and-shame: the finding is about risk
  concentration, and identities are quoted as the view renders them.
- Non-git targets: this lens is unavailable — one coverage line, no findings.
