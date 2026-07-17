"""57B-11 S3: PM identity — field > lockfile > npm default; conflicts disclosed."""

import json

from analysis_wrapper.discovery.pm import identify


def test_package_manager_field_wins_over_conflicting_lockfiles(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"packageManager": "pnpm@9.0.0"}))
    (tmp_path / "yarn.lock").write_text("")
    (tmp_path / "package-lock.json").write_text("{}")
    pm = identify(tmp_path)
    assert pm.name == "pnpm"
    assert "packageManager field" in pm.evidence
    assert "conflicting lockfiles" in pm.evidence and "disclosed" in pm.evidence


def test_single_lockfile_decides(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "yarn.lock").write_text("")
    pm = identify(tmp_path)
    assert (pm.name, pm.lockfile) == ("yarn", "yarn.lock")


def test_dual_lockfiles_without_field_default_npm_with_disclosure(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "yarn.lock").write_text("")
    (tmp_path / "package-lock.json").write_text("{}")
    pm = identify(tmp_path)
    assert pm.name == "npm" and pm.lockfile == "package-lock.json"
    assert "yarn.lock" in pm.evidence and "package-lock.json" in pm.evidence
    assert "never silently" in pm.evidence


def test_manifest_without_lockfile_is_npm_default(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    pm = identify(tmp_path)
    assert pm.name == "npm" and pm.lockfile == ""
    assert "without lockfile" in pm.evidence


def test_field_disagreeing_with_single_lockfile_is_disclosed(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"packageManager": "yarn@1.22.22"}))
    (tmp_path / "package-lock.json").write_text("{}")
    pm = identify(tmp_path)
    assert pm.name == "yarn"
    assert "disagrees" in pm.evidence and "package-lock.json" in pm.evidence


def test_go_repo_with_and_without_sum(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    pm = identify(tmp_path)
    assert (pm.name, pm.lockfile) == ("go", "")
    assert "MISSING" in pm.evidence
    (tmp_path / "go.sum").write_text("")
    pm = identify(tmp_path)
    assert (pm.name, pm.lockfile) == ("go", "go.sum")


def test_no_manifest_is_none(tmp_path):
    assert identify(tmp_path).name == "none"
