# Project Analysis

A portable Agent Skill that examines a codebase (single- or multi-repo workspace) with
zero target-specific configuration and produces a **project overview + diagnosis**:
module map, ranked problems with evidence, and honest per-lens coverage reporting.

**v1 scope is overview + diagnosis only.** Module drill-down (a PM-readable module PRD
and a dev-facing health report) is planned but **not supported in v1** — do not invoke
it and do not expect it to work yet.

First-class stack support in v1: **JS/TS and Go**. Other stacks are analyzed with
explicitly disclosed reduced coverage.

## Supported platforms

- **macOS** and **Linux** — supported and validated.
- **WSL2** — supported (treated as Linux).
- **Native Windows** — not supported in v1.

**Agents officially validated for v1: Claude Code and Codex.** Other agents that support
the `npx skills` ecosystem or a symlinked skills directory (Cursor and others) are
expected to work but are best-effort, not validated.

## Prerequisite: Python 3.11+

This is the **only hard prerequisite**. The Python environment bootstrap below asks for
consent before installing anything (see the paragraph after the `bootstrap` command).
Node and Go tooling for the JS/TS and Go lanes is provisioned by the `setup` command
(`<wrapper-executable> setup`, or `doctor` then `setup` for the toolchain your target
actually needs) — it shows a plan and asks before installing anything, and never
installs Node, pnpm, or Go themselves. See
[Environment and coverage](#environment-and-coverage) below for what `setup` does and
the manual fallback commands it automates. Skip a lane you don't need and it runs with
disclosed reduced coverage instead of failing the whole run.

- **macOS:** `brew install python@3.11` (or newer), or use `pyenv`/`uv`.
- **Linux:** use your distribution's package manager (e.g. `apt install python3.11`) or
  `pyenv`/`uv`.
- **WSL2:** same as Linux, inside the WSL2 distribution.

Confirm with `python3 --version`.

## Install

### Primary: `npx skills add` (recommended)

[`npx skills`](https://github.com/vercel-labs/skills) is the open, multi-agent Skills
CLI. Install this skill for the agent(s) you use, at **global/user scope** (recommended,
so it's available in every project):

```bash
# Claude Code, global/user scope
npx skills add Jingqi-57blocks/project-analysis -a claude-code -g

# Codex, global/user scope
npx skills add Jingqi-57blocks/project-analysis -a codex -g

# Both at once
npx skills add Jingqi-57blocks/project-analysis -a claude-code -a codex -g
```

Omit `-g` to install **project-local** instead (installs under `./<agent>/skills/` and is
committed with that project) — useful if you want the skill version pinned per-repo
rather than shared globally:

```bash
npx skills add Jingqi-57blocks/project-analysis -a claude-code
```

By default `add` installs interactively and lets you choose symlink (recommended) vs.
copy; pass `-y`/`--yes` to skip prompts (e.g. in CI or a scripted setup).

> **Verified vs. unconfirmed:** the `-a/--agent`, `-g/--global`, `-y/--yes`, and the
> `add`/`list`/`update`/`remove` verbs above are documented in the
> [`vercel-labs/skills` README](https://github.com/vercel-labs/skills). The exact
> repository shorthand (`Jingqi-57blocks/project-analysis`) is this project's current
> GitHub remote; update it here if the repository moves (for example to an organization
> account) before release. If your installed
> CLI version behaves differently, treat its own `--help` output as authoritative and
> fall back to the git-clone channel below.

### Dev channel: `git clone` + symlink

For local development, or if you want the checkout itself (not a CLI-managed copy):

```bash
git clone <repository-url> project-analysis
cd project-analysis
cd wrapper
python3 -m analysis_wrapper.bootstrap
cd ..
```

Bootstrap requires Python 3.11+ and installs only this project's Python packages into
an isolated virtual environment under the external **data root**
(`<data-root>/runtime/<contract>/venv`, resolved via `$PROJECT_ANALYSIS_HOME` — see
"Where results go" below) — **never** into `wrapper/.venv`; that path is stale and no
longer used. It never installs or changes Node, Go, Homebrew, nvm, asdf, mise, or
standalone analysis tools. Bootstrap prints the exact venv and wrapper-executable paths
it used every time it runs, so you never need to hard-code the data-root location
yourself.

Confirm the wrapper is available — prefer the self-locating launcher, which finds the
bootstrapped venv for you regardless of where the data root resolves to on your machine:

```bash
bin/project-analysis --help
```

Then register the checkout with your client(s):

```bash
# Codex
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD" "${CODEX_HOME:-$HOME/.codex}/skills/project-analysis"

# Claude Code
mkdir -p "$HOME/.claude/skills"
ln -s "$PWD" "$HOME/.claude/skills/project-analysis"

# Cursor
mkdir -p "$HOME/.cursor/skills"
ln -s "$PWD" "$HOME/.cursor/skills/project-analysis"
```

**Do not use `ln -sf`.** If a destination already exists, inspect it and decide manually
whether it should be removed or retained — `-f` silently clobbers whatever is there.
Start a new client session after registration.

### Optional convenience: Claude Code plugin

This repository also ships a minimal Claude Code plugin wrapper
(`.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json`) so Claude Code users
can install it through `/plugin` instead of `npx skills`. This is a **Claude-Code-only
convenience path, secondary to `npx skills add`** — it doesn't help Codex or other
agents, and it uses Claude Code's own plugin cache/versioning rather than the
cross-agent Skills CLI.

```text
/plugin marketplace add <repository-url-or-owner/repo>
/plugin install project-analysis@project-analysis-marketplace
/reload-plugins
```

Because this skill ships as a single `SKILL.md` at the plugin root (no `skills/`
subdirectory), Claude Code loads it as one plugin-scoped skill,
`/project-analysis:project-analysis`. See
[Claude Code plugin docs](https://code.claude.com/docs/en/plugins) for how plugin
installs, caching, and updates work.

## First run

The zero-arg happy path — just invoke the skill, no flags required:

```text
# Claude Code or Cursor
/project-analysis
/project-analysis /absolute/path/to/project

# Codex
$project-analysis
$project-analysis Analyze /absolute/path/to/project
```

With no `path`, the current workspace is analyzed. The run language auto-detects from
the host locale (falling back to English when that's undecidable or not a delivered
language); pass `--language en` or `--language zh-CN` to override. Add
`--run-id <label>` for a readable run label. **Module drill-down (`prd.md`/`health.md`)
is not available in v1** — v1 ships overview + diagnosis only.

Run `<wrapper-executable> help` (or just `<wrapper-executable>` with no arguments) any
time for a one-screen, task-grouped tour of every command — get-started, analyze, find
results, maintain, and low-level/diagnostic (driven by the skill, never by hand).

The **first run checks the toolchain** and asks for **one confirmation**, not several:
the agent runs `doctor` to see what's needed, and — only if something is missing — runs
`setup --plan` to compute the install plan. It then presents, together, both the setup
plan (what would be installed, where, which network hosts contacted) and the run
parameters (workspace, discovered repos/languages, run language, export on/off,
approximate duration). Nothing is installed until you approve that one confirmation;
approving runs `setup --yes` and then the analysis. Declining the setup plan does not
abort the run — it proceeds with disclosed reduced coverage for whatever the missing
tooling would have covered, unless core execution is impossible. This agent-side flow is
UX layered on top of a hard guarantee the wrapper itself enforces on every invocation: a
real analysis command refuses outright if the installed runtime has drifted from what
the code expects, regardless of what the agent does.

It never installs Node, pnpm, or Go themselves — those stay yours to install (see
[Environment and coverage](#environment-and-coverage) below) — and a lane whose runtime
is missing is skipped with a clear reason rather than failing the whole command.

## Where results go

All persistent output lives in an external **data root**, never inside the installed
skill code and never inside the analyzed project:

- `$PROJECT_ANALYSIS_HOME` (explicit override), else
- macOS: `~/Library/Application Support/project-analysis`
- Linux/WSL2: `${XDG_DATA_HOME:-~/.local/share}/project-analysis`

Under the data root, each run always writes Markdown reports. Start with:

- **`overview.md`** — the PM-primary report. Start here.
- **`technical-overview.md`** — the same run's full-detail companion.
- **`project-map.md`** — the reusable topology (module boundaries, dependency edges).

The offline HTML export is **opt-in**, not automatic: request it up front
(`--export html`) or run `<wrapper-executable> export --run <run-dir>` afterward at any
time. When produced, it lands under `<data-root>/exported/<project>-analysis/<run-id>/html/`
— open `index.html` in a browser.

## Upgrade

- **git-clone channel:** `git pull` inside the checkout, then re-run bootstrap if
  `wrapper/pyproject.toml` changed:
  `cd wrapper && python3 -m analysis_wrapper.bootstrap && cd ..`
- **`npx skills` channel:** run the CLI's own `update` verb, e.g.
  `npx skills update project-analysis -g` (add `-y` to skip prompts). If your installed
  CLI version doesn't support `update` for your source, re-running the original `add`
  command re-installs the latest revision.
- **Claude Code plugin channel:** there is no per-plugin `/plugin update` verb.
  `/plugin marketplace update project-analysis-marketplace` refreshes the marketplace
  catalog; Claude Code's own background auto-update then updates the installed plugin
  (toggle auto-update for this marketplace from `/plugin` → **Marketplaces**; it is off
  by default for local/third-party marketplaces). To force an immediate update instead
  of waiting, uninstall and reinstall:
  `/plugin uninstall project-analysis@project-analysis-marketplace` then
  `/plugin install project-analysis@project-analysis-marketplace`. Either way, finish
  with `/reload-plugins`.

In every channel, **your data root is untouched by upgrading the skill code** — runs,
state, and exports under the data root survive.

## Uninstall / data cleanup

Removing the skill only removes the **code**; the **data root persists** — deliberately,
so you don't lose run history by upgrading or reinstalling. If you actually want to
delete your analysis history, do so explicitly:

- **git-clone channel:** delete the checkout and its symlink(s), e.g.
  `rm "$HOME/.claude/skills/project-analysis"` (repeat for each client you registered),
  then remove the cloned directory itself.
- **`npx skills` channel:** `npx skills remove project-analysis -g` (or without `-g` for
  a project-local install; add `--agent <agent>` to target one agent, `-y` to skip
  prompts).
- **Claude Code plugin channel:** `/plugin uninstall project-analysis@project-analysis-marketplace`
  (or remove the whole marketplace with
  `/plugin marketplace remove project-analysis-marketplace`, which also uninstalls any
  plugins installed from it).

**None of the above deletes the data root.** To remove it as well:

```bash
# macOS
rm -rf "${PROJECT_ANALYSIS_HOME:-$HOME/Library/Application Support/project-analysis}"

# Linux / WSL2
rm -rf "${PROJECT_ANALYSIS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/project-analysis}"
```

Double-check `$PROJECT_ANALYSIS_HOME` isn't set to something unexpected before running
this — it deletes every run, all state, and all exports.

## Design

- OSS tools produce repository-wide numbers; the model produces judgment.
- Provenance-anchored citations (`repo@commit:path:line`); per-signal manifests.
- Immutable runs; explicit accepted-run pointers; project-scoped persistent state.
- No schemas, no gates, no checker scripts in v1. The tool wrapper invokes
  allowlisted tools, applies safe flags, redacts, bounds output, and records
  manifests — it never interprets findings or scores reports.
- Zero target-project literals in tracked files (the analyzer is general-purpose).

## Privacy & packaging

Per-target analysis output is never committed and never lives inside this checkout: runs
write to the external data root described above (`$PROJECT_ANALYSIS_HOME` and its
platform defaults), not to any `output/`/`exported/` directory inside the skill code. The
tracked tree is **target-neutral** — `SKILL.md`, the lens definitions, the templates, the
tool wrapper, and the generic `tools/README.md`.

Per-target **acceptance evidence** (spike bake-offs, benchmark checklists, and validation
runs against real repositories — which contain real author names, internal architecture,
and vulnerability details) is kept in a **private acceptance store outside this repository**,
reachable for our own reproducibility but never shipped. Git history is likewise clean of
that evidence: it was removed with `git filter-repo`. (Commit messages and tags may still
reference a target project by name — that was an explicit scope choice; the requirement is
that no target's evidence *content* is tracked or retrievable from history.)

Third-party components this project installs, vendors, or invokes (and their licenses)
are listed in [`NOTICE.md`](NOTICE.md).

## Environment and coverage

Project Analysis reports missing tools as reduced coverage; it never changes a
developer's language runtime or global toolchain. Install only the lanes you need, using
your preferred manager (`nvm`, `asdf`, `mise`, Homebrew, system packages, and so on):

- Python 3.11+: wrapper, PyDriller history analysis, SQL parsing, and HTML rendering.
- Git: history, ownership/co-change evidence, and reproducible revision citations;
  non-git folders remain supported with disclosed reduced coverage.
- `scc`: repository-wide size and language inventory.
- `lizard`: complexity metrics.
- `jscpd`: within-repository and same-language cross-repository duplication.
- `ast-grep`: structural route, integration, table, and access-model discovery.
- Node + pnpm + analyzer-owned dependency-cruiser/TypeScript: JS/TS dependency and call
  graphs. Node may be supplied by nvm, asdf, mise, or another developer-selected manager.
- Go + `staticcheck` + callgraph: Go dependency, quality, and call-graph lanes. These are
  unnecessary for non-Go targets.
- `osv-scanner`: optional vulnerability evidence; the network lane remains disabled
  unless the user explicitly authorizes it for the analysis run.

The project intentionally does not prescribe how Node, Go, or standalone binaries are
installed. Validated tool versions and invocation details are listed in
[`tools/README.md`](tools/README.md) and the machine-readable
[`tools/manifest.json`](tools/manifest.json) (validated against
[`tools/manifest.schema.json`](tools/manifest.schema.json)); actual versions and
resulting coverage are recorded in every run's manifests.

### Automated, consent-gated setup: JS/TS and Go lanes

`<wrapper-executable> setup` provisions the analyzer-managed pieces of both lanes:
dependency-cruiser + TypeScript for JS/TS, and the pinned `callgraph` binary for Go.
It reuses `doctor`'s own target sniff, so a pure-JS target's plan never includes the Go
lane (and vice versa), and it never installs Node, pnpm, or Go themselves — those stay
developer-managed. Preview first, then run for real:

```bash
<wrapper-executable> setup --workspace /path/to/target --plan   # shows the plan only
<wrapper-executable> setup --workspace /path/to/target          # asks, then installs
<wrapper-executable> setup --workspace /path/to/target --yes    # prior authorization
                                                                  # (the plan is still
                                                                  # printed first)
```

Each lane names the network host its install step contacts (PyPI, `registry.npmjs.org`,
`proxy.golang.org`) and the exact destination under the data root; nothing is fetched or
written until you consent (or pass `--yes`). If a lane's own developer-managed runtime
(Node/pnpm for JS/TS, Go for the Go lane) is missing, `setup` reports and skips just
that lane rather than failing the whole command. Re-running `setup` is safe and is the
upgrade path — it reconciles drifted versions and never touches an already up-to-date
tool.

`setup` automates exactly the manual procedure below — use the manual commands only if
you prefer not to run `setup` (e.g. air-gapped provisioning from a script you audit
yourself):

```bash
# JS/TS lane: copy the tracked manifests into the data-root runtime location,
# then install there (pnpm resolves --modules-dir relative to --dir).
mkdir -p "<data-root>/runtime/1/node_tools"
cp wrapper/node_tools/package.json wrapper/node_tools/pnpm-lock.yaml \
   "<data-root>/runtime/1/node_tools/"
pnpm install --dir "<data-root>/runtime/1/node_tools" --frozen-lockfile --ignore-scripts

# Go lane: install the pinned callgraph binary into an analyzer-owned GOBIN.
GOBIN="<data-root>/runtime/1/go_tools/bin" go install golang.org/x/tools/cmd/callgraph@v0.48.0
```

(`<data-root>/runtime/<contract>/...` — `bin/project-analysis`/bootstrap's own output
reports the current `<contract>` value, `1` at the time of writing.) This installs
dependency-cruiser 18.1.0 + TypeScript 5.9.3 exactly as pinned. A legacy install
directly into `wrapper/node_tools/node_modules` or `wrapper/go_tools/bin`
(pre-relocation) is still honored as a fallback if the runtime location above is empty,
but new installs (automated or manual) should target the runtime location above. See
`wrapper/README.md` for more detail.

### Running tests

Developers working on the wrapper add `--dev` to bootstrap first
(`python3 -m analysis_wrapper.bootstrap --dev`, from `wrapper/`) — `pytest` ships in the
`dev` extra and is not installed by a plain bootstrap. Then run tests with the
bootstrapped interpreter, e.g. `<venv>/bin/python -m pytest` where `<venv>` is the path
`bootstrap` printed (or use `bin/project-analysis`'s own venv resolution — see
"Dev channel" above).
