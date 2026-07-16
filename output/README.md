# output/ — analysis runs (runtime, never committed)

Everything under `output/` except this README is gitignored: run artifacts describe
*target* projects and never enter the skill repo.

```
output/<project-id>/
  overview/<run-id>/
    overview.md  project_map.md  module_candidates.md
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
