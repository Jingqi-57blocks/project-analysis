"""57B-135 contract and acceptance-baseline tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from analysis_wrapper.module_drill.acceptance import AcceptanceFixture
from analysis_wrapper.module_drill.coverage import Coverage
from analysis_wrapper.module_drill.protocol import MODULE_TASK_TYPES, schema_for_task_type
from analysis_wrapper.module_drill.run_state import AuditResult, RunStateProjection
from analysis_wrapper.module_drill.scope import ScopeCandidate
from analysis_wrapper.module_drill.source import SourceManifest
from analysis_wrapper.module_drill.validation import ContractError
from analysis_wrapper.orchestrator.contracts import TaskPacket
from analysis_wrapper.orchestrator.schemas import validate_output


FIXTURE = Path(__file__).parent / "fixtures" / "module_drill_v2" / "three_repo_feature.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_three_repository_fixture_is_a_complete_contract_baseline():
    fixture = AcceptanceFixture.from_dict(load_fixture())
    assert fixture.scope.feature_id == "sample-capability"
    assert fixture.model.closure_status == "closed"
    assert fixture.expected_path_edge_ids[0] == "edge-ui-client"
    assert fixture.scope.candidates[0].candidate_id == fixture.scope.selected_candidate_id
    assert {item.expected_closure_status for item in fixture.mutations} == {"open", "blocked"}


def test_source_manifest_rejects_bounded_view_as_provider_authority():
    payload = load_fixture()["source_manifest"]
    payload = copy.deepcopy(payload)
    payload["providers"][0]["artifact_ids"] = ["compact-index"]
    with pytest.raises(ContractError, match="cannot treat index artifact"):
        SourceManifest.from_dict(payload)

    payload = load_fixture()["source_manifest"]
    payload = copy.deepcopy(payload)
    payload["providers"][0]["artifact_ids"] = []
    with pytest.raises(ContractError, match="requires canonical evidence"):
        SourceManifest.from_dict(payload)


def test_source_manifest_binds_its_mode_to_overview_lineage():
    payload = load_fixture()["source_manifest"]
    payload = copy.deepcopy(payload)
    payload["source_mode"] = "overview-backed"
    with pytest.raises(ContractError, match="source_overview_run"):
        SourceManifest.from_dict(payload)

    payload["source_overview_run"] = "overview_123.abc-456"
    assert SourceManifest.from_dict(payload).source_overview_run == "overview_123.abc-456"


def test_fixture_rejects_stale_manifest_binding_and_missing_linkage():
    stale = load_fixture()
    stale["module_scope"]["source_manifest_digest"] = "0" * 64
    with pytest.raises(ContractError, match="does not bind"):
        AcceptanceFixture.from_dict(stale)

    broken = load_fixture()
    broken["module_model"]["edges"] = [
        edge for edge in broken["module_model"]["edges"]
        if edge["edge_id"] != "edge-client-route"
    ]
    with pytest.raises(ContractError, match="unknown edge"):
        AcceptanceFixture.from_dict(broken)

    outside_snapshot = load_fixture()
    outside_snapshot["module_model"]["nodes"][0]["repository_ref"] = "not-in-snapshot"
    with pytest.raises(ContractError, match="outside its source manifest"):
        AcceptanceFixture.from_dict(outside_snapshot)


def test_two_axis_coverage_requires_positive_not_applicable_evidence():
    with pytest.raises(ContractError, match="requires positive evidence"):
        Coverage("not-applicable", "complete", (), ())


def test_scope_candidate_requires_deterministic_seed_and_repository_evidence():
    candidate = ScopeCandidate(
        "candidate-create-record", ("seed-ui-create",), ("web-app", "api-service"),
        "selected", "exact route and client anchors connect the repositories",
    )
    assert candidate.to_dict()["disposition"] == "selected"


def test_scope_rejects_selected_candidate_outside_deterministic_candidates():
    payload = load_fixture()
    payload["module_scope"]["selected_candidate_id"] = "candidate-not-observed"
    with pytest.raises(ContractError, match="must name a scope candidate"):
        AcceptanceFixture.from_dict(payload)


def test_module_task_types_use_the_shared_packet_and_schema_dispatch():
    outputs = {
        "module-candidate-ranking": {
            "decision": "selected", "candidate_ids": ["candidate-a"],
            "selected_candidate_id": "candidate-a", "reason_code": "clear-dominant",
        },
        "module-frontier-expansion": {"dispositions": []},
        "module-sync-recovery": {"claims": [], "flows": []},
        "module-async-recovery": {"claims": [], "flows": []},
        "module-model-merge": {"module_model": {}},
        "module-claim-verification": {"verdicts": []},
        "module-section-generate": {"sections": []},
    }
    assert set(outputs) == MODULE_TASK_TYPES
    for task_type, output in outputs.items():
        packet = TaskPacket.create(
            task_id="module-task", task_type=task_type,
            template_id="module-contract", template_version="1",
            instructions="Return the declared JSON schema only.", inputs={},
            output_schema_id=schema_for_task_type(task_type), context_budget_tokens=100,
        )
        assert packet.task_type == task_type
        assert validate_output(task_type, output) == []


def test_completed_projection_requires_a_passing_audit():
    audit = AuditResult(False, ("source-integrity",), ("source-integrity",))
    with pytest.raises(ContractError, match="requires a passing audit"):
        RunStateProjection("module-run", "a" * 64, "b" * 64, True, audit)
