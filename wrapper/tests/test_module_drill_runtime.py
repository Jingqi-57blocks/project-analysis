"""Overview-backed Module Drill initialization tests for 57B-136."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_wrapper import identity, lifecycle, run_provenance
from analysis_wrapper.module_drill.runtime import initialize_from_overview
from analysis_wrapper.module_drill.validation import ContractError, sha256_json
from analysis_wrapper.module_drill.run_state import RunStateProjection
from test_module_drill_source_manifest import _overview_run


def _prepared_overview(tmp_path: Path) -> Path:
    run, spec = _overview_run(tmp_path)
    analyzer = tmp_path / "analyzer"
    analyzer.mkdir()
    project_id = identity.load(run).project.internal_id
    state = lifecycle.RunState.create("overview-source", project_id, spec, language="en")
    state.mark("discovery")
    state.mark("signals")
    state.save(run)
    run_provenance.write(run, run_provenance.create_document(
        spec, analyzer_root=analyzer, language="en", analyzed_at=state.analyzed_at))
    return run


def test_initialization_writes_an_isolated_incomplete_run_bound_to_source(tmp_path):
    source = _prepared_overview(tmp_path)
    result = initialize_from_overview(
        source, output_root=tmp_path / "exports", project_key="workspace",
        selector="create record", language="zh-CN", run_label="module_run")

    assert result.run_dir.is_relative_to(tmp_path / "exports")
    assert not result.run_dir.is_relative_to(tmp_path / "workspace")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    state = RunStateProjection.from_dict(json.loads(result.state_path.read_text(encoding="utf-8")))
    assert manifest["source_overview_run"] == "overview-source"
    assert state.complete is False
    assert state.source_manifest_digest == sha256_json(manifest)
    assert "module_run" in result.run_id


def test_initialization_refuses_output_inside_analyzed_repository(tmp_path):
    source = _prepared_overview(tmp_path)
    with pytest.raises(ContractError, match="outside analyzed repositories"):
        initialize_from_overview(
            source, output_root=tmp_path / "workspace" / "service" / "analysis-output",
            project_key="workspace", selector="create record", language="en")


def test_initialization_refuses_an_escaping_project_key(tmp_path):
    source = _prepared_overview(tmp_path)
    with pytest.raises(ContractError, match="safe output-path segment"):
        initialize_from_overview(
            source, output_root=tmp_path / "exports", project_key="../escape",
            selector="create record", language="en")


def test_initialization_refuses_a_stale_source_snapshot(tmp_path):
    source = _prepared_overview(tmp_path)
    (tmp_path / "workspace" / "service" / "changed.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ContractError, match="source is stale"):
        initialize_from_overview(
            source, output_root=tmp_path / "exports", project_key="workspace",
            selector="create record", language="en")
