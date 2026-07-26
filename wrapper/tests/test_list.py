"""57B-109: `list` — read-only run inventory."""

import json
import os
from pathlib import Path

import pytest

from analysis_wrapper import list_runs, paths
from analysis_wrapper.cli import main
from analysis_wrapper.lifecycle import RunState


def _new_run(tmp_path, target, capsys, *, label=""):
    args = ["new-run", "--workspace", str(Path(target.path).parent)]
    if label:
        args += ["--run-id", label]
    assert main(args) == 0
    run_dir = Path(capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1])
    return run_dir


def _mark_all(run_dir, capsys):
    for stage in ("signals", "findings", "map", "overview"):
        assert main(["mark-stage", "--run", str(run_dir), "--stage", stage]) == 0
    capsys.readouterr()


def _snapshot(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(p) for p in root.rglob("*")}


# --------------------------------------------------------------------------
# Empty data root
# --------------------------------------------------------------------------

def test_empty_data_root_friendly_message_no_creation(capsys):
    data_root = paths.resolved_data_root()
    # The pytest-side isolation fixture allocates the throwaway HOME directory
    # itself (tmp_path_factory.mktemp), so the bare directory legitimately
    # exists already; what `list` must never do is create its OWN
    # output/state subtrees inside it just from being asked to report.
    assert not (data_root / "output").exists()
    assert not (data_root / "state").exists()
    code = list_runs.run(None, as_json=False)
    out = capsys.readouterr().out
    assert code == 0
    assert "no runs yet" in out
    assert str(data_root) in out
    assert not (data_root / "output").exists()
    assert not (data_root / "state").exists()


def test_empty_data_root_json(capsys):
    data_root = paths.resolved_data_root()
    code = list_runs.run(None, as_json=True)
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out)
    assert report["projects"] == []
    assert not (data_root / "output").exists()
    assert not (data_root / "state").exists()


def test_cli_list_subcommand_empty(capsys):
    code = main(["list"])
    out = capsys.readouterr().out
    assert code == 0
    assert "no runs yet" in out


# --------------------------------------------------------------------------
# Populated data root
# --------------------------------------------------------------------------

def test_grouped_newest_first_with_statuses_and_pointers(tmp_path, target, capsys):
    complete_run = _new_run(tmp_path, target, capsys, label="first-run")
    _mark_all(complete_run, capsys)
    incomplete_run = _new_run(tmp_path, target, capsys, label="second-run")

    assert main(["accept", "--run", str(complete_run)]) == 0
    capsys.readouterr()

    report = list_runs.build_report()
    assert len(report["projects"]) == 1
    project = report["projects"][0]
    run_ids = [r["run_id"] for r in project["runs"]]
    # newest first
    assert run_ids[0] == incomplete_run.name
    assert run_ids[1] == complete_run.name

    by_id = {r["run_id"]: r for r in project["runs"]}
    assert by_id[complete_run.name]["status"] == "complete"
    assert by_id[complete_run.name]["is_current"] is True
    assert by_id[complete_run.name]["is_latest_completed"] is True
    assert by_id[incomplete_run.name]["status"] == "incomplete"
    assert by_id[incomplete_run.name]["resume_stage"] == "signals"
    assert by_id[incomplete_run.name]["is_current"] is False
    assert by_id[incomplete_run.name]["location"] == str(incomplete_run)


def test_inspection_only_run_reported(tmp_path, target, synthetic_repo, capsys):
    (synthetic_repo / "dirty.js").write_text("1;\n")  # uncommitted -> dirty
    run_dir = _new_run(tmp_path, target, capsys, label="dirty-run")
    _mark_all(run_dir, capsys)
    report = list_runs.build_report()
    run = next(r for p in report["projects"] for r in p["runs"]
               if r["run_id"] == run_dir.name)
    assert run["status"] == "inspection-only"


def test_two_projects_grouped_separately(tmp_path, capsys):
    import subprocess
    from analysis_wrapper import gitinfo
    from analysis_wrapper.targetspec import RepoTarget, TechnologyFacet, GitProvenance, stable_repo_id

    def _make(name):
        repo = tmp_path / name / "app"
        repo.mkdir(parents=True)
        (repo / "index.js").write_text("module.exports = 1;\n")
        subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-qm", "init"], check=True)
        return repo

    repo_a = _make("proj-a")
    repo_b = _make("proj-b")
    assert main(["new-run", "--workspace", str(repo_a.parent)]) == 0
    capsys.readouterr()
    assert main(["new-run", "--workspace", str(repo_b.parent)]) == 0
    capsys.readouterr()

    report = list_runs.build_report()
    assert len(report["projects"]) == 2
    keys = {p["project_key"] for p in report["projects"]}
    assert len(keys) == 2

    only_a = next(p for p in report["projects"] if "proj-a" in p["project_key"])
    filtered = list_runs.build_report(only_a["project_key"])
    assert len(filtered["projects"]) == 1
    assert filtered["projects"][0]["project_key"] == only_a["project_key"]


