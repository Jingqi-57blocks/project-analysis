"""57B-34: discovery excludes the analyzer's own checkout by canonical path.

Fixtures are domain-neutral tmp_path repos. Identity is always by resolved
filesystem path — never repository name — so the basename-collision case is the
load-bearing proof that we do not exclude by name.
"""

import subprocess
from pathlib import Path

import pytest

from analysis_wrapper.cli import main
from analysis_wrapper.discovery import emit, self_exclusion


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    return path


def _repo_names(spec) -> set[str]:
    return {Path(r.path).name for r in spec.repos}


# --- unit: classify + default root -----------------------------------------

def test_default_analyzer_root_is_the_repo_root():
    root = self_exclusion.default_analyzer_root()
    # The package lives at <root>/wrapper/analysis_wrapper; the root owns the
    # wrapper package and the skill manifest.
    assert (root / "wrapper" / "analysis_wrapper").is_dir()
    assert (root / "SKILL.md").is_file()


def test_default_analyzer_root_fails_closed_on_foreign_layout(tmp_path, monkeypatch):
    """A layout parents[2] cannot vouch for (e.g. a non-editable install landing
    in site-packages) must raise, never silently admit the analyzer."""
    import analysis_wrapper

    fake_pkg = tmp_path / "site-packages" / "analysis_wrapper"
    fake_pkg.mkdir(parents=True)
    monkeypatch.setattr(analysis_wrapper, "__file__", str(fake_pkg / "__init__.py"))
    with pytest.raises(self_exclusion.AnalyzerBoundaryConflict) as exc:
        self_exclusion.default_analyzer_root()
    assert "--analyzer-root" in str(exc.value)


def test_classify_self_conflict_and_admit(tmp_path):
    analyzer = (tmp_path / "analyzer").resolve()
    analyzer.mkdir()
    assert self_exclusion.classify(analyzer, analyzer) == self_exclusion.SELF
    # analyzer strictly inside -> the enclosing dir is a boundary conflict.
    assert self_exclusion.classify(tmp_path, analyzer) == self_exclusion.CONFLICT
    # unrelated sibling -> admit.
    sibling = tmp_path / "other"
    sibling.mkdir()
    assert self_exclusion.classify(sibling, analyzer) == self_exclusion.ADMIT


# --- acceptance: discover() integration ------------------------------------

def test_standalone_analyzer_inside_workspace_excluded(tmp_path):
    ws = tmp_path / "ws"
    analyzer = _init_repo(ws / "project-analysis")
    _init_repo(ws / "service")
    spec, report = emit.discover(ws, analyzer_root=analyzer)
    assert _repo_names(spec) == {"service"}
    assert any(
        str(analyzer.resolve()) in line
        and self_exclusion.SELF_EXCLUSION_REASON in line
        for line in report["not_targeted"]
    ), report["not_targeted"]


def test_self_exclusion_independent_of_operator_exclude(tmp_path):
    """Self-exclusion holds without --exclude, and coexists with it (#5)."""
    ws = tmp_path / "ws"
    analyzer = _init_repo(ws / "project-analysis")
    _init_repo(ws / "keep")
    _init_repo(ws / "drop")
    spec, report = emit.discover(ws, exclude_names=["drop"], analyzer_root=analyzer)
    assert _repo_names(spec) == {"keep"}
    assert any(self_exclusion.SELF_EXCLUSION_REASON in x for x in report["not_targeted"])
    assert any("excluded by operator flag" in x for x in report["not_targeted"])


def test_analyzer_outside_workspace_leaves_discovery_unchanged(tmp_path):
    ws = tmp_path / "ws"
    _init_repo(ws / "service")
    _init_repo(ws / "web")
    outside = _init_repo(tmp_path / "elsewhere" / "project-analysis")
    with_outside, report = emit.discover(ws, analyzer_root=outside)
    # Baseline uses the real package root, which is also outside this tmp ws.
    baseline, _ = emit.discover(ws)
    assert _repo_names(with_outside) == _repo_names(baseline) == {"service", "web"}
    assert not any(
        self_exclusion.SELF_EXCLUSION_REASON in x for x in report["not_targeted"]
    )


