"""Candidate-ranking task preparation for Module Drill.

This module makes one deliberately narrow LLM task.  It does not form a
candidate, traverse a dependency, or recover business behaviour: the task can
only select an existing deterministic candidate ID, or leave the selector
unresolved.  Scope materialization is a later, separate stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..orchestrator.contracts import TaskPacket
from .candidate_universe import load as load_universe
from .context import SourceContext
from .driver import ModuleDriver
from .exact_selector import write as write_exact_resolution
from .validation import ContractError, sha256_json

TASK_ID = "module-candidate-ranking"
TASK_TYPE = "module-candidate-ranking"
TEMPLATE_ID = "module-candidate-ranking"
TEMPLATE_VERSION = "v3"
OUTPUT_SCHEMA_ID = "module-candidate-ranking/v2"
CONTEXT_BUDGET_TOKENS = 24_000
PLAN_VERSION = "candidate-ranking-plan/v1"
PLAN_FILENAME = "candidate-ranking-plan.json"

# Candidate ranking is a selection task, not a source-reading task.  Keep its
# compact index comfortably below the declared context budget, and partition
# by stable candidate order before a model ever sees it.  The complete
# canonical evidence index remains local and is rechecked at finalization.
MAX_PACKET_BYTES = 80_000

_INSTRUCTIONS = """Resolve the user's selector against only the supplied deterministic candidates.

Return exactly one JSON object matching module-candidate-ranking/v2.
Never create, rename, merge, or infer a candidate ID. Do not claim any source
fact in this response. For a whole feature, select every supplied candidate
directly supported by the selector; do not silently discard a directly
supported candidate. If the packet cannot distinguish competing feature
interpretations, return decision=ambiguous with those existing IDs. If it
provides no supporting candidate, return decision=no-match.
"""


def _load_selector(context: SourceContext) -> str:
    try:
        provenance = json.loads((context.module_run / "provenance.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("module provenance is invalid for candidate ranking") from exc
    selector = provenance.get("selector") if isinstance(provenance, dict) else None
    if not isinstance(selector, str) or not selector.strip():
        raise ContractError("module provenance has no non-empty selector")
    return selector


def _load_feature_evidence(context: SourceContext, universe: dict[str, Any]) -> list[dict[str, Any]]:
    path = context.module_run / "evidence" / "feature-evidence.json"
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("feature evidence is invalid for candidate ranking") from exc
    if not isinstance(document, dict) or sha256_json(document) != universe["feature_evidence_digest"]:
        raise ContractError("candidate universe and feature evidence disagree")
    items = document.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ContractError("feature evidence items are invalid for candidate ranking")
    return items


def _compact_scalar_values(value: object, *, limit: int = 24) -> list[str]:
    """Return a bounded, technology-neutral semantic index of provider data."""
    result: list[str] = []

    def visit(current: object) -> None:
        if len(result) >= limit:
            return
        if isinstance(current, (str, int, float, bool)) or current is None:
            text = "" if current is None else str(current)
            if text and len(text) <= 240:
                result.append(text)
            return
        if isinstance(current, list):
            for item in current:
                visit(item)
                if len(result) >= limit:
                    break
        elif isinstance(current, dict):
            for key in sorted(current):
                if key in {"source_refs", "evidence", "content"}:
                    continue
                visit(current[key])
                if len(result) >= limit:
                    break

    visit(value)
    return sorted(set(result))


def _locator(ref: object) -> str:
    """Keep one evidence locator without copying a candidate's full support set."""
    if not isinstance(ref, str):
        return ""
    repository, marker, path_and_line = ref.partition("@")
    return f"{repository}:{path_and_line}" if marker and path_and_line else ""


