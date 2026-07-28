"""Bounded selector-ranking task tests for 57B-137."""

from __future__ import annotations

import json

import pytest

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.candidate_universe import write as write_candidates
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.module_drill.feature_evidence import write as write_feature_evidence
from analysis_wrapper.module_drill.ranking import TASK_ID, build_packet
from analysis_wrapper.module_drill.scope import ModuleScope
from analysis_wrapper.module_drill.selection import finalize
from analysis_wrapper.module_drill.validation import ContractError
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import now_iso
from analysis_wrapper.orchestrator.schemas import validate_output
from test_module_drill_feature_evidence import _run


def _prepared_run(tmp_path):
    _, module_run = _run(tmp_path)
    context = load(module_run)
    write_feature_evidence(context)
    write_candidates(load(module_run))
    return module_run


def _result(task_id, attempt, output):
    at = now_iso()
    return TaskResult(
        task_id=task_id, status="ok", output=output,
        executor=ExecutorInfo(kind="test", model="test-model", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=attempt,
    ).to_dict()


def _validated_ranking(module_run, output):
    driver = ModuleDriver(module_run)
    assert driver.register((build_packet(driver.context),)) == [TASK_ID]
    claim = driver.claim(1, executor_kind="test", model="test-model")[0]
    outcome = driver.submit(claim.packet.task_id, _result(claim.packet.task_id, claim.attempt, output))
    assert outcome["status"] == "validated"
    return driver


def test_ranking_packet_binds_selector_to_complete_candidate_evidence(tmp_path):
    module_run = _prepared_run(tmp_path)
    packet = build_packet(load(module_run))

    assert packet.task_id == TASK_ID
    assert json.loads(packet.inputs["selector.json"].content) == {"selector": "record"}
    universe = json.loads(packet.inputs["candidate-universe.json"].content)
    evidence = json.loads(packet.inputs["candidate-evidence.json"].content)
    assert {row["candidate_id"] for row in evidence} == {
        row["candidate_id"] for row in universe["candidates"]
    }
    assert all(row["evidence"] for row in evidence)


def test_ranking_packet_keeps_every_candidate_when_route_inventory_exceeds_overview_caps(tmp_path):
    _, module_run = _run(tmp_path, route_count=205)
    write_feature_evidence(load(module_run))
    write_candidates(load(module_run))
    packet = build_packet(load(module_run))
    universe = json.loads(packet.inputs["candidate-universe.json"].content)
    evidence = json.loads(packet.inputs["candidate-evidence.json"].content)
    assert len(universe["candidates"]) > 200
    assert len(evidence) == len(universe["candidates"])


def test_ranking_rejects_a_tampered_deterministic_candidate_universe(tmp_path):
    module_run = _prepared_run(tmp_path)
    path = module_run / "evidence" / "candidate-universe.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["candidates"][0]["reason"] = "tampered"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractError, match="differs from deterministic"):
        build_packet(load(module_run))


def test_module_driver_rejects_candidate_ids_outside_supplied_universe(tmp_path):
    module_run = _prepared_run(tmp_path)
    driver = ModuleDriver(module_run)
    assert driver.register((build_packet(driver.context),)) == [TASK_ID]
    claim = driver.claim(1, executor_kind="test", model="test-model")[0]
    outcome = driver.submit(claim.packet.task_id, _result(
        claim.packet.task_id, claim.attempt,
        {"decision": "selected", "candidate_ids": ["candidate-invented"],
         "selected_candidate_id": "candidate-invented", "reason_code": "clear-dominant"},
    ))
    assert outcome["status"] == "failed"
    assert outcome["failures"][0]["check"] == "ranking-candidate-universe"


def test_selected_ranking_materializes_one_open_scope_from_canonical_evidence(tmp_path):
    module_run = _prepared_run(tmp_path)
    candidate_id = json.loads((module_run / "evidence" / "candidate-universe.json").read_text())["candidates"][0]["candidate_id"]
    _validated_ranking(module_run, {
        "decision": "selected", "candidate_ids": [candidate_id],
        "selected_candidate_id": candidate_id, "reason_code": "clear-dominant",
    })

    result = finalize(module_run)
    assert result.decision == "selected"
    assert result.scope_path is not None
    scope = ModuleScope.from_dict(json.loads(result.scope_path.read_text(encoding="utf-8")))
    assert scope.selected_candidate_id == candidate_id
    assert scope.closure_status == "open"
    assert {candidate.disposition for candidate in scope.candidates} >= {"selected", "alternative"}


def test_ambiguous_ranking_never_materializes_a_scope(tmp_path):
    module_run = _prepared_run(tmp_path)
    candidates = json.loads((module_run / "evidence" / "candidate-universe.json").read_text())["candidates"]
    _validated_ranking(module_run, {
        "decision": "ambiguous", "candidate_ids": [candidates[0]["candidate_id"], candidates[1]["candidate_id"]],
        "selected_candidate_id": None, "reason_code": "equally-supported",
    })

    result = finalize(module_run)
    assert result.decision == "ambiguous"
    assert result.scope_path is None
    assert result.resolution_path.is_file()
    assert not (module_run / "evidence" / "module-scope.json").exists()


def test_cli_finalizes_a_validated_selected_ranking(tmp_path, capsys):
    module_run = _prepared_run(tmp_path)
    candidate_id = json.loads((module_run / "evidence" / "candidate-universe.json").read_text())["candidates"][0]["candidate_id"]
    _validated_ranking(module_run, {
        "decision": "selected", "candidate_ids": [candidate_id],
        "selected_candidate_id": candidate_id, "reason_code": "clear-dominant",
    })
    assert main(["module-finalize-ranking", "--run", str(module_run)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["decision"] == "selected"
    assert printed["scope"].endswith("module-scope.json")


def test_ranking_schema_requires_explicit_unresolved_decisions():
    assert validate_output("module-candidate-ranking", {
        "decision": "ambiguous", "candidate_ids": ["candidate-a", "candidate-b"],
        "selected_candidate_id": None, "reason_code": "equally-supported",
    }) == []
    assert validate_output("module-candidate-ranking", {
        "decision": "no-match", "candidate_ids": [],
        "selected_candidate_id": None, "reason_code": "insufficient-evidence",
    }) == []
    failures = validate_output("module-candidate-ranking", {
        "decision": "ambiguous", "candidate_ids": ["candidate-a"],
        "selected_candidate_id": None, "reason_code": "equally-supported",
    })
    assert failures[0]["check"] == "ranking-ambiguous-shape"


def test_cli_registers_ranking_task_only_after_canonical_inputs_exist(tmp_path, capsys):
    module_run = _prepared_run(tmp_path)
    assert main(["module-plan-ranking", "--run", str(module_run)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["created"] == [TASK_ID]
    assert printed["next"] == "claim module-candidate-ranking"
