"""Module Drill task-driver tests for 57B-136."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.module_drill.runtime import initialize_from_overview
from analysis_wrapper.module_drill.validation import ContractError, sha256_json
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo,
    TaskPacket,
    TaskResult,
    TaskTiming,
    ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import now_iso
from test_module_drill_runtime import _prepared_overview


def _driver(tmp_path: Path) -> ModuleDriver:
    overview = _prepared_overview(tmp_path, {"src/create.ts": "export const create = () => true;\n"})
    initialized = initialize_from_overview(
        overview, output_root=tmp_path / "module-output", project_key="workspace",
        selector="create record", language="en", run_label="driver-test")
    return ModuleDriver(initialized.run_dir)


def _packet(*, task_type: str = "module-candidate-ranking",
            schema_id: str = "module-candidate-ranking/v2") -> TaskPacket:
    return TaskPacket.create(
        task_id="candidate-ranking", task_type=task_type,
        template_id="module-candidate-ranking", template_version="v1",
        instructions="Select an existing feature candidate only.",
        inputs={"candidates": "candidate-a: observed seed"}, output_schema_id=schema_id,
        context_budget_tokens=500,
    )


def _result(task_id: str, attempt: int) -> dict:
    at = now_iso()
    return TaskResult(
        task_id=task_id, status="ok",
        output={
            "decision": "selected", "candidate_ids": ["candidate-a"],
            "reason_code": "clear-dominant",
        },
        executor=ExecutorInfo(kind="test", model="test-model", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=attempt,
    ).to_dict()


def test_driver_registers_claims_and_submits_through_shared_ledger(tmp_path):
    driver = _driver(tmp_path)

    assert driver.register([_packet()]) == ["candidate-ranking"]
    created = driver.status()
    assert created.task_states == {"candidate-ranking": "pending"}
    assert created.complete is False
    assert "pending-final-module-audit" in created.audit.failed_checks

    claim = driver.claim(1, executor_kind="host", model="gpt-test")[0]
    outcome = driver.submit(claim.packet.task_id, _result(claim.packet.task_id, claim.attempt))
    resumed = ModuleDriver(driver.run).status()

    assert outcome["status"] == "validated"
    assert resumed.task_states == {"candidate-ranking": "validated"}
    state = json.loads((driver.run / "run-state.json").read_text(encoding="utf-8"))
    assert state["ledger_digest"] != sha256_json([])
    assert state["complete"] is False


def test_driver_refuses_foreign_tasks_and_wrong_module_schema(tmp_path):
    driver = _driver(tmp_path)
    with pytest.raises(ContractError, match="non-module task type"):
        driver.register([_packet(task_type="lens-findings", schema_id="lens-findings.v1")])
    with pytest.raises(ContractError, match="must use"):
        driver.register([_packet(schema_id="wrong/v1")])
    assert driver.status().task_states == {}


def test_driver_refuses_a_stale_source_before_state_changes(tmp_path):
    driver = _driver(tmp_path)
    source = tmp_path / "workspace" / "service" / "src" / "create.ts"
    source.write_text("export const create = () => false;\n", encoding="utf-8")

    with pytest.raises(ContractError, match="source snapshot is stale"):
        driver.register([_packet()])
    assert not driver.engine.ledger_exists()
