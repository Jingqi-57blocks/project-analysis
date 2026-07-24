"""B1 regression — the layering stages must never write THROUGH a planted
symlink into a read-only analyzed repo.

A dangling symlink is invisible to a bare ``.exists()`` (False) yet ``write_text``
FOLLOWS it, so a planted ``imports/<file> -> <path inside a target repo>`` would
create a file inside an analyzed repo. Both guard layers are exercised: the
executor's ``use_existing_run_directory`` (stage subdir + marker) and the emit
stages' ``create_stage_dir`` + O_EXCL writes.
"""

from pathlib import Path

import pytest

from analysis_wrapper import identity
from analysis_wrapper.cli import main
from analysis_wrapper.executor import WrapperSafetyError
from analysis_wrapper.targetspec import (GitProvenance, RepoTarget, TargetSpec,
                                         TechnologyFacet, stable_repo_id)


def _go_target(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/app\n")
    return repo, RepoTarget(repo_id="app-1", path=str(repo), facets=[
        TechnologyFacet("language.go", "language", ["."], ["go.mod"])
    ],
                            git=GitProvenance(head="e" * 40))


def _identities(tmp_path, target):
    return identity.build(
        TargetSpec([target]), workspace_root=tmp_path,
        project_id=stable_repo_id(str(tmp_path)))


def test_cli_refuses_dangling_symlink_marker_into_target(tmp_path, target):
    # Discovery-made run dir; imports/ pre-exists with a PLANTED dangling marker
    # symlink pointing at a nonexistent file INSIDE the analyzed repo.
    run = tmp_path / "run"
    (run / "imports").mkdir(parents=True)
    TargetSpec([target], produced_by="t").save(run / "targets.json")
    planted = Path(target.path) / "PWNED.json"
    (run / "imports" / "depmap-coverage.json").symlink_to(planted)

    rc = main(["--targets", str(run / "targets.json"), "--out", str(run),
               "dependency-map"])
    assert rc == 4                                    # WrapperSafetyError -> exit 4
    assert not planted.exists()                       # nothing written into the target


def test_cli_refuses_symlinked_stage_subdir_into_target(tmp_path, target):
    run = tmp_path / "run"
    run.mkdir()
    TargetSpec([target], produced_by="t").save(run / "targets.json")
    evil = Path(target.path) / "evil"
    evil.mkdir()
    (run / "imports").symlink_to(evil)                # imports/ redirects into target

    rc = main(["--targets", str(run / "targets.json"), "--out", str(run),
               "dependency-map"])
    assert rc == 4
    assert list(evil.iterdir()) == []                 # target dir untouched


def test_depmap_go_provider_refuses_dangling_symlink_map_file(tmp_path, monkeypatch):
    """Provider-path successor to the retired ``run_depmap`` (57B-85): past
    the executor guard, a dangling symlink planted at the PER-REPO map path
    must still be refused by ``DepmapGoProvider``'s own artifact write
    (``_write_json`` -> ``replace_artifact_text``) — a ``WrapperSafetyError``
    (an explicit symlink check) rather than ``run_depmap``'s old
    ``FileExistsError`` (bare O_EXCL semantics); same guard property, a
    different write primitive. ``go_lane.analyze`` is stubbed so this test
    exercises the write guard, not a real Go toolchain — the same isolation
    the retired ``run=`` stub gave the legacy test."""
    from analysis_wrapper.depmap import go_lane as dm_go_lane
    from analysis_wrapper.depmap.contract import RepoDepCoverage
    from analysis_wrapper.profiles.contracts import RunContext
    from analysis_wrapper.profiles.providers import DepmapGoProvider
    from analysis_wrapper.profiles.tool_access import ExecutorToolAccess

    repo, tgt = _go_target(tmp_path)
    run = tmp_path / "run"
    (run / "imports").mkdir(parents=True)
    identities = _identities(tmp_path, tgt)
    artifact_key = identities.artifact_key_for(tgt.repo_id)
    planted = repo / "PWNED.json"
    (run / "imports" / f"{artifact_key}.golist.json").symlink_to(planted)

    monkeypatch.setattr(dm_go_lane, "analyze", lambda *a, **k: (
        {"packages": []},
        RepoDepCoverage(repository_ref="app", lane="go", status="complete",
                        tool="go-list")))

    spec = TargetSpec([tgt])
    access = ExecutorToolAccess(spec, identities, run, "2026-07-18")
    context = RunContext(targets=spec, output_dir=run, scan_date="2026-07-18",
                         network_authorized=False, provenance={},
                         tool_access=access, identities=identities)

    with pytest.raises(WrapperSafetyError):
        DepmapGoProvider().run(context, tgt)
    assert not planted.exists()


def test_callgraph_go_provider_refuses_symlinked_stage_dir(tmp_path, monkeypatch):
    """Provider-path successor to the retired ``run_callgraph`` (57B-85):
    the callgraph stage dir's own ``create_stage_dir`` guard must still
    refuse a symlinked ``callgraph/`` directory. ``go_lane.analyze`` is
    stubbed for the same reason as the depmap test above."""
    from analysis_wrapper.callgraph import go_lane as cg_go_lane
    from analysis_wrapper.callgraph.contract import RepoCoverage
    from analysis_wrapper.profiles.contracts import RunContext
    from analysis_wrapper.profiles.providers import CallgraphGoProvider
    from analysis_wrapper.profiles.tool_access import ExecutorToolAccess

    repo, tgt = _go_target(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    evil = repo / "evil"
    evil.mkdir()
    (run / "callgraph").symlink_to(evil)
    identities = _identities(tmp_path, tgt)

    monkeypatch.setattr(cg_go_lane, "analyze", lambda *a, **k: (
        [], RepoCoverage(repository_ref="app", lang="go", status="complete",
                         tool="callgraph")))

    spec = TargetSpec([tgt])
    access = ExecutorToolAccess(spec, identities, run, "2026-07-18")
    context = RunContext(targets=spec, output_dir=run, scan_date="2026-07-18",
                         network_authorized=False, provenance={},
                         tool_access=access, identities=identities)

    with pytest.raises(WrapperSafetyError):
        CallgraphGoProvider().run(context, tgt)
    assert list(evil.iterdir()) == []
