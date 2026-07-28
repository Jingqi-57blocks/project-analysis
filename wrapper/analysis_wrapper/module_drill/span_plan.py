"""Deterministic semantic-span requests for bounded Module Drill recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from .context import SourceContext
from .feature_graph import FILENAME as GRAPH_FILENAME
from .frontier_candidates import FILENAME as CANDIDATES_FILENAME
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "semantic-span-requests/v1"
FILENAME = "semantic-span-requests.json"


def _load(context: SourceContext, filename: str, schema: str) -> dict[str, Any]:
    path = context.module_run / "evidence" / filename
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{filename} is required before semantic span planning") from exc
    if not isinstance(document, dict) or document.get("schema_version") != schema:
        raise ContractError(f"{filename} has an unsupported schema")
    if document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError(f"{filename} does not bind the current source manifest")
    return document


def _span_id(kind: str, ref: str) -> str:
    return "span-" + hashlib.sha256((kind + "\x1f" + ref).encode("utf-8")).hexdigest()[:20]


def _node_kind(node: dict[str, Any]) -> str:
    kind = node.get("kind")
    if kind == "route":
        return "handler"
    if kind == "configuration":
        return "config-block"
    return "function"


def _add(requests: dict[tuple[str, str], dict[str, str]], *, kind: str, ref: str, purpose: str) -> None:
    if not isinstance(ref, str) or not ref:
        raise ContractError("semantic span source ref must be non-empty")
    requests.setdefault((kind, ref), {
        "span_id": _span_id(kind, ref), "kind": kind, "ref": ref, "purpose": purpose,
    })


def build(context: SourceContext) -> dict[str, Any]:
    """Plan only span reads justified by selected graph anchors or call candidates."""
    graph = _load(context, GRAPH_FILENAME, "feature-graph/v1")
    candidates = _load(context, CANDIDATES_FILENAME, "frontier-candidates/v1")
    if candidates.get("feature_graph_digest") != sha256_json(graph):
        raise ContractError("frontier candidates do not bind the current feature graph")
    requests: dict[tuple[str, str], dict[str, str]] = {}
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or not isinstance(node.get("evidence_refs"), list):
            raise ContractError("feature graph node has invalid evidence refs")
        for ref in node["evidence_refs"]:
            _add(requests, kind=_node_kind(node), ref=ref,
                 purpose="verify selected structural graph anchor")
    for candidate in candidates.get("candidates", []):
        if not isinstance(candidate, dict):
            raise ContractError("frontier candidate must be an object")
        target_ref = candidate.get("target_ref")
        _add(requests, kind="function", ref=target_ref,
             purpose="verify bounded candidate call target")
    ordered = sorted(requests.values(), key=lambda row: row["span_id"])
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "feature_graph_digest": sha256_json(graph),
        "frontier_candidates_digest": sha256_json(candidates),
        "feature_id": graph.get("feature_id"),
        "requests": ordered,
    }


def write(context: SourceContext) -> Path:
    """Persist a deterministic span plan once; fetching remains a separate step."""
    out = create_stage_dir(context.module_run / "evidence") / FILENAME
    write_new_text(out, json.dumps(build(context), indent=2, sort_keys=True) + "\n")
    return out
