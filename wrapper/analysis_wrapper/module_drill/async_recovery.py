"""Bounded semantic recovery for feature-local async and boundary behaviour."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
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


def build_packet(context: SourceContext) -> TaskPacket:
    """Build one semantic partition from span-bound feature boundaries."""
    closure = _load(context, "feature-boundary-closure.json", "feature-boundary-closure/v1")
    spans = _load(context, "semantic-spans.json", "semantic-spans/v1")
    if closure.get("semantic_spans_digest") != sha256_json(spans):
        raise ContractError("feature boundary closure does not bind current semantic spans")
    requirements = _requirements(closure)
    return TaskPacket.create(
        task_id=TASK_ID,
        task_type=TASK_TYPE,
        template_id=TEMPLATE_ID,
        template_version=TEMPLATE_VERSION,
        instructions=_INSTRUCTIONS,
        inputs={
            "async-requirements.json": json.dumps(requirements, sort_keys=True),
            "feature-boundary-closure.json": json.dumps(closure, sort_keys=True),
            "semantic-spans.json": json.dumps(spans, sort_keys=True),
        },
        output_schema_id=OUTPUT_SCHEMA_ID,
        context_budget_tokens=CONTEXT_BUDGET_TOKENS,
    )


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
