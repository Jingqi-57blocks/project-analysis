"""Tests for the ``doctor`` preflight/readiness check (57B-91).

Hermetic: PATH is monkeypatched per test (never depends on what happens to be
installed on the machine running the suite), and the autouse
``_isolated_data_root`` fixture in conftest.py already points
``$PROJECT_ANALYSIS_HOME`` at a throwaway directory.
"""

import json
import os
import stat

import pytest

from analysis_wrapper import doctor


def _empty_path(monkeypatch, tmp_path):
    """Point PATH at an empty directory so no developer-managed tool is
    ever accidentally found on the machine running the suite."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return empty


def _fake_bin(bin_dir, name: str, version_line: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _tool_by_id(report: dict, tool_id: str) -> dict:
    return next(t for t in report["tools"] if t["id"] == tool_id)


# --------------------------------------------------------------------------
# Lane sniff / target-aware applicability
# --------------------------------------------------------------------------

def test_pure_js_workspace_marks_go_lane_not_applicable(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "package.json").write_text("{}")
    (ws / "index.js").write_text("module.exports = 1;\n")

    report = doctor.build_report(ws)

    assert _tool_by_id(report, "go")["classification"] == "not-applicable"
    assert _tool_by_id(report, "go-callgraph")["classification"] == "not-applicable"
    assert _tool_by_id(report, "staticcheck")["classification"] == "not-applicable"
    assert _tool_by_id(report, "node")["classification"] == "needed-for-this-target"
    assert _tool_by_id(report, "dependency-cruiser")["classification"] == "needed-for-this-target"
    assert _tool_by_id(report, "typescript")["classification"] == "needed-for-this-target"


def test_go_only_workspace_marks_js_lane_not_applicable(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "go.mod").write_text("module example.com/x\n")
    (ws / "main.go").write_text("package main\nfunc main() {}\n")

    report = doctor.build_report(ws)

    assert _tool_by_id(report, "node")["classification"] == "not-applicable"
    assert _tool_by_id(report, "pnpm")["classification"] == "not-applicable"
    assert _tool_by_id(report, "dependency-cruiser")["classification"] == "not-applicable"
    assert _tool_by_id(report, "typescript")["classification"] == "not-applicable"
    assert _tool_by_id(report, "go")["classification"] == "needed-for-this-target"
    assert _tool_by_id(report, "go-callgraph")["classification"] == "needed-for-this-target"
    assert _tool_by_id(report, "staticcheck")["classification"] == "needed-for-this-target"


def test_mixed_multirepo_workspace_both_lanes_applicable(monkeypatch, tmp_path):
    """repoA is a normal git repo (JS); repoB is identified by a `.git` FILE
    (worktree/submodule style) and is Go; repoC is nested inside repoB and is
    its own separate git repo, also Go — all must be found."""
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()

    repo_a = ws / "repoA"
    repo_a.mkdir()
    (repo_a / ".git").mkdir()
    (repo_a / "package.json").write_text("{}")

    repo_b = ws / "repoB"
    repo_b.mkdir()
    (repo_b / ".git").write_text("gitdir: ../../.git-worktrees/repoB\n")
    (repo_b / "go.mod").write_text("module example.com/b\n")

    repo_c = repo_b / "nested" / "repoC"
    repo_c.mkdir(parents=True)
    (repo_c / ".git").mkdir()
    (repo_c / "main.go").write_text("package main\nfunc main() {}\n")

    report = doctor.build_report(ws)

    assert report["lane_applicability"]["js"] is True
    assert report["lane_applicability"]["go"] is True
    assert report["lane_sniff"]["has_repo"] is True
    assert _tool_by_id(report, "go")["classification"] == "needed-for-this-target"
    assert _tool_by_id(report, "dependency-cruiser")["classification"] == "needed-for-this-target"


def test_js_with_no_package_json_still_applicable(monkeypatch, tmp_path):
    """Deliberate superset bias: a bare .ts file with no package.json must
    still mark the JS lane applicable."""
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "script.ts").write_text("export const x = 1;\n")

    report = doctor.build_report(ws)

    assert report["lane_applicability"]["js"] is True
    assert _tool_by_id(report, "dependency-cruiser")["classification"] == "needed-for-this-target"


def test_workspace_with_neither_js_nor_go(monkeypatch, tmp_path):
    empty = _empty_path(monkeypatch, tmp_path)
    # core_ok is now generic over every `requirement: required` manifest tool
    # (FIX 3), not just python — fake the other required tool
    # (analysis-wrapper) present so this test still exercises the
    # "everything required is fine" case it was written for.
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.4.0")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("nothing here\n")

    report = doctor.build_report(ws)

    assert _tool_by_id(report, "node")["classification"] == "not-applicable"
    assert _tool_by_id(report, "go")["classification"] == "not-applicable"
    assert _tool_by_id(report, "python")["classification"] == "required"
    assert _tool_by_id(report, "python")["state"] == "present"
    assert _tool_by_id(report, "analysis-wrapper")["state"] == "present"
    assert report["core_ok"] is True


def test_no_workspace_reports_lanes_unknown_not_guessed(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    report = doctor.build_report(None)
    assert report["lane_applicability"]["js"] is None
    assert report["lane_applicability"]["go"] is None
    assert report["lane_sniff"] is None
    # unknown lane -> still "needed-for-this-target" (never silently hidden)
    assert _tool_by_id(report, "node")["classification"] == "needed-for-this-target"
    assert _tool_by_id(report, "go")["classification"] == "needed-for-this-target"


# --------------------------------------------------------------------------
# Presence / absence, exit codes
# --------------------------------------------------------------------------

def test_missing_optional_tool_is_disclosed_not_a_failure(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    rc = doctor.run(None, as_json=True)
    assert rc == doctor.EXIT_OK


def test_missing_optional_tool_state_and_verdict(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    report = doctor.build_report(None)
    git = _tool_by_id(report, "git")
    assert git["state"] == "unavailable"
    assert git["what_you_lose"]
    assert report["verdict"] in ("ready-reduced-coverage", "setup-needed")


def test_malformed_manifest_is_installation_corrupt(monkeypatch, tmp_path):
    from analysis_wrapper import paths as paths_mod
    bad_root = tmp_path / "bad-skill"
    (bad_root / "tools").mkdir(parents=True)
    (bad_root / "tools" / "manifest.json").write_text("{not valid json")
    monkeypatch.setattr(paths_mod, "skill_root", lambda: bad_root)
    rc = doctor.run(None, as_json=True)
    assert rc == doctor.EXIT_INSTALLATION_CORRUPT


def test_missing_manifest_is_installation_corrupt(monkeypatch, tmp_path):
    from analysis_wrapper import paths as paths_mod
    bad_root = tmp_path / "bad-skill-2"
    bad_root.mkdir()
    monkeypatch.setattr(paths_mod, "skill_root", lambda: bad_root)
    rc = doctor.run(None, as_json=True)
    assert rc == doctor.EXIT_INSTALLATION_CORRUPT


def test_nonexistent_workspace_is_invalid_invocation(tmp_path):
    missing = tmp_path / "does-not-exist"
    rc = doctor.run(str(missing), as_json=True)
    assert rc == doctor.EXIT_INVALID_INVOCATION


# --------------------------------------------------------------------------
# --json output
# --------------------------------------------------------------------------

def test_json_output_parses_and_has_documented_keys(monkeypatch, tmp_path, capsys):
    _empty_path(monkeypatch, tmp_path)
    rc = doctor.run(None, as_json=True)
    assert rc == doctor.EXIT_OK
    out = capsys.readouterr().out
    doc = json.loads(out)
    for key in ("schema_version", "skill_version", "data_root", "python_version",
                "python_ok", "workspace", "lane_sniff", "lane_applicability",
                "tools", "verdict", "core_ok", "setup_needed",
                "network_required_for_setup"):
        assert key in doc, key
    assert doc["workspace"] is None


def test_json_verdict_matches_human_verdict(monkeypatch, tmp_path, capsys):
    _empty_path(monkeypatch, tmp_path)
    report = doctor.build_report(None)
    human = doctor.render_human(report)
    assert doctor._VERDICT_LINES[report["verdict"]].strip() in human


# --------------------------------------------------------------------------
# Version drift
# --------------------------------------------------------------------------

def test_version_drift_detected_for_ast_grep(monkeypatch, tmp_path):
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "ast-grep", "ast-grep 0.99.9")
    report = doctor.build_report(None)
    row = _tool_by_id(report, "ast-grep")
    assert row["state"] == "present"
    assert row["detected_version"] == "0.99.9"
    assert "drift" in row["drift"]
    assert "0.44.1" in row["drift"]


def test_no_drift_when_version_matches(monkeypatch, tmp_path):
    empty = _empty_path(monkeypatch, tmp_path)
    validated = _tool_by_id(doctor.build_report(None), "ast-grep")["validated_version"]
    _fake_bin(empty, "ast-grep", f"ast-grep {validated}")
    report = doctor.build_report(None)
    row = _tool_by_id(report, "ast-grep")
    assert row["state"] == "present"
    assert row["drift"] == ""


def test_no_drift_when_detected_satisfies_accepted_range(monkeypatch, tmp_path):
    """python validated_version "3.11", accepted_range ">=3.11" — a host on
    3.12/3.13 (most modern machines) must not print a scary false alarm
    (57B-91 review FIX 4)."""
    assert doctor._drift_note("3.11", "3.13.5", ">=3.11") == ""
    assert doctor._drift_note("3.11", "3.11.0", ">=3.11") == ""


def test_drift_still_flagged_for_genuine_older_version():
    assert "drift" in doctor._drift_note("3.11", "3.9.0", ">=3.11")


def test_drift_falls_back_conservatively_on_unparseable_range():
    """An accepted_range this module doesn't understand (OR-clauses, ``.x``
    wildcards, ...) must never be treated as satisfied — fall back to the
    prior, more conservative drift-flagging behavior."""
    assert doctor._drift_note("22", "23.0.0", "22.x || 24.x || >=26") != ""


# --------------------------------------------------------------------------
# FIX 1: depth-bound truncation must set `truncated` (over-inclusion failsafe)
# --------------------------------------------------------------------------

def test_deep_go_source_past_depth_bound_still_applicable(monkeypatch, tmp_path):
    """A workspace whose only .go file sits past _MAX_WALK_DEPTH must not
    silently under-detect the Go lane: `truncated` must be set and the lane
    forced applicable."""
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    current = ws
    for i in range(doctor._MAX_WALK_DEPTH + 2):
        current = current / f"d{i}"
    current.mkdir(parents=True)
    (current / "main.go").write_text("package main\nfunc main() {}\n")

    sniff = doctor.sniff_lanes(ws)
    assert sniff["truncated"] is True
    assert sniff["go"] is True

    report = doctor.build_report(ws)
    assert _tool_by_id(report, "go")["classification"] == "needed-for-this-target"


def test_entries_bound_truncation_still_sets_truncated_and_applicable(monkeypatch, tmp_path):
    """Existing entries-bound truncation path: still correct after FIX 1."""
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(doctor, "_MAX_WALK_ENTRIES", 5)
    for i in range(20):
        (ws / f"file{i}.txt").write_text("x")
    sniff = doctor.sniff_lanes(ws)
    assert sniff["truncated"] is True
    assert sniff["go"] is True
    assert sniff["js"] is True
    assert sniff["sql"] is True


# --------------------------------------------------------------------------
# FIX 2: state/output/exported are the user's own source, never pruned
# --------------------------------------------------------------------------

def test_sources_under_dir_named_output_are_detected(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    (ws / "output").mkdir(parents=True)
    (ws / "output" / "main.go").write_text("package main\nfunc main() {}\n")
    (ws / "output" / "seed.sql").write_text("select 1;\n")

    sniff = doctor.sniff_lanes(ws)
    assert sniff["go"] is True
    assert sniff["sql"] is True

    report = doctor.build_report(ws)
    assert _tool_by_id(report, "go")["classification"] == "needed-for-this-target"


def test_sources_under_dir_named_state_are_detected(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    (ws / "state").mkdir(parents=True)
    (ws / "state" / "app.js").write_text("module.exports = 1;\n")

    sniff = doctor.sniff_lanes(ws)
    assert sniff["js"] is True


def test_sources_under_dir_named_exported_are_detected(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    (ws / "exported").mkdir(parents=True)
    (ws / "exported" / "report.sql").write_text("select 1;\n")

    sniff = doctor.sniff_lanes(ws)
    assert sniff["sql"] is True


def test_build_artifact_dirs_still_pruned(monkeypatch, tmp_path):
    """node_modules/vendor/dist/build/coverage remain pruned — only
    state/output/exported were removed from the skip list."""
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1;\n")
    (ws / "vendor" / "lib").mkdir(parents=True)
    (ws / "vendor" / "lib" / "main.go").write_text("package main\nfunc main() {}\n")

    sniff = doctor.sniff_lanes(ws)
    assert sniff["js"] is False
    assert sniff["go"] is False


# --------------------------------------------------------------------------
# sql / history lane sniffs
# --------------------------------------------------------------------------

def test_sql_lane_sniffed_directly(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "seed.sql").write_text("select 1;\n")

    report = doctor.build_report(ws)
    assert report["lane_applicability"]["sql"] is True
    assert _tool_by_id(report, "sqlglot")["classification"] == "needed-for-this-target"


def test_history_lane_true_only_with_repo_marker(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("no repo here\n")
    report = doctor.build_report(ws)
    assert report["lane_applicability"]["history"] is False

    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    (ws2 / ".git").mkdir()
    report2 = doctor.build_report(ws2)
    assert report2["lane_applicability"]["history"] is True


# --------------------------------------------------------------------------
# Python-too-old -> exit 3 (simulated, no old interpreter required)
# --------------------------------------------------------------------------

def test_python_too_old_reports_environment_incomplete(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "MIN_PYTHON", (99, 0))
    rc = doctor.run(None, as_json=True)
    assert rc == doctor.EXIT_ENVIRONMENT_INCOMPLETE


def test_python_too_old_verdict_blocked(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "MIN_PYTHON", (99, 0))
    report = doctor.build_report(None)
    assert report["python_ok"] is False
    assert report["verdict"] == "blocked"


# --------------------------------------------------------------------------
# FIX 3: core_ok generic over the manifest's required set
# --------------------------------------------------------------------------

def test_core_ok_false_when_a_required_manifest_tool_is_missing(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    real_read_manifest = doctor.read_manifest

    def _patched():
        doc = json.loads(json.dumps(real_read_manifest()))
        doc["tools"].append({
            "id": "synthetic-required-tool", "name": "Synthetic Required Tool",
            "lanes": ["core"], "requirement": "required", "ownership": "host-provided",
            "executable": "definitely-not-a-real-binary-xyz",
        })
        return doc

    monkeypatch.setattr(doctor, "read_manifest", _patched)
    report = doctor.build_report(None)
    tool = _tool_by_id(report, "synthetic-required-tool")
    assert tool["classification"] == "required"
    assert tool["state"] == "unavailable"
    assert report["core_ok"] is False


# --------------------------------------------------------------------------
# FIX 6: manifest entry missing a required field -> exit 4, not 1
# --------------------------------------------------------------------------

def test_manifest_entry_missing_required_field_is_installation_corrupt(monkeypatch, tmp_path):
    from analysis_wrapper import paths as paths_mod
    bad_root = tmp_path / "bad-skill-3"
    (bad_root / "tools").mkdir(parents=True)
    (bad_root / "tools" / "manifest.json").write_text(json.dumps({
        "tools": [{"id": "incomplete-tool", "lanes": ["core"]}],
    }))
    monkeypatch.setattr(paths_mod, "skill_root", lambda: bad_root)
    rc = doctor.run(None, as_json=True)
    assert rc == doctor.EXIT_INSTALLATION_CORRUPT


def test_read_manifest_raises_for_entry_missing_required_field(monkeypatch, tmp_path):
    from analysis_wrapper import paths as paths_mod
    bad_root = tmp_path / "bad-skill-4"
    (bad_root / "tools").mkdir(parents=True)
    (bad_root / "tools" / "manifest.json").write_text(json.dumps({
        "tools": [{"id": "incomplete-tool", "lanes": ["core"], "name": "X"}],
    }))
    monkeypatch.setattr(paths_mod, "skill_root", lambda: bad_root)
    with pytest.raises(doctor.ManifestError):
        doctor.read_manifest()


# --------------------------------------------------------------------------
# CLI dispatch
# --------------------------------------------------------------------------

def test_cli_main_doctor_json_returns_ok_and_parseable(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    from analysis_wrapper import cli
    rc = cli.main(["doctor", "--json"])
    assert rc == 0


def test_cli_main_doctor_json_output_is_parseable(monkeypatch, tmp_path, capsys):
    _empty_path(monkeypatch, tmp_path)
    from analysis_wrapper import cli
    rc = cli.main(["doctor", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert "verdict" in doc


# --------------------------------------------------------------------------
# FIX 7 / read-only assertion: doctor must never mutate the workspace it scans
# --------------------------------------------------------------------------

def _snapshot(root):
    entries = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        st = path.stat()
        entries[rel] = (path.is_dir(), st.st_mtime_ns, st.st_size if path.is_file() else None)
    return entries


def test_doctor_never_mutates_the_scanned_workspace(monkeypatch, tmp_path):
    _empty_path(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "package.json").write_text("{}")
    (ws / "index.js").write_text("module.exports = 1;\n")
    (ws / "sub").mkdir()
    (ws / "sub" / "main.go").write_text("package main\nfunc main() {}\n")

    before = _snapshot(ws)
    rc = doctor.run(str(ws), as_json=True)
    assert rc == doctor.EXIT_OK
    after = _snapshot(ws)
    assert before == after


def test_doctor_does_not_create_the_data_root(monkeypatch, tmp_path):
    """A read-only doctor must not mkdir a host directory as a side effect
    of merely reporting where the data root would resolve to (FIX 7)."""
    home = tmp_path / "would-be-data-root"
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(home))
    assert not home.exists()
    rc = doctor.run(None, as_json=True)
    assert rc == doctor.EXIT_OK
    assert not home.exists()
