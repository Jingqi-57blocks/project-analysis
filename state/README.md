# state/ — per-target persistent facts (runtime, never committed)

Everything under `state/` except this README is gitignored: it is knowledge about
*target* projects, which never enters the skill repo (generalization discipline).

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