def _compact_candidate_index(universe: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Produce selection-only summaries; never put canonical evidence in a packet."""
    by_id = {item.get("evidence_id"): item for item in items if isinstance(item.get("evidence_id"), str)}
    result: list[dict[str, Any]] = []
    for candidate in universe["candidates"]:
        evidence_ids = candidate["evidence_ids"]
        evidence = [by_id.get(evidence_id) for evidence_id in evidence_ids]
        if any(item is None for item in evidence):
            raise ContractError("candidate universe references missing feature evidence")
        summaries: list[dict[str, Any]] = []
        for item in evidence:
            assert isinstance(item, dict)  # narrowed by the guard above
            refs = item.get("source_refs")
            summaries.append({
                "kind": item.get("kind", ""),
                "repositories": item.get("repository_refs", []),
                "locator": _locator(refs[0]) if isinstance(refs, list) and refs else "",
                "terms": _compact_scalar_values(item.get("data", {})),
            })
        result.append({
            "candidate_id": candidate["candidate_id"],
            "repository_refs": candidate["repository_refs"],
            "reason": candidate["reason"],
            "evidence": summaries,
        })
    return sorted(result, key=lambda row: row["candidate_id"])


def _partitions(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Partition the complete candidate universe without omission or overlap."""
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        row_bytes = len(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if row_bytes > MAX_PACKET_BYTES:
            raise ContractError("one compact candidate index row exceeds the ranking packet budget")
        # Include the JSON envelope in the budget check.  The partition ID is
        # fixed-width, so the provisional value precisely bounds every real
        # packet generated below.
        trial = current + [row]
        trial_bytes = len(json.dumps({
            "schema_version": PLAN_VERSION, "partition_id": "ranking-999", "candidates": trial,
        }, sort_keys=True).encode("utf-8"))
        if not current and trial_bytes > MAX_PACKET_BYTES:
            raise ContractError("one compact candidate index row exceeds the ranking packet budget")
        if current and trial_bytes > MAX_PACKET_BYTES:
            result.append(current)
            current = []
        current.append(row)
    if current:
        result.append(current)
    if not result:
        raise ContractError("candidate ranking requires at least one deterministic candidate")
    return result


def _packet(context: SourceContext, *, task_id: str,
            partition_id: str, rows: list[dict[str, Any]]) -> TaskPacket:
    partition = {
        "schema_version": PLAN_VERSION,
        "partition_id": partition_id,
        "candidates": rows,
    }
    encoded = json.dumps(partition, sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_PACKET_BYTES:
        raise ContractError("candidate ranking partition exceeds the packet byte budget")
    return TaskPacket.create(
        task_id=task_id,
        task_type=TASK_TYPE,
        template_id=TEMPLATE_ID,
        template_version=TEMPLATE_VERSION,
        instructions=_INSTRUCTIONS,
        inputs={
            "selector.json": json.dumps({"selector": _load_selector(context)}, sort_keys=True),
            "candidate-partition.json": encoded,
        },
        output_schema_id=OUTPUT_SCHEMA_ID,
        context_budget_tokens=CONTEXT_BUDGET_TOKENS,
    )


def build_packets(context: SourceContext) -> tuple[TaskPacket, ...]:
    """Build every bounded selection packet for the immutable candidate universe."""
    universe = load_universe(context)
    rows = _compact_candidate_index(universe, _load_feature_evidence(context, universe))
    partitions = _partitions(rows)
    total = len(partitions)
    return tuple(_packet(
        context,
        task_id=TASK_ID if total == 1 else f"{TASK_ID}-{index + 1:03d}",
        partition_id=f"ranking-{index + 1:03d}",
        rows=partition,
    ) for index, partition in enumerate(partitions))


def build_plan(context: SourceContext) -> dict[str, Any]:
    """Persist the complete partition universe so finalization can prove no loss."""
    universe = load_universe(context)
    packets = build_packets(context)
    return {
        "schema_version": PLAN_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "candidate_universe_digest": sha256_json(universe),
        "partitions": [{
            "partition_id": json.loads(packet.inputs["candidate-partition.json"].content)["partition_id"],
            "task_id": packet.task_id,
            "candidate_ids": [row["candidate_id"] for row in json.loads(
                packet.inputs["candidate-partition.json"].content)["candidates"]],
            "packet_input_digest": packet.input_digest,
        } for packet in packets],
    }


def _write_plan(context: SourceContext, plan: dict[str, Any]) -> Path:
    path = context.module_run / "evidence" / PLAN_FILENAME
    if path.exists():
        try:
            existing = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError("candidate ranking plan is invalid") from exc
        if existing != plan:
            raise ContractError("candidate ranking plan does not match the current evidence universe")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    from ..executor import write_new_text
    write_new_text(path, json.dumps(plan, indent=2, sort_keys=True) + "\n")
    return path


def load_plan(context: SourceContext) -> dict[str, Any]:
    """Load and rederive a plan; stale, incomplete, or modified plans fail closed."""
    expected = build_plan(context)
    path = context.module_run / "evidence" / PLAN_FILENAME
    try:
        actual = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("candidate ranking plan is required before scope finalization") from exc
    if actual != expected:
        raise ContractError("candidate ranking plan does not match the current evidence universe")
    partition_ids = [row["candidate_id"] for packet in build_packets(context)
                     for row in json.loads(packet.inputs["candidate-partition.json"].content)["candidates"]]
    if len(partition_ids) != len(set(partition_ids)):
        raise ContractError("candidate ranking plan duplicates a candidate across partitions")
    if set(partition_ids) != {row["candidate_id"] for row in load_universe(context)["candidates"]}:
        raise ContractError("candidate ranking plan does not cover the full candidate universe")
    return actual


def build_packet(context: SourceContext) -> TaskPacket:
    """Return the single packet for small universes.

    Callers that support general projects must use :func:`build_packets` so a
    large candidate universe is never silently forced through one model call.
    """
    packets = build_packets(context)
    if len(packets) != 1:
        raise ContractError("candidate ranking is partitioned; use build_packets")
    return packets[0]


def register(module_run: str | Path) -> list[str]:
    """Register ranking only when exact evidence cannot resolve the selector.

    ``write_exact_resolution`` records the deterministic alternative before
    this function returns, so callers can continue directly to finalization
    without minting a pretend model task.
    """
    driver = ModuleDriver(module_run)
    if write_exact_resolution(driver.context) is not None:
        return []
    _write_plan(driver.context, build_plan(driver.context))
    return driver.register(build_packets(driver.context))
