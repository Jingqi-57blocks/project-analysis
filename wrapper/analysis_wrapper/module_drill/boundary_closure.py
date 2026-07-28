"""Source-span-bound closure for feature-local non-call boundaries.

The complete provider index may contain every timer, configuration reference,
datastore access, access check, and integration candidate in a repository.  A
feature may consume one only when its evidence site is inside a fetched span
for an already observed handler/service node.  This is intentionally a narrow
closure step: absence of that relation is an excluded or unresolved boundary,
never a reason to absorb a repository-wide candidate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from ..system_model.ids import parse_citation
from .context import SourceContext
from .feature_evidence import FILENAME as EVIDENCE_FILENAME
from .graph_closure import FILENAME as GRAPH_CLOSURE_FILENAME
from .span_fetch import FILENAME as SPANS_FILENAME
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "feature-boundary-closure/v1"
FILENAME = "feature-boundary-closure.json"

_BOUNDARY_KINDS = frozenset({
    "async-boundary", "configuration", "datastore", "access-check",
    "integration-host", "integration-package",
})
_EDGE_KINDS = {
    "async-boundary": "async-boundary",
    "configuration": "configuration-boundary",
    "datastore": "datastore-boundary",
    "access-check": "authorization-boundary",
    "integration-host": "integration-boundary",
    "integration-package": "integration-boundary",
}


def _load(context: SourceContext, filename: str, schema: str) -> dict[str, Any]:
    path = context.module_run / "evidence" / filename
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{filename} is required before feature boundary closure") from exc
    if not isinstance(document, dict) or document.get("schema_version") != schema:
        raise ContractError(f"{filename} has an unsupported schema")
    if document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError(f"{filename} does not bind the current source manifest")
    return document


def _token(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def _parts(ref: str) -> tuple[str, str, str, int]:
    repository, revision, path, line, _ = parse_citation(ref)
    if not repository or not revision or not path or line is None:
        raise ContractError("feature boundary evidence must be an exact source reference")
    return repository, revision, path, line


def _within(span: dict[str, Any], ref: str) -> bool:
    """Whether ``ref`` is inside one fetched semantic span, inclusively."""
    if span.get("status") != "fetched":
        return False
    start, end = span.get("start_ref"), span.get("end_ref")
    if not isinstance(start, str) or not isinstance(end, str):
        return False
    repo, revision, path, line = _parts(ref)
    start_repo, start_revision, start_path, start_line = _parts(start)
    end_repo, end_revision, end_path, end_line = _parts(end)
    return (repo, revision, path) == (start_repo, start_revision, start_path) \
        and (repo, revision, path) == (end_repo, end_revision, end_path) \
        and start_line <= line <= end_line


def _same_file(left: str, right: str) -> bool:
    left_repo, left_revision, left_path, _ = _parts(left)
    right_repo, right_revision, right_path, _ = _parts(right)
    return (left_repo, left_revision, left_path) == (right_repo, right_revision, right_path)


def _item_node_id(evidence_id: str) -> str:
    return "node-boundary-" + hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()[:20]


def _async_role(item: dict[str, Any]) -> str:
    """Classify only the observed API direction; never infer a counterpart."""
    if item.get("kind") != "async-boundary":
        return "not-applicable"
    data = item.get("data")
    operation = data.get("operation") if isinstance(data, dict) else ""
    if operation in {"emit", "publish", "Emit", "Publish"}:
        return "producer"
    if operation in {"subscribe", "on", "addListener", "Subscribe", "On"}:
        return "consumer"
    return "unknown"


def _disposition(item: dict[str, Any], *, state: str, reason: str,
                 resulting_ids: list[str], coverage_impact: str) -> dict[str, Any]:
    evidence_id = item["evidence_id"]
    return {
        "evidence_id": evidence_id, "boundary_kind": item["kind"],
        "async_role": _async_role(item),
        # This key prevents a later traversal wave from silently re-expanding
        # the same handler-to-boundary pair.
        "cycle_key": "boundary-" + _token(evidence_id),
        "evidence_refs": sorted(item["source_refs"]),
        "data": dict(item.get("data", {})),
        "state": state, "reason": reason, "resulting_ids": resulting_ids,
        "coverage_impact": coverage_impact,
    }


def build(context: SourceContext) -> dict[str, Any]:
    """Attach only source-span-local provider candidates to the feature graph."""
    graph = _load(context, GRAPH_CLOSURE_FILENAME, "feature-graph-closure/v1")
    evidence = _load(context, EVIDENCE_FILENAME, "feature-evidence/v1")
    spans = _load(context, SPANS_FILENAME, "semantic-spans/v1")
    if spans.get("feature_graph_digest") != graph.get("feature_graph_digest"):
        raise ContractError("semantic spans and graph closure bind different feature graphs")

    nodes = {row.get("node_id"): dict(row) for row in graph.get("nodes", []) if isinstance(row, dict)}
    edges = {row.get("edge_id"): dict(row) for row in graph.get("edges", []) if isinstance(row, dict)}
    if None in nodes or None in edges:
        raise ContractError("graph closure nodes and edges require stable IDs")
    handler_ids = {
        node_id for node_id, node in nodes.items()
        if node.get("kind") == "symbol" and node.get("observation") == "observed"
    }
    fetched_spans = [row for row in spans.get("spans", []) if isinstance(row, dict)]
    if not all(isinstance(row.get("span_id"), str) for row in fetched_spans):
        raise ContractError("semantic span rows require stable IDs")

    dispositions: list[dict[str, Any]] = []
    handler_dispositions: list[dict[str, Any]] = []
    items = evidence.get("items")
    if not isinstance(items, list):
        raise ContractError("feature evidence items must be a list")
    for item in sorted(items, key=lambda row: str(row.get("evidence_id", ""))):
        if not isinstance(item, dict) or item.get("kind") not in _BOUNDARY_KINDS:
            continue
        evidence_id, refs, repositories = item.get("evidence_id"), item.get("source_refs"), item.get("repository_refs")
        if not isinstance(evidence_id, str) or not evidence_id \
                or not isinstance(refs, list) or not refs \
                or not isinstance(repositories, list) or not repositories \
                or not all(isinstance(ref, str) and ref for ref in refs):
            raise ContractError("feature boundary evidence item is invalid")
        existing_id = "node-" + evidence_id.removeprefix("evidence-")
        if existing_id in nodes:
            dispositions.append(_disposition(
                item, state="excluded", reason="already represented by selected feature graph",
                resulting_ids=[existing_id], coverage_impact="none"))
            continue
        matches: list[tuple[str, dict[str, Any], str]] = []
        unresolved_handlers: set[str] = set()
        for handler_id in sorted(handler_ids):
            handler_refs = nodes[handler_id].get("evidence_refs", [])
            if not isinstance(handler_refs, list):
                raise ContractError("handler node lacks evidence refs")
            handler_spans = [span for span in fetched_spans
                             if span.get("ref") in handler_refs]
            for ref in refs:
                matching_span = next((span for span in handler_spans if _within(span, ref)), None)
                if matching_span is not None:
                    matches.append((handler_id, matching_span, ref))
                elif any(span.get("status") != "fetched"
                         and isinstance(span.get("ref"), str) and _same_file(span["ref"], ref)
                         for span in handler_spans):
                    # An unresolved handler span affects only provider evidence
                    # in that same source file; unrelated repository-wide
                    # candidates remain explicitly excluded, not unknown.
                    unresolved_handlers.add(handler_id)
        if not matches:
            if unresolved_handlers:
                dispositions.append(_disposition(
                    item, state="unresolved", reason="feature-scoped handler semantic span is unresolved",
                    resulting_ids=[], coverage_impact="partial"))
            else:
                dispositions.append(_disposition(
                    item, state="excluded",
                    reason="no exact feature-scoped handler span contains this provider evidence",
                    resulting_ids=[], coverage_impact="none"))
            continue
        node_id = _item_node_id(evidence_id)
        nodes[node_id] = {
            "node_id": node_id, "kind": item["kind"], "repository_ref": repositories[0],
            "observation": "observed", "evidence_refs": sorted(set(refs)),
        }
        result_ids = [node_id]
        for handler_id, span, ref in sorted(matches, key=lambda row: (row[0], row[2])):
            edge_id = "edge-" + _token(handler_id, node_id, str(span["span_id"]), ref)
            edges[edge_id] = {
                "edge_id": edge_id, "kind": _EDGE_KINDS[item["kind"]],
                "source_node_id": handler_id, "target_node_id": node_id,
                "observation": "observed", "evidence_refs": sorted({ref, str(span["ref"])}),
            }
            result_ids.append(edge_id)
        dispositions.append(_disposition(
            item, state="expanded",
            reason="provider evidence is inside a fetched feature-scoped handler span",
            resulting_ids=sorted(result_ids), coverage_impact="none"))

    for handler_id in sorted(handler_ids):
        linked = [row["evidence_id"] for row in dispositions
                  if row["state"] == "expanded" and any(
                      edge.get("source_node_id") == handler_id and edge.get("edge_id") in row["resulting_ids"]
                      for edge in edges.values())]
        handler_dispositions.append({
            "frontier_id": "frontier-boundary-" + _token(handler_id),
            "anchor_id": handler_id,
            "state": "expanded" if linked else "terminal",
            "resulting_ids": sorted(linked),
            "reason": "expanded source-span-local provider boundaries" if linked
                      else "no source-span-local provider boundary evidence",
        })
    if len({row["evidence_id"] for row in dispositions}) != len(dispositions):
        raise ContractError("each feature boundary evidence item requires one disposition")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "feature_graph_closure_digest": sha256_json(graph),
        "feature_evidence_digest": sha256_json(evidence),
        "semantic_spans_digest": sha256_json(spans),
        "feature_id": graph.get("feature_id"),
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": [edges[key] for key in sorted(edges)],
        "handler_frontier_dispositions": handler_dispositions,
        "boundary_dispositions": dispositions,
    }


def write(context: SourceContext) -> Path:
    out = create_stage_dir(context.module_run / "evidence") / FILENAME
    write_new_text(out, json.dumps(build(context), indent=2, sort_keys=True) + "\n")
    return out
