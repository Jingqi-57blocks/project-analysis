"""Bounded semantic recovery for feature-local async and boundary behaviour."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from ..orchestrator.composer import estimate_tokens
from ..orchestrator.contracts import TaskPacket
from .context import SourceContext
from .driver import ModuleDriver
from .validation import ContractError, sha256_json

TASK_ID = "module-async-recovery"
TASK_TYPE = "module-async-recovery"
TEMPLATE_ID = "module-async-recovery"
TEMPLATE_VERSION = "v1"
OUTPUT_SCHEMA_ID = "module-async-recovery/v1"
CONTEXT_BUDGET_TOKENS = 32_000
INPUT_BUDGET_TOKENS = 24_000
SCHEMA_VERSION = "async-recovery/v1"
FILENAME = "async-recovery.json"

_INSTRUCTIONS = """Recover only source-verified asynchronous and boundary behaviour from this bounded feature packet.

Return exactly one JSON object matching module-async-recovery/v1. Every supplied
requirement_id must receive exactly one disposition. Do not create, remove,
merge, or rename a requirement, graph anchor, edge, or evidence reference.
Claims may use only supplied graph anchors and source references. A task,
registration, configuration reference, or integration identifier proves only
what the code observes: it does not prove production activation, runtime
configuration, a producer/consumer counterpart, delivery, or external-service
implementation. Preserve supplied identifiers and literals verbatim. Unknown
must name the missing semantic evidence. Do not write report prose or Mermaid.
"""


def _load(context: SourceContext, filename: str, schema: str) -> dict[str, Any]:
    path = context.module_run / "evidence" / filename
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{filename} is required before asynchronous semantic recovery") from exc
    if not isinstance(document, dict) or document.get("schema_version") != schema:
        raise ContractError(f"{filename} has an unsupported schema")
    if document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError(f"{filename} does not bind the current source manifest")
    return document


def _requirements(closure: dict[str, Any]) -> dict[str, Any]:
    rows = closure.get("boundary_dispositions")
    if not isinstance(rows, list):
        raise ContractError("feature boundary closure dispositions must be a list")
    requirements: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ContractError("feature boundary disposition must be an object")
        state, evidence_id, refs = row.get("state"), row.get("evidence_id"), row.get("evidence_refs")
        if state == "excluded":
            # These are retained in the closure artifact as a terminal
            # disposition, not silently discarded and not a semantic task.
            continue
        if state not in {"expanded", "unresolved"}:
            raise ContractError("feature boundary disposition has invalid terminal state")
        if not isinstance(evidence_id, str) or not evidence_id \
                or not isinstance(refs, list) or not refs \
                or not all(isinstance(ref, str) and ref for ref in refs):
            raise ContractError("feature boundary disposition lacks stable evidence")
        resulting = row.get("resulting_ids", [])
        if not isinstance(resulting, list) or not all(isinstance(value, str) for value in resulting):
            raise ContractError("feature boundary disposition has invalid resulting IDs")
        requirements.append({
            "requirement_id": "requirement-boundary-" + evidence_id.removeprefix("evidence-"),
            "evidence_id": evidence_id,
            "kind": "feature-boundary",
            "anchor_ids": sorted(value for value in resulting if value.startswith(("node-", "edge-"))),
            "evidence_refs": sorted(set(refs)),
            "boundary_kind": row.get("boundary_kind", ""),
            "async_role": row.get("async_role", ""),
            "boundary_data": row.get("data", {}),
            "boundary_state": state,
            "unresolved_reason": row.get("reason", ""),
        })
    ids = [row["requirement_id"] for row in requirements]
    if len(ids) != len(set(ids)):
        raise ContractError("asynchronous recovery requirements are not unique")
    return {
        "schema_version": "module-async-recovery-requirements/v1",
        "feature_boundary_closure_digest": sha256_json(closure),
        "feature_id": closure.get("feature_id"),
        "requirements": sorted(requirements, key=lambda row: row["requirement_id"]),
    }


def _packet_inputs(closure: dict[str, Any], spans: dict[str, Any],
                   requirements: dict[str, Any]) -> dict[str, str]:
    """Project shared evidence down to the one bounded semantic task.

    The closure artifact preserves every provider boundary for audit, but a
    semantic task must see only its applicable boundary requirements.  In
    particular, excluded boundaries and unrelated source spans must not be
    copied into the task merely because they share the feature's closure file.
    """
    rows = requirements["requirements"]
    evidence_ids = {row.get("evidence_id") for row in rows if isinstance(row, dict)}
    if not evidence_ids or not all(isinstance(value, str) and value for value in evidence_ids):
        raise ContractError("asynchronous recovery requirements lack stable evidence IDs")
    local_boundaries = [row for row in closure.get("boundary_dispositions", [])
                        if isinstance(row, dict) and row.get("evidence_id") in evidence_ids]
    if {row.get("evidence_id") for row in local_boundaries} != evidence_ids:
        raise ContractError("asynchronous recovery boundary projection is incomplete")
    anchor_ids = {
        anchor for row in rows for anchor in row.get("anchor_ids", [])
        if isinstance(anchor, str) and anchor
    }
    edge_by_id = {row.get("edge_id"): row for row in closure.get("edges", [])
                  if isinstance(row, dict) and isinstance(row.get("edge_id"), str)}
    node_ids = {row.get("node_id") for row in closure.get("nodes", [])
                if isinstance(row, dict) and isinstance(row.get("node_id"), str)
                and row.get("node_id") in anchor_ids}
    local_edges = [edge_by_id[edge_id] for edge_id in sorted(anchor_ids & set(edge_by_id))]
    for edge in local_edges:
        node_ids.update(value for value in (edge.get("source_node_id"), edge.get("target_node_id"))
                        if isinstance(value, str) and value)
    local_nodes = [row for row in closure.get("nodes", []) if isinstance(row, dict)
                   and row.get("node_id") in node_ids]
    refs = {ref for row in rows for ref in row.get("evidence_refs", [])
            if isinstance(ref, str) and ref}
    local_spans = [row for row in spans.get("spans", []) if isinstance(row, dict)
                   and any(row.get(key) in refs for key in ("ref", "start_ref", "end_ref"))]
    closure_doc = {
        "schema_version": closure.get("schema_version"),
        "source_manifest_digest": closure.get("source_manifest_digest"),
        "feature_id": closure.get("feature_id"),
        "semantic_spans_digest": closure.get("semantic_spans_digest"),
        "nodes": local_nodes,
        "edges": local_edges,
        "boundary_dispositions": local_boundaries,
    }
    spans_doc = {
        "schema_version": spans.get("schema_version"),
        "source_manifest_digest": spans.get("source_manifest_digest"),
        "feature_id": spans.get("feature_id"),
        "feature_graph_digest": spans.get("feature_graph_digest"),
        "semantic_span_plan_digest": spans.get("semantic_span_plan_digest"),
        "frontier_candidates_digest": spans.get("frontier_candidates_digest"),
        "spans": local_spans,
    }
    return {
        "async-requirements.json": json.dumps(requirements, sort_keys=True),
        "feature-boundary-closure.json": json.dumps(closure_doc, sort_keys=True),
        "semantic-spans.json": json.dumps(spans_doc, sort_keys=True),
    }


def build_packet(context: SourceContext) -> TaskPacket:
    """Build one semantic partition from span-bound feature boundaries."""
    closure = _load(context, "feature-boundary-closure.json", "feature-boundary-closure/v1")
    spans = _load(context, "semantic-spans.json", "semantic-spans/v1")
    if closure.get("semantic_spans_digest") != sha256_json(spans):
        raise ContractError("feature boundary closure does not bind current semantic spans")
    requirements = _requirements(closure)
    packet = TaskPacket.create(
        task_id=TASK_ID,
        task_type=TASK_TYPE,
        template_id=TEMPLATE_ID,
        template_version=TEMPLATE_VERSION,
        instructions=_INSTRUCTIONS,
        inputs=_packet_inputs(closure, spans, requirements),
        output_schema_id=OUTPUT_SCHEMA_ID,
        context_budget_tokens=CONTEXT_BUDGET_TOKENS,
    )
    input_tokens = estimate_tokens(packet.instructions) + sum(
        estimate_tokens(item.content) for item in packet.inputs.values())
    if input_tokens > INPUT_BUDGET_TOKENS:
        raise ContractError(
            "asynchronous recovery input exceeds the "
            f"{INPUT_BUDGET_TOKENS}-token budget")
    return packet


def register(module_run: str | Path) -> list[str]:
    return ModuleDriver(module_run).register((build_packet(ModuleDriver(module_run).context),))


def finalize(module_run: str | Path) -> Path:
    """Persist the validated task result with its exact packet lineage."""
    driver = ModuleDriver(module_run)
    packet, output = driver.validated_task(TASK_ID)
    expected = build_packet(driver.context)
    if packet.input_digest != expected.input_digest or packet.output_schema_id != OUTPUT_SCHEMA_ID:
        raise ContractError("validated asynchronous recovery does not match the current evidence packet")
    requirements = json.loads(packet.inputs["async-requirements.json"].content)
    document = {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(driver.context.manifest.to_dict()),
        "task_id": TASK_ID,
        "packet_input_digest": packet.input_digest,
        "feature_boundary_closure_digest": requirements["feature_boundary_closure_digest"],
        "semantic_spans_digest": sha256_json(json.loads(packet.inputs["semantic-spans.json"].content)),
        "requirements": requirements,
        "output": output,
    }
    out = create_stage_dir(driver.run / "evidence") / FILENAME
    write_new_text(out, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return out
