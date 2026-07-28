"""Authoritative ModuleModel finalization and fail-closed audit tests."""

from __future__ import annotations

import json

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.async_recovery import build_packet as async_packet
from analysis_wrapper.module_drill.async_recovery import finalize as finalize_async, register as register_async
from analysis_wrapper.module_drill.boundary_closure import write as write_boundary_closure
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.module_drill.finalize import finalize
from analysis_wrapper.module_drill.frontier_candidates import write as write_candidates
from analysis_wrapper.module_drill.graph_closure import write as write_graph_closure
from analysis_wrapper.module_drill.span_fetch import write as write_spans
from analysis_wrapper.module_drill.span_plan import write as write_plan
from analysis_wrapper.module_drill.sync_recovery import build_packets as sync_packets
from analysis_wrapper.module_drill.sync_recovery import finalize as finalize_sync, register as register_sync
from analysis_wrapper.orchestrator.contracts import ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome
from analysis_wrapper.orchestrator.engine import now_iso
from test_module_drill_frontier_candidates import _prepared


def _no_concern(packet, name):
    requirements = json.loads(packet.inputs[name].content)["requirements"]
    return {"dispositions": [
        {"requirement_id": row["requirement_id"], "outcome": "no-concern-observed",
         "claim_ids": [], "evidence_refs": row["evidence_refs"], "reason": ""}
        for row in requirements], "claims": [], "flows": []}


def _submit(driver, packet, output):
    claim = driver.claim(1, executor_kind="test", model="test-model")[0]
    now = now_iso()
    result = TaskResult(
        task_id=packet.task_id, status="ok", output=output,
        executor=ExecutorInfo(kind="test", model="test-model", params={}),
        timing=TaskTiming(started_at=now, finished_at=now, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=claim.attempt,
    )
    assert driver.submit(packet.task_id, result.to_dict())["status"] == "validated"


def _ready(tmp_path, *, sync_outcome="no-concern-observed"):
    module_run = _prepared(tmp_path)
    write_candidates(load(module_run))
    write_graph_closure(load(module_run))
    write_plan(load(module_run))
    write_spans(load(module_run))
    write_boundary_closure(load(module_run))
    driver = ModuleDriver(module_run)
    sync = sync_packets(load(module_run))
    register_sync(module_run)
    expected_sync = {packet.task_id: packet for packet in sync}
    for _ in sync:
        claim = driver.claim(1, executor_kind="test", model="test-model")[0]
        packet = expected_sync[claim.packet.task_id]
        now = now_iso()
        output = _no_concern(packet, "sync-requirements.json")
        if sync_outcome != "no-concern-observed":
            output["dispositions"][0]["outcome"] = sync_outcome
            output["dispositions"][0]["reason"] = "source span could not establish the required behaviour"
        result = TaskResult(
            task_id=packet.task_id, status="ok", output=output,
            executor=ExecutorInfo(kind="test", model="test-model", params={}),
            timing=TaskTiming(started_at=now, finished_at=now, wall_clock_s=0.0),
            tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=claim.attempt,
        )
        assert driver.submit(packet.task_id, result.to_dict())["status"] == "validated"
    finalize_sync(module_run)
    async_task = async_packet(load(module_run))
    register_async(module_run)
    _submit(driver, async_task, _no_concern(async_task, "async-requirements.json"))
    finalize_async(module_run)
    return module_run


def test_finalization_merges_current_validated_artifacts_and_completes_projection(tmp_path):
    module_run = _ready(tmp_path)
    model_path, audit = finalize(module_run)
    assert audit.passed
    assert model_path is not None
    document = json.loads(model_path.read_text())
    assert document["schema_version"] == "module-model-artifact/v1"
    assert document["model"]["closure_status"] == "closed"
    state = json.loads((module_run / "run-state.json").read_text())
    assert state["complete"] is True and state["audit"]["passed"] is True


def test_missing_mandatory_async_output_fails_closed_without_module_model(tmp_path):
    module_run = _prepared(tmp_path)
    write_candidates(load(module_run))
    write_graph_closure(load(module_run))
    write_plan(load(module_run))
    write_spans(load(module_run))
    write_boundary_closure(load(module_run))
    model_path, audit = finalize(module_run)
    assert model_path is None and not audit.passed
    state = json.loads((module_run / "run-state.json").read_text())
    assert state["complete"] is False
    assert not (module_run / "evidence" / "module-model.json").exists()


def test_unresolved_mandatory_frontier_fails_closed_without_module_model(tmp_path):
    module_run = _ready(tmp_path)
    closure_path = module_run / "evidence" / "feature-graph-closure.json"
    closure = json.loads(closure_path.read_text())
    closure["frontier_dispositions"][0]["state"] = "unresolved"
    closure_path.write_text(json.dumps(closure), encoding="utf-8")

    model_path, audit = finalize(module_run)

    assert model_path is None and not audit.passed
    assert "mandatory feature frontiers" in audit.failed_checks[0]


def test_tampered_sync_partition_receipt_fails_closed_without_module_model(tmp_path):
    module_run = _ready(tmp_path)
    sync_path = module_run / "evidence" / "sync-recovery.json"
    sync = json.loads(sync_path.read_text())
    sync["tasks"][0]["partition"]["requirement_ids"] = ["requirement-invented"]
    sync_path.write_text(json.dumps(sync), encoding="utf-8")

    model_path, audit = finalize(module_run)

    assert model_path is None and not audit.passed
    assert "partition receipt" in audit.failed_checks[0]


def test_incomplete_mandatory_provider_coverage_fails_closed(tmp_path):
    module_run = _ready(tmp_path, sync_outcome="unknown")

    model_path, audit = finalize(module_run)

    assert model_path is None and not audit.passed
    assert "mandatory feature dimensions are incomplete: synchronous-behavior" in audit.failed_checks[0]


def test_cli_returns_nonzero_when_final_audit_fails(tmp_path, capsys):
    module_run = _prepared(tmp_path)
    assert main(["module-finalize-model", "--run", str(module_run)]) == 3
    assert json.loads(capsys.readouterr().out)["audit"]["passed"] is False
