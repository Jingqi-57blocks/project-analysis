# Release checklist

A short, mechanical pre-publish gate. Run through every item before making any checkout,
tag, or plugin/marketplace entry public. This checklist only verifies readiness — it does
not publish anything; publishing is a separate, explicit step owned by the release owner.

- [ ] **LICENSE present and correct.**
      How to verify: `test -f LICENSE && head -1 LICENSE` — confirms the MIT license text
      and current copyright year/holder.

- [ ] **NOTICE.md complete.**
      How to verify: diff the components listed in `NOTICE.md` against
      `wrapper/pyproject.toml` optional-dependencies, `wrapper/node_tools/package.json`,
      `wrapper/analysis_wrapper/report_html/vendor/VENDOR.txt`, and `tools/manifest.json`
      — every analyzer-managed or vendored entry in those sources must have a matching
      row; no license should read as guessed (mark unverifiable ones "to verify").

- [ ] **Supported-platforms statement is accurate.**
      How to verify: `README.md` states macOS + Linux + WSL2 supported, native Windows
      not supported in v1 — matches `tools/manifest.json`'s `"platforms"` arrays (all
      list `["macos", "linux", "wsl2"]`, none list a Windows-native platform).

- [ ] **`VERSION` set and matching the plugin manifest.**
      How to verify:
      `diff <(cat VERSION) <(python3 -c "import json;print(json.load(open('.claude-plugin/plugin.json'))['version'])")`
      — must be identical, and both must match the `version` field for the
      `project-analysis` entry inside `.claude-plugin/marketplace.json`.

- [ ] **`tools/manifest.json` validates against its schema.**
      How to verify: run the wrapper's own manifest validation (e.g.
      `<venv>/bin/python -m pytest wrapper/tests -k manifest`, where `<venv>` is the
      path `python3 -m analysis_wrapper.bootstrap --dev` printed (data-root
      `runtime/<contract>/venv`, NOT `wrapper/.venv` — that path is stale), or
      `python3 -c "import json, jsonschema; jsonschema.validate(json.load(open('tools/manifest.json')), json.load(open('tools/manifest.schema.json')))"`
      if `jsonschema` is installed) — must pass with no errors.

- [ ] **Tests green.**
      How to verify: bootstrap with `--dev` first — `cd wrapper && python3 -m
      analysis_wrapper.bootstrap --dev` — since `pytest` ships only in the `dev` extra
      and a plain bootstrap does not install it. Then run `<venv>/bin/python -m pytest`
      from the skill root (`<venv>` is the path bootstrap printed) and confirm it exits 0.

- [ ] **Docs state v1 scope plainly: overview + diagnosis only, no drill-down.**
      How to verify: grep `README.md` and `SKILL.md` for "drill-down" / "module" —
      confirm every mention either says v1 does not support it, or documents the
      overview+diagnosis-only invocation; no example implies drill-down is available now.

- [ ] **No analyzed-project literals in tracked files.**
      How to verify: `git grep -Iil` for the acceptance-target project names/paths used
      during development, across tracked files only (not `output/`, `state/`, `exported/`,
      which are gitignored) — zero hits. Re-confirm `.gitignore` still excludes
      `output/`, `state/`, and `exported/`.

- [ ] **Data-root and uninstall behavior documented.**
      How to verify: `README.md` states the `$PROJECT_ANALYSIS_HOME` resolution order,
      the macOS and Linux/WSL2 default paths, that data persists after the skill code is
      removed, and the exact command to delete it if desired.

- [ ] **Install and upgrade commands verified against the real CLI.**
      How to verify: every `npx skills …` command shown in `README.md` matches a verb
      documented in the `vercel-labs/skills` repository README at the time of release
      (`add`, `list`/`ls`, `update`, `remove`/`rm`, `-a/--agent`, `-g/--global`,
      `-s/--skill`, `-y/--yes`); re-check against
      https://github.com/vercel-labs/skills if the CLI version in use has changed since
      this checklist was last run.

- [ ] **Smoke-test install on Claude Code.**
      How to verify: from a clean environment, install via the documented `npx skills
      add` command targeting `-a claude-code` (or via `git clone` + symlink), start a new
      session, and confirm `/project-analysis` is discoverable and its first run shows a
      setup plan before installing anything.

- [ ] **Smoke-test install on Codex.**
      How to verify: same as above targeting `-a codex` (or the Codex symlink path from
      `README.md`), and confirm `$project-analysis` is discoverable and behaves the same
      way on first run.

- [ ] **Claude Code plugin path (optional convenience) is internally consistent.**
      How to verify:
      `python3 -c "import json;[json.load(open(p)) for p in ['.claude-plugin/plugin.json','.claude-plugin/marketplace.json']];print('ok')"`
      and confirm the `version` in both files matches `VERSION`; confirm `README.md`
      documents this path as secondary to `npx skills add`. This JSON-parses check
      cannot catch a source-resolution failure — see the smoke test below for that.

- [ ] **Plugin/marketplace channel end-to-end smoke test.**
      How to verify: from a local checkout of this repository, run
      `/plugin marketplace add <path-to-local-checkout>`, then
      `/plugin install project-analysis@project-analysis-marketplace`, then
      `/reload-plugins`, and confirm the skill actually appears (discoverable and
      invocable) — not just that the JSON parses. This specifically catches a
      `marketplace.json` `source` value that parses fine but fails to resolve (e.g. a
      relative path not starting with `./`).

- [ ] **v1 drill-down gating is implemented and verified in code (expected to FAIL
      until that phase lands).**
      How to verify: confirm the wrapper/CLI actually refuses or rejects a
      drill-down invocation at runtime (not just that the docs say v1 doesn't support
      it) — e.g. attempt a drill-down command against a completed overview run and
      confirm it is rejected with a clear message, or point to the specific code path
      that enforces the v1 gate. A docs-only grep (see the "Docs state v1 scope
      plainly" item above) is NOT sufficient evidence for this item. This item is
      expected to fail today: the code-level gate is a later phase, not yet
      implemented in this checkout.
