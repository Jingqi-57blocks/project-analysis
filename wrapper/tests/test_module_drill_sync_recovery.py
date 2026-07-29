"""Bounded synchronous semantic-recovery task tests for 57B-153."""

from __future__ import annotations

import json

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.frontier_candidates import write as write_candidates
from analysis_wrapper.module_drill.span_fetch import write as write_spans
from analysis_wrapper.module_drill.span_plan import write as write_plan
from analysis_wrapper.module_drill.sync_recovery import (
    INPUT_BUDGET_TOKENS, _bounded_packets, _estimated_input_tokens, _packet as _build_packet,
    _requirements, build_packets,
)
from analysis_wrapper.module_drill.sync_recovery import finalize, register
from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.orchestrator.contracts import ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome
from analysis_wrapper.orchestrator.engine import now_iso
from analysis_wrapper.orchestrator.schemas import validate_output
from test_module_drill_frontier_candidates import _prepared


def _packets(tmp_path):
    module_run = _prepared(tmp_path)
    write_candidates(load(module_run))
    write_plan(load(module_run))
    write_spans(load(module_run))
    return module_run, build_packets(load(module_run))


def _packet(tmp_path):
    """Return a representative local packet for schema-level tests."""
    module_run, packets = _packets(tmp_path)
    return module_run, max(
        packets,
        key=lambda packet: len(json.loads(packet.inputs["sync-requirements.json"].content)["requirements"]),
    )


def _output(packet):
    requirements = json.loads(packet.inputs["sync-requirements.json"].content)["requirements"]
    graph = json.loads(packet.inputs["feature-graph.json"].content)
    nodes = {row["node_id"]: row for row in graph["nodes"]}
    route_handlers = {}
    for edge in graph["edges"]:
        if edge["kind"] == "routes-to" and nodes[edge["target_node_id"]]["kind"] in {"handler", "symbol"}:
            route_handlers.setdefault(edge["source_node_id"], []).append(edge["edge_id"])
    flows = []
    for edge in graph["edges"]:
        if edge["kind"] == "ui-route":
            flows.append({
                "flow_id": "flow-" + edge["edge_id"].removeprefix("edge-"),
                "edge_ids": [edge["edge_id"], *route_handlers.get(edge["target_node_id"], [])[:1]],
                "claim_ids": [],
            })
    return {
        "dispositions": [{
            "requirement_id": row["requirement_id"],
            "outcome": "no-concern-observed",
            "claim_ids": [],
            "evidence_refs": row["evidence_refs"],
            "reason": "",
        } for row in requirements],
        "claims": [],
        "flows": flows,
    }


def test_packet_uses_only_local_graph_and_plan_bound_semantic_spans(tmp_path):
    _, packet = _packet(tmp_path)
    assert packet.task_type == "module-sync-recovery"
    assert set(packet.inputs) == {
        "sync-requirements.json", "feature-graph.json", "semantic-spans.json", "partition.json",
    }
    requirements = json.loads(packet.inputs["sync-requirements.json"].content)["requirements"]
    assert requirements
    assert len({row["requirement_id"] for row in requirements}) == len(requirements)


def test_packets_are_bounded_and_disposition_the_complete_requirement_universe_once(tmp_path):
    _, packets = _packets(tmp_path)
    assert packets
    requirement_ids = set()
    packet_ids = set()
    for packet in packets:
        assert packet.task_id not in packet_ids
        packet_ids.add(packet.task_id)
        assert packet.task_id.startswith("module-sync-recovery-")
        assert _estimated_input_tokens(packet) <= INPUT_BUDGET_TOKENS
        partition = json.loads(packet.inputs["partition.json"].content)
        local_ids = set(partition["requirement_ids"])
        assert local_ids
        assert not requirement_ids & local_ids
        requirement_ids.update(local_ids)
        local_requirements = {
            row["requirement_id"]
            for row in json.loads(packet.inputs["sync-requirements.json"].content)["requirements"]
        }
        assert local_requirements == local_ids
    assert requirement_ids


