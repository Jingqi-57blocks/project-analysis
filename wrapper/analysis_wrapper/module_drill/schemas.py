"""Structural output schemas for the Module Drill task protocol.

Semantic cross-checks are added with the phase that owns the input universe;
these checks only establish a strict, executor-independent envelope now.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .model import FeatureFlow
from .protocol import MODULE_TASK_TYPES

Failure = dict[str, str]

_REQUIRED_FIELDS = {
    "module-candidate-ranking": {"decision", "candidate_ids", "reason_code"},
    "module-frontier-expansion": {"dispositions"},
    "module-sync-recovery": {"dispositions", "claims", "flows"},
    "module-async-recovery": {"dispositions", "claims", "flows"},
    "module-model-merge": {"module_model"},
    "module-claim-verification": {"verdicts"},
    "module-section-generate": {"sections"},
}


def _failure(check: str, detail: str, location: str = "") -> list[Failure]:
    return [{"check": check, "detail": detail, "location": location}]


_RANKING_DECISIONS = frozenset({"selected", "ambiguous", "no-match"})
_RANKING_REASON_CODES = frozenset({
    "clear-dominant", "equally-supported", "insufficient-evidence",
})


def _validate_candidate_ranking(output: Any) -> list[Failure]:
    if not isinstance(output, dict):
        return _failure("output-shape", "task output must be an object")
    expected = _REQUIRED_FIELDS["module-candidate-ranking"]
    if set(output) != expected:
        missing = sorted(expected - set(output))
        extras = sorted(set(output) - expected)
        return _failure("output-fields", f"missing={missing}; unexpected={extras}")

    decision = output["decision"]
    candidate_ids = output["candidate_ids"]
    reason_code = output["reason_code"]
    if decision not in _RANKING_DECISIONS:
        return _failure("ranking-decision", "decision must be selected, ambiguous, or no-match", "decision")
    if not isinstance(candidate_ids, list) or not all(
            isinstance(value, str) and value for value in candidate_ids):
        return _failure("ranking-candidate-ids", "candidate_ids must be a string list", "candidate_ids")
    if len(candidate_ids) != len(set(candidate_ids)):
        return _failure("ranking-candidate-ids", "candidate_ids must not contain duplicates", "candidate_ids")
    if reason_code not in _RANKING_REASON_CODES:
        return _failure("ranking-reason-code", "reason_code is not recognized", "reason_code")
    if decision == "selected":
        if not candidate_ids or reason_code != "clear-dominant":
            return _failure(
                "ranking-selected-shape",
                "selected requires one or more candidates and clear-dominant",
            )
    elif decision == "ambiguous":
        if len(candidate_ids) < 2 or reason_code != "equally-supported":
            return _failure(
                "ranking-ambiguous-shape",
                "ambiguous requires at least two candidates and equally-supported",
            )
    elif candidate_ids or reason_code != "insufficient-evidence":
        return _failure(
            "ranking-no-match-shape",
            "no-match requires no candidates and insufficient-evidence",
        )
    return []


def _crosscheck_candidate_ranking(output: Any, packet_inputs: Mapping[str, str]) -> list[Failure]:
    """Ensure ranking can only choose IDs actually supplied in the packet."""
    raw = packet_inputs.get("candidate-partition.json")
    if raw is None:
        # Kept only for direct schema callers that validate an older small
        # packet.  Production ranking packets use bounded partitions.
        raw = packet_inputs.get("candidate-universe.json")
    if raw is None or not isinstance(output, dict):
        return []
    try:
        universe = json.loads(raw)
        rows = universe["candidates"]
        expected_ids = {
            row["candidate_id"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("candidate_id"), str)
        }
    except (TypeError, ValueError, KeyError):
        # A malformed packet is a packet-construction failure, not an executor
        # result failure. The command that creates it owns that validation.
        return []
    supplied = set(output.get("candidate_ids", []))
    unknown = sorted(supplied - expected_ids)
    if unknown:
        return _failure(
            "ranking-candidate-universe",
            "candidate_ids must be chosen from the supplied candidate partition: " + ", ".join(unknown),
            "candidate_ids",
        )
    return []


_SYNC_OUTCOMES = frozenset({"claimed", "no-concern-observed", "unknown", "not-applicable"})
_SYNC_CLAIM_KINDS = frozenset({
    "actor", "ui-visibility", "authorization", "validation", "comparison", "default",
    "state-transition", "persistence-effect", "flow-condition", "error", "cancellation",
})
_SYNC_OPERATIONS = frozenset({
    "allows", "denies", "compares", "assigns", "increments", "decrements", "transitions",
    "reads", "writes", "validates", "requires", "emits",
})
_SUPPORT_ROLES = frozenset({"condition", "effect", "authorization", "persistence", "trigger"})
_ASYNC_OUTCOMES = _SYNC_OUTCOMES
_ASYNC_CLAIM_KINDS = frozenset({
    "async-effect", "scheduler", "event", "queue", "notification", "configuration",
    "feature-flag", "integration", "external-boundary",
})
_ASYNC_OPERATIONS = frozenset({
    "schedules", "starts", "emits", "consumes", "retries", "notifies", "configures",
    "guards", "invokes", "reads", "writes",
})
_ASYNC_SUPPORT_ROLES = frozenset({"trigger", "condition", "effect", "persistence", "notification", "integration"})


def _packet_json(packet_inputs: Mapping[str, str], name: str) -> tuple[Any | None, list[Failure]]:
    raw = packet_inputs.get(name)
    if raw is None:
        return None, _failure("packet-input", f"missing required packet input {name}", name)
    try:
        return json.loads(raw), []
    except (TypeError, ValueError):
        return None, _failure("packet-input", f"{name} is not valid JSON", name)


def _crosscheck_sync_recovery(output: Any, packet_inputs: Mapping[str, str]) -> list[Failure]:
    """Require exact disposition of all local anchors and semantic spans."""
    requirements_doc, failures = _packet_json(packet_inputs, "sync-requirements.json")
    graph, graph_failures = _packet_json(packet_inputs, "feature-graph.json")
    spans, span_failures = _packet_json(packet_inputs, "semantic-spans.json")
    failures += graph_failures + span_failures
    if failures:
        return failures
    if not isinstance(requirements_doc, dict) or not isinstance(requirements_doc.get("requirements"), list):
        return _failure("sync-requirements", "sync requirements packet is invalid", "sync-requirements.json")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) \
            or not isinstance(graph.get("edges"), list):
        return _failure("sync-graph", "feature graph packet is invalid", "feature-graph.json")
    if not isinstance(spans, dict) or not isinstance(spans.get("spans"), list):
        return _failure("sync-spans", "semantic spans packet is invalid", "semantic-spans.json")
    required: dict[str, dict[str, Any]] = {}
    for row in requirements_doc["requirements"]:
        if not isinstance(row, dict) or not isinstance(row.get("requirement_id"), str):
            return _failure("sync-requirements", "requirement lacks a stable ID", "sync-requirements.json")
        if row["requirement_id"] in required:
            return _failure("sync-requirements", "requirement IDs must be unique", "sync-requirements.json")
        required[row["requirement_id"]] = row
    node_ids = {row.get("node_id") for row in graph["nodes"] if isinstance(row, dict)}
    edge_ids = {row.get("edge_id") for row in graph["edges"] if isinstance(row, dict)}
    allowed_anchors = {value for value in node_ids | edge_ids if isinstance(value, str)}
    allowed_refs = {
        ref for row in graph["nodes"] + graph["edges"] if isinstance(row, dict)
        for ref in row.get("evidence_refs", []) if isinstance(ref, str) and ref
    }
    for row in spans["spans"]:
        if isinstance(row, dict):
            allowed_refs.update(
                ref for ref in (row.get("ref"), row.get("start_ref"), row.get("end_ref"))
                if isinstance(ref, str) and ref)
    dispositions = output.get("dispositions") if isinstance(output, dict) else None
    if not isinstance(dispositions, list):
        return _failure("sync-dispositions", "dispositions must be a list", "dispositions")
    seen: set[str] = set()
    claim_ids: set[str] = set()
    claims: list[dict[str, Any]] = []
    for index, row in enumerate(output.get("claims", [])):
        fields = {"claim_id", "kind", "anchor_ids", "support", "subject", "operation", "value"}
        if not isinstance(row, dict) or set(row) != fields:
            failures += _failure("sync-claim", "claim has an invalid field set", f"claims[{index}]")
            continue
        claim_id = row["claim_id"]
        if not isinstance(claim_id, str) or not claim_id:
            failures += _failure("sync-claim", "claim_id must be a non-empty string", f"claims[{index}].claim_id")
            continue
        if claim_id in claim_ids:
            failures += _failure("sync-claim", "claim IDs must be unique", f"claims[{index}].claim_id")
        claim_ids.add(claim_id)
        anchors = row["anchor_ids"]
        if not isinstance(anchors, list) or not anchors or not all(isinstance(anchor, str) for anchor in anchors) \
                or not set(anchors) <= allowed_anchors:
            failures += _failure("sync-claim-anchor", "claim names an anchor outside the supplied graph", f"claims[{index}]")
        if row["kind"] not in _SYNC_CLAIM_KINDS:
            failures += _failure("sync-claim-kind", "claim kind is not recognized", f"claims[{index}].kind")
        if row["operation"] not in _SYNC_OPERATIONS:
            failures += _failure("sync-claim-operation", "claim operation is not recognized", f"claims[{index}].operation")
        if not isinstance(row["subject"], str) or not row["subject"].strip():
            failures += _failure("sync-claim-subject", "claim subject must be non-empty", f"claims[{index}].subject")
        if not isinstance(row["value"], (str, int, float, bool)) and row["value"] is not None:
            failures += _failure("sync-claim-value", "claim value must be a scalar or null", f"claims[{index}].value")
        support = row["support"]
        if not isinstance(support, list) or not support:
            failures += _failure("sync-claim-support", "claim requires support", f"claims[{index}].support")
        else:
            for support_index, item in enumerate(support):
                if not isinstance(item, dict) or set(item) != {"ref", "role"} \
                        or item.get("role") not in _SUPPORT_ROLES \
                        or item.get("ref") not in allowed_refs:
                    failures += _failure("sync-claim-support", "claim support is outside the packet or malformed",
                                         f"claims[{index}].support[{support_index}]")
        claims.append(row)
    for index, row in enumerate(dispositions):
        if not isinstance(row, dict) or set(row) != {"requirement_id", "outcome", "claim_ids", "evidence_refs", "reason"}:
            failures += _failure("sync-disposition", "disposition has an invalid field set", f"dispositions[{index}]")
            continue
        requirement_id, outcome = row["requirement_id"], row["outcome"]
        if not isinstance(requirement_id, str) or requirement_id not in required:
            failures += _failure("sync-disposition-id", "disposition names an unknown requirement", f"dispositions[{index}].requirement_id")
            continue
        if requirement_id in seen:
            failures += _failure("sync-disposition-id", "requirement must be dispositioned exactly once", f"dispositions[{index}].requirement_id")
        seen.add(requirement_id)
        if outcome not in _SYNC_OUTCOMES:
            failures += _failure("sync-disposition-outcome", "outcome is not recognized", f"dispositions[{index}].outcome")
        refs = row["evidence_refs"]
        disposition_claim_ids = row["claim_ids"]
        if not isinstance(refs, list) or not all(isinstance(ref, str) and ref in allowed_refs for ref in refs):
            failures += _failure("sync-disposition-evidence", "disposition cites evidence outside the packet", f"dispositions[{index}].evidence_refs")
        if not isinstance(disposition_claim_ids, list) or not all(isinstance(value, str) and value in claim_ids for value in disposition_claim_ids):
            failures += _failure("sync-disposition-claims", "disposition names an unknown claim", f"dispositions[{index}].claim_ids")
        if outcome == "claimed" and not disposition_claim_ids:
            failures += _failure("sync-disposition-claims", "claimed disposition requires a claim", f"dispositions[{index}]")
        if outcome in {"no-concern-observed", "not-applicable"} and not refs:
            failures += _failure("sync-disposition-evidence", "outcome requires positive evidence", f"dispositions[{index}]")
        if outcome == "unknown" and (not isinstance(row["reason"], str) or not row["reason"].strip()):
            failures += _failure("sync-disposition-reason", "unknown outcome requires a reason", f"dispositions[{index}].reason")
        requirement = required[requirement_id]
        if requirement.get("kind") == "semantic-span" and requirement.get("span_status") != "fetched" \
                and outcome != "unknown":
            failures += _failure(
                "sync-span-unresolved",
                "an unresolved semantic span must remain unknown rather than become a clean outcome",
                f"dispositions[{index}]",
            )
    missing = sorted(set(required) - seen)
    if missing:
        failures += _failure("sync-disposition-missing", "missing dispositions: " + ", ".join(missing), "dispositions")
    flow_ids: set[str] = set()
    for index, row in enumerate(output.get("flows", [])):
        try:
            flow = FeatureFlow.from_dict(row, f"flows[{index}]")
        except (ContractError, ValueError) as exc:
            failures += _failure("sync-flow", str(exc), f"flows[{index}]")
            continue
        if flow.flow_id in flow_ids:
            failures += _failure("sync-flow", "flow IDs must be unique", f"flows[{index}].flow_id")
        flow_ids.add(flow.flow_id)
        if not set(flow.edge_ids) <= allowed_anchors or not set(flow.claim_ids) <= claim_ids:
            failures += _failure("sync-flow", "flow references an unknown edge or claim", f"flows[{index}]")
    return failures


def _crosscheck_async_recovery(output: Any, packet_inputs: Mapping[str, str]) -> list[Failure]:
    """Require exact disposition of all scoped boundary requirements."""
    requirements_doc, failures = _packet_json(packet_inputs, "async-requirements.json")
    graph, graph_failures = _packet_json(packet_inputs, "feature-boundary-closure.json")
    spans, span_failures = _packet_json(packet_inputs, "semantic-spans.json")
    failures += graph_failures + span_failures
    if failures:
        return failures
    if not isinstance(requirements_doc, dict) or not isinstance(requirements_doc.get("requirements"), list):
        return _failure("async-requirements", "async requirements packet is invalid", "async-requirements.json")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) \
            or not isinstance(graph.get("edges"), list):
        return _failure("async-graph", "feature boundary closure packet is invalid", "feature-boundary-closure.json")
    if not isinstance(spans, dict) or not isinstance(spans.get("spans"), list):
        return _failure("async-spans", "semantic spans packet is invalid", "semantic-spans.json")
    required: dict[str, dict[str, Any]] = {}
    for row in requirements_doc["requirements"]:
        if not isinstance(row, dict) or not isinstance(row.get("requirement_id"), str):
            return _failure("async-requirements", "requirement lacks a stable ID", "async-requirements.json")
        if row["requirement_id"] in required:
            return _failure("async-requirements", "requirement IDs must be unique", "async-requirements.json")
        required[row["requirement_id"]] = row
    node_ids = {row.get("node_id") for row in graph["nodes"] if isinstance(row, dict)}
    edge_ids = {row.get("edge_id") for row in graph["edges"] if isinstance(row, dict)}
    allowed_anchors = {value for value in node_ids | edge_ids if isinstance(value, str)}
    allowed_refs = {
        ref for row in graph["nodes"] + graph["edges"] if isinstance(row, dict)
        for ref in row.get("evidence_refs", []) if isinstance(ref, str) and ref
    }
    for row in graph.get("boundary_dispositions", []):
        if isinstance(row, dict):
            allowed_refs.update(ref for ref in row.get("evidence_refs", []) if isinstance(ref, str) and ref)
    for row in spans["spans"]:
        if isinstance(row, dict):
            allowed_refs.update(ref for ref in (row.get("ref"), row.get("start_ref"), row.get("end_ref"))
                                if isinstance(ref, str) and ref)
    dispositions = output.get("dispositions") if isinstance(output, dict) else None
    if not isinstance(dispositions, list):
        return _failure("async-dispositions", "dispositions must be a list", "dispositions")
    seen: set[str] = set()
    claim_ids: set[str] = set()
    for index, row in enumerate(output.get("claims", [])):
        fields = {"claim_id", "kind", "anchor_ids", "support", "subject", "operation", "value"}
        if not isinstance(row, dict) or set(row) != fields:
            failures += _failure("async-claim", "claim has an invalid field set", f"claims[{index}]")
            continue
        claim_id = row["claim_id"]
        if not isinstance(claim_id, str) or not claim_id:
            failures += _failure("async-claim", "claim_id must be a non-empty string", f"claims[{index}].claim_id")
            continue
        if claim_id in claim_ids:
            failures += _failure("async-claim", "claim IDs must be unique", f"claims[{index}].claim_id")
        claim_ids.add(claim_id)
        anchors = row["anchor_ids"]
        if not isinstance(anchors, list) or not anchors or not all(isinstance(anchor, str) for anchor in anchors) \
                or not set(anchors) <= allowed_anchors:
            failures += _failure("async-claim-anchor", "claim names an anchor outside the supplied graph", f"claims[{index}]")
        if row["kind"] not in _ASYNC_CLAIM_KINDS:
            failures += _failure("async-claim-kind", "claim kind is not recognized", f"claims[{index}].kind")
        if row["operation"] not in _ASYNC_OPERATIONS:
            failures += _failure("async-claim-operation", "claim operation is not recognized", f"claims[{index}].operation")
        if not isinstance(row["subject"], str) or not row["subject"].strip():
            failures += _failure("async-claim-subject", "claim subject must be non-empty", f"claims[{index}].subject")
        if not isinstance(row["value"], (str, int, float, bool)) and row["value"] is not None:
            failures += _failure("async-claim-value", "claim value must be a scalar or null", f"claims[{index}].value")
        support = row["support"]
        if not isinstance(support, list) or not support:
            failures += _failure("async-claim-support", "claim requires support", f"claims[{index}].support")
        else:
            for support_index, item in enumerate(support):
                if not isinstance(item, dict) or set(item) != {"ref", "role"} \
                        or item.get("role") not in _ASYNC_SUPPORT_ROLES \
                        or item.get("ref") not in allowed_refs:
                    failures += _failure("async-claim-support", "claim support is outside the packet or malformed",
                                         f"claims[{index}].support[{support_index}]")
    for index, row in enumerate(dispositions):
        fields = {"requirement_id", "outcome", "claim_ids", "evidence_refs", "reason"}
        if not isinstance(row, dict) or set(row) != fields:
            failures += _failure("async-disposition", "disposition has an invalid field set", f"dispositions[{index}]")
            continue
        requirement_id, outcome = row["requirement_id"], row["outcome"]
        if not isinstance(requirement_id, str) or requirement_id not in required:
            failures += _failure("async-disposition-id", "disposition names an unknown requirement", f"dispositions[{index}].requirement_id")
            continue
        if requirement_id in seen:
            failures += _failure("async-disposition-id", "requirement must be dispositioned exactly once", f"dispositions[{index}].requirement_id")
        seen.add(requirement_id)
        if outcome not in _ASYNC_OUTCOMES:
            failures += _failure("async-disposition-outcome", "outcome is not recognized", f"dispositions[{index}].outcome")
        refs, disposition_claim_ids = row["evidence_refs"], row["claim_ids"]
        if not isinstance(refs, list) or not all(isinstance(ref, str) and ref in allowed_refs for ref in refs):
            failures += _failure("async-disposition-evidence", "disposition cites evidence outside the packet", f"dispositions[{index}].evidence_refs")
        if not isinstance(disposition_claim_ids, list) or not all(isinstance(value, str) and value in claim_ids for value in disposition_claim_ids):
            failures += _failure("async-disposition-claims", "disposition names an unknown claim", f"dispositions[{index}].claim_ids")
        if outcome == "claimed" and not disposition_claim_ids:
            failures += _failure("async-disposition-claims", "claimed disposition requires a claim", f"dispositions[{index}]")
        if outcome in {"no-concern-observed", "not-applicable"} and not refs:
            failures += _failure("async-disposition-evidence", "outcome requires positive evidence", f"dispositions[{index}]")
        if outcome == "unknown" and (not isinstance(row["reason"], str) or not row["reason"].strip()):
            failures += _failure("async-disposition-reason", "unknown outcome requires a reason", f"dispositions[{index}].reason")
        if required[requirement_id].get("boundary_state") == "unresolved" and outcome != "unknown":
            failures += _failure("async-boundary-unresolved", "an unresolved boundary must remain unknown", f"dispositions[{index}]")
    missing = sorted(set(required) - seen)
    if missing:
        failures += _failure("async-disposition-missing", "missing dispositions: " + ", ".join(missing), "dispositions")
    flow_ids: set[str] = set()
    for index, row in enumerate(output.get("flows", [])):
        try:
            flow = FeatureFlow.from_dict(row, f"flows[{index}]")
        except (ContractError, ValueError) as exc:
            failures += _failure("async-flow", str(exc), f"flows[{index}]")
            continue
        if flow.flow_id in flow_ids:
            failures += _failure("async-flow", "flow IDs must be unique", f"flows[{index}].flow_id")
        flow_ids.add(flow.flow_id)
        if not set(flow.edge_ids) <= allowed_anchors or not set(flow.claim_ids) <= claim_ids:
            failures += _failure("async-flow", "flow references an unknown edge or claim", f"flows[{index}]")
    return failures


def validate_output(task_type: str, output: Any, *,
                    packet_inputs: Mapping[str, str] | None = None) -> list[Failure]:
    """Validate an envelope; phase-specific code validates its inner records."""
    if task_type not in MODULE_TASK_TYPES:
        return _failure("task-type", f"unknown Module Drill task type: {task_type!r}", "task_type")
    if task_type == "module-candidate-ranking":
        failures = _validate_candidate_ranking(output)
        if not failures and packet_inputs is not None:
            failures += _crosscheck_candidate_ranking(output, packet_inputs)
        return failures
    if not isinstance(output, dict):
        return _failure("output-shape", "task output must be an object")
    missing = sorted(_REQUIRED_FIELDS[task_type] - set(output))
    if missing:
        return _failure("output-required-fields", f"missing required fields: {missing}")
    extras = sorted(set(output) - _REQUIRED_FIELDS[task_type])
    if extras:
        return _failure("output-fields", f"unexpected fields: {extras}")
    for name, value in output.items():
        if name == "selected_candidate_id":
            if value is not None and (not isinstance(value, str) or not value):
                return _failure("selected-candidate", "selected_candidate_id must be a string or null", name)
        elif not isinstance(value, (list, dict)):
                return _failure("output-field-shape", f"{name} must be a list or object", name)
    if task_type == "module-sync-recovery" and packet_inputs is not None:
        return _crosscheck_sync_recovery(output, packet_inputs)
    if task_type == "module-async-recovery" and packet_inputs is not None:
        return _crosscheck_async_recovery(output, packet_inputs)
    return []
