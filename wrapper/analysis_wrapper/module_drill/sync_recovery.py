"""Bounded task preparation for synchronous Module Drill semantic recovery."""

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

TASK_ID = "module-sync-recovery"
TASK_TYPE = "module-sync-recovery"
TEMPLATE_ID = "module-sync-recovery"
TEMPLATE_VERSION = "v2"
OUTPUT_SCHEMA_ID = "module-sync-recovery/v1"
CONTEXT_BUDGET_TOKENS = 32_000
# Reserve room for a structured response and validation repair.  This uses
# the same documented estimator as the shared packet composer, so a Module
# Drill packet cannot claim a 32k context budget while actually exceeding it.
INPUT_BUDGET_TOKENS = 24_000
SCHEMA_VERSION = "sync-recovery/v2"
FILENAME = "sync-recovery.json"

_INSTRUCTIONS = """Recover only source-verified synchronous behaviour from this bounded feature packet.

Return exactly one JSON object matching module-sync-recovery/v1. Every supplied
requirement_id must receive exactly one disposition. Do not create, remove,
merge, or rename a requirement, graph anchor, edge, or evidence reference.
Claims may use only supplied graph anchors and source references. Distinguish
UI visibility from backend authorization. Do not infer runtime activation,
asynchronous behaviour, configuration, notifications, or external-service
semantics. A no-concern or not-applicable result needs cited evidence; unknown
must name the missing semantic evidence. Do not write report prose or Mermaid.
"""

_ARTIFACTS = {
    "feature-graph.json": "feature-graph/v1",
    "semantic-spans.json": "semantic-spans/v1",
}


def _load(context: SourceContext, filename: str, schema: str) -> dict[str, Any]:
    path = context.module_run / "evidence" / filename
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{filename} is required before synchronous semantic recovery") from exc
    if not isinstance(document, dict) or document.get("schema_version") != schema:
        raise ContractError(f"{filename} has an unsupported schema")
    if document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError(f"{filename} does not bind the current source manifest")
    return document


def _requirements(graph: dict[str, Any], spans: dict[str, Any]) -> dict[str, Any]:
    requirements: list[dict[str, Any]] = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or not isinstance(node.get("node_id"), str):
            raise ContractError("feature graph node is invalid for synchronous recovery")
        refs = node.get("evidence_refs")
        if not isinstance(refs, list) or not all(isinstance(ref, str) and ref for ref in refs):
            raise ContractError("feature graph node lacks evidence refs for synchronous recovery")
        requirements.append({
            "requirement_id": "requirement-anchor-" + node["node_id"],
            "kind": "graph-anchor",
            "anchor_ids": [node["node_id"]],
            "evidence_refs": sorted(set(refs)),
        })
    for span in spans.get("spans", []):
        if not isinstance(span, dict) or not isinstance(span.get("span_id"), str):
            raise ContractError("semantic span is invalid for synchronous recovery")
        refs = [span.get("ref"), span.get("start_ref"), span.get("end_ref")]
        valid = sorted({ref for ref in refs if isinstance(ref, str) and ref})
        if not valid:
            raise ContractError("semantic span lacks source references for synchronous recovery")
        requirements.append({
            "requirement_id": "requirement-span-" + span["span_id"],
            "kind": "semantic-span",
            "anchor_ids": [],
            "evidence_refs": valid,
            "span_status": span.get("status"),
            "unresolved_reason": span.get("reason", ""),
        })
    ids = [row["requirement_id"] for row in requirements]
    if len(ids) != len(set(ids)):
        raise ContractError("synchronous recovery requirements are not unique")
    return {
        "schema_version": "module-sync-recovery-requirements/v1",
        "feature_graph_digest": sha256_json(graph),
        "semantic_spans_digest": sha256_json(spans),
        "feature_id": graph.get("feature_id"),
        "requirements": sorted(requirements, key=lambda row: row["requirement_id"]),
    }


def _category_for_node(node: dict[str, Any]) -> str:
    """Stable semantic responsibility groups, independent of project vocabulary."""
    kind = node.get("kind")
    if kind == "route":
        return "routes"
    if kind == "datastore":
        return "data"
    if kind in {"ui-action", "async-boundary"}:
        return "ui-async"
    if kind in {"access-check", "access-role"}:
        return "authorization"
    return "other"


