"""Technology-facet detection stays composable and evidence-backed."""

import json

from analysis_wrapper.profiles.detection import detect


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _facets(report):
    return {facet.profile_id: facet for facet in report.facets}


def test_polyglot_repository_keeps_languages_ecosystems_and_frameworks_separate(tmp_path):
    _write(tmp_path / "go.mod",
           "module example.com/api\n\nrequire github.com/gin-gonic/gin v1.9.0\n")
    _write(tmp_path / "main.go", "package main")
    _write(tmp_path / "web" / "package.json",
           json.dumps({"dependencies": {"react": "18"}}))
    _write(tmp_path / "web" / "tsconfig.json", "{}")
    _write(tmp_path / "web" / "src" / "app.tsx", "export {};\n")

    facets = _facets(detect(tmp_path))

    assert {"language.go", "language.typescript"} <= set(facets)
    assert {"ecosystem.go-module", "ecosystem.node"} <= set(facets)
    assert {"framework.gin", "framework.react"} <= set(facets)
    assert facets["language.go"].kind == "language"
    assert facets["ecosystem.node"].kind == "ecosystem"
    assert facets["framework.react"].kind == "framework"


def test_javascript_and_typescript_are_independent_observations(tmp_path):
    _write(tmp_path / "package.json", "{}")
    _write(tmp_path / "tsconfig.json", "{}")
    _write(tmp_path / "src" / "app.ts", "export {};\n")
    _write(tmp_path / "scripts" / "build.js", "module.exports = {};\n")

    facets = _facets(detect(tmp_path))

    assert "language.javascript" in facets
    assert "language.typescript" in facets
    assert facets["language.javascript"].evidence != facets["language.typescript"].evidence


def test_conflicting_node_package_manager_evidence_is_preserved(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"packageManager": "pnpm@9.0.0"}))
    _write(tmp_path / "package-lock.json", "{}")
    _write(tmp_path / "index.js", "module.exports = 1;\n")

    node = _facets(detect(tmp_path))["ecosystem.node"]

    assert node.state == "conflicting"
    assert node.confidence == "medium"
    assert any("conflicting package-manager evidence" in item for item in node.evidence)


def test_unknown_stack_is_disclosed_instead_of_guessed(tmp_path):
    _write(tmp_path / "Sources" / "App.swift", "print(\"hello\")\n")

    report = detect(tmp_path)
    facets = _facets(report)

    assert set(facets) == {"repository.unclassified"}
    assert facets["repository.unclassified"].state == "unknown"
    assert any(row["extension"] == ".swift" for row in report.unclassified_inventory)
    assert report.notes


def test_detection_is_deterministic(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"express": "4"}}))
    _write(tmp_path / "src" / "server.js", "module.exports = 1;\n")

    assert detect(tmp_path) == detect(tmp_path)
