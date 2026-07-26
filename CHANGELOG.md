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
- `bin/project-analysis` — a pre-venv launcher, invocable by absolute path from any working
  directory: self-locates the skill, checks the one hard prerequisite (Python 3.11+) with an
  actionable message, and dispatches to the wrapper. Low syntax floor so an unsupported
  interpreter still gets the message instead of a `SyntaxError`.
- `analysis_wrapper/paths.py` — canonical, environment-independent skill-root / wrapper-root
  resolver for new code (no `CLAUDE_SKILL_DIR` dependency), plus the persistent-data and
  generated-runtime roots: `data_root()` (`$PROJECT_ANALYSIS_HOME` → macOS Application
  Support → `${XDG_DATA_HOME:-~/.local/share}`), `validate_data_root()`, `runtime_root()`,
  `output_root()` / `state_root()` / `exported_root()`, and `venv_dir()` /
  `node_tools_runtime()` / `go_tools_bin()`.
- `migrate` command — moves persistent data from a legacy in-code location to the data root.
  Idempotent, never merges distinct project namespaces, never relocates generated runtimes
  (those are rebuilt), and exits non-zero on failure.
- `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` — optional Claude Code
  plugin packaging (secondary to the multi-agent Skills CLI channel).
- `NOTICE.md` — third-party notices for bundled Python/Node/Go components, kept separate from
  the external tools the analyzer only invokes.
- `RELEASE_CHECKLIST.md` — mechanical pre-publish gate, including an end-to-end plugin-channel
  smoke test and an item that intentionally fails until the v1 drill-down gating lands.

### Changed

- **Persistent data and generated runtimes now live outside the installed code tree.**
  `state/`, `output/`, and `exported/` resolve under the data root instead of the skill
  directory, and the virtualenv, installed Node modules, and Go binary live under
  `<data-root>/runtime/<contract>/`. Install, upgrade, and reinstall replace only code, so
  they can no longer destroy user data. Tracked install sources (`wrapper/node_tools`
  manifests, rules, lenses, templates) stay in the code tree.
- The data root is validated before use and is refused if it resolves inside the skill's own
  code tree or inside the analyzed workspace, protecting the read-only-target guarantee.
- `--skill-root` is still accepted but now means the *code* root only; it no longer determines
  where data is written.
- `SKILL.md` — `${CLAUDE_SKILL_DIR}` is now documented as an optional host convenience; the
  wrapper self-locates from its own path (analysis-run instructions no longer depend on it).
  "Two directory worlds" became three: code, data, and target.
- `README.md` — rewritten install / first-run / upgrade / uninstall story: the multi-agent
  Skills CLI is the primary channel (global-user scope), git-clone + symlink is the dev
  channel, Python 3.11+ is stated as the only hard prerequisite, results are documented at the
  external data root, and uninstall now warns that the data root persists. v1 scope (overview
  and diagnosis only; module drill-down not yet supported) is stated explicitly.
- `output/README.md`, `state/README.md`, `.gitignore` comments — corrected to reflect that runs
  write under the external data root; these in-tree paths are legacy placeholders only.
