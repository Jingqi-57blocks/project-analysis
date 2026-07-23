"""57B-11 S2: stack detection + analysis roots (polyglot, evidence-backed)."""

import json

from analysis_wrapper.discovery.stacks import detect


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_ts_app_with_src_tree_uses_src_root(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"react": "18"}}))
    _write(tmp_path / "tsconfig.json", "{}")
    _write(tmp_path / "src" / "app.tsx", "export {};")
    report = detect(tmp_path)
    assert "ts" in report.stacks
    assert report.analysis_roots == ["src"]
    assert "react" in report.frameworks
    assert any("src" in e for e in report.evidence)


def test_js_app_spread_at_root_scans_repo_root(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"express": "4"}}))
    _write(tmp_path / "app.js", "module.exports = 1;")
    _write(tmp_path / "routes" / "user.js", "module.exports = 2;")
    report = detect(tmp_path)
    assert report.stacks == ["js"]
    assert report.analysis_roots == []
    assert "express" in report.frameworks


def test_go_repo(tmp_path):
    _write(tmp_path / "go.mod",
           "module example.com/svc\n\nrequire github.com/gin-gonic/gin v1.9.0\n")
    _write(tmp_path / "main.go", "package main")
    report = detect(tmp_path)
    assert report.stacks == ["go"]
    assert report.analysis_roots == []
    assert report.frameworks == ["github.com/gin-gonic/gin"]


def test_polyglot_go_root_with_js_subapp_collapses_and_discloses(tmp_path):
    _write(tmp_path / "go.mod", "module example.com/svc\n")
    _write(tmp_path / "frontend" / "package.json", "{}")
    _write(tmp_path / "frontend" / "index.js", "1;")
    report = detect(tmp_path)
    assert set(report.stacks) == {"go", "js"}
    assert report.analysis_roots == []  # root scan covers the sub-app
    assert any("collapsed" in e and "frontend" in e for e in report.evidence)


def test_subapps_only_no_root_manifest_emits_named_roots(tmp_path):
    _write(tmp_path / "web" / "package.json", "{}")
    _write(tmp_path / "web" / "index.js", "1;")
    _write(tmp_path / "api" / "go.mod", "module example.com/api\n")
    report = detect(tmp_path)
    assert set(report.stacks) == {"go", "js"}
    assert report.analysis_roots == ["api", "web"]


def test_no_first_class_stack_is_empty_not_guessed(tmp_path):
    _write(tmp_path / "main.py", "print(1)")
    report = detect(tmp_path)
    assert report.stacks == []
    assert report.analysis_roots == []


def test_vendored_manifests_ignored(tmp_path):
    _write(tmp_path / "package.json", "{}")
    _write(tmp_path / "index.js", "1;")
    _write(tmp_path / "node_modules" / "dep" / "package.json",
           json.dumps({"dependencies": {"vue": "3"}}))
    report = detect(tmp_path)
    assert "vue" not in report.frameworks


def test_datastore_dependency_does_not_appear_in_frameworks_or_stacks_evidence(tmp_path):
    """57B-80 PR1: datastore.* facets are additive in technology_facets
    ONLY — this legacy stacks block (frameworks display list, kind ==
    "framework" only, AND its evidence roll-up) is frozen to the pre-PR
    facet kinds and must not leak a datastore dependency like sequelize."""
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"sequelize": "6"}}))
    _write(tmp_path / "index.js", "1;")
    report = detect(tmp_path)
    assert "sequelize" not in report.frameworks
    assert not any("datastore" in e for e in report.evidence)


def test_deterministic_output(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"react": "18", "express": "4"}}))
    _write(tmp_path / "index.js", "1;")
    first, second = detect(tmp_path), detect(tmp_path)
    assert first == second
    assert first.frameworks == sorted(first.frameworks)
