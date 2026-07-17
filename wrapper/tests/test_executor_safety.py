"""Round-2 executor review fixes: allowlist, containment, bounded views,
fail-closed provenance, staleness, drift."""

import os
from pathlib import Path
import pytest

from analysis_wrapper import gitinfo
from analysis_wrapper.executor import prepare_output_directory, run_tool
from analysis_wrapper.status import Status
from analysis_wrapper.tooldefs import ToolDef

from test_executor import bash_tool, run  # reuse helpers

SCAN_DATE = "2026-07-16"


def test_allowlist_rejects_unapproved_argv0(target, tmp_path):
    td = ToolDef(name="sneaky", binary="bash",
                 argv_builder=lambda t: ["python3", "-c", "print(1)"],
                 version_argv=["bash", "--version"])
    r = run(td, target, tmp_path)
    assert r.status is Status.FAILED
    assert "not the approved binary" in r.reason


def test_allowlist_rejects_same_basename_different_executable(target, tmp_path):
    fake = tmp_path / "bin" / "bash"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)
    td = ToolDef(name="same-name", binary="bash",
                 argv_builder=lambda t: [str(fake)],
                 version_argv=["bash", "--version"])
    r = run(td, target, tmp_path)
    assert r.status is Status.FAILED and "not the approved binary" in r.reason


def test_guard_runs_before_version_probe(target, tmp_path):
    marker = tmp_path / "probed"
    fake = tmp_path / "probe-tool"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\necho 1.0\n")
    fake.chmod(0o755)
    td = ToolDef(name="guard-before-probe", binary=str(fake),
                 argv_builder=lambda t: [str(fake)],
                 guards=[lambda t: "unsafe target"])
    r = run(td, target, tmp_path)
    assert r.status is Status.SKIPPED
    assert not marker.exists(), "version probe must not run before target guards"


def test_raw_containment_dir_is_self_gitignoring(target, tmp_path):
    result = run(bash_tool("t1", "echo hi"), target, tmp_path)
    gi = tmp_path / "signals" / "raw" / ".gitignore"
    assert gi.read_text() == "*\n", "raw/ must always self-ignore"
    assert (gi.parent.stat().st_mode & 0o777) == 0o700
    assert (result.raw_path.stat().st_mode & 0o777) == 0o600
    assert (result.raw_path.with_suffix(".err").stat().st_mode & 0o777) == 0o600


def test_output_inside_target_is_refused_before_any_write(target, synthetic_repo):
    attempted = synthetic_repo / "output" / "signals"
    with pytest.raises(ValueError, match="inside target"):
        run_tool(bash_tool("reader", "echo hi"), target, attempted, SCAN_DATE)
    assert not attempted.exists()


def test_output_is_validated_against_all_targets_before_creation(target, tmp_path):
    from analysis_wrapper.targetspec import RepoTarget
    later = tmp_path / "later"
    later.mkdir()
    second = RepoTarget(repo_id="later", path=str(later))
    attempted = later / "signals"
    with pytest.raises(ValueError, match="inside target"):
        prepare_output_directory(attempted, [target, second])
    assert not attempted.exists()


def test_fresh_output_directory_is_required(target, tmp_path):
    out = tmp_path / "existing"
    out.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        prepare_output_directory(out, [target])


def test_missing_or_duplicate_targets_are_rejected_before_output(tmp_path, target):
    missing = type(target)(repo_id="missing", path=str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="not a directory"):
        prepare_output_directory(tmp_path / "out-missing", [missing])
    duplicate = type(target)(repo_id="duplicate", path=target.path)
    with pytest.raises(ValueError, match="same target"):
        prepare_output_directory(tmp_path / "out-duplicate", [target, duplicate])
    assert not (tmp_path / "out-missing").exists()
    assert not (tmp_path / "out-duplicate").exists()


def test_network_definition_is_not_probed_without_authorization(target, tmp_path):
    marker = tmp_path / "probed"
    fake = tmp_path / "net-tool"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\necho 1.0\n")
    fake.chmod(0o755)
    td = ToolDef(name="network-guard", binary=str(fake), network=True,
                 argv_builder=lambda t: [str(fake)])
    result = run(td, target, tmp_path)
    assert result.status is Status.SKIPPED
    assert "explicit authorization" in result.reason
    assert not marker.exists()


def test_binary_resolving_inside_target_is_refused(target, tmp_path, synthetic_repo):
    binary = synthetic_repo / "owned-tool"
    binary.write_text("#!/bin/sh\necho 1.0\n")
    binary.chmod(0o755)
    td = ToolDef(name="owned", binary=str(binary),
                 argv_builder=lambda t: [str(binary)])
    result = run(td, target, tmp_path)
    assert result.status is Status.FAILED
    assert "inside target" in result.reason


