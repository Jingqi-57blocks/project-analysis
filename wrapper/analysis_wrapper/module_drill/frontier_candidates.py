"""Deterministic call-edge candidates for unresolved structural frontiers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..callgraph.contract import CallEdge
from ..executor import create_stage_dir, write_new_text
from ..system_model.ids import parse_citation
from .context import SourceContext
from .feature_graph import FILENAME as GRAPH_FILENAME
from .frontier_receipts import FILENAME as STATE_FILENAME
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "frontier-candidates/v1"
FILENAME = "frontier-candidates.json"


def _load_json(context: SourceContext, filename: str, schema: str) -> dict[str, Any]:
    path = context.module_run / "evidence" / filename
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{filename} is required before frontier candidate construction") from exc
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise ContractError(f"{filename} has an unsupported schema")
    if value.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError(f"{filename} does not bind the current source manifest")
    return value


def _call_edges(context: SourceContext) -> tuple[CallEdge, ...]:
    rows: list[CallEdge] = []
    for artifact in context.manifest.artifacts:
        if artifact.kind != "canonical" or artifact.integrity != "verified" \
                or not artifact.relative_path.startswith("callgraph/") \
                or not artifact.relative_path.endswith(".jsonl"):
            continue
        path = context.source_run / artifact.relative_path
        try:
            resolved = path.resolve(strict=True)
            payload = path.read_bytes()
        except OSError as exc:
            raise ContractError(f"canonical callgraph fragment is missing: {artifact.relative_path}") from exc
        if path.is_symlink() or not resolved.is_relative_to(context.source_run):
            raise ContractError(f"canonical callgraph fragment is unsafe: {artifact.relative_path}")
        if hashlib.sha256(payload).hexdigest() != artifact.digest:
            raise ContractError(f"canonical callgraph fragment digest changed: {artifact.relative_path}")
        for line_no, line in enumerate(payload.decode("utf-8", errors="strict").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(CallEdge.from_dict(json.loads(line)))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ContractError(f"canonical callgraph fragment is invalid: {artifact.relative_path}:{line_no}") from exc
    return tuple(sorted(set(rows), key=lambda edge: edge.sort_key()))


def _same_file(left: str, right: str) -> bool:
    left_repo, left_revision, left_path, _, _ = parse_citation(left)
    right_repo, right_revision, right_path, _, _ = parse_citation(right)
    return bool(left_path) and (left_repo, left_revision, left_path) == (right_repo, right_revision, right_path)


def _candidate_id(frontier_id: str, edge: CallEdge) -> str:
    material = "\x1f".join((frontier_id, edge.callsite_citation, edge.callee_citation, edge.callee_symbol))
    return "frontier-candidate-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _for_route_handler(frontier: dict[str, Any], edges: tuple[CallEdge, ...]) -> list[dict[str, Any]]:
    anchor_refs = frontier.get("evidence_refs")
    if not isinstance(anchor_refs, list) or not all(isinstance(ref, str) for ref in anchor_refs):
        raise ContractError("feature graph route-handler frontier lacks evidence refs")
    candidates: list[dict[str, Any]] = []
    for edge in edges:
        if not any(_same_file(anchor, edge.callsite_citation) or _same_file(anchor, edge.caller_citation)
                       for anchor in anchor_refs):
            continue
        callee_repo, _, _, _, _ = parse_citation(edge.callee_citation)
        candidates.append({
            "candidate_id": _candidate_id(frontier["frontier_id"], edge),
            "frontier_id": frontier["frontier_id"],
            "target_id": "node-symbol-" + hashlib.sha256(
                (edge.callee_symbol + "\x1f" + edge.callee_citation).encode("utf-8")).hexdigest()[:20],
            "target_kind": "symbol",
            "target_repository_ref": callee_repo,
            "target_ref": edge.callee_citation,
            "observation": "observed" if edge.resolution == "observed" else "inferred",
            "edge_kind": "call",
            "caller_symbol": edge.caller_symbol,
            "callee_symbol": edge.callee_symbol,
            "evidence_refs": sorted({edge.callsite_citation, edge.caller_citation, edge.callee_citation}),
        })
    return sorted(candidates, key=lambda row: row["candidate_id"])


def build(context: SourceContext) -> dict[str, Any]:
    """Index candidate call edges without asserting they belong to the feature."""
    graph = _load_json(context, GRAPH_FILENAME, "feature-graph/v1")
    state = _load_json(context, STATE_FILENAME, "frontier-state/v1")
    if state.get("feature_graph_digest") != sha256_json(graph):
        raise ContractError("frontier state does not bind the current feature graph")
    graph_frontiers = {row.get("frontier_id"): row for row in graph.get("frontiers", [])
                       if isinstance(row, dict) and isinstance(row.get("frontier_id"), str)}
    call_edges = _call_edges(context)
    candidates: list[dict[str, Any]] = []
    for receipt in state.get("frontiers", []):
        if not isinstance(receipt, dict) or receipt.get("state") != "pending":
            continue
        frontier = graph_frontiers.get(receipt.get("frontier_id"))
        if frontier is None:
            raise ContractError("frontier state references a frontier absent from its graph")
        if frontier.get("edge_kind") == "route-handler":
            candidates.extend(_for_route_handler(frontier, call_edges))
    if len({row["candidate_id"] for row in candidates}) != len(candidates):
        raise ContractError("frontier candidate construction produced duplicate IDs")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "feature_graph_digest": sha256_json(graph),
        "frontier_state_digest": sha256_json(state),
        "feature_id": graph.get("feature_id"),
        "candidates": candidates,
    }


def write(context: SourceContext) -> Path:
    """Persist a candidate universe once for the bounded expansion task."""
    out = create_stage_dir(context.module_run / "evidence") / FILENAME
    write_new_text(out, json.dumps(build(context), indent=2, sort_keys=True) + "\n")
    return out
