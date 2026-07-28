"""Bounded asynchronous semantic-recovery task tests for 57B-144."""

from __future__ import annotations

import json

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.async_recovery import INPUT_BUDGET_TOKENS, _packet_inputs, build_packet, finalize, register
from analysis_wrapper.module_drill.boundary_closure import write as write_boundary_closure
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.orchestrator.contracts import ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome
from analysis_wrapper.orchestrator.engine import now_iso
from analysis_wrapper.orchestrator.composer import estimate_tokens
from analysis_wrapper.orchestrator.schemas import validate_output
from test_module_drill_boundary_closure import _ready


def _packet(tmp_path):
    module_run = _ready(tmp_path)
    write_boundary_closure(load(module_run))
    return module_run, build_packet(load(module_run))


def _output(packet):
    requirements = json.loads(packet.inputs["async-requirements.json"].content)["requirements"]
    return {
        "dispositions": [{
            "requirement_id": row["requirement_id"], "outcome": "no-concern-observed",
            "claim_ids": [], "evidence_refs": row["evidence_refs"], "reason": "",
        } for row in requirements],
        "claims": [], "flows": [],
    }


def test_packet_uses_only_span_bound_boundary_closure_and_semantic_spans(tmp_path):
    _, packet = _packet(tmp_path)
    assert packet.task_type == "module-async-recovery"
    assert set(packet.inputs) == {
        "async-requirements.json", "feature-boundary-closure.json", "semantic-spans.json"}
    requirements = json.loads(packet.inputs["async-requirements.json"].content)["requirements"]
    assert requirements
    assert len({row["requirement_id"] for row in requirements}) == len(requirements)
    local_closure = json.loads(packet.inputs["feature-boundary-closure.json"].content)
    expected_ids = {row["evidence_id"] for row in requirements}
    assert {row["evidence_id"] for row in local_closure["boundary_dispositions"]} == expected_ids
    assert all(row["state"] != "excluded" for row in local_closure["boundary_dispositions"])
    assert estimate_tokens(packet.instructions) + sum(
        estimate_tokens(item.content) for item in packet.inputs.values()) <= INPUT_BUDGET_TOKENS


def test_empty_applicable_boundary_universe_is_an_auditable_empty_task():
    """All-excluded boundaries must not prevent final feature recovery."""
    closure = {
        "schema_version": "feature-boundary-closure/v1",
        "source_manifest_digest": "digest", "feature_id": "feature",
        "semantic_spans_digest": "digest", "nodes": [], "edges": [],
        "boundary_dispositions": [{
            "evidence_id": "evidence-excluded", "state": "excluded",
            "evidence_refs": ["repo@NON-GIT:src/example.ts:1"],
        }],
    }
    spans = {
        "schema_version": "semantic-spans/v1", "source_manifest_digest": "digest",
        "feature_id": "feature", "feature_graph_digest": "digest",
        "semantic_span_plan_digest": "digest", "frontier_candidates_digest": "digest", "spans": [],
    }
    requirements = {
        "schema_version": "module-async-recovery-requirements/v1",
        "feature_boundary_closure_digest": "digest", "feature_id": "feature", "requirements": [],
    }
    inputs = _packet_inputs(closure, spans, requirements)
    assert json.loads(inputs["async-requirements.json"])["requirements"] == []
    assert json.loads(inputs["feature-boundary-closure.json"])["boundary_dispositions"] == []
    assert json.loads(inputs["semantic-spans.json"])["spans"] == []


def test_async_output_requires_exact_dispositions_and_packet_local_claims(tmp_path):
    _, packet = _packet(tmp_path)
    inputs = {name: item.content for name, item in packet.inputs.items()}
    output = _output(packet)
    assert validate_output("module-async-recovery", output, packet_inputs=inputs) == []

    output["dispositions"] = output["dispositions"][:-1]
    failures = validate_output("module-async-recovery", output, packet_inputs=inputs)
    assert any(failure["check"] == "async-disposition-missing" for failure in failures)

    output = _output(packet)
    output["claims"] = [{
        "claim_id": "claim-integration", "kind": "integration",
        "anchor_ids": ["node-not-supplied"],
        "support": [{"ref": "service@NON-GIT:src/not-supplied.ts:1", "role": "integration"}],
        "subject": "remote", "operation": "invokes", "value": "remote",
    }]
    output["dispositions"][0].update({"outcome": "claimed", "claim_ids": ["claim-integration"]})
    failures = validate_output("module-async-recovery", output, packet_inputs=inputs)
    checks = {failure["check"] for failure in failures}
    assert {"async-claim-anchor", "async-claim-support"} <= checks


def test_unresolved_boundary_cannot_be_disguised_as_clean_outcome(tmp_path):
    _, packet = _packet(tmp_path)
    requirements = json.loads(packet.inputs["async-requirements.json"].content)
    requirements["requirements"][0]["boundary_state"] = "unresolved"
    inputs = {name: item.content for name, item in packet.inputs.items()}
    inputs["async-requirements.json"] = json.dumps(requirements)
    failures = validate_output("module-async-recovery", _output(packet), packet_inputs=inputs)
    assert any(failure["check"] == "async-boundary-unresolved" for failure in failures)


def test_async_recovery_materializes_only_a_validated_current_packet(tmp_path):
    module_run, packet = _packet(tmp_path)
    driver = ModuleDriver(module_run)
    assert register(module_run) == ["module-async-recovery"]
    claim = driver.claim(1, executor_kind="test", model="test-model")[0]
    at = now_iso()
    output = _output(packet)
    result = TaskResult(
        task_id="module-async-recovery", status="ok", output=output,
        executor=ExecutorInfo(kind="test", model="test-model", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=claim.attempt,
    )
    assert driver.submit(claim.packet.task_id, result.to_dict())["status"] == "validated"
    document = json.loads(finalize(module_run).read_text())
    assert document["schema_version"] == "async-recovery/v1"
    assert document["output"] == output


def test_cli_registers_the_bounded_async_recovery_task(tmp_path, capsys):
    module_run, _ = _packet(tmp_path)
    assert main(["module-plan-async-recovery", "--run", str(module_run)]) == 0
    assert json.loads(capsys.readouterr().out)["created"] == ["module-async-recovery"]
