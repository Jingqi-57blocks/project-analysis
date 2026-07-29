"""Authoritative ModuleModel finalization and fail-closed audit tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.async_recovery import build_packet as async_packet
from analysis_wrapper.module_drill.async_recovery import finalize as finalize_async, register as register_async
from analysis_wrapper.module_drill.boundary_closure import write as write_boundary_closure
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.driver import ModuleDriver
from analysis_wrapper.module_drill.finalize import _claims, _coverage, finalize
from analysis_wrapper.module_drill.frontier_candidates import write as write_candidates
from analysis_wrapper.module_drill.model import FeatureClaim, FeatureNode
from analysis_wrapper.module_drill.graph_closure import write as write_graph_closure
from analysis_wrapper.module_drill.span_fetch import write as write_spans
from analysis_wrapper.module_drill.span_plan import write as write_plan
from analysis_wrapper.module_drill.sync_recovery import build_packets as sync_packets
from analysis_wrapper.module_drill.validation import ContractError
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


def _async_output(packet):
    """Give the happy-path fixture claims for applicable boundary dimensions."""
    requirements = json.loads(packet.inputs["async-requirements.json"].content)["requirements"]
    graph = json.loads(packet.inputs["feature-boundary-closure.json"].content)
    anchors = {row["node_id"] for row in graph["nodes"]} | {row["edge_id"] for row in graph["edges"]}
    claims = []
    dispositions = []
    for index, row in enumerate(requirements):
        kind = row.get("boundary_kind")
        claim_ids = []
        claim_kind, operation, role = {
            "configuration": ("configuration", "configures", "effect"),
            "integration-host": ("integration", "invokes", "integration"),
            "integration-package": ("integration", "invokes", "integration"),
            "async-boundary": ("async-effect", "emits", "effect"),
        }.get(kind, ("", "", ""))
        local_anchors = [item for item in row["anchor_ids"] if item in anchors]
        if claim_kind and local_anchors:
            claim_id = f"claim-boundary-{index}"
            claims.append({
                "claim_id": claim_id, "kind": claim_kind, "anchor_ids": local_anchors[:1],
                "support": [{"ref": row["evidence_refs"][0], "role": role}],
                "subject": f"{kind} boundary {index}", "operation": operation, "value": kind,
            })
            claim_ids = [claim_id]
        dispositions.append({
            "requirement_id": row["requirement_id"],
            "outcome": "claimed" if claim_ids else "no-concern-observed",
            "claim_ids": claim_ids, "evidence_refs": row["evidence_refs"], "reason": "",
        })
    return {"dispositions": dispositions, "claims": claims, "flows": []}


def _sync_output(packet, *, outcome="no-concern-observed", include_ui_claim=True):
    """Keep the happy-path fixture semantically complete for its UI seed."""
    requirements = json.loads(packet.inputs["sync-requirements.json"].content)["requirements"]
    graph = json.loads(packet.inputs["feature-graph.json"].content)
    nodes = {row["node_id"]: row for row in graph["nodes"]}
    route_handlers = {}
    for edge in graph["edges"]:
        target = nodes.get(edge["target_node_id"], {})
        if edge["kind"] == "routes-to" and target.get("kind") in {"handler", "symbol"}:
            route_handlers.setdefault(edge["source_node_id"], []).append(edge["edge_id"])
    claims = []
    dispositions = []
    for index, row in enumerate(requirements):
        anchors = [anchor for anchor in row.get("anchor_ids", []) if anchor in nodes]
        ui_anchor = next((anchor for anchor in anchors if nodes[anchor].get("kind") == "ui-action"), None)
        selected_outcome = outcome if index == 0 and outcome != "no-concern-observed" else "no-concern-observed"
        claim_ids = []
        if selected_outcome == "no-concern-observed" and include_ui_claim and ui_anchor is not None:
            claim_id = "claim-ui-action-" + str(index)
            claim_ids = [claim_id]
            claims.append({
                "claim_id": claim_id, "kind": "ui-visibility", "anchor_ids": [ui_anchor],
                "support": [{"ref": row["evidence_refs"][0], "role": "trigger"}],
                "subject": "record UI action", "operation": "allows", "value": "submitRecord",
            })
            selected_outcome = "claimed"
        dispositions.append({
            "requirement_id": row["requirement_id"], "outcome": selected_outcome,
            "claim_ids": claim_ids, "evidence_refs": row["evidence_refs"],
            "reason": "source span could not establish the required behaviour" if selected_outcome == "unknown" else "",
        })
    flows = [{
        "flow_id": "flow-" + edge["edge_id"].removeprefix("edge-"),
        "edge_ids": [edge["edge_id"], *route_handlers.get(edge["target_node_id"], [])[:1]],
        "claim_ids": [
            claim["claim_id"] for claim in claims
            if set(claim["anchor_ids"]) & {edge["source_node_id"], edge["target_node_id"], edge["edge_id"]}
        ],
    } for edge in graph["edges"] if edge["kind"] == "ui-route"]
    return {"dispositions": dispositions, "claims": claims, "flows": flows}


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


def _ready(tmp_path, *, sync_outcome="no-concern-observed", include_ui_claim=True):
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
        output = _sync_output(packet, outcome=sync_outcome, include_ui_claim=include_ui_claim)
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
    _submit(driver, async_task, _async_output(async_task))
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
    assert "synchronous-behavior" in audit.failed_checks[0]


def test_finalization_marks_observed_datastore_as_applicable_not_unavailable(tmp_path):
    module_run = _ready(tmp_path)

    model_path, audit = finalize(module_run)

    assert audit.passed and model_path is not None
    model = json.loads(model_path.read_text())["model"]
    data = model["dimension_coverage"]["data"]["coverage"]
    assert data["applicability"] == "applicable"
    assert data["status"] == "complete"
    assert data["positive_evidence_refs"]


def test_verified_authorization_claim_is_feature_coverage_without_access_node():
    async_doc = {"requirements": {"requirements": []}, "output": {"dispositions": [], "claims": [], "flows": []}}
    claim = FeatureClaim(
        "claim-access", "authorization", ("node-anchor",),
        ("service@NON-GIT:routes/access.ts:10",), ("authorization",),
        "actor", "allows", "action",
    )
    dimensions = _coverage(
        SimpleNamespace(candidates=(), seeds=()),
        ({"dispositions": [{"outcome": "no-concern-observed"}]},),
        async_doc, "closed", nodes=(), claims=(claim,),
    )
    authorization = dimensions["authorization"].coverage.to_dict()
    assert authorization["applicability"] == "applicable"
    assert authorization["status"] == "complete"
    assert authorization["positive_evidence_refs"] == ["service@NON-GIT:routes/access.ts:10"]


def test_source_verified_configuration_anchor_is_not_reported_unknown():
    """Synchronous configuration guards are not async-boundary-only facts."""
    async_doc = {"requirements": {"requirements": []}, "output": {"dispositions": [], "claims": [], "flows": []}}
    node = FeatureNode(
        "node-config", "configuration", "service", "observed",
        ("service@NON-GIT:config.ts:10",), "FEATURE_LIMIT",
    )
    claim = FeatureClaim(
        "claim-config", "validation", ("node-config",),
        ("service@NON-GIT:config.ts:10",), ("condition",),
        "configured limit", "validates", "observed",
    )
    dimensions = _coverage(
        SimpleNamespace(candidates=(), seeds=()),
        ({"dispositions": [{"outcome": "claimed"}]},),
        async_doc, "closed", nodes=(node,), claims=(claim,),
    )
    configuration = dimensions["configuration"].coverage.to_dict()
    assert configuration["applicability"] == "applicable"
    assert configuration["status"] == "complete"
    assert configuration["positive_evidence_refs"] == ["service@NON-GIT:config.ts:10"]


def test_claims_with_distinct_anchors_are_not_false_contradictions():
    claims = _claims({
        "dispositions": [
            {"outcome": "claimed", "claim_ids": ["claim-one"], "requirement_id": "one"},
            {"outcome": "claimed", "claim_ids": ["claim-two"], "requirement_id": "two"},
        ],
        "claims": [
            {"claim_id": "claim-one", "kind": "authorization", "anchor_ids": ["node-one"],
             "support": [{"ref": "service@NON-GIT:routes.ts:10", "role": "authorization"}],
             "subject": "route handler", "operation": "requires", "value": "auth.one"},
            {"claim_id": "claim-two", "kind": "authorization", "anchor_ids": ["node-two"],
             "support": [{"ref": "service@NON-GIT:routes.ts:20", "role": "authorization"}],
             "subject": "route handler", "operation": "requires", "value": "auth.two"},
        ], "flows": [],
    })

    assert [claim.claim_id for claim in claims] == ["claim-one", "claim-two"]


def test_claims_with_same_anchor_and_subject_reject_competing_values():
    output = {
        "dispositions": [
            {"outcome": "claimed", "claim_ids": ["claim-one", "claim-two"], "requirement_id": "one"},
        ],
        "claims": [
            {"claim_id": "claim-one", "kind": "authorization", "anchor_ids": ["node-one"],
             "support": [{"ref": "service@NON-GIT:routes.ts:10", "role": "authorization"}],
             "subject": "route handler", "operation": "requires", "value": "auth.one"},
            {"claim_id": "claim-two", "kind": "authorization", "anchor_ids": ["node-one"],
             "support": [{"ref": "service@NON-GIT:routes.ts:11", "role": "authorization"}],
             "subject": "route handler", "operation": "requires", "value": "auth.two"},
        ], "flows": [],
    }

    with pytest.raises(ContractError, match="contradictory claim values"):
        _claims(output)


def test_non_async_boundary_does_not_mark_async_coverage_complete(tmp_path):
    async_doc = {
        "requirements": {"requirements": [{
            "boundary_kind": "access-check", "async_role": "not-applicable",
            "evidence_refs": ["service@NON-GIT:src/app.ts:1"],
        }]},
        "output": {"dispositions": [{"outcome": "no-concern-observed"}]},
    }
    dimensions = _coverage(
        SimpleNamespace(candidates=(), seeds=()), (), async_doc, "closed", nodes=(), claims=(),
    )
    asynchronous = dimensions["asynchronous-behavior"].coverage.to_dict()
    assert asynchronous["applicability"] == "unknown"
    assert asynchronous["status"] == "unavailable"
    assert "no feature-local asynchronous boundary was observed" in asynchronous["limitations"]
    for name in ("configuration", "integration"):
        coverage = dimensions[name].coverage.to_dict()
        assert coverage["applicability"] == "unknown"
        assert coverage["status"] == "unavailable"


def test_selected_ui_action_without_a_semantic_claim_fails_closed(tmp_path):
    module_run = _ready(tmp_path, include_ui_claim=False)

    model_path, audit = finalize(module_run)

    assert model_path is None and not audit.passed
    assert "ui-entry" in audit.failed_checks[0]


def test_cli_returns_nonzero_when_final_audit_fails(tmp_path, capsys):
    module_run = _prepared(tmp_path)
    assert main(["module-finalize-model", "--run", str(module_run)]) == 3
    assert json.loads(capsys.readouterr().out)["audit"]["passed"] is False