def test_large_semantic_group_splits_only_on_whole_requirements():
    """A context limit never causes a requirement to be truncated or copied."""
    graph = {
        "nodes": [{
            "node_id": f"node-{index}", "kind": "route",
            "evidence_refs": [f"ref-{index}-" + "x" * 40_000],
        } for index in range(3)],
        "edges": [],
    }
    requirements = {
        "schema_version": "module-sync-recovery-requirements/v1",
        "feature_graph_digest": "graph", "semantic_spans_digest": "spans", "feature_id": "feature",
        "requirements": [{
            "requirement_id": f"requirement-anchor-node-{index}", "kind": "graph-anchor",
            "anchor_ids": [f"node-{index}"], "evidence_refs": graph["nodes"][index]["evidence_refs"],
        } for index in range(3)],
    }
    packets = _bounded_packets(
        None, group="routes", requirements=requirements, graph=graph, spans={"spans": []},
        rows=requirements["requirements"],
    )
    assert len(packets) > 1
    seen = set()
    for packet in packets:
        assert _estimated_input_tokens(packet) <= INPUT_BUDGET_TOKENS
        ids = set(json.loads(packet.inputs["partition.json"].content)["requirement_ids"])
        assert not seen & ids
        seen.update(ids)
    assert seen == {row["requirement_id"] for row in requirements["requirements"]}


def test_packet_does_not_expand_every_node_that_shares_a_provenance_ref():
    """A shared framework locator is provenance, not an unbounded graph edge."""
    shared_ref = "repo@NON-GIT:src/router.ts:1"
    graph = {
        "schema_version": "feature-graph/v1", "feature_id": "feature", "nodes": [
            {"node_id": "node-selected", "kind": "route", "repository_ref": "repo",
             "observation": "observed", "evidence_refs": [shared_ref]},
            {"node_id": "node-unrelated", "kind": "route", "repository_ref": "repo",
             "observation": "observed", "evidence_refs": [shared_ref]},
        ],
        "edges": [{"edge_id": "edge-shared", "kind": "routes-to",
                   "source_node_id": "node-selected", "target_node_id": "node-unrelated",
                   "observation": "observed", "evidence_refs": [shared_ref]}],
        "frontiers": [{"frontier_id": "frontier-not-for-sync-recovery"}],
    }
    requirements = {
        "schema_version": "module-sync-recovery-requirements/v1",
        "feature_graph_digest": "graph", "semantic_spans_digest": "spans", "feature_id": "feature",
        "requirements": [{
            "requirement_id": "requirement-anchor-node-selected", "kind": "graph-anchor",
            "anchor_ids": ["node-selected"], "evidence_refs": [shared_ref],
        }],
    }
    packet = _build_packet(None, partition_id="routes-01", requirements=requirements,
                           graph=graph, spans={"schema_version": "semantic-spans/v1", "spans": []},
                           rows=requirements["requirements"])
    local_graph = json.loads(packet.inputs["feature-graph.json"].content)
    assert [row["node_id"] for row in local_graph["nodes"]] == ["node-selected"]
    assert local_graph["edges"] == []
    assert "frontiers" not in local_graph
    shared_span = _requirements(graph, {"spans": [{
        "span_id": "span-shared", "ref": shared_ref, "start_ref": shared_ref,
        "end_ref": shared_ref, "status": "fetched", "reason": "",
    }]})["requirements"]
    assert next(row for row in shared_span if row["kind"] == "semantic-span")["anchor_ids"] == []


def test_packet_keeps_observed_ui_to_route_bridge_without_provenance_fanout():
    ui_ref = "repo@NON-GIT:ui/button.tsx:10"
    route_ref = "repo@NON-GIT:api/routes.ts:20"
    graph = {
        "schema_version": "feature-graph/v1", "feature_id": "feature", "nodes": [
            {"node_id": "node-ui", "kind": "ui-action", "repository_ref": "web",
             "observation": "observed", "evidence_refs": [ui_ref]},
            {"node_id": "node-route", "kind": "route", "repository_ref": "api",
             "observation": "observed", "evidence_refs": [route_ref]},
            {"node_id": "node-unrelated", "kind": "route", "repository_ref": "api",
             "observation": "observed", "evidence_refs": [ui_ref]},
        ],
        "edges": [{"edge_id": "edge-ui-route", "kind": "ui-to-route",
                   "source_node_id": "node-ui", "target_node_id": "node-route",
                   "observation": "observed", "evidence_refs": [ui_ref, route_ref]}],
    }
    requirements = {
        "schema_version": "module-sync-recovery-requirements/v1",
        "feature_graph_digest": "graph", "semantic_spans_digest": "spans", "feature_id": "feature",
        "requirements": [{
            "requirement_id": "requirement-anchor-node-ui", "kind": "graph-anchor",
            "anchor_ids": ["node-ui"], "evidence_refs": [ui_ref],
        }],
    }
    packet = _build_packet(None, partition_id="ui-async-01", requirements=requirements,
                           graph=graph, spans={"schema_version": "semantic-spans/v1", "spans": []},
                           rows=requirements["requirements"])
    local_graph = json.loads(packet.inputs["feature-graph.json"].content)
    assert {row["node_id"] for row in local_graph["nodes"]} == {"node-ui", "node-route"}
    assert [row["edge_id"] for row in local_graph["edges"]] == ["edge-ui-route"]


