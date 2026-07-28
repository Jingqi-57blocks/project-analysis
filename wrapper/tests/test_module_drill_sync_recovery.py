"""Bounded synchronous semantic-recovery task tests for 57B-153."""

from __future__ import annotations

import json

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.frontier_candidates import write as write_candidates
from analysis_wrapper.module_drill.span_fetch import write as write_spans
from analysis_wrapper.module_drill.span_plan import write as write_plan
from analysis_wrapper.module_drill.sync_recovery import build_packet
from analysis_wrapper.module_drill.sync_recovery import finalize, register
from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.orchestrator.contracts import ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome
from analysis_wrapper.orchestrator.engine import now_iso
from analysis_wrapper.orchestrator.schemas import validate_output
from test_module_drill_frontier_candidates import _prepared


def _packet(tmp_path):
    module_run = _prepared(tmp_path)
    write_candidates(load(module_run))
    write_plan(load(module_run))
    write_spans(load(module_run))
    return module_run, build_packet(load(module_run))


def _output(packet):
    requirements = json.loads(packet.inputs["sync-requirements.json"].content)["requirements"]
    return {
        "dispositions": [{
            "requirement_id": row["requirement_id"],
            "outcome": "no-concern-observed",
            "claim_ids": [],
            "evidence_refs": row["evidence_refs"],
            "reason": "",
        } for row in requirements],
        "claims": [],
        "flows": [],
    }


def test_packet_uses_only_local_graph_and_plan_bound_semantic_spans(tmp_path):
    _, packet = _packet(tmp_path)
    assert packet.task_type == "module-sync-recovery"
    assert set(packet.inputs) == {"sync-requirements.json", "feature-graph.json", "semantic-spans.json"}
    requirements = json.loads(packet.inputs["sync-requirements.json"].content)["requirements"]
    assert requirements
    assert len({row["requirement_id"] for row in requirements}) == len(requirements)


def test_sync_output_requires_exact_requirement_dispositions(tmp_path):
    _, packet = _packet(tmp_path)
    inputs = {name: item.content for name, item in packet.inputs.items()}
    output = _output(packet)
    assert validate_output("module-sync-recovery", output, packet_inputs=inputs) == []

    output["dispositions"] = output["dispositions"][:-1]
    failures = validate_output("module-sync-recovery", output, packet_inputs=inputs)
    assert any(failure["check"] == "sync-disposition-missing" for failure in failures)


def test_sync_output_rejects_invented_claim_evidence_and_unknown_requirement(tmp_path):
    _, packet = _packet(tmp_path)
    inputs = {name: item.content for name, item in packet.inputs.items()}
    output = _output(packet)
    output["claims"] = [{
        "claim_id": "claim-authorization", "kind": "authorization",
        "anchor_ids": ["node-not-supplied"],
        "support": [{"ref": "service@NON-GIT:src/not-supplied.ts:1", "role": "authorization"}],
        "subject": "actor", "operation": "allows", "value": "action",
    }]
    output["dispositions"][0].update({"outcome": "claimed", "claim_ids": ["claim-authorization"]})
    output["dispositions"][1]["requirement_id"] = "requirement-not-supplied"
    failures = validate_output("module-sync-recovery", output, packet_inputs=inputs)
    checks = {failure["check"] for failure in failures}
    assert {"sync-claim-anchor", "sync-claim-support", "sync-disposition-id"} <= checks


def test_unresolved_semantic_span_cannot_be_disguised_as_a_clean_outcome(tmp_path):
    _, packet = _packet(tmp_path)
    requirements = json.loads(packet.inputs["sync-requirements.json"].content)
    span_requirement = next(row for row in requirements["requirements"] if row["kind"] == "semantic-span")
    span_requirement["span_status"] = "unresolved"
    inputs = {name: item.content for name, item in packet.inputs.items()}
    inputs["sync-requirements.json"] = json.dumps(requirements)
    output = _output(packet)
    output["dispositions"] = [{
        **row,
        "outcome": "no-concern-observed" if row["requirement_id"] == span_requirement["requirement_id"] else row["outcome"],
    } for row in output["dispositions"]]
    failures = validate_output("module-sync-recovery", output, packet_inputs=inputs)
    assert any(failure["check"] == "sync-span-unresolved" for failure in failures)


def test_sync_recovery_materializes_only_a_validated_current_packet(tmp_path):
    module_run, packet = _packet(tmp_path)
    driver = ModuleDriver(module_run)
    assert register(module_run) == ["module-sync-recovery"]
    claim = driver.claim(1, executor_kind="test", model="test-model")[0]
    at = now_iso()
    output = _output(packet)
    result = TaskResult(
        task_id="module-sync-recovery", status="ok", output=output,
        executor=ExecutorInfo(kind="test", model="test-model", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=claim.attempt,
    )
    assert driver.submit(claim.packet.task_id, result.to_dict())["status"] == "validated"
    document = json.loads(finalize(module_run).read_text())
    assert document["schema_version"] == "sync-recovery/v1"
    assert document["output"] == output


def test_cli_registers_the_bounded_sync_recovery_task(tmp_path, capsys):
    module_run, _ = _packet(tmp_path)
    assert main(["module-plan-sync-recovery", "--run", str(module_run)]) == 0
    assert json.loads(capsys.readouterr().out)["created"] == ["module-sync-recovery"]
