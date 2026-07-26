# state/ — legacy placeholder (runs no longer write here)

**This directory is a legacy placeholder.** Per-target persistent state now lives
entirely under the external **data root** (`<data-root>/state/...`, resolved via
`$PROJECT_ANALYSIS_HOME` or the platform default — see `README.md`'s "Where results
go"), never inside this checkout. The shape below documents the layout under the data
root; this in-repo `state/` directory is never populated by a current version of the
wrapper. Everything else under `state/` besides this README stays gitignored, kept only
for a pre-relocation layout / `migrate --legacy-skill-root` compatibility.

```
state/<project-id>/
  pointers.json         run pointers for this project
  confirmed_facts.md    ONLY user-confirmed corrections, with provenance
```

- `<project-id>` — deterministic: workspace-root basename + short hash of the canonical
  absolute path (same rule the wrapper uses for repo-ids).
- `pointers.json` — `{"latest_completed": "<run-id>", "current": "<run-id>|null"}`.
  `latest_completed` is set automatically when any overview finishes and is for
  **inspection only** — it is never an implicit drill-down source. `current` is set
  ONLY on the user's explicit acceptance and is the default source for module
  drill-downs.
- `confirmed_facts.md` — one record per confirmed correction: scope, source, date,
  status (`active | superseded | conflicts_with_observation`). A fact that contradicts
  observed code is surfaced in reports, never silently applied.