def test_symlink_identity_still_excluded(tmp_path):
    """Workspace reaches the analyzer via a different spelling -> still excluded."""
    ws = tmp_path / "ws"
    analyzer = _init_repo(ws / "project-analysis")
    _init_repo(ws / "service")
    link = tmp_path / "analyzer-link"
    link.symlink_to(analyzer, target_is_directory=True)
    spec, report = emit.discover(ws, analyzer_root=link)
    assert _repo_names(spec) == {"service"}
    assert any(self_exclusion.SELF_EXCLUSION_REASON in x for x in report["not_targeted"])


def test_basename_collision_unrelated_repo_included(tmp_path):
    """A different repo that merely shares the basename must be analyzed."""
    ws = tmp_path / "ws"
    analyzer = _init_repo(ws / "a" / "project-analysis")
    _init_repo(ws / "b" / "project-analysis")  # same name, different path
    spec, _ = emit.discover(ws, analyzer_root=analyzer)
    # Both share basename; only the real analyzer (by path) is dropped.
    assert _repo_names(spec) == {"project-analysis"}
    assert len(spec.repos) == 1
    assert Path(spec.repos[0].path).resolve() != analyzer.resolve()


def test_embedded_boundary_conflict_fails_closed(tmp_path):
    ws = tmp_path / "ws"
    service = _init_repo(ws / "service")
    embedded_analyzer = service / "tools" / "project-analysis"
    embedded_analyzer.mkdir(parents=True)
    with pytest.raises(self_exclusion.AnalyzerBoundaryConflict) as exc:
        emit.discover(ws, analyzer_root=embedded_analyzer)
    message = str(exc.value)
    assert "boundary conflict" in message
    assert str(service.resolve()) in message


def test_repo_nested_inside_analyzer_disclosed_as_not_scanned(tmp_path):
    """A repo inside the analyzer checkout is never admitted, and its disclosure
    must not promise it is 'scanned as part of the enclosing repo' — the
    enclosing repo is the self-excluded analyzer and is not scanned at all."""
    ws = tmp_path / "ws"
    analyzer = _init_repo(ws / "project-analysis")
    _init_repo(analyzer / "fixtures" / "mini-repo")
    _init_repo(ws / "service")
    spec, report = emit.discover(ws, analyzer_root=analyzer)
    assert _repo_names(spec) == {"service"}
    nested_lines = [x for x in report["not_targeted"] if "mini-repo" in x]
    assert nested_lines and all("not scanned" in x for x in nested_lines)
    assert not any("scanned as part of" in x for x in nested_lines)


# --- CLI wiring ------------------------------------------------------------

def test_cli_discover_self_excludes_via_analyzer_root(tmp_path, capsys):
    ws = tmp_path / "ws"
    analyzer = _init_repo(ws / "project-analysis")
    _init_repo(ws / "service")
    run_dir = tmp_path / "run"
    code = main(["--out", str(run_dir), "discover", "--workspace", str(ws),
                 "--analyzer-root", str(analyzer)])
    assert code == 0
    out = capsys.readouterr().out
    assert "1 target repo(s)" in out
    assert self_exclusion.SELF_EXCLUSION_REASON in out


def test_cli_discover_boundary_conflict_is_input_error(tmp_path, capsys):
    ws = tmp_path / "ws"
    service = _init_repo(ws / "service")
    embedded = service / "tools" / "project-analysis"
    embedded.mkdir(parents=True)
    code = main(["--out", str(tmp_path / "run"), "discover", "--workspace",
                 str(ws), "--analyzer-root", str(embedded)])
    assert code == 2
    assert "boundary conflict" in capsys.readouterr().err