def test_target_controlled_path_entry_is_refused(monkeypatch, target, tmp_path, synthetic_repo):
    marker = tmp_path / "invoked"
    monkeypatch.setenv("PATH", os.environ["PATH"] + os.pathsep + str(synthetic_repo))
    result = run(bash_tool("unsafe-path", f"touch '{marker}'"), target, tmp_path)
    assert result.status is Status.FAILED
    assert "PATH contains target-controlled" in result.reason
    assert not marker.exists()


def test_bounded_view_produced_sanitized_and_listed(target, tmp_path):
    r = run(bash_tool("viewer", "echo 'token=abc123'; echo 'plain line'"),
            target, tmp_path)
    assert r.view_path is not None and r.view_path.exists()
    view = r.view_path.read_text()
    assert view.startswith("sample: ")            # retained/total header
    assert "abc123" not in view and "plain line" in view
    outs = r.manifest.output_files
    assert any("view.txt" in o for o in outs)
    assert any(o.endswith(".out") for o in outs) and any(o.endswith(".err") for o in outs)


def test_view_produced_even_for_failed_runs(target, tmp_path):
    r = run(bash_tool("crash2", "echo partial-output; exit 9"), target, tmp_path)
    assert r.status is Status.FAILED
    assert r.view_path is not None and "partial-output" in r.view_path.read_text()


def test_stale_targetspec_head_is_failed(target, tmp_path):
    target.git.head = "f" * 40  # recorded HEAD no longer matches live repo
    r = run(bash_tool("anytool", "echo hi"), target, tmp_path)
    assert r.status is Status.FAILED and "TargetSpec stale" in r.reason


def test_stale_targetspec_dirty_state_is_failed(target, tmp_path, synthetic_repo):
    (synthetic_repo / "late.txt").write_text("changed after discovery\n")
    r = run(bash_tool("anytool", "echo hi"), target, tmp_path)
    assert r.status is Status.FAILED and "dirty worktree state changed" in r.reason


def test_version_drift_recorded(target, tmp_path):
    td = bash_tool("drifty", "echo ok", validated_version="bash 999.9")
    r = run(td, target, tmp_path)
    assert r.manifest.version_drift.startswith("validated bash 999.9, found ")


def test_gitinfo_fails_closed_on_non_repo(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert gitinfo.porcelain_snapshot(plain) is None
    assert gitinfo.dirty_detail(plain) == "unknown (git status unavailable)"


def test_porcelain_markers_not_stripped(synthetic_repo):
    (synthetic_repo / "index.js").write_text("changed\n")  # unstaged modify
    snap = gitinfo.porcelain_snapshot(synthetic_repo)
    assert snap is not None and snap.startswith(" M "), f"marker corrupted: {snap!r}"
    (synthetic_repo / "index.js").write_text("module.exports = 1;\n")  # restore


def test_non_git_target_runs_in_reduced_mode(tmp_path):
    from analysis_wrapper.targetspec import RepoTarget, stable_repo_id
    plain = tmp_path / "plain-dir"
    plain.mkdir()
    (plain / "a.js").write_text("x\n")
    t = RepoTarget(repo_id=stable_repo_id(str(plain)), path=str(plain))
    r = run(bash_tool("lister", "ls"), t, tmp_path)
    assert r.status is Status.COMPLETE
    assert "immutability compare skipped" in r.manifest.notes
    assert r.manifest.repos[0].dirty_detail == "unknown (git status unavailable)"


def test_bounded_view_failure_is_partial(target, tmp_path):
    td = bash_tool("bad-view", "echo valid", view_builder=lambda *_: 1 / 0)
    r = run(td, target, tmp_path)
    assert r.status is Status.PARTIAL
    assert "bounded-view failure" in r.reason


def test_normalized_outputs_are_deterministic(target, tmp_path):
    td = bash_tool("stable", "printf 'b\\na\\n'")
    td.cwd_mode = "output"
    first = run_tool(td, target, tmp_path / "one", SCAN_DATE)
    second = run_tool(td, target, tmp_path / "two", SCAN_DATE)
    assert first.view_path.read_bytes() == second.view_path.read_bytes()
    name = f"stable-{target.repo_id}.manifest.normalized.json"
    assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()
    assert '"cwd": "<output>"' in (tmp_path / "one" / name).read_text()


def test_relative_output_directory_is_normalized(monkeypatch, target, tmp_path):
    monkeypatch.chdir(tmp_path)
    td = bash_tool("relative", "echo stable")
    td.cwd_mode = "output"
    result = run_tool(td, target, Path("relative-signals"), SCAN_DATE)
    normalized = Path("relative-signals") / f"relative-{target.repo_id}.manifest.normalized.json"
    assert result.status is Status.COMPLETE
    assert '"cwd": "<output>"' in normalized.read_text()