def test_packet_keeps_route_handler_after_an_observed_ui_route_bridge():
    ui_ref = "repo@NON-GIT:ui/button.tsx:10"
    route_ref = "repo@NON-GIT:api/routes.ts:20"
    handler_ref = "repo@NON-GIT:api/service.ts:30"
    graph = {
        "schema_version": "feature-graph/v1", "feature_id": "feature", "nodes": [
            {"node_id": "node-ui", "kind": "ui-action", "repository_ref": "web",
             "observation": "observed", "evidence_refs": [ui_ref]},
            {"node_id": "node-route", "kind": "route", "repository_ref": "api",
             "observation": "observed", "evidence_refs": [route_ref]},
            {"node_id": "node-handler", "kind": "handler", "repository_ref": "api",
             "observation": "observed", "evidence_refs": [handler_ref]},
        ],
        "edges": [
            {"edge_id": "edge-ui-route", "kind": "ui-route", "source_node_id": "node-ui",
             "target_node_id": "node-route", "observation": "observed", "evidence_refs": [ui_ref, route_ref]},
            {"edge_id": "edge-route-handler", "kind": "routes-to", "source_node_id": "node-route",
             "target_node_id": "node-handler", "observation": "observed", "evidence_refs": [route_ref, handler_ref]},
        ],
    }
    requirements = {
        "schema_version": "module-sync-recovery-requirements/v1",
        "feature_graph_digest": "graph", "semantic_spans_digest": "spans", "feature_id": "feature",
        "requirements": [{
            "requirement_id": "requirement-anchor-node-ui", "kind": "graph-anchor",
            "anchor_ids": ["node-ui"], "evidence_refs": [ui_ref],
        }],
    }
    packet = _build_packet(None, partition_id="ui-async-01", requirements=requirements,
                           graph=graph, spans={"schema_version": "semantic-spans/v1", "spans": []},
                           rows=requirements["requirements"])
    local_graph = json.loads(packet.inputs["feature-graph.json"].content)
    assert {row["node_id"] for row in local_graph["nodes"]} == {"node-ui", "node-route", "node-handler"}
    assert [row["edge_id"] for row in local_graph["edges"]] == ["edge-route-handler", "edge-ui-route"]


def test_sync_output_requires_exact_requirement_dispositions(tmp_path):
    _, packet = _packet(tmp_path)
    inputs = {name: item.content for name, item in packet.inputs.items()}
    output = _output(packet)
    assert validate_output("module-sync-recovery", output, packet_inputs=inputs) == []

    output["dispositions"] = output["dispositions"][:-1]
    failures = validate_output("module-sync-recovery", output, packet_inputs=inputs)
    assert any(failure["check"] == "sync-disposition-missing" for failure in failures)


def test_sync_output_cannot_drop_a_supplied_ui_route_flow(tmp_path):
    _, packet = _packet(tmp_path)
    inputs = {name: item.content for name, item in packet.inputs.items()}
    output = _output(packet)
    graph = json.loads(inputs["feature-graph.json"])
    if not any(row["kind"] == "ui-route" for row in graph["edges"]):
        return
    output["flows"] = []
    failures = validate_output("module-sync-recovery", output, packet_inputs=inputs)
    assert any(failure["check"] == "sync-flow-ui-route" for failure in failures)