def test_project_filter_no_match_is_friendly_not_an_error(tmp_path, target, capsys):
    _new_run(tmp_path, target, capsys, label="only-run")
    code = list_runs.run("does-not-exist", as_json=False)
    out = capsys.readouterr().out
    assert code == 0
    assert "no runs found" in out


def test_json_shape(tmp_path, target, capsys):
    run_dir = _new_run(tmp_path, target, capsys, label="json-run")
    _mark_all(run_dir, capsys)
    code = main(["list", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out)
    assert report["schema_version"] == "1.0.0"
    assert "data_root" in report
    project = report["projects"][0]
    assert {"project_key", "project_name", "runs"} <= set(project)
    run = project["runs"][0]
    assert {
        "run_id", "kind", "date", "language", "status", "resume_stage",
        "is_current", "is_latest_completed", "location", "error",
    } <= set(run)
    assert run["kind"] == "overview"


# --------------------------------------------------------------------------
# Corrupt / partial run directories
# --------------------------------------------------------------------------

def test_partially_written_run_reported_unreadable_not_crashed(tmp_path, target, capsys):
    run_dir = _new_run(tmp_path, target, capsys, label="partial-run")
    (run_dir / RunState.FILENAME).unlink()  # simulate interruption before state.save()

    code = list_runs.run(None, as_json=False)
    out = capsys.readouterr().out
    assert code == 0
    assert "unreadable" in out
    report = list_runs.build_report()
    run = next(r for p in report["projects"] for r in p["runs"]
               if r["run_id"] == run_dir.name)
    assert run["status"] == "unreadable"
    assert run["error"]


def test_corrupt_run_state_json_reported_unreadable(tmp_path, target, capsys):
    run_dir = _new_run(tmp_path, target, capsys, label="corrupt-run")
    (run_dir / RunState.FILENAME).write_text("{not json", "utf-8")

    report = list_runs.build_report()
    run = next(r for p in report["projects"] for r in p["runs"]
               if r["run_id"] == run_dir.name)
    assert run["status"] == "unreadable"
    assert run["error"]

    # A corrupt run must not hide other, readable runs.
    other = _new_run(tmp_path, target, capsys, label="healthy-run")
    report = list_runs.build_report()
    run_ids = {r["run_id"] for p in report["projects"] for r in p["runs"]}
    assert {run_dir.name, other.name} <= run_ids


# --------------------------------------------------------------------------
# Strict read-only
# --------------------------------------------------------------------------

def test_unreadable_run_directory_permission_denied_does_not_hide_others(
        tmp_path, target, capsys):
    """Review fix (57B-109): `state_path.is_file()` can raise PermissionError
    (EACCES) -- Python 3.11 pathlib only swallows ENOENT-class stat()
    failures, not permission errors. Before the fix, a single `chmod 000`
    run directory blew past `_describe_run` entirely and crashed the WHOLE
    report (`list: internal failure`, zero runs shown, exit 1). One bad run
    directory must degrade only itself to `unreadable`; every sibling run
    must still list, and the command must still exit 0."""
    if os.name != "posix" or hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("permission bits are meaningless for root")

    good_run = _new_run(tmp_path, target, capsys, label="good-run")
    locked_run = _new_run(tmp_path, target, capsys, label="locked-run")
    locked_run.chmod(0o000)
    try:
        code = list_runs.run(None, as_json=False)
        out = capsys.readouterr().out
        assert code == 0
        assert "unreadable" in out
        assert good_run.name in out

        report = list_runs.build_report()
        by_id = {r["run_id"]: r for p in report["projects"] for r in p["runs"]}
        assert by_id[good_run.name]["status"] != "unreadable"
        assert by_id[locked_run.name]["status"] == "unreadable"
        assert by_id[locked_run.name]["error"]
    finally:
        locked_run.chmod(0o755)  # restore so pytest's tmp_path cleanup can remove it


def test_strictly_read_only(tmp_path, target, capsys):
    run_dir = _new_run(tmp_path, target, capsys, label="ro-run")
    _mark_all(run_dir, capsys)
    assert main(["accept", "--run", str(run_dir)]) == 0
    capsys.readouterr()

    data_root = paths.resolved_data_root()
    before = _snapshot(data_root)
    before_mtimes = {
        str(p): p.stat().st_mtime for p in data_root.rglob("*") if p.is_file()
    }

    list_runs.run(None, as_json=False)
    list_runs.run(None, as_json=True)
    capsys.readouterr()

    after = _snapshot(data_root)
    after_mtimes = {
        str(p): p.stat().st_mtime for p in data_root.rglob("*") if p.is_file()
    }
    assert after == before
    assert after_mtimes == before_mtimes
