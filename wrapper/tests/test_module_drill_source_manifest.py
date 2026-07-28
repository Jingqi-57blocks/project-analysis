"""Canonical source-manifest construction tests for 57B-136."""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper import identity
from analysis_wrapper.module_drill.source_manifest import build_from_overview
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _overview_run(tmp_path: Path) -> tuple[Path, TargetSpec]:
    workspace = tmp_path / "workspace"
    repository = workspace / "service"
    repository.mkdir(parents=True)
    target = RepoTarget(repo_id=stable_repo_id(str(repository)), path=str(repository))
    spec = TargetSpec([target])
    run = tmp_path / "overview-source"
    run.mkdir()
    (run / "targets.json").write_text(spec.to_json(), encoding="utf-8")
    identities = identity.build(
        spec, workspace_root=workspace, project_id=stable_repo_id(str(workspace)))
    identity.write_mapping(run, identities)
    _write_json(run / "discovery-report.json", {"project_ref": identities.project.reference})
    _write_json(run / "run-provenance.json", {
        "preparation": {"include_network": False},
        "tool_versions": [{"tool": "staticcheck", "version": "1.0"}],
    })
    _write_json(run / "provider-execution.json", {
        "executions": [{
            "provider_id": "route-provider", "capability_id": "route-linkage",
            "repository_ref": "service", "outcome": "completed", "reason": "",
            "coverage": {"applicability": "applicable", "status": "complete"},
            "tools": [{"tool_id": "ast-grep", "status": "complete"}],
        }],
    })
    _write_json(run / "routes" / "route-inventory.json", {"schema_version": "routes/v1"})
    _write_json(run / "synthesis-input.json", {"schema_version": "view/v1"})
    return run, spec


def test_overview_manifest_indexes_canonical_artifacts_without_promoting_views(tmp_path):
    run, spec = _overview_run(tmp_path)
    manifest = build_from_overview(run, snapshot_id="a" * 64)

    assert manifest.source_mode == "overview-backed"
    assert manifest.source_overview_run == run.name
    assert manifest.repositories[0].repository_ref == "service"
    by_path = {artifact.relative_path: artifact for artifact in manifest.artifacts}
    assert by_path["routes/route-inventory.json"].kind == "canonical"
    assert by_path["synthesis-input.json"].kind == "index"
    assert manifest.providers[0].artifact_ids
    assert manifest.tools[0].tool_id == "staticcheck"
    assert spec.repos[0].repo_id not in manifest.repository_refs


def test_not_applicable_provider_without_feature_evidence_becomes_unknown(tmp_path):
    run, _ = _overview_run(tmp_path)
    execution = json.loads((run / "provider-execution.json").read_text(encoding="utf-8"))
    execution["executions"][0]["coverage"]["applicability"] = "not-applicable"
    _write_json(run / "provider-execution.json", execution)

    manifest = build_from_overview(run, snapshot_id="a" * 64)
    outcome = manifest.providers[0]
    assert outcome.coverage.applicability == "unknown"
    assert "positive feature-level evidence" in outcome.coverage.limitations[0]


def test_long_canonical_artifact_names_stay_inside_the_contract_id_limit(tmp_path):
    run, _ = _overview_run(tmp_path)
    _write_json(run / "routes" / ("x" * 100 + ".json"), {"schema_version": "routes/v1"})

    manifest = build_from_overview(run, snapshot_id="a" * 64)
    assert max(len(record.artifact_id) for record in manifest.artifacts) <= 64
