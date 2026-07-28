"""Bounded callgraph candidate tests for 57B-138."""

import json

import pytest

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.candidate_universe import write as write_candidates
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.feature_evidence import write as write_feature_evidence
from analysis_wrapper.module_drill.feature_graph import write as write_graph
from analysis_wrapper.module_drill.frontier_candidates import build, write
from analysis_wrapper.module_drill.frontier_receipts import write as write_receipts
from analysis_wrapper.module_drill.ranking import TASK_ID, build_packet, register as register_ranking
from analysis_wrapper.module_drill.selection import finalize
from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.module_drill.validation import ContractError
from analysis_wrapper.orchestrator.contracts import ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome
from analysis_wrapper.orchestrator.engine import now_iso
from test_module_drill_feature_evidence import _run


def _edge():
    return {
        "lang": "ts", "resolution": "observed", "kind": "static-call",
        "caller_symbol": "createRoute", "caller_citation": "service@NON-GIT:src/routes.ts:7",
        "callee_symbol": "createRecord", "callee_citation": "service@NON-GIT:src/service.ts:15",
        "callsite_citation": "service@NON-GIT:src/routes.ts:8",
    }


def _prepared(tmp_path, *, integration_path="src/service.ts", route_handler_anchors=None):
    _, module_run = _run(
        tmp_path,
        call_edges=[_edge()],
        integration_path=integration_path,
        route_handler_anchors=route_handler_anchors,
    )
    write_feature_evidence(load(module_run))
    write_candidates(load(module_run))
    driver = ModuleDriver(module_run)
    assert register_ranking(module_run) == [TASK_ID]
    claim = driver.claim(1, executor_kind="test", model="test-model")[0]
    rows = json.loads((module_run / "evidence" / "candidate-universe.json").read_text())["candidates"]
    candidate_id = next(row["candidate_id"] for row in rows if len(row["evidence_ids"]) == 2)
    now = now_iso()
    driver.submit(claim.packet.task_id, TaskResult(
        task_id=TASK_ID, status="ok",
        output={"decision": "selected", "candidate_ids": [candidate_id],
                "reason_code": "clear-dominant"},
        executor=ExecutorInfo(kind="test", model="test-model", params={}),
        timing=TaskTiming(started_at=now, finished_at=now, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=claim.attempt,
    ).to_dict())
    finalize(module_run)
    write_graph(load(module_run))
    write_receipts(load(module_run))
    return module_run


def test_candidates_expose_only_call_edges_adjacent_to_pending_route_handler(tmp_path):
    module_run = _prepared(tmp_path)
    document = build(load(module_run))
    assert len(document["candidates"]) == 1
    row = document["candidates"][0]
    assert row["edge_kind"] == "call"
    assert row["callee_symbol"] == "createRecord"
    assert row["observation"] == "observed"


def test_candidates_do_not_treat_same_file_adjacency_as_a_handler_link(tmp_path):
    unrelated = _edge()
    unrelated["callee_symbol"] = "unrelatedFunction"
    unrelated["callee_citation"] = "service@NON-GIT:src/service.ts:15"
    _, module_run = _run(tmp_path, call_edges=[unrelated])
    write_feature_evidence(load(module_run))
    write_candidates(load(module_run))
    driver = ModuleDriver(module_run)
    assert register_ranking(module_run) == [TASK_ID]
    claim = driver.claim(1, executor_kind="test", model="test-model")[0]
    rows = json.loads((module_run / "evidence" / "candidate-universe.json").read_text())["candidates"]
    candidate_id = next(row["candidate_id"] for row in rows if len(row["evidence_ids"]) == 2)
    now = now_iso()
    driver.submit(claim.packet.task_id, TaskResult(
        task_id=TASK_ID, status="ok",
        output={"decision": "selected", "candidate_ids": [candidate_id], "reason_code": "clear-dominant"},
        executor=ExecutorInfo(kind="test", model="test-model", params={}),
        timing=TaskTiming(started_at=now, finished_at=now, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=claim.attempt,
    ).to_dict())
    finalize(module_run)
    write_graph(load(module_run))
    write_receipts(load(module_run))

    assert build(load(module_run))["candidates"] == []


def test_candidates_refuse_changed_canonical_callgraph(tmp_path):
    module_run = _prepared(tmp_path)
    context = load(module_run)
    path = context.source_run / "callgraph" / "service.jsonl"
    path.write_text(json.dumps(_edge()) + "\n{}\n", encoding="utf-8")
    with pytest.raises(ContractError, match="digest changed"):
        build(load(module_run))


def test_cli_writes_frontier_candidates(tmp_path, capsys):
    module_run = _prepared(tmp_path)
    assert main(["module-build-frontier-candidates", "--run", str(module_run)]) == 0
    assert json.loads(capsys.readouterr().out)["frontier_candidates"].endswith("frontier-candidates.json")
