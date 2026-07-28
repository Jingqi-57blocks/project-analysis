"""Feature-local provider-boundary closure tests for 57B-155."""

import json

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.boundary_closure import build
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.frontier_candidates import write as write_candidates
from analysis_wrapper.module_drill.graph_closure import write as write_graph_closure
from analysis_wrapper.module_drill.span_fetch import write as write_spans
from analysis_wrapper.module_drill.span_plan import write as write_span_plan
from test_module_drill_frontier_candidates import _prepared


def _ready(tmp_path, *, integration_path="src/service.ts", route_handler_anchors=None):
    module_run = _prepared(
        tmp_path,
        integration_path=integration_path,
        route_handler_anchors=route_handler_anchors,
    )
    write_candidates(load(module_run))
    write_graph_closure(load(module_run))
    write_span_plan(load(module_run))
    write_spans(load(module_run))
    return module_run


def test_boundary_closure_expands_only_provider_evidence_inside_handler_span(tmp_path):
    module_run = _ready(tmp_path)
    document = build(load(module_run))

    kinds = {node["kind"] for node in document["nodes"]}
    assert {"async-boundary", "configuration", "integration-host", "integration-package"} <= kinds
    assert {edge["kind"] for edge in document["edges"]} >= {
        "async-boundary", "configuration-boundary", "integration-boundary"}
    linked = [row for row in document["boundary_dispositions"] if row["state"] == "expanded"]
    assert {row["coverage_impact"] for row in linked} == {"none"}
    async_row = next(row for row in linked if row["boundary_kind"] == "async-boundary")
    assert async_row["async_role"] == "unknown"
    assert async_row["data"]["operation"] == "setInterval"
    assert all(row["cycle_key"] for row in document["boundary_dispositions"])
    assert all(row["state"] in {"expanded", "excluded", "unresolved"}
               for row in document["boundary_dispositions"])


def test_boundary_closure_expands_evidence_inside_a_source_resolved_route_handler(tmp_path):
    module_run = _ready(tmp_path, route_handler_anchors=[{
        "symbol": "createRecord", "evidence": "src/service.ts:15",
    }])
    document = build(load(module_run))

    handler = next(row for row in document["nodes"] if row["kind"] == "handler")
    linked_edges = [row for row in document["edges"]
                    if row["source_node_id"] == handler["node_id"]]
    assert {row["kind"] for row in linked_edges} >= {
        "async-boundary", "configuration-boundary", "integration-boundary",
    }
    handler_frontier = next(
        row for row in document["handler_frontier_dispositions"]
        if row["anchor_id"] == handler["node_id"]
    )
    assert handler_frontier["state"] == "expanded"


def test_boundary_closure_excludes_same_provider_candidate_outside_handler_span(tmp_path):
    module_run = _ready(tmp_path, integration_path="src/integration.ts")
    document = build(load(module_run))
    excluded = [row for row in document["boundary_dispositions"] if row["state"] == "excluded"]
    assert len(excluded) >= 2
    assert all("no exact feature-scoped handler span" in row["reason"] for row in excluded)
    assert "integration-host" not in {node["kind"] for node in document["nodes"]}


def test_cli_writes_span_bound_boundary_closure(tmp_path, capsys):
    module_run = _ready(tmp_path)
    assert main(["module-build-boundary-closure", "--run", str(module_run)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["boundary_closure"].endswith("feature-boundary-closure.json")
