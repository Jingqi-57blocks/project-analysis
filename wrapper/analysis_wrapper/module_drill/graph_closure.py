"""Deterministic first closure of feature-scoped structural frontiers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from .context import SourceContext
from .feature_graph import FILENAME as GRAPH_FILENAME
from .frontier_candidates import FILENAME as CANDIDATES_FILENAME
from .frontier_receipts import FILENAME as STATE_FILENAME
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "feature-graph-closure/v1"
FILENAME = "feature-graph-closure.json"


def _load(context: SourceContext, filename: str, schema: str) -> dict[str, Any]:
    path = context.module_run / "evidence" / filename
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{filename} is required before feature graph closure") from exc
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise ContractError(f"{filename} has an unsupported schema")
    if value.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError(f"{filename} does not bind the current source manifest")
    return value


def _token(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def _anchor_node_id(frontier: dict[str, Any]) -> str:
    return "node-" + frontier["anchor_id"].removeprefix("seed-")


def build(context: SourceContext) -> dict[str, Any]:
    """Materialize exact observed call candidates; disclose every other frontier."""
    graph = _load(context, GRAPH_FILENAME, "feature-graph/v1")
    state = _load(context, STATE_FILENAME, "frontier-state/v1")
    candidates = _load(context, CANDIDATES_FILENAME, "frontier-candidates/v1")
    graph_digest = sha256_json(graph)
    if state.get("feature_graph_digest") != graph_digest or candidates.get("feature_graph_digest") != graph_digest:
        raise ContractError("frontier inputs do not bind the current feature graph")
    if candidates.get("frontier_state_digest") != sha256_json(state):
        raise ContractError("frontier candidates do not bind the current frontier state")
    frontiers = {row.get("frontier_id"): row for row in graph.get("frontiers", []) if isinstance(row, dict)}
    states = {row.get("frontier_id"): row for row in state.get("frontiers", []) if isinstance(row, dict)}
    if set(frontiers) != set(states) or None in frontiers:
        raise ContractError("frontier state must disposition the complete graph frontier universe")
    nodes = {row.get("node_id"): dict(row) for row in graph.get("nodes", []) if isinstance(row, dict)}
    edges = {row.get("edge_id"): dict(row) for row in graph.get("edges", []) if isinstance(row, dict)}
    if None in nodes or None in edges:
        raise ContractError("feature graph nodes and edges require stable IDs")
    by_frontier: dict[str, list[dict[str, Any]]] = {}
    for row in candidates.get("candidates", []):
        if not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str):
            raise ContractError("frontier candidate lacks a stable ID")
        frontier_id = row.get("frontier_id")
        if frontier_id not in frontiers:
            raise ContractError("frontier candidate is outside the graph frontier universe")
        by_frontier.setdefault(frontier_id, []).append(row)
    frontier_dispositions: list[dict[str, Any]] = []
    candidate_dispositions: list[dict[str, Any]] = []
    for frontier_id, frontier in sorted(frontiers.items()):
        receipt = states[frontier_id]
        if receipt.get("state") == "expanded":
            frontier_dispositions.append(dict(receipt))
            continue
        rows = sorted(by_frontier.get(frontier_id, []), key=lambda row: row["candidate_id"])
        expanded_ids: list[str] = []
        for candidate in rows:
            if candidate.get("observation") != "observed":
                candidate_dispositions.append({
                    "candidate_id": candidate["candidate_id"], "state": "unresolved",
                    "reason": "candidate call is not an exact observed relationship", "resulting_ids": [],
                })
                continue
            target_id = candidate.get("target_id")
            source_id = _anchor_node_id(frontier)
            target_ref = candidate.get("target_ref")
            refs = candidate.get("evidence_refs")
            if not all(isinstance(value, str) and value for value in (target_id, source_id, target_ref)) \
                    or not isinstance(refs, list) or not all(isinstance(ref, str) and ref for ref in refs):
                raise ContractError("observed frontier candidate has invalid structural fields")
            if source_id not in nodes:
                raise ContractError("frontier candidate source anchor is absent from the graph")
            nodes.setdefault(target_id, {
                "node_id": target_id, "kind": candidate.get("target_kind", "symbol"),
                "repository_ref": candidate.get("target_repository_ref", ""), "observation": "observed",
                "evidence_refs": sorted({target_ref, *refs}),
            })
            edge_id = "edge-" + _token(source_id, target_id, candidate["candidate_id"])
            edges[edge_id] = {
                "edge_id": edge_id, "kind": candidate.get("edge_kind", "call"),
                "source_node_id": source_id, "target_node_id": target_id, "observation": "observed",
                "evidence_refs": sorted(set(refs)),
            }
            expanded_ids.append(target_id)
            candidate_dispositions.append({
                "candidate_id": candidate["candidate_id"], "state": "expanded",
                "reason": "exact observed bounded call candidate", "resulting_ids": [target_id, edge_id],
            })
        if expanded_ids:
            frontier_dispositions.append({
                "frontier_id": frontier_id, "state": "expanded", "resulting_ids": sorted(set(expanded_ids)),
                "reason": "expanded exact observed bounded call candidates",
            })
        else:
            frontier_dispositions.append({
                "frontier_id": frontier_id, "state": "unresolved", "resulting_ids": [],
                "reason": "no exact observed bounded candidate resolves this frontier",
            })
    if len({row["candidate_id"] for row in candidate_dispositions}) != len(candidate_dispositions):
        raise ContractError("each frontier candidate must have exactly one disposition")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "feature_graph_digest": graph_digest,
        "frontier_state_digest": sha256_json(state),
        "frontier_candidates_digest": sha256_json(candidates),
        "feature_id": graph.get("feature_id"),
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": [edges[key] for key in sorted(edges)],
        "frontier_dispositions": sorted(frontier_dispositions, key=lambda row: row["frontier_id"]),
        "candidate_dispositions": sorted(candidate_dispositions, key=lambda row: row["candidate_id"]),
    }


def write(context: SourceContext) -> Path:
    out = create_stage_dir(context.module_run / "evidence") / FILENAME
    write_new_text(out, json.dumps(build(context), indent=2, sort_keys=True) + "\n")
    return out
