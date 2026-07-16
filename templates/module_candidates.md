# {{project_name}} — Module Candidates (PRELIMINARY)

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.

> **PRELIMINARY** — produced by mechanical discovery before any lens ran. Candidate
> boundaries and names WILL change in `project_map.md`; nothing here is a conclusion.
> Run `{{run_id}}`; provenance in `overview.md`.

## Module candidates

| candidate-id | suggested name | signals | evidence |
|---|---|---|---|
| `{{candidate_id}}` | {{name}} | {{routes / folder-structure / table-names / committed-api-config}} | {{citations}} |

Signal notes:
- **routes**: route registrations / handler tables found in committed code.
- **folder-structure**: recurring top-level or domain folder patterns (weak alone).
- **table-names**: migration files, model/schema definitions.
- **committed-api-config**: OpenAPI/proto/GraphQL schemas, gateway config committed to
  the repo.

## Integration candidates (mechanical, undispositioned)

Disposition (`included | unresolved | excluded`) happens in the map stage with lens
evidence — NOT here. Dependency-only and lockfile-only signals are labeled as such and
never treated as proof of an active integration.

| candidate | signal kind(s) | source repo | evidence |
|---|---|---|---|
| {{value}} | {{one_or_more_of: import / client_init / outbound_endpoint / config / env / oauth_provider / ci_resource — a candidate observed several ways lists every kind (e.g. `import+client_init`); when dependency/lockfile entries are the ONLY signals, label it `dependency-only`}} | {{repo_id}} | {{citations}} |

## Coverage notes

{{what discovery could NOT see: unparsed file kinds, excluded paths, non-git folders,
stacks with reduced support — each with the consequence for candidate completeness}}
