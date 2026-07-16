# Lens: complexity (group A)

**Question:** which specific functions/files are hard to change correctly, and
does that difficulty sit where change actually happens?

**Signals:** lizard view (per repo: function CCN/NLOC/params, worst
offenders), scc complexity column (corroboration), git-history churn (from
group B's signal view — read it for corroboration, its interpretation belongs
to the hotspots lens).

Look for, with evidence:
- **Concentrated complexity** — functions with extreme cyclomatic complexity
  (lizard top rows), especially clusters in one file/module: cite each.
- **Long-parameter / god-function patterns** — high param counts + high NLOC.
- **Complexity × churn** — a complex file that history shows changing often
  is a priority multiplier; cite BOTH views and raise priority, not
  confidence.
- **Threshold honesty** — lizard's defaults are conventions; a CCN of 15 in a
  parser table is not a CCN of 15 in payment logic. Judge, don't census.

Rules:
- Every function named in a claim cites the lizard view row AND the source
  location (`repo@commit:path:line` of the function).
- Do not enumerate every offender — findings are for the ones whose impact
  you can argue. The rest stay in the view for the drill-down.
- If lizard ran on a subset of languages (view manifest shows the -l set),
  state the uncovered languages in the coverage line.
