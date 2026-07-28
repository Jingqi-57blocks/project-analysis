"""Observed structural feature-graph tests for 57B-138."""

import json

import pytest

from analysis_wrapper.module_drill.candidate_universe import write as write_candidates
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.feature_evidence import write as write_feature_evidence
from analysis_wrapper.module_drill.feature_graph import _node, build, write
from analysis_wrapper.module_drill.ranking import TASK_ID, build_packet, register as register_ranking
from analysis_wrapper.module_drill.selection import finalize
from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.module_drill.validation import ContractError
from analysis_wrapper.cli import main
from analysis_wrapper.orchestrator.contracts import ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome
from analysis_wrapper.orchestrator.engine import now_iso
from test_module_drill_feature_evidence import _run


def _prepared_scope(tmp_path, **kwargs):
    _, module_run = _run(tmp_path, **kwargs)
    write_feature_evidence(load(module_run))
    write_candidates(load(module_run))
    driver = ModuleDriver(module_run)
    assert register_ranking(module_run) == [TASK_ID]
    claim = driver.claim(1, executor_kind="test", model="test-model")[0]
    candidates = json.loads((module_run / "evidence" / "candidate-universe.json").read_text())["candidates"]
    candidate_id = next(row["candidate_id"] for row in candidates if len(row["evidence_ids"]) == 2)
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
    return module_run


def test_graph_contains_only_selected_observed_anchors_and_exact_ui_route_edge(tmp_path):
    module_run = _prepared_scope(tmp_path)
    graph = build(load(module_run))
    assert graph["schema_version"] == "feature-graph/v1"
    assert {node["kind"] for node in graph["nodes"]} == {"route", "ui-action"}
    assert [edge["kind"] for edge in graph["edges"]] == ["ui-route"]
    assert graph["frontiers"]
    assert all(row["wave"] == 0 for row in graph["frontiers"])


def test_ui_action_node_retains_frontend_local_evidence_not_backend_link_evidence():
    node = _node({
        "evidence_id": "evidence-ui", "kind": "ui-action", "repository_refs": ["web", "api"],
        "source_refs": ["api@NON-GIT:internal/routes.go:9", "web@NON-GIT:src/submit.ts:4"],
        "data": {"frontend_source_refs": ["web@NON-GIT:src/submit.ts:4"],
                 "backend_source_refs": ["api@NON-GIT:internal/routes.go:9"]},
    })
    assert node.repository_ref == "web"
    assert node.evidence_refs == ("web@NON-GIT:src/submit.ts:4",)


def test_graph_adds_a_route_to_exact_source_resolved_handler_only(tmp_path):
    module_run = _prepared_scope(tmp_path, route_handler_anchors=[{
        "symbol": "createRecord", "evidence": "src/service.ts:15",
    }])

    graph = build(load(module_run))

    handlers = [node for node in graph["nodes"] if node["kind"] == "handler"]
    route_edges = [edge for edge in graph["edges"] if edge["kind"] == "routes-to"]
    assert len(handlers) == 1
    assert handlers[0]["observation"] == "observed"
    assert handlers[0]["evidence_refs"] == ["service@NON-GIT:src/service.ts:15"]
    assert len(route_edges) == 1
    assert route_edges[0]["observation"] == "observed"


def test_graph_refuses_scope_changed_after_ranking(tmp_path):
    module_run = _prepared_scope(tmp_path)
    path = module_run / "evidence" / "module-scope.json"
    document = json.loads(path.read_text())
    document["feature_id"] = "feature-tampered"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractError, match="selection receipt"):
        build(load(module_run))


def test_graph_writes_once(tmp_path):
    module_run = _prepared_scope(tmp_path)
    out = write(load(module_run))
    assert out.is_file()
    with pytest.raises(FileExistsError):
        write(load(module_run))


def test_cli_builds_the_feature_graph(tmp_path, capsys):
    module_run = _prepared_scope(tmp_path)
    assert main(["module-build-graph", "--run", str(module_run)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["graph"].endswith("feature-graph.json")
