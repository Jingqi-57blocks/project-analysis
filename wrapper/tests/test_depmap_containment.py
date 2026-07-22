"""B1 regression — the layering stages must never write THROUGH a planted
symlink into a read-only analyzed repo.

A dangling symlink is invisible to a bare ``.exists()`` (False) yet ``write_text``
FOLLOWS it, so a planted ``imports/<file> -> <path inside a target repo>`` would
create a file inside an analyzed repo. Both guard layers are exercised: the
executor's ``use_existing_run_directory`` (stage subdir + marker) and the emit
stages' ``create_stage_dir`` + O_EXCL writes.
"""

import json
from pathlib import Path

import pytest

from analysis_wrapper import identity
from analysis_wrapper.callgraph import emit as cg_emit
from analysis_wrapper.cli import main
from analysis_wrapper.depmap import emit as dm_emit
from analysis_wrapper.executor import WrapperSafetyError
from analysis_wrapper.targetspec import (GitProvenance, RepoTarget, TargetSpec,
                                         stable_repo_id)

_MODULE = "example.com/app"
_STREAM = "\n".join(json.dumps(o) for o in [
    {"ImportPath": _MODULE, "Dir": "/x", "Imports": ["example.com/app/internal/s"]},
    {"ImportPath": "example.com/app/internal/s", "Dir": "/x/s", "Imports": []},
])


def _fake_go_run(argv, **kwargs):
    class _Proc:
        returncode = 0
        stdout = _STREAM
        stderr = ""
    return _Proc()


def _go_target(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "go.mod").write_text(f"module {_MODULE}\n")
    return repo, RepoTarget(repo_id="app-1", path=str(repo), stacks=["go"],
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


def test_run_depmap_refuses_dangling_symlink_map_file(tmp_path):
    # Past the executor guard: a dangling symlink planted at a PER-REPO map path.
    # The O_EXCL write must refuse it rather than follow it into the target.
    repo, tgt = _go_target(tmp_path)
    run = tmp_path / "run"
    (run / "imports").mkdir(parents=True)
    planted = repo / "PWNED.json"
    (run / "imports" / "app.golist.json").symlink_to(planted)

    with pytest.raises(OSError):                       # FileExistsError (EEXIST)
        dm_emit.run_depmap(
            TargetSpec(repos=[tgt]), run, "2026-07-18",
            identities=_identities(tmp_path, tgt), run=_fake_go_run)
    assert not planted.exists()


def test_run_callgraph_refuses_symlinked_stage_dir(tmp_path):
    # Parity: the callgraph stage (newly on the layering path) uses the same guard.
    repo, tgt = _go_target(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    evil = repo / "evil"
    evil.mkdir()
    (run / "callgraph").symlink_to(evil)

    with pytest.raises(WrapperSafetyError):
        cg_emit.run_callgraph(
            TargetSpec(repos=[tgt]), run, "2026-07-18",
            identities=_identities(tmp_path, tgt), run=_fake_go_run)
    assert list(evil.iterdir()) == []
