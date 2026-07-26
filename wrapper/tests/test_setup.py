"""Tests for the ``setup`` provisioning command (57B-92).

Hermetic: no real network call and no real pip/pnpm/go/venv installer ever
runs. ``run_cmd``/``create_venv`` are always fake callables injected into
``setup.run()``; PATH is monkeypatched per test (the autouse
``_isolated_data_root`` fixture in conftest.py already points
``$PROJECT_ANALYSIS_HOME`` at a throwaway directory, so venv/node_tools/
go_tools destinations are always a fresh tmp tree too).
"""

import json
import os
import socket
import stat
import subprocess
import sys
import time

import pytest

from analysis_wrapper import doctor, setup


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _empty_path(monkeypatch, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return empty


def _fake_bin(bin_dir, name: str, version_line: str = "") -> None:
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_report(overrides: dict[str, dict] | None = None) -> dict:
    """A ``doctor.build_report``-shaped dict with every manifest tool
    defaulted to present/no-drift/needed-for-this-target, so individual
    tests only have to override what they care about."""
    overrides = overrides or {}
    manifest = doctor.read_manifest()
    tools = []
    for tool in manifest["tools"]:
        row = {
            "id": tool["id"], "ownership": tool["ownership"],
            "classification": "needed-for-this-target",
            "state": "present", "drift": "", "network_host": tool.get("network_host"),
        }
        row.update(overrides.get(tool["id"], {}))
        tools.append(row)
    return {"workspace": None, "data_root": "/fake/data-root", "tools": tools}


def _item(plan: dict, lane: str) -> dict | None:
    return next((i for i in plan["items"] if i["lane"] == lane), None)


class _FakeRun:
    """Records every invocation and always reports success unless told to
    fail for a specific argv[0]."""

    def __init__(self, fail_for: set[str] | None = None):
        self.calls: list[list[str]] = []
        self._fail_for = fail_for or set()

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv and argv[0] in self._fail_for:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
        if len(argv) > 1 and argv[1] == "-c":
            # bootstrap's own interpreter-identity probe: the venv's prefix is
            # two levels above its `bin/python` interpreter.
            from pathlib import Path
            environment = Path(argv[0]).resolve().parents[1]
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps({"prefix": str(environment), "base": "/host"}))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def _fake_create_venv(environment):
    from analysis_wrapper.bootstrap import environment_python
    python = environment_python(environment)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("")


def _never_confirm(_item):
    return False


def _always_confirm(_item):
    return True


# --------------------------------------------------------------------------
# Plan: lane inclusion is target-aware (real doctor sniff, real PATH probing)
# --------------------------------------------------------------------------

def test_plan_pure_js_target_includes_node_lane_excludes_go_entirely(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "package.json").write_text("{}")
    (ws / "index.js").write_text("module.exports = 1;\n")

    plan = setup.compute_plan(ws)

    assert _item(plan, "js") is not None
    assert _item(plan, "go") is None
    assert "go" in [row["lane"] for row in plan["excluded"]]
    assert _item(plan, "core") is not None


def test_plan_pure_go_target_includes_go_lane_excludes_node_entirely(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "go.mod").write_text("module example.com/x\n")
    (ws / "main.go").write_text("package main\nfunc main() {}\n")

    plan = setup.compute_plan(ws)

    assert _item(plan, "go") is not None
    assert _item(plan, "js") is None
    assert "js" in [row["lane"] for row in plan["excluded"]]


def test_plan_mixed_target_includes_both_lanes(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "package.json").write_text("{}")
    (ws / "index.js").write_text("module.exports = 1;\n")
    (ws / "go.mod").write_text("module example.com/x\n")
    (ws / "main.go").write_text("package main\nfunc main() {}\n")

    plan = setup.compute_plan(ws)

    assert _item(plan, "js") is not None
    assert _item(plan, "go") is not None


def test_developer_managed_tools_never_appear_in_the_plan(monkeypatch, tmp_path):
    """git/scc/lizard/jscpd/ast-grep/staticcheck/osv-scanner/node/pnpm/go/python
    are all developer-managed -- none may show up as a tool the plan would
    install."""
    plan = setup.compute_plan(None)
    installable_tools = {tid for item in plan["items"] for tid in item["tools"]}
    manifest = doctor.read_manifest()
    developer_managed = {t["id"] for t in manifest["tools"]
                         if t["ownership"] == "developer-managed"}
    assert installable_tools & developer_managed == set()


# --------------------------------------------------------------------------
# Consent
# --------------------------------------------------------------------------

