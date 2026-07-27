"""Deterministic test/CI evidence tests (57B-116, Part A): glob matching,
exclusion policy, CI config discovery, package.json/go.mod parsing, the
per-repo row shape, and integration into _lens_inputs for safety-net and
open-lens ONLY."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator import planner
from analysis_wrapper.targetspec import RepoTarget


def _target(root: Path, **overrides) -> RepoTarget:
    kwargs = {"repo_id": "sample-11111111", "path": str(root)}
    kwargs.update(overrides)
    return RepoTarget(**kwargs)


# --------------------------------------------------------------------------- #
# _is_test_file
# --------------------------------------------------------------------------- #

def test_is_test_file_matches_go_test_suffix():
    assert planner._is_test_file("internal/service_test.go")
    assert not planner._is_test_file("internal/service.go")


def test_is_test_file_matches_dot_test_and_dot_spec_anywhere_in_the_name():
    assert planner._is_test_file("src/bar.test.js")
    assert planner._is_test_file("src/foo.spec.ts")
    assert not planner._is_test_file("src/testing-utils.js")  # no ".test." / ".spec." substring


def test_is_test_file_matches_any_test_or_tests_directory_component():
    assert planner._is_test_file("tests/helper.py")
    assert planner._is_test_file("pkg/test/fixtures.go")  # not just the immediate parent
    assert planner._is_test_file("a/b/tests/c/d/deep.py")
    assert not planner._is_test_file("testing/helper.py")  # "testing" != "test"/"tests"


# --------------------------------------------------------------------------- #
# _iter_repo_relative_files -- Tier-1/Tier-2 exclusion policy
# --------------------------------------------------------------------------- #

def test_iter_repo_relative_files_skips_tier1_dirs(tmp_path):
    root = tmp_path / "repo"
    (root / "internal").mkdir(parents=True)
    (root / "internal" / "service_test.go").write_text("package internal\n", "utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "whatever_test.go").write_text("x", "utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x", "utf-8")

    target = _target(root)
    found = set(planner._iter_repo_relative_files(target))
    assert "internal/service_test.go" in found
    assert not any("node_modules" in path for path in found)
    assert not any(path.startswith(".git/") for path in found)


def test_iter_repo_relative_files_respects_tier2_exclusions(tmp_path):
    root = tmp_path / "repo"
    (root / "vendored-thing").mkdir(parents=True)
    (root / "vendored-thing" / "code_test.go").write_text("x", "utf-8")
    (root / "keep").mkdir()
    (root / "keep" / "code_test.go").write_text("x", "utf-8")

    target = _target(root, tier2_exclusions=["vendored-thing"])
    found = set(planner._iter_repo_relative_files(target))
    assert "keep/code_test.go" in found
    assert not any("vendored-thing" in path for path in found)


# --------------------------------------------------------------------------- #
# _ci_config_relative_paths
# --------------------------------------------------------------------------- #

def test_ci_config_relative_paths_finds_fixed_files_and_yml_workflows_only(tmp_path):
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", "utf-8")
    (root / ".github" / "workflows" / "release.yaml").write_text("name: release\n", "utf-8")
    (root / "bitbucket-pipelines.yml").write_text("pipelines: {}\n", "utf-8")
    (root / "Jenkinsfile").write_text("pipeline {}\n", "utf-8")

    target = _target(root)
    found = planner._ci_config_relative_paths(target)
    assert "bitbucket-pipelines.yml" in found
    assert "Jenkinsfile" in found
    assert ".github/workflows/ci.yml" in found
    assert ".gitlab-ci.yml" not in found  # not present
    # only *.yml workflows are covered (a disclosed, documented limitation);
    # *.yaml is not silently included.
    assert ".github/workflows/release.yaml" not in found


def test_ci_config_relative_paths_empty_when_none_present(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert planner._ci_config_relative_paths(_target(root)) == []


# --------------------------------------------------------------------------- #
# _package_json_scripts / _go_mod_module_line
# --------------------------------------------------------------------------- #

def test_package_json_scripts_returns_the_scripts_block(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"name": "x", "scripts": {"test": "jest", "build": "tsc"}}), "utf-8")
    assert planner._package_json_scripts(_target(root)) == {"test": "jest", "build": "tsc"}


def test_package_json_scripts_is_none_when_file_absent(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert planner._package_json_scripts(_target(root)) is None


def test_package_json_scripts_is_empty_dict_when_scripts_key_missing(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"name": "x"}), "utf-8")
    assert planner._package_json_scripts(_target(root)) == {}


def test_package_json_scripts_is_none_on_malformed_json(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{not valid json", "utf-8")
    assert planner._package_json_scripts(_target(root)) is None


def test_go_mod_module_line_returns_the_module_declaration(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "go.mod").write_text("module github.com/example/sample\n\ngo 1.22\n", "utf-8")
    assert planner._go_mod_module_line(_target(root)) == "module github.com/example/sample"


def test_go_mod_module_line_is_none_when_file_absent(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert planner._go_mod_module_line(_target(root)) is None


# --------------------------------------------------------------------------- #
# _test_ci_evidence_row -- the full per-repo row, cap disclosure
# --------------------------------------------------------------------------- #

def test_test_ci_evidence_row_shape_and_cap_disclosure(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "internal").mkdir(parents=True)
    (root / "internal" / "service_test.go").write_text("package internal\n", "utf-8")
    (root / "bitbucket-pipelines.yml").write_text("pipelines: {}\n", "utf-8")
    (root / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), "utf-8")
    (root / "go.mod").write_text("module example.com/sample\n", "utf-8")

    row = planner._test_ci_evidence_row(_target(root), "sample")
    assert row["repository_ref"] == "sample"
    assert row["test_files"]["total_count"] == 1
    assert row["test_files"]["included_count"] == 1
    assert row["test_files"]["truncated"] is False
    assert row["test_files"]["cap"] == planner._TEST_FILE_CAP
    assert row["test_files"]["paths"] == ["internal/service_test.go"]
    assert row["ci_configs"] == [{"path": "bitbucket-pipelines.yml",
                                 "content": "pipelines: {}", "truncated": False}]
    assert row["package_json_scripts"] == {"test": "jest"}
    assert row["go_mod_module"] == "module example.com/sample"


def test_test_ci_evidence_row_discloses_the_test_file_cap_when_exceeded(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for i in range(planner._TEST_FILE_CAP + 10):
        (root / f"case_{i:04d}_test.go").write_text("package repo\n", "utf-8")

    row = planner._test_ci_evidence_row(_target(root), "sample")
    assert row["test_files"]["total_count"] == planner._TEST_FILE_CAP + 10
    assert row["test_files"]["included_count"] == planner._TEST_FILE_CAP
    assert row["test_files"]["truncated"] is True
    assert len(row["test_files"]["paths"]) == planner._TEST_FILE_CAP


def test_test_ci_evidence_row_truncates_an_oversized_ci_config_with_disclosure(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Jenkinsfile").write_text(
        "\n".join(f"line {i}" for i in range(planner._CI_CONFIG_LINE_CAP + 5)), "utf-8")
    row = planner._test_ci_evidence_row(_target(root), "sample")
    config = row["ci_configs"][0]
    assert config["truncated"] is True
    assert len(config["content"].splitlines()) == planner._CI_CONFIG_LINE_CAP
