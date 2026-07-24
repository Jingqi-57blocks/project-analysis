# Changelog

All notable changes to Project Analysis are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The single source of the current version is the `VERSION` file at the repository root;
`project-analysis-wrapper --version` prints it.

## [Unreleased]

Work toward the first public release — packaging the analyzer as an installable, portable,
upgradeable Agent Skill (portable install, lane-conditional toolchain, safe upgrades).
Tracked in Linear 57B-89.

### Added

- `VERSION` — single source of the skill version; `project-analysis-wrapper --version`
  prints it alongside the `analysis-wrapper` package version.
- `LICENSE` — MIT.
- Supported-platforms statement in `README.md`.
- `tools/manifest.json` + `tools/manifest.schema.json` — a machine-readable,
  schema-validated inventory of the analyzer toolchain (ownership, lane, requirement,
  validated versions, install source, network host, platform support, runtime-contract
  impact, and reconcile behavior). Consumed by later phases (`doctor`, `setup`, the
  compatibility check); the human-readable companion remains `tools/README.md`.
