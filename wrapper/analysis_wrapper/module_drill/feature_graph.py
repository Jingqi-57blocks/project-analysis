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
            or resolution.get("selected_candidate_ids") != list(scope.selected_candidate_ids) \
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
    selected = [row for row in universe["candidates"] if row["candidate_id"] in scope.selected_candidate_ids]
    if {row["candidate_id"] for row in selected} != set(scope.selected_candidate_ids):
        raise ContractError("module scope selected candidate is absent from the current universe")
    scope_candidates = {row.candidate_id: row for row in scope.candidates if row.candidate_id in scope.selected_candidate_ids}
    if any(tuple(row["seed_ids"]) != scope_candidates[row["candidate_id"]].seed_ids for row in selected):
        raise ContractError("module scope selected candidate differs from the current universe")
    evidence_ids = [evidence_id for row in selected for evidence_id in row["evidence_ids"]]
    missing = [evidence_id for evidence_id in evidence_ids if evidence_id not in items]
    if missing:
        raise ContractError("selected candidate references missing feature evidence: " + ", ".join(missing))
    return tuple(items[evidence_id] for evidence_id in evidence_ids)


def _label(item: dict[str, Any]) -> str:
    """Return a stable source-derived node label without interpreting intent."""
    data = item.get("data")
    if not isinstance(data, dict):
        return ""
    kind = item.get("kind")
    if kind in {"route", "ui-action"}:
        method, path = data.get("method"), data.get("path")
        if isinstance(method, str) and method and isinstance(path, str):
            return f"{method} {path}".strip()
    if kind == "datastore":
        name = data.get("physical_name", data.get("name"))
        return name if isinstance(name, str) else ""
    if kind in {"access-check", "access-role", "configuration"}:
        name = data.get("name", data.get("category"))
        return name if isinstance(name, str) else ""
    if kind == "async-boundary":
        operation = data.get("operation")
        return operation if isinstance(operation, str) else ""
    if kind == "integration-host":
        value = data.get("value")
        return value if isinstance(value, str) else ""
    if kind == "integration-package":
        package = data.get("package")
        return package if isinstance(package, str) else ""
    return ""


def _node(item: dict[str, Any]) -> FeatureNode:
    evidence_id = item["evidence_id"]
    local_refs = item["source_refs"]
    if item.get("kind") == "ui-action":
        data = item.get("data")
        frontend_refs = data.get("frontend_source_refs") if isinstance(data, dict) else None
        if isinstance(frontend_refs, list) and frontend_refs \
                and all(isinstance(ref, str) and ref for ref in frontend_refs):
            local_refs = frontend_refs
    return FeatureNode(
        node_id="node-" + evidence_id.removeprefix("evidence-"),
        kind=str(item.get("kind", "evidence")),
        repository_ref=item["repository_refs"][0],
        observation="observed",
        evidence_refs=tuple(local_refs),
        label=_label(item),
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


def _route_handler_edges(items: tuple[dict[str, Any], ...],
                         nodes: dict[str, FeatureNode]) -> tuple[FeatureEdge, ...]:
    """Attach only uniquely source-resolved route handler definitions.

    A route inventory may also carry handler *references* which could not be
    resolved to a definition.  Those remain a pending frontier; constructing a
    node for them here would turn a token spelling into an invented code unit.
    """
    edges: list[FeatureEdge] = []
    for item in items:
        if item.get("kind") != "route":
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            raise ContractError("route evidence has invalid data")
        anchors = data.get("handler_anchors", [])
        if not isinstance(anchors, list):
            raise ContractError("route handler anchors must be a list")
        source_id = nodes[item["evidence_id"]].node_id
        repository_ref = item["repository_refs"][0]
        for anchor in anchors:
            if not isinstance(anchor, dict):
                raise ContractError("route handler anchor must be an object")
            symbol, refs = anchor.get("symbol"), anchor.get("source_refs")
            if not isinstance(symbol, str) or not symbol \
                    or not isinstance(refs, list) or not refs \
                    or not all(isinstance(ref, str) and ref for ref in refs):
                raise ContractError("route handler anchor has invalid fields")
            target_id = "node-handler-" + _token(repository_ref, symbol, *sorted(refs))
            nodes.setdefault(target_id, FeatureNode(
                node_id=target_id, kind="handler", repository_ref=repository_ref,
                observation="observed", evidence_refs=tuple(sorted(set(refs))), label=symbol,
            ))
            edges.append(FeatureEdge(
                edge_id="edge-" + _token(source_id, target_id, "routes-to"),
                kind="routes-to", source_node_id=source_id, target_node_id=target_id,
                observation="observed",
                evidence_refs=tuple(sorted(set(item["source_refs"] + refs))),
            ))
    return tuple(sorted(edges, key=lambda edge: edge.edge_id))


def build(context: SourceContext) -> dict[str, Any]:
    """Build only the observed seed graph; unresolved work remains frontiers."""
    scope = _load_scope(context)
    selected = _selected_items(context, scope, _load_items(context))
    nodes = {item["evidence_id"]: _node(item) for item in selected}
    ui_edges = _ui_route_edges(selected, nodes)
    handler_edges = _route_handler_edges(selected, nodes)
    node_rows = tuple(sorted(nodes.values(), key=lambda node: node.node_id))
    edge_rows = tuple(sorted((*ui_edges, *handler_edges), key=lambda edge: edge.edge_id))
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