def test_default_no_yes_declines_and_installs_nothing(monkeypatch, tmp_path):
    from analysis_wrapper import paths
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"}}))
    fake_run = _FakeRun()
    rc = setup.run(None, run_cmd=fake_run, create_venv=_fake_create_venv)
    assert fake_run.calls == []
    assert not paths.venv_dir().exists()
    assert rc == setup.EXIT_CONSENT_DECLINED


def test_plan_flag_installs_nothing_and_makes_no_network_call(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"},
         "dependency-cruiser": {"state": "unavailable"},
         "go-callgraph": {"state": "unavailable"}}))
    fake_run = _FakeRun()
    rc = setup.run(None, dry_run=True, yes=True, run_cmd=fake_run,
                   create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK
    assert fake_run.calls == []


def test_yes_prints_plan_and_proceeds(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"}}))
    fake_run = _FakeRun()
    rc = setup.run(None, yes=True, run_cmd=fake_run, create_venv=_fake_create_venv)
    out = capsys.readouterr().out
    assert "Project Analysis setup plan" in out
    assert "[core]" in out
    assert rc == setup.EXIT_OK
    assert any(call[1] == "-m" for call in fake_run.calls)  # bootstrap's pip install ran


def test_consent_declined_for_one_lane_only_others_proceed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"},
         "dependency-cruiser": {"state": "unavailable"}}))

    def confirm(item):
        return item["lane"] != "js"

    rc = setup.run(None, as_json=True, confirm=confirm, run_cmd=_FakeRun(),
                   create_venv=_fake_create_venv)
    doc = json.loads(capsys.readouterr().out)
    js_result = _item(doc, "js")
    core_result = _item(doc, "core")
    assert js_result["action_taken"] == "skipped-consent-declined"
    assert core_result["action_taken"] == "installed"
    assert rc == setup.EXIT_CONSENT_DECLINED


# --------------------------------------------------------------------------
# Missing developer-managed runtime
# --------------------------------------------------------------------------

def test_missing_runtime_skips_lane_others_still_proceed(monkeypatch, tmp_path):
    """No pnpm on PATH: the js lane is skipped with a clear reason; core
    still installs and the command does not hard-fail."""
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"pnpm": {"state": "unavailable"},
         "analysis-wrapper": {"state": "unavailable"}}))
    fake_run = _FakeRun()
    rc = setup.run(None, yes=True, run_cmd=fake_run, create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK  # default --lane all: reduced coverage, not fatal

    plan = setup.compute_plan(None)
    js = _item(plan, "js")
    assert js["status"] == "unavailable-missing-runtime"
    assert "pnpm" in js["reason"]
    core = _item(plan, "core")
    assert core["status"] == "install"


def test_explicit_single_lane_missing_runtime_is_distinct_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"pnpm": {"state": "unavailable"}}))
    rc = setup.run(None, lanes=["js"], yes=True, run_cmd=_FakeRun(),
                   create_venv=_fake_create_venv)
    assert rc == setup.EXIT_RUNTIME_MISSING


# --------------------------------------------------------------------------
# Idempotency / drift-driven reconcile
# --------------------------------------------------------------------------

def test_everything_present_no_drift_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report())
    fake_run = _FakeRun()
    rc = setup.run(None, yes=True, run_cmd=fake_run, create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK
    assert fake_run.calls == []

    # Running again changes nothing.
    fake_run2 = _FakeRun()
    rc2 = setup.run(None, yes=True, run_cmd=fake_run2, create_venv=_fake_create_venv)
    assert rc2 == setup.EXIT_OK
    assert fake_run2.calls == []


def test_drift_triggers_reconcile(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"typescript": {"state": "present", "drift": "drift: detected 5.0.0, validated 5.9.3"}}))
    plan = setup.compute_plan(None)
    js = _item(plan, "js")
    assert js["status"] == "reconcile"

    fake_run = _FakeRun()
    rc = setup.run(None, yes=True, run_cmd=fake_run, create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK
    assert any("pnpm" in call[0] for call in fake_run.calls)


# --------------------------------------------------------------------------
# Package-manager / cache-incompatible failures
# --------------------------------------------------------------------------

def test_package_manager_failure_is_reported_and_distinct_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"dependency-cruiser": {"state": "unavailable"}}))
    fake_run = _FakeRun(fail_for={"pnpm"})
    rc = setup.run(None, yes=True, run_cmd=fake_run, create_venv=_fake_create_venv)
    assert rc == setup.EXIT_PACKAGE_MANAGER_FAILED


def test_cache_incompatible_destination_is_reported(monkeypatch, tmp_path):
    from analysis_wrapper import paths
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"go-callgraph": {"state": "unavailable"}}))
    dest = paths.go_tools_bin()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("not a directory")  # occupies the destination with a file

    rc = setup.run(None, yes=True, run_cmd=_FakeRun(), create_venv=_fake_create_venv)
    assert rc == setup.EXIT_CACHE_INCOMPATIBLE


