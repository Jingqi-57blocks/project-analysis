"""Deterministic structural graph for one selected Module Drill feature."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from .candidate_universe import load as load_universe
from .context import SourceContext
from .model import FeatureEdge, FeatureNode
from .scope import ModuleScope
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "feature-graph/v1"
SCOPE_FILENAME = "module-scope.json"
FILENAME = "feature-graph.json"


def _token(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def _load_scope(context: SourceContext) -> ModuleScope:
    path = context.module_run / "evidence" / SCOPE_FILENAME
    resolution_path = context.module_run / "evidence" / "selector-resolution.json"
    try:
        scope = ModuleScope.from_dict(json.loads(path.read_text("utf-8")))
        resolution = json.loads(resolution_path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("a validated module scope is required before graph construction") from exc
    if scope.source_manifest_digest != sha256_json(context.manifest.to_dict()):
        raise ContractError("module scope does not bind the current source manifest")
    if not isinstance(resolution, dict) \
            or resolution.get("schema_version") != "selector-resolution/v1" \
            or resolution.get("decision") != "selected" \
            or resolution.get("selected_candidate_id") != scope.selected_candidate_id \
            or resolution.get("module_scope_digest") != sha256_json(scope.to_dict()):
        raise ContractError("module scope does not match its validated selection receipt")
    return scope


def _load_items(context: SourceContext) -> dict[str, dict[str, Any]]:
    path = context.module_run / "evidence" / "feature-evidence.json"
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("feature evidence is required before graph construction") from exc
    if not isinstance(document, dict) or document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError("feature evidence does not bind the current source manifest")
    rows = document.get("items")
    if not isinstance(rows, list):
        raise ContractError("feature evidence items must be a list")
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ContractError("feature evidence item must be an object")
        evidence_id = row.get("evidence_id")
        repositories = row.get("repository_refs")
        source_refs = row.get("source_refs")
        if not isinstance(evidence_id, str) or not evidence_id \
                or not isinstance(repositories, list) or not repositories \
                or not all(isinstance(value, str) and value for value in repositories) \
                or not isinstance(source_refs, list) or not all(isinstance(value, str) for value in source_refs):
            raise ContractError("feature evidence item has invalid graph fields")
        if evidence_id in items:
            raise ContractError("feature evidence contains duplicate evidence IDs")
        items[evidence_id] = row
    return items


def _selected_items(context: SourceContext, scope: ModuleScope,
                    items: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    universe = load_universe(context)
    selected = next((row for row in universe["candidates"]
                     if row["candidate_id"] == scope.selected_candidate_id), None)
    if selected is None:
        raise ContractError("module scope selected candidate is absent from the current universe")
    scope_candidate = next(row for row in scope.candidates
                           if row.candidate_id == scope.selected_candidate_id)
    if tuple(selected["seed_ids"]) != scope_candidate.seed_ids:
        raise ContractError("module scope selected candidate differs from the current universe")
    evidence_ids = selected["evidence_ids"]
    missing = [evidence_id for evidence_id in evidence_ids if evidence_id not in items]
    if missing:
        raise ContractError("selected candidate references missing feature evidence: " + ", ".join(missing))
    return tuple(items[evidence_id] for evidence_id in evidence_ids)


def _node(item: dict[str, Any]) -> FeatureNode:
    evidence_id = item["evidence_id"]
    return FeatureNode(
        node_id="node-" + evidence_id.removeprefix("evidence-"),
        kind=str(item.get("kind", "evidence")),
        repository_ref=item["repository_refs"][0],
        observation="observed",
        evidence_refs=tuple(item["source_refs"]),
    )


def _ui_route_edges(items: tuple[dict[str, Any], ...], nodes: dict[str, FeatureNode]) -> tuple[FeatureEdge, ...]:
    routes: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        if item.get("kind") != "route":
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            raise ContractError("route evidence has invalid data")
        method, path = data.get("method"), data.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise ContractError("route evidence lacks method or path")
        routes[(item["repository_refs"][0], method, path)] = item

    edges: list[FeatureEdge] = []
    for item in items:
        if item.get("kind") != "ui-action":
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            raise ContractError("UI action evidence has invalid data")
        backend, method, path = (data.get("target_repository_ref"),
                                 data.get("method"), data.get("path"))
        if not all(isinstance(value, str) and value for value in (backend, method, path)):
            raise ContractError("UI action evidence lacks route linkage")
        route = routes.get((backend, method, path))
        if route is None:
            continue
        source_id = nodes[item["evidence_id"]].node_id
        target_id = nodes[route["evidence_id"]].node_id
        edges.append(FeatureEdge(
            edge_id="edge-" + _token(source_id, target_id, "ui-route"),
            kind="ui-route", source_node_id=source_id, target_node_id=target_id,
            observation="observed",
            evidence_refs=tuple(sorted(set(item["source_refs"] + route["source_refs"]))),
        ))
    return tuple(sorted(edges, key=lambda edge: edge.edge_id))


def build(context: SourceContext) -> dict[str, Any]:
    """Build only the observed seed graph; unresolved work remains frontiers."""
    scope = _load_scope(context)
    selected = _selected_items(context, scope, _load_items(context))
    nodes = {item["evidence_id"]: _node(item) for item in selected}
    node_rows = tuple(sorted(nodes.values(), key=lambda node: node.node_id))
    edge_rows = _ui_route_edges(selected, nodes)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "module_scope_digest": sha256_json(scope.to_dict()),
        "feature_id": scope.feature_id,
        "nodes": [node.to_dict() for node in node_rows],
        "edges": [edge.to_dict() for edge in edge_rows],
        "frontiers": [frontier.to_dict() for frontier in scope.frontiers],
    }


def write(context: SourceContext) -> Path:
    """Persist the graph once as input to later frontier expansion waves."""
    out = create_stage_dir(context.module_run / "evidence") / FILENAME
    write_new_text(out, json.dumps(build(context), indent=2, sort_keys=True) + "\n")
    return out