def _partition_members(requirements: dict[str, Any], graph: dict[str, Any],
                       spans: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Assign every requirement once to a bounded semantic responsibility group."""
    nodes = {row.get("node_id"): row for row in graph.get("nodes", [])
             if isinstance(row, dict) and isinstance(row.get("node_id"), str)}
    by_ref: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        for ref in node.get("evidence_refs", []):
            if isinstance(ref, str) and ref:
                by_ref.setdefault(ref, set()).add(node_id)
    span_by_id = {row.get("span_id"): row for row in spans.get("spans", [])
                  if isinstance(row, dict) and isinstance(row.get("span_id"), str)}
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in requirements["requirements"]:
        if not isinstance(row, dict) or not isinstance(row.get("requirement_id"), str):
            raise ContractError("synchronous recovery requirement lacks a stable ID")
        categories: set[str] = set()
        for anchor_id in row.get("anchor_ids", []):
            node = nodes.get(anchor_id)
            if node is not None:
                categories.add(_category_for_node(node))
        if row.get("kind") == "semantic-span":
            span_id = row["requirement_id"].removeprefix("requirement-span-")
            span = span_by_id.get(span_id)
            if span is None:
                raise ContractError("semantic span requirement is absent from its span artifact")
            ref = span.get("ref")
            if isinstance(ref, str):
                categories.update(_category_for_node(nodes[node_id])
                                  for node_id in by_ref.get(ref, set()))
        # A shared source span stays in one deterministic group; the full
        # requirement is never copied into several packets.
        group = sorted(categories)[0] if categories else "other"
        groups.setdefault(group, []).append(row)
    return {key: sorted(rows, key=lambda row: row["requirement_id"])
            for key, rows in sorted(groups.items())}


def _packet_inputs(requirements: dict[str, Any], graph: dict[str, Any], spans: dict[str, Any],
                   *, partition_id: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    requirement_ids = {row["requirement_id"] for row in rows}
    node_ids = {
        anchor_id for row in rows for anchor_id in row.get("anchor_ids", [])
        if isinstance(anchor_id, str)
    }
    span_ids = {
        requirement_id.removeprefix("requirement-span-") for requirement_id in requirement_ids
        if requirement_id.startswith("requirement-span-")
    }
    local_spans = [row for row in spans.get("spans", [])
                   if isinstance(row, dict) and row.get("span_id") in span_ids]
    refs = {
        ref for row in rows for ref in row.get("evidence_refs", []) if isinstance(ref, str) and ref
    }
    local_nodes = [row for row in graph.get("nodes", []) if isinstance(row, dict) and (
        row.get("node_id") in node_ids or any(ref in refs for ref in row.get("evidence_refs", []))
    )]
    local_node_ids = {row["node_id"] for row in local_nodes if isinstance(row.get("node_id"), str)}
    local_edges = [row for row in graph.get("edges", []) if isinstance(row, dict)
                   and row.get("source_node_id") in local_node_ids
                   and row.get("target_node_id") in local_node_ids]
    requirement_doc = {**requirements, "requirements": rows}
    graph_doc = {**graph, "nodes": local_nodes, "edges": local_edges}
    spans_doc = {**spans, "spans": local_spans}
    partition = {
        "partition_id": partition_id,
        "requirement_ids": sorted(requirement_ids),
        "omitted_requirement_count": len(requirements["requirements"]) - len(rows),
    }
    return {
        "sync-requirements.json": json.dumps(requirement_doc, sort_keys=True),
        "feature-graph.json": json.dumps(graph_doc, sort_keys=True),
        "semantic-spans.json": json.dumps(spans_doc, sort_keys=True),
        "partition.json": json.dumps(partition, sort_keys=True),
    }


def _packet(context: SourceContext, *, partition_id: str, requirements: dict[str, Any],
            graph: dict[str, Any], spans: dict[str, Any], rows: list[dict[str, Any]]) -> TaskPacket:
    return TaskPacket.create(
        task_id=f"{TASK_ID}-{partition_id}", task_type=TASK_TYPE,
        template_id=TEMPLATE_ID, template_version=TEMPLATE_VERSION,
        instructions=_INSTRUCTIONS,
        inputs=_packet_inputs(requirements, graph, spans, partition_id=partition_id, rows=rows),
        output_schema_id=OUTPUT_SCHEMA_ID, context_budget_tokens=CONTEXT_BUDGET_TOKENS,
    )


def _estimated_input_tokens(packet: TaskPacket) -> int:
    return estimate_tokens(packet.instructions) + sum(
        estimate_tokens(item.content) for item in packet.inputs.values())


def _bounded_packets(context: SourceContext, *, group: str, requirements: dict[str, Any],
                     graph: dict[str, Any], spans: dict[str, Any], rows: list[dict[str, Any]],
                     start: int = 1) -> list[TaskPacket]:
    """Split only on whole requirements; each output retains exact local scope."""
    partition_id = f"{group}-{start:02d}"
    packet = _packet(context, partition_id=partition_id, requirements=requirements,
                     graph=graph, spans=spans, rows=rows)
    estimated_tokens = _estimated_input_tokens(packet)
    if estimated_tokens <= INPUT_BUDGET_TOKENS:
        return [packet]
    if len(rows) == 1:
        raise ContractError(
            "one synchronous recovery requirement exceeds the "
            f"{INPUT_BUDGET_TOKENS}-token input budget")
    midpoint = len(rows) // 2
    left = _bounded_packets(context, group=group, requirements=requirements, graph=graph,
                            spans=spans, rows=rows[:midpoint], start=start)
    right = _bounded_packets(context, group=group, requirements=requirements, graph=graph,
                             spans=spans, rows=rows[midpoint:], start=start + len(left))
    return left + right


def build_packets(context: SourceContext) -> tuple[TaskPacket, ...]:
    """Build bounded, non-overlapping packets from verified graph/span evidence."""
    graph = _load(context, "feature-graph.json", _ARTIFACTS["feature-graph.json"])
    spans = _load(context, "semantic-spans.json", _ARTIFACTS["semantic-spans.json"])
    if spans.get("feature_graph_digest") != sha256_json(graph):
        raise ContractError("semantic spans do not bind the current feature graph")
    requirements = _requirements(graph, spans)
    packets: list[TaskPacket] = []
    for group, rows in _partition_members(requirements, graph, spans).items():
        packets.extend(_bounded_packets(context, group=group, requirements=requirements,
                                        graph=graph, spans=spans, rows=rows))
    ids = [packet.task_id for packet in packets]
    if not packets or len(ids) != len(set(ids)):
        raise ContractError("synchronous recovery partition planning produced invalid task IDs")
    packet_requirement_rows = [
        requirement_id
        for packet in packets
        for requirement_id in (
            json.loads(packet.inputs["partition.json"].content)["requirement_ids"])
    ]
    packet_requirements = set(packet_requirement_rows)
    expected_requirements = {row["requirement_id"] for row in requirements["requirements"]}
    if packet_requirements != expected_requirements or len(packet_requirement_rows) != len(packet_requirements):
        raise ContractError("synchronous recovery partitions must cover every requirement exactly once")
    return tuple(packets)


def build_packet(context: SourceContext) -> TaskPacket:
    """Compatibility helper for callers whose evidence fits one semantic partition."""
    packets = build_packets(context)
    if len(packets) != 1:
        raise ContractError("synchronous recovery is partitioned; use build_packets")
    return packets[0]


def register(module_run: str | Path) -> list[str]:
    """Register every bounded synchronous-recovery partition through ModuleDriver."""
    driver = ModuleDriver(module_run)
    return driver.register(build_packets(driver.context))


def finalize(module_run: str | Path) -> Path:
    """Materialize the one validated synchronous result with its packet lineage."""
    driver = ModuleDriver(module_run)
    expected = {packet.task_id: packet for packet in build_packets(driver.context)}
    tasks: list[dict[str, Any]] = []
    for task_id, expected_packet in sorted(expected.items()):
        packet, output = driver.validated_task(task_id)
        if packet.input_digest != expected_packet.input_digest or packet.output_schema_id != OUTPUT_SCHEMA_ID:
            raise ContractError("validated synchronous recovery does not match the current evidence packet")
        tasks.append({
            "task_id": task_id, "packet_input_digest": packet.input_digest,
            "partition": json.loads(packet.inputs["partition.json"].content), "output": output,
        })
    document = {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(driver.context.manifest.to_dict()),
        "feature_graph_digest": json.loads(next(iter(expected.values())).inputs["sync-requirements.json"].content)["feature_graph_digest"],
        "semantic_spans_digest": json.loads(next(iter(expected.values())).inputs["sync-requirements.json"].content)["semantic_spans_digest"],
        "tasks": tasks,
    }
    out = create_stage_dir(driver.run / "evidence") / FILENAME
    write_new_text(out, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return out
