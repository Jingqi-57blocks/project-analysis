"""57B-152 frozen-input and executor-provenance acceptance contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_wrapper import acceptance, identity, run_provenance
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskPacket, TaskResult, TaskTiming, TokenUsage, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id


def _write(run: Path, relative: str, value: dict) -> None:
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", "utf-8")


def _prepared_run(run: Path, repo: Path, analyzer: Path) -> tuple[Path, TargetSpec]:
    run.mkdir(parents=True)
    target = RepoTarget(repo_id=stable_repo_id(str(repo)), path=str(repo))
    spec = TargetSpec([target])
    spec.save(run / "targets.json")
    identities = identity.build(spec, workspace_root=repo.parent,
                                project_id=stable_repo_id(str(repo.parent)))
    identity.write_mapping(run, identities)
    _write(run, "discovery-report.json", {"project_ref": identities.project.reference,
                                           "repos": []})
    provenance = run_provenance.create_document(
        spec, analyzer_root=analyzer, language="en", model="", effort="",
        analyzed_at="2026-07-28T00:00:00+00:00")
    provenance["preparation"] = {
        "scan_date": "2026-07-28", "history_since": "2024-07-28",
        "coupling_sample_cap": 0, "network_authorized": False, "allowed_hosts": [],
    }
    provenance["tool_versions"] = [{"tool": "generic-tool", "version": "1",
                                    "version_drift": "", "sources": ["signals/x"]}]
    run_provenance.write(run, provenance)
    _write(run, "signals/run-summary.json", {
        "schema_version": "3.0.0", "aggregate_status": "complete", "signals": []})
    _write(run, "provider-execution.json", {
        "schema_version": "1", "executions": [{
            "provider_id": "generic-provider", "capability_id": "generic-capability",
            "repository_ref": identities.reference_for(target.repo_id), "matched_profiles": [],
            "outcome": "completed", "coverage": {"applicability": "applicable",
            "status": "complete", "reason_code": "ok"}, "tools": []}]})
    _write(run, "capabilities.json", {"aggregate_status": "complete", "capabilities": [{
        "capability_id": "generic-capability", "applicable": True, "status": "complete",
        "reason": "", "expected_artifacts": [], "observed_artifacts": [],
        "missing_artifacts": []}]})
    _write(run, "system-model.json", {"nodes": [], "edges": [], "coverage": {}})
    _write(run, "module-candidates.json", {"candidates": []})
    _write(run, "synthesis-input.json", {"repositories": {"items": []}})
    _write(run, "tasks/lens-semantic-partitions.json", {"schema_version": 1, "plans": []})
    packet = TaskPacket.create(
        task_id="lens-generic", task_type="lens-findings", template_id="generic",
        template_version="v1", instructions="return findings", inputs={
            "semantic-partition.json": json.dumps({"plan_id": "plan-1",
                                                     "partition_id": "repo-a",
                                                     "kind": "repository",
                                                     "repository_ref": "repo",
                                                     "active": True})},
        output_schema_id="lens-findings.v1", context_budget_tokens=24000)
    Engine(run).create_tasks([packet])
    return run, spec


def _submit_recorded_api_task(run: Path) -> None:
    engine = Engine(run)
    claimed = engine.claim(1, executor_kind="openai-compatible", model="model-a",
                           params={"temperature": 0.2, "effort": "medium"})[0]
    result = TaskResult(
        task_id=claimed.packet.task_id, status="ok",
        output={"findings": [], "coverage": []},
        executor=ExecutorInfo(kind="openai-compatible", model="model-a", params={
            "temperature": 0.2, "effort": "medium", "request_wall_clock_s": 0.4}),
        timing=TaskTiming(started_at="2026-07-28T00:00:01Z",
                          finished_at="2026-07-28T00:00:02Z", wall_clock_s=1.0),
        tokens=TokenUsage(input=10, cached_input=3, output=5),
        validation=ValidationOutcome(passed=True, failures=()), attempt=claimed.attempt)
    assert engine.submit(claimed.packet.task_id, result.to_dict())["status"] == "validated"


def test_freeze_is_immutable_and_never_serializes_target_paths(tmp_path):
    repo = tmp_path / "workspace" / "repo"
    repo.mkdir(parents=True)
    analyzer = tmp_path / "analyzer"
    analyzer.mkdir()
    run, _ = _prepared_run(tmp_path / "run", repo, analyzer)

    path = acceptance.freeze(run)
    document = acceptance.load_manifest(run)
    assert path.name == "acceptance-manifest.json"
    assert document["task_packets"][0]["input_digest"]
    assert document["semantic_partition_plan"]["status"] == "recorded"
    assert str(repo) not in path.read_text("utf-8")
    assert acceptance.freeze(run) == path

    _write(run, "synthesis-input.json", {"repositories": {"items": ["changed"]}})
    with pytest.raises(ValueError, match="inputs changed after freezing"):
        acceptance.freeze(run)


def test_execution_provenance_records_real_api_fields_and_marks_host_gaps(tmp_path):
    repo = tmp_path / "workspace" / "repo"
    repo.mkdir(parents=True)
    analyzer = tmp_path / "analyzer"
    analyzer.mkdir()
    run, _ = _prepared_run(tmp_path / "run", repo, analyzer)
    acceptance.freeze(run)
    _submit_recorded_api_task(run)

    document = acceptance.build_execution_provenance(run)
    task = document["tasks"][0]
    assert document["summary"]["model_ab_ready"] is True
    assert task["model"] == {"status": "recorded", "value": "model-a", "reason": ""}
    assert task["tokens"]["cached_input"]["value"] == 3
    assert task["semantic_partition"]["value"]["partition_id"] == "repo-a"

    # A never-claimed task has a specific protocol limitation, not invented
    # model/token/timing values.
    second = tmp_path / "second"
    _prepared_run(second, repo, analyzer)
    unrecorded = acceptance.build_execution_provenance(second)["tasks"][0]
    assert unrecorded["model"]["status"] == "unavailable"
    assert "actual model identifier" in unrecorded["model"]["reason"]


def test_compare_classifies_packet_change_as_pipeline_not_model_difference(tmp_path):
    repo = tmp_path / "workspace" / "repo"
    repo.mkdir(parents=True)
    analyzer = tmp_path / "analyzer"
    analyzer.mkdir()
    left, _ = _prepared_run(tmp_path / "left", repo, analyzer)
    right, _ = _prepared_run(tmp_path / "right", repo, analyzer)
    acceptance.freeze(left)
    _write(right, "synthesis-input.json", {"repositories": {"items": ["different"]}})
    acceptance.freeze(right)
    _submit_recorded_api_task(left)
    _submit_recorded_api_task(right)
    acceptance.write_execution_provenance(left)
    acceptance.write_execution_provenance(right)

    report = acceptance.compare(left, right)
    assert report["classification"] == "input-pipeline-difference"
    assert {row["field"] for row in report["input_differences"]} >= {"artifact_digests"}


def test_new_packet_after_freeze_disqualifies_model_only_comparison(tmp_path):
    repo = tmp_path / "workspace" / "repo"
    repo.mkdir(parents=True)
    analyzer = tmp_path / "analyzer"
    analyzer.mkdir()
    run, _ = _prepared_run(tmp_path / "run", repo, analyzer)
    acceptance.freeze(run)
    Engine(run).create_tasks([TaskPacket.create(
        task_id="later-packet", task_type="lens-findings", template_id="generic",
        template_version="v1", instructions="later", inputs={"x": "y"},
        output_schema_id="lens-findings.v1", context_budget_tokens=24000)])

    document = acceptance.build_execution_provenance(run)
    assert document["summary"]["model_ab_ready"] is False
    assert document["summary"]["frozen_packet_mismatches"] == ["later-packet"]
