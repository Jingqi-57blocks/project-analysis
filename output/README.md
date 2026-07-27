# output/ — analysis runs (runtime, never committed)

Everything under `output/` except this README is gitignored: run artifacts describe
*target* projects and never enter the skill repo.

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
```

- `<run-id>` — `YYYYMMDDThhmmssZ-<6-hex digest>`: a UTC start timestamp plus a short
  digest of the run inputs (ordered repo HEADs, dirty markers, language). These are
  identifying labels, not a uniqueness guarantee — uniqueness comes from never reusing
  an existing run directory (if the computed name exists, the first free `-2`, `-3`, …
  suffix is appended). Runs are **immutable snapshots**: a repo mismatch
  (HEAD/dirty/tool versions/analysis identity) always means a NEW overview run, never a
  partial refresh of an old one.
- Module Drill has no runtime layout yet. Its approved standalone and overview-backed
  source contract is in `references/module-drill-mvp-contract.md`.
