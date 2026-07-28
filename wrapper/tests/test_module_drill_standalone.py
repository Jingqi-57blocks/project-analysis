"""Standalone deterministic-source lifecycle tests for 57B-136."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.module_drill.spans import fetch
from analysis_wrapper.module_drill.standalone import initialize
from analysis_wrapper.module_drill.validation import ContractError


def _prepared_evidence(args) -> int:
    run = Path(args.run)
    (run / "routes").mkdir(exist_ok=True)
    (run / "routes" / "route-inventory.json").write_text(
        '{"schema_version":"routes/v1"}\n', encoding="utf-8")
    (run / "provider-execution.json").write_text(json.dumps({
        "executions": [{
            "provider_id": "route-provider", "capability_id": "route-linkage",
            "repository_ref": "service", "outcome": "completed", "reason": "",
            "coverage": {"applicability": "applicable", "status": "complete"},
            "tools": [],
        }],
    }), encoding="utf-8")
    state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
    state["stages"]["signals"] = "done"
    (run / "run-state.json").write_text(json.dumps(state), encoding="utf-8")
    return 0


def test_standalone_initialization_builds_a_module_owned_evidence_snapshot(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    service = workspace / "service"
    service.mkdir(parents=True)
    (service / "package.json").write_text('{"name":"service"}', encoding="utf-8")
    (service / "src.ts").write_text("export const create = () => true;\n", encoding="utf-8")
    monkeypatch.setattr("analysis_wrapper.module_drill.standalone.prepare_deterministic_evidence",
                        _prepared_evidence)

    result = initialize(
        workspace, output_root=tmp_path / "output", project_key="workspace",
        selector="create record", language="en", run_label="direct-module")

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_mode"] == "standalone"
    assert manifest["source_overview_run"] is None
    assert result.evidence_dir.is_relative_to(result.run_dir)
    assert (result.evidence_dir / "targets.json").is_file()
    assert not (tmp_path / "output" / "workspace" / "overview").exists()
    assert ModuleDriver(result.run_dir).status().complete is False
    rows = json.loads(fetch(result.run_dir, [{
        "span_id": "create", "kind": "declaration",
        "ref": "service@NON-GIT:src.ts:1", "purpose": "read the direct entry",
    }]).read_text(encoding="utf-8"))
    assert rows[0]["status"] == "fetched"
    assert "create" in rows[0]["content"]

    (service / "src.ts").write_text("export const changed = () => false;\n", encoding="utf-8")
    with pytest.raises(ContractError, match="source snapshot is stale"):
        ModuleDriver(result.run_dir).status()


def test_standalone_refuses_analyzed_output_and_cleans_failed_initialization(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    service = workspace / "service"
    service.mkdir(parents=True)
    (service / "package.json").write_text('{"name":"service"}', encoding="utf-8")
    monkeypatch.setattr("analysis_wrapper.module_drill.standalone.prepare_deterministic_evidence",
                        lambda _args: 3)

    with pytest.raises(ContractError, match="outside analyzed repositories"):
        initialize(workspace, output_root=service / "output", project_key="workspace",
                   selector="create", language="en")
    with pytest.raises(ContractError, match="preparation failed"):
        initialize(workspace, output_root=tmp_path / "output", project_key="workspace",
                   selector="create", language="en", run_label="failed")
    module_root = tmp_path / "output" / "workspace" / "modules"
    assert not module_root.exists() or not any(module_root.iterdir())
