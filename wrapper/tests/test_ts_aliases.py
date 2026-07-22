"""TS alias resolver → analyzer-owned depcruise config generation (item 2)."""

import json
import subprocess

from analysis_wrapper.resolvers import ts_aliases
from analysis_wrapper.targetspec import RepoTarget, TechnologyFacet


def _target(tmp_path):
    repo = tmp_path / "widget-ui"
    repo.mkdir()
    (repo / "tsconfig.app.json").write_text("{}")
    return RepoTarget(repo_id="widget-ui", path=str(repo), facets=[
        TechnologyFacet("language.typescript", "language", ["."], ["tsconfig.app.json"])
    ]), repo


def test_resolver_generates_analysis_tsconfig_and_depcruise_config(tmp_path):
    target, repo = _target(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    helper_out = json.dumps({
        "aliases": {"src": str(repo / "src"), "gadget$": str(repo / "lib" / "gadget")},
        "baseUrl": str(repo), "paths": {"src/*": ["./src/*"]},
        "references": [], "unresolved": ["vite alias 'dyn' -> non-literal (dynamic)"],
        "sources": ["tsconfig.app.json"], "typescriptVersion": "5.9.3",
    })

    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=helper_out, stderr="")

    res = ts_aliases.resolve_and_generate(
        target, out, artifact_key="widget-ui",
        tsconfig="tsconfig.app.json", exclude_re="^(dist)",
        run=run, node="/usr/bin/node")

    assert res.config_path and res.config_path.exists()
    analysis_ts = json.loads((out / "tsconfig-analysis-widget-ui.json").read_text())
    assert analysis_ts["extends"].endswith("tsconfig.app.json")
    assert analysis_ts["compilerOptions"]["baseUrl"] == str(repo)
    paths = analysis_ts["compilerOptions"]["paths"]
    assert paths["src/*"] == ["src/*"]          # prefix alias, relative to baseUrl
    assert paths["gadget"] == ["lib/gadget"]    # exact alias ($ stripped)
    cfg = json.loads(res.config_path.read_text())
    assert cfg["options"]["tsConfig"]["fileName"].endswith("tsconfig-analysis-widget-ui.json")
    assert "alias" not in cfg["options"]["enhancedResolveOptions"]  # depcruise rejects it
    assert "2 alias(es) resolved, 1 unresolved" in res.notes
    assert res.reads == ["tsconfig.app.json"]


def test_resolver_error_falls_back_with_disclosure(tmp_path):
    target, _repo = _target(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    res = ts_aliases.resolve_and_generate(
        target, out, artifact_key="widget-ui",
        tsconfig="tsconfig.app.json", exclude_re="^(dist)",
        run=run, node="/usr/bin/node")
    assert res.config_path is None
    assert "without an alias config" in res.notes
