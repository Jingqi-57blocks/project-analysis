"""Deterministic receipts for frontier edges already proven by FeatureGraph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from .context import SourceContext
from .feature_graph import FILENAME as GRAPH_FILENAME, SCHEMA_VERSION as GRAPH_VERSION
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "frontier-state/v1"
FILENAME = "frontier-state.json"


def _load_graph(context: SourceContext) -> dict[str, Any]:
    path = context.module_run / "evidence" / GRAPH_FILENAME
    try:
        graph = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("feature graph is required before frontier receipts") from exc
    if not isinstance(graph, dict) or graph.get("schema_version") != GRAPH_VERSION:
        raise ContractError("feature graph has an unsupported schema")
    if graph.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError("feature graph does not bind the current source manifest")
    for field in ("nodes", "edges", "frontiers"):
        if not isinstance(graph.get(field), list):
            raise ContractError(f"feature graph {field} must be a list")
    return graph


def _state(frontier: dict[str, Any], edges: list[dict[str, Any]]) -> dict[str, Any]:
    anchor = frontier["anchor_id"]
    if frontier["edge_kind"] == "ui-route":
        source_node = "node-" + anchor.removeprefix("seed-")
        targets = sorted({edge["target_node_id"] for edge in edges
                          if edge.get("kind") == "ui-route"
                          and edge.get("source_node_id") == source_node})
        if targets:
            return {
                "frontier_id": frontier["frontier_id"], "state": "expanded",
                "resulting_ids": targets,
                "reason": "exact observed UI-to-route graph edge",
            }
    return {
        "frontier_id": frontier["frontier_id"], "state": "pending",
        "resulting_ids": [],
        "reason": "no deterministic graph edge resolves this frontier yet",
    }


def build(context: SourceContext) -> dict[str, Any]:
    """Resolve only exact graph edges and preserve all other work as pending."""
    graph = _load_graph(context)
    frontiers = graph["frontiers"]
    if len({row.get("frontier_id") for row in frontiers if isinstance(row, dict)}) != len(frontiers):
        raise ContractError("feature graph frontiers must have unique IDs")
    rows = []
    for frontier in sorted(frontiers, key=lambda row: (row.get("wave", -1), row.get("frontier_id", ""))):
        if not isinstance(frontier, dict) or not isinstance(frontier.get("frontier_id"), str) \
                or not isinstance(frontier.get("anchor_id"), str) \
                or not isinstance(frontier.get("edge_kind"), str):
            raise ContractError("feature graph frontier has invalid required fields")
        rows.append(_state(frontier, graph["edges"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "feature_graph_digest": sha256_json(graph),
        "feature_id": graph.get("feature_id"),
        "frontiers": rows,
    }


def write(context: SourceContext) -> Path:
    """Persist the first deterministic frontier-state receipt once."""
    out = create_stage_dir(context.module_run / "evidence") / FILENAME
    write_new_text(out, json.dumps(build(context), indent=2, sort_keys=True) + "\n")
    return out
