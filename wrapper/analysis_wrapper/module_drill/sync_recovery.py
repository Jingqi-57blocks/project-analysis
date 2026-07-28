"""Bounded task preparation for synchronous Module Drill semantic recovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from ..orchestrator.contracts import TaskPacket
from .context import SourceContext
from .driver import ModuleDriver
from .validation import ContractError, sha256_json

TASK_ID = "module-sync-recovery"
TASK_TYPE = "module-sync-recovery"
TEMPLATE_ID = "module-sync-recovery"
TEMPLATE_VERSION = "v1"
OUTPUT_SCHEMA_ID = "module-sync-recovery/v1"
CONTEXT_BUDGET_TOKENS = 32_000
SCHEMA_VERSION = "sync-recovery/v1"
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


def build_packet(context: SourceContext) -> TaskPacket:
    """Build one local semantic partition from verified graph and span evidence."""
    graph = _load(context, "feature-graph.json", _ARTIFACTS["feature-graph.json"])
    spans = _load(context, "semantic-spans.json", _ARTIFACTS["semantic-spans.json"])
    if spans.get("feature_graph_digest") != sha256_json(graph):
        raise ContractError("semantic spans do not bind the current feature graph")
    requirements = _requirements(graph, spans)
    return TaskPacket.create(
        task_id=TASK_ID,
        task_type=TASK_TYPE,
        template_id=TEMPLATE_ID,
        template_version=TEMPLATE_VERSION,
        instructions=_INSTRUCTIONS,
        inputs={
            "sync-requirements.json": json.dumps(requirements, sort_keys=True),
            "feature-graph.json": json.dumps(graph, sort_keys=True),
            "semantic-spans.json": json.dumps(spans, sort_keys=True),
        },
        output_schema_id=OUTPUT_SCHEMA_ID,
        context_budget_tokens=CONTEXT_BUDGET_TOKENS,
    )


def register(module_run: str | Path) -> list[str]:
    """Register the bounded synchronous-recovery task through ModuleDriver."""
    driver = ModuleDriver(module_run)
    return driver.register((build_packet(driver.context),))


def finalize(module_run: str | Path) -> Path:
    """Materialize the one validated synchronous result with its packet lineage."""
    driver = ModuleDriver(module_run)
    packet, output = driver.validated_task(TASK_ID)
    expected = build_packet(driver.context)
    if packet.input_digest != expected.input_digest or packet.output_schema_id != OUTPUT_SCHEMA_ID:
        raise ContractError("validated synchronous recovery does not match the current evidence packet")
    document = {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(driver.context.manifest.to_dict()),
        "task_id": TASK_ID,
        "packet_input_digest": packet.input_digest,
        "feature_graph_digest": json.loads(packet.inputs["sync-requirements.json"].content)["feature_graph_digest"],
        "semantic_spans_digest": json.loads(packet.inputs["sync-requirements.json"].content)["semantic_spans_digest"],
        "output": output,
    }
    out = create_stage_dir(driver.run / "evidence") / FILENAME
    write_new_text(out, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return out
