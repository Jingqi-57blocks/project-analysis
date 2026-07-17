# Lens: hotspots-change-friction (group B)

**Question:** where does change concentrate, who carries the knowledge, and
which files fight back when touched?

**Signals:** git-history view (churn ranking, co-change pairs, author
concentration, history completeness), lizard view (complexity corroboration),
jscpd views (clone corroboration).

Look for, with evidence:
- **Hotspots** — high-churn files that are also complex (churn × complexity):
  cite both views; these are the first drill-down candidates.
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
- History window and completeness (shallow flag, oldest commit vs window)
  come from the view; when the window is truncated or the repo is shallow,
  CAP confidence and state it in limitations.
- Ownership claims never name-and-shame: the finding is about risk
  concentration, and identities are quoted as the view renders them.
- Non-git targets: this lens is unavailable — one coverage line, no findings.