def test_sync_output_allows_a_context_only_ui_route_without_a_flow():
    """A bridge edge is context; its owning UI requirement emits the flow."""
    ui_ref = "web@NON-GIT:src/page.tsx:10"
    route_ref = "api@NON-GIT:routes/items.ts:20"
    handler_ref = "api@NON-GIT:handlers/items.ts:30"
    inputs = {
        "sync-requirements.json": json.dumps({
            "requirements": [{
                "requirement_id": "requirement-anchor-route",
                "kind": "graph-anchor",
                "anchor_ids": ["node-route"],
                "evidence_refs": [route_ref],
            }],
        }),
        "feature-graph.json": json.dumps({
            "nodes": [
                {"node_id": "node-ui", "kind": "ui-action", "evidence_refs": [ui_ref]},
                {"node_id": "node-route", "kind": "route", "evidence_refs": [route_ref]},
                {"node_id": "node-handler", "kind": "handler", "evidence_refs": [handler_ref]},
            ],
            "edges": [
                {"edge_id": "edge-ui-route", "kind": "ui-route", "source_node_id": "node-ui",
                 "target_node_id": "node-route", "evidence_refs": [ui_ref, route_ref]},
                {"edge_id": "edge-route-handler", "kind": "routes-to", "source_node_id": "node-route",
                 "target_node_id": "node-handler", "evidence_refs": [route_ref, handler_ref]},
            ],
        }),
        "semantic-spans.json": json.dumps({"spans": []}),
    }
    output = {
        "dispositions": [{
            "requirement_id": "requirement-anchor-route", "outcome": "no-concern-observed",
            "claim_ids": [], "evidence_refs": [route_ref], "reason": "",
        }],
        "claims": [],
        "flows": [],
    }
    assert validate_output("module-sync-recovery", output, packet_inputs=inputs) == []


def test_sync_output_requires_flow_when_the_packet_owns_the_ui_source():
    ui_ref = "web@NON-GIT:src/page.tsx:10"
    route_ref = "api@NON-GIT:routes/items.ts:20"
    inputs = {
        "sync-requirements.json": json.dumps({
            "requirements": [{
                "requirement_id": "requirement-anchor-ui",
                "kind": "graph-anchor",
                "anchor_ids": ["node-ui"],
                "evidence_refs": [ui_ref],
            }],
        }),
        "feature-graph.json": json.dumps({
            "nodes": [
                {"node_id": "node-ui", "kind": "ui-action", "evidence_refs": [ui_ref]},
                {"node_id": "node-route", "kind": "route", "evidence_refs": [route_ref]},
            ],
            "edges": [{
                "edge_id": "edge-ui-route", "kind": "ui-route", "source_node_id": "node-ui",
                "target_node_id": "node-route", "evidence_refs": [ui_ref, route_ref],
            }],
        }),
        "semantic-spans.json": json.dumps({"spans": []}),
    }
    output = {
        "dispositions": [{
            "requirement_id": "requirement-anchor-ui", "outcome": "no-concern-observed",
            "claim_ids": [], "evidence_refs": [ui_ref], "reason": "",
        }],
        "claims": [],
        "flows": [],
    }
    failures = validate_output("module-sync-recovery", output, packet_inputs=inputs)
    assert any(failure["check"] == "sync-flow-ui-route" for failure in failures)


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
    module_run, packets = _packets(tmp_path)
    driver = ModuleDriver(module_run)
    created = register(module_run)
    assert set(created) == {packet.task_id for packet in packets}
    expected = {packet.task_id: packet for packet in packets}
    outputs = {}
    for _ in packets:
        claim = driver.claim(1, executor_kind="test", model="test-model")[0]
        packet = expected[claim.packet.task_id]
        assert claim.packet.input_digest == packet.input_digest
        at = now_iso()
        output = _output(packet)
        outputs[packet.task_id] = output
        result = TaskResult(
            task_id=packet.task_id, status="ok", output=output,
            executor=ExecutorInfo(kind="test", model="test-model", params={}),
            timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.0),
            tokens=None, validation=ValidationOutcome(passed=True, failures=()), attempt=claim.attempt,
        )
        assert driver.submit(packet.task_id, result.to_dict())["status"] == "validated"
    document = json.loads(finalize(module_run).read_text())
    assert document["schema_version"] == "sync-recovery/v2"
    assert {row["task_id"] for row in document["tasks"]} == set(outputs)
    assert {row["task_id"]: row["output"] for row in document["tasks"]} == outputs


def test_cli_registers_the_bounded_sync_recovery_task(tmp_path, capsys):
    module_run, _ = _packet(tmp_path)
    assert main(["module-plan-sync-recovery", "--run", str(module_run)]) == 0
    created = json.loads(capsys.readouterr().out)["created"]
    assert created and all(task_id.startswith("module-sync-recovery-") for task_id in created)
