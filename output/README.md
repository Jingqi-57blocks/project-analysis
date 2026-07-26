# output/ — legacy placeholder (runs no longer write here)

**This directory is a legacy placeholder.** Run output now lives entirely under the
external **data root** (`<data-root>/output/...`, resolved via `$PROJECT_ANALYSIS_HOME`
or the platform default — see `README.md`'s "Where results go"), never inside this
checkout. The shape below documents the run-artifact layout under the data root; this
in-repo `output/` directory is never populated by a current version of the wrapper.
Everything else under `output/` besides this README stays gitignored, kept only for a
pre-relocation layout / `migrate --legacy-skill-root` compatibility.

```
output/<project-id>/
  overview/<run-id>/
    overview.md              PM-primary document (nine sections, ~10-min read)
    technical-overview.md    full-detail companion (findings, metrics, disposition, coverage)
    project-map.md           reusable topology
    module_candidates.md     preliminary, pre-lens
    signals/                 wrapper output: views + manifests + run-summary.json
      raw/                   contained raw tool output (self-gitignored, never
                             model-read, never packaged)
  drilldown/<run-id>/
    <module-id>/prd.md  <module-id>/health.md
    source_overview_run      link back to the overview run this was built from
```

- `<run-id>` — `YYYYMMDDThhmmssZ-<6-hex digest>`: a UTC start timestamp plus a short
  digest of the run inputs (ordered repo HEADs, dirty markers, language). These are
  identifying labels, not a uniqueness guarantee — uniqueness comes from never reusing
  an existing run directory (if the computed name exists, the first free `-2`, `-3`, …
  suffix is appended). Runs are **immutable snapshots**: a repo mismatch
  (HEAD/dirty/tool versions/analysis identity) always means a NEW overview run, never a
  partial refresh of an old one.
- Overview and drill-down trees are separate on purpose: drill-downs reference their
  source overview via `source_overview_run` instead of mutating it.