# --------------------------------------------------------------------------
# Lock
# --------------------------------------------------------------------------

def test_lock_contention_gets_lock_held_exit_code(monkeypatch, tmp_path):
    lock_path = setup._lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999999@some-other-host 111111.0\n")

    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"}}))
    rc = setup.run(None, yes=True, run_cmd=_FakeRun(), create_venv=_fake_create_venv)
    assert rc == setup.EXIT_LOCK_HELD


def test_lock_released_even_when_guarded_body_raises(tmp_path):
    lock_path = tmp_path / "runtime" / "1" / ".setup.lock"
    with pytest.raises(RuntimeError):
        with setup._exclusive_lock(lock_path):
            raise RuntimeError("boom")
    assert not lock_path.exists()


def test_stale_lock_is_reclaimed(monkeypatch, tmp_path):
    import os
    import socket
    lock_path = setup._lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # A pid that (almost certainly) does not exist, on THIS host, is stale.
    lock_path.write_text(f"999999999@{socket.gethostname()} 111111.0\n")

    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"}}))
    rc = setup.run(None, yes=True, run_cmd=_FakeRun(), create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK


# --------------------------------------------------------------------------
# Never writes into the analyzed target
# --------------------------------------------------------------------------

def _snapshot(root):
    entries = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        st = path.stat()
        entries[rel] = (path.is_dir(), st.st_mtime_ns, st.st_size if path.is_file() else None)
    return entries


def test_setup_never_writes_into_the_target(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "package.json").write_text("{}")
    (ws / "index.js").write_text("module.exports = 1;\n")

    before = _snapshot(ws)
    rc = setup.run(str(ws), yes=True, run_cmd=_FakeRun(), create_venv=_fake_create_venv)
    after = _snapshot(ws)
    assert before == after
    assert rc == setup.EXIT_OK


def test_setup_refuses_a_data_root_inside_the_target(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(ws / "data"))
    rc = setup.run(str(ws), dry_run=True)
    assert rc == setup.EXIT_DATA_ROOT_NOT_WRITABLE


# --------------------------------------------------------------------------
# --json shape
# --------------------------------------------------------------------------

def test_json_plan_output_parses_and_has_documented_keys(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report())
    rc = setup.run(None, dry_run=True, as_json=True)
    assert rc == setup.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    for key in ("schema_version", "workspace", "data_root", "items", "excluded", "mode"):
        assert key in doc, key
    assert doc["mode"] == "plan"


def test_json_apply_output_parses_and_has_documented_keys(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"}}))
    rc = setup.run(None, as_json=True, yes=True, run_cmd=_FakeRun(),
                   create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    for key in ("schema_version", "workspace", "data_root", "items", "excluded",
                "mode", "outcome"):
        assert key in doc, key
    assert doc["mode"] == "apply"
    for item in doc["items"]:
        assert "action_taken" in item


# --------------------------------------------------------------------------
# CLI dispatch
# --------------------------------------------------------------------------

def test_cli_main_setup_plan_returns_ok(monkeypatch, tmp_path):
    from analysis_wrapper import cli
    rc = cli.main(["setup", "--plan", "--json"])
    assert rc == setup.EXIT_OK


def test_cli_main_setup_invalid_workspace_is_invalid_invocation(monkeypatch, tmp_path):
    from analysis_wrapper import cli
    missing = tmp_path / "does-not-exist"
    rc = cli.main(["setup", "--plan", "--workspace", str(missing)])
    assert rc == setup.EXIT_INVALID_INVOCATION


# --------------------------------------------------------------------------
# Review fixes (57B-92 follow-up): plan-before-install ordering in JSON mode
# --------------------------------------------------------------------------

def test_json_yes_emits_plan_before_any_install(monkeypatch, tmp_path):
    """FIX 2 regression test: with --json --yes, nothing may be spawned
    before the plan is disclosed. The plan doc is written to stderr (FIX 2/3
    -- stdout stays a single parseable apply-result document); we assert
    ordering by recording both the stderr write and the first installer
    call into one shared list."""
    order: list[str] = []
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"}}))

    real_write = sys.stderr.write

    def recording_write(s):
        if s.strip():
            order.append("plan")
        return real_write(s)

    monkeypatch.setattr(sys.stderr, "write", recording_write)

    def recording_run(argv, **kwargs):
        order.append("install")
        return _FakeRun()(argv, **kwargs)

    rc = setup.run(None, as_json=True, yes=True, run_cmd=recording_run,
                   create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK
    assert order, "expected at least one recorded event"
    assert order[0] == "plan"
    assert "install" in order


# --------------------------------------------------------------------------
# Review fixes: lock liveness (FIX 1) and ownership-checked release (FIX 1)
# --------------------------------------------------------------------------

def test_live_same_host_pid_with_old_mtime_is_not_reclaimed(monkeypatch, tmp_path):
    """A lock held by a LIVE process on this host must never be considered
    stale, no matter how old its mtime is -- this is the exact scenario of a
    consent prompt left open for hours. We use our own test process's pid,
    which is guaranteed alive for the duration of the test."""
    lock_path = setup._lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(f"{os.getpid()}@{socket.gethostname()} 111111.0\n")
    old = time.time() - (setup.LOCK_STALE_SECONDS + 3600)
    os.utime(lock_path, (old, old))

    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"}}))
    rc = setup.run(None, yes=True, run_cmd=_FakeRun(), create_venv=_fake_create_venv)
    assert rc == setup.EXIT_LOCK_HELD


def test_release_does_not_unlink_a_lock_it_no_longer_owns(tmp_path):
    """If the lock file no longer contains the payload we wrote at acquire
    time (e.g. it was reclaimed/replaced from under us), release must leave
    it alone rather than deleting someone else's lock."""
    lock_path = tmp_path / "runtime" / "1" / ".setup.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with setup._exclusive_lock(lock_path):
        lock_path.write_text("someone-else@otherhost 999999.0\n")
    assert lock_path.exists()
    assert lock_path.read_text("utf-8") == "someone-else@otherhost 999999.0\n"


# --------------------------------------------------------------------------
# Review fixes: supply-chain-critical pnpm flags + tracked source untouched
# --------------------------------------------------------------------------

def test_js_lane_install_argv_has_frozen_lockfile_and_ignore_scripts(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"dependency-cruiser": {"state": "unavailable"}}))
    fake_run = _FakeRun()
    rc = setup.run(None, yes=True, run_cmd=fake_run, create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK
    pnpm_calls = [c for c in fake_run.calls if c and c[0] == "pnpm"]
    assert pnpm_calls, "expected a pnpm install call"
    assert "--frozen-lockfile" in pnpm_calls[0]
    assert "--ignore-scripts" in pnpm_calls[0]


def test_js_lane_install_does_not_modify_tracked_node_tools_source(monkeypatch, tmp_path):
    from analysis_wrapper import paths
    src = paths.wrapper_root() / "node_tools"
    before_pkg = (src / "package.json").read_bytes()
    before_lock = (src / "pnpm-lock.yaml").read_bytes()

    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"dependency-cruiser": {"state": "unavailable"}}))
    rc = setup.run(None, yes=True, run_cmd=_FakeRun(), create_venv=_fake_create_venv)
    assert rc == setup.EXIT_OK
    assert (src / "package.json").read_bytes() == before_pkg
    assert (src / "pnpm-lock.yaml").read_bytes() == before_lock


# --------------------------------------------------------------------------
# Review fix: manifest coverage (FIX 4)
# --------------------------------------------------------------------------

def test_manifest_analyzer_managed_tools_are_all_claimed_by_a_lane_group():
    """Every ``ownership: analyzer-managed`` tool in tools/manifest.json must
    be claimed by some ``_LANE_GROUPS`` entry -- otherwise ``doctor`` would
    report it missing forever while ``setup`` silently never provisions it."""
    manifest = doctor.read_manifest()
    analyzer_managed = {t["id"] for t in manifest["tools"]
                        if t["ownership"] == "analyzer-managed"}
    claimed = {tid for group in setup._LANE_GROUPS.values() for tid in group["tools"]}
    unclaimed = analyzer_managed - claimed
    assert not unclaimed, (
        f"analyzer-managed tool(s) not claimed by any setup lane group: "
        f"{sorted(unclaimed)}")


# --------------------------------------------------------------------------
# Review fix: data root permissions (FIX 5)
# --------------------------------------------------------------------------

def test_data_root_is_0700_after_a_declined_run(monkeypatch, tmp_path):
    from analysis_wrapper import paths
    monkeypatch.setattr(doctor, "build_report", lambda workspace: _fake_report(
        {"analysis-wrapper": {"state": "unavailable"}}))
    # No --yes, and stdin is not a tty under pytest -- declines by default.
    rc = setup.run(None, run_cmd=_FakeRun(), create_venv=_fake_create_venv)
    assert rc == setup.EXIT_CONSENT_DECLINED
    root = paths.resolved_data_root()
    assert root.exists()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
