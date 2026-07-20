"""Deterministic, bounded overview synthesis packet.

This is a transport projection, not a diagnosis.  It selects and groups facts
from canonical artifacts, records counts and truncation, and never interprets
business meaning.  Every model effort level receives the same packet bytes for
the same deterministic artifacts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .sanitize import sanitize_text
from .executor import replace_artifact_text

SCHEMA_VERSION = "1.0.0"
_LIST_LIMIT = 200
_HUB_LIMIT = 100
_VIEW_LINE_LIMIT = 120
_CANDIDATE_LIMIT = 500
_SIGNAL_LIMIT = 200
_TEXT_LINE_LIMIT = 2_000


def _load(path: Path, default: dict | None = None) -> dict:
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return dict(default or {})
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bounded(items: list[dict], *, key, limit: int = _LIST_LIMIT) -> dict:
    ordered = sorted(items, key=key)
    selected = ordered[:limit]
    return {
        "total_count": len(ordered),
        "included_count": len(selected),
        "truncated": len(selected) < len(ordered),
        "items": selected,
    }


def _coverage_projection(coverage: dict) -> dict:
    projected = {}
    for name, partition in sorted(coverage.items()):
        row = dict(partition)
        counts = dict(row.get("counts", {}))
        if isinstance(counts.get("per_repo"), list):
            counts["per_repo"] = _bounded(
                counts["per_repo"], key=lambda item: (
                    str(item.get("repo_id", "")), str(item.get("lang", ""))))
        row["counts"] = counts
        projected[name] = row
    return projected


def _capability_projection(doc: dict) -> dict:
    rows = []
    for capability in doc.get("capabilities", []):
        row = dict(capability)
        row["details"] = _bounded(
            list(row.get("details", [])), key=lambda item: (
                str(item.get("repo_id", "")),
                str(item.get("lang", item.get("lane", ""))),
                str(item.get("tool", ""))))
        rows.append(row)
    return {key: value for key, value in doc.items() if key != "capabilities"} | {
        "capabilities": rows}


def _graph_projection(model: dict) -> dict:
    nodes = model.get("nodes", [])
    edges = model.get("edges", [])
    degree: dict[str, int] = {}
    for edge in edges:
        if edge.get("src"):
            degree[edge["src"]] = degree.get(edge["src"], 0) + 1
        if edge.get("dst"):
            degree[edge["dst"]] = degree.get(edge["dst"], 0) + 1
    by_id = {node["id"]: node for node in nodes}
    hubs = [{"node_id": node_id,
             "kind": by_id.get(node_id, {}).get("kind", ""),
             "label": by_id.get(node_id, {}).get("label", ""),
             "repo_id": by_id.get(node_id, {}).get("repo_id", ""),
             "degree": count,
             "evidence_basis": by_id.get(node_id, {}).get("evidence_basis", "")}
            for node_id, count in degree.items()]
    node_groups = {}
    for kind in ("repository", "module", "route", "data-store",
                 "external-boundary", "deployable-unit"):
        rows = [{"id": node.get("id", ""), "repo_id": node.get("repo_id", ""),
                 "label": node.get("label", ""), "status": node.get("status", ""),
                 "evidence_basis": node.get("evidence_basis", ""),
                 "evidence": node.get("evidence", []), "attrs": node.get("attrs", {})}
                for node in nodes if node.get("kind") == kind]
        node_groups[kind] = _bounded(
            rows, key=lambda row: (row["repo_id"], row["label"], row["id"]))
    edge_counts: dict[str, dict[str, int]] = {}
    for edge in edges:
        edge_type = str(edge.get("type", ""))
        status = str(edge.get("status", ""))
        edge_counts.setdefault(edge_type, {})[status] = \
            edge_counts.setdefault(edge_type, {}).get(status, 0) + 1
    return {
        "stats": model.get("stats", {}),
        "coverage": _coverage_projection(model.get("coverage", {})),
        "nodes": node_groups,
        "edges_by_type_and_status": {
            kind: dict(sorted(values.items()))
            for kind, values in sorted(edge_counts.items())},
        "highest_degree_nodes": _bounded(
            hubs, key=lambda row: (-row["degree"], row["kind"], row["node_id"]),
            limit=_HUB_LIMIT),
    }


def _signal_views(run: Path, summary: dict) -> dict:
    rows = []
    signals = sorted(summary.get("signals", []), key=lambda row: (
            str(row.get("repo_id", "")), str(row.get("tool", "")),
            str(row.get("view", ""))))
    for signal in signals[:_SIGNAL_LIMIT]:
        rel = str(signal.get("view", ""))
        path = run / "signals" / rel if rel else None
        lines = (path.read_text("utf-8", errors="replace").splitlines()
                 if path and path.is_file() else [])
        rows.append({
            "tool": signal.get("tool", ""),
            "repo_id": signal.get("repo_id", ""),
            "status": signal.get("status", ""),
            "reason": signal.get("reason", ""),
            "view": f"signals/{rel}" if rel else "",
            "sha256": _digest(path) if path and path.is_file() else "",
            "total_lines": len(lines),
            "included_lines": min(len(lines), _VIEW_LINE_LIMIT),
            "truncated": len(lines) > _VIEW_LINE_LIMIT,
            "content": [line[:_TEXT_LINE_LIMIT] for line in lines[:_VIEW_LINE_LIMIT]],
        })
    return {"total_count": len(signals), "included_count": len(rows),
            "truncated": len(rows) < len(signals), "items": rows}


def build(run_dir: str | Path) -> dict:
    run = Path(run_dir).expanduser().resolve()
    discovery = _load(run / "discovery-report.json")
    targets = _load(run / "targets.json")
    capabilities = _load(run / "capabilities.json")
    model = _load(run / "system-model.json")
    candidates = _load(run / "module-candidates.json")
    signal_summary = _load(run / "signals" / "run-summary.json")
    module_map = _load(run / "module-map.json")

    repos = []
    report_by_id = {row.get("repo_id", ""): row
                    for row in discovery.get("repos", [])}
    for repo in sorted(targets.get("repos", []), key=lambda row: row.get("repo_id", "")):
        block = report_by_id.get(repo.get("repo_id", ""), {})
        repos.append({
            "repo_id": repo.get("repo_id", ""),
            "stacks": repo.get("stacks", []),
            "analysis_roots": repo.get("analysis_roots", []),
            "tier2_exclusions": repo.get("tier2_exclusions", []),
            "git": repo.get("git", {}),
            "frameworks": block.get("stacks", {}).get("frameworks", []),
            "package_manager": block.get("package_manager", {}),
            "access_model": block.get("access_model", {}),
            "deployable_units": block.get("deployable_units", {}),
            "table_coverage": {
                "available": block.get("table_evidence", {}).get("available", False),
                "distinct_table_count": block.get("table_evidence", {}).get(
                    "distinct_table_count", 0),
                "notes": block.get("table_evidence", {}).get("notes", []),
            },
        })

    artifact_paths = [
        "targets.json", "discovery-report.json", "signals/run-summary.json",
        "callgraph-coverage.json", "imports/depmap-coverage.json",
        "system-model.json", "capabilities.json", "module-candidates.json",
        "coverage-summary.md",
        "module-summary.md",
    ]
    artifact_digests = {
        rel: _digest(run / rel) for rel in artifact_paths if (run / rel).is_file()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": discovery.get("project_id", ""),
        "input_artifact_sha256": dict(sorted(artifact_digests.items())),
        "capabilities": _capability_projection(capabilities),
        "repositories": _bounded(repos, key=lambda row: row["repo_id"]),
        "signal_summary": {
            "aggregate_status": signal_summary.get("aggregate_status", "failed"),
            "signals": _bounded(
                list(signal_summary.get("signals", [])), key=lambda row: (
                    str(row.get("repo_id", "")), str(row.get("tool", "")),
                    str(row.get("view", ""))), limit=_SIGNAL_LIMIT),
        },
        "signal_views": _signal_views(run, signal_summary),
        "graph": _graph_projection(model),
        # The packet stays bounded. The full, hash-addressed candidate artifact
        # remains the authority for the mandatory one-time disposition pass.
        "module_candidates": {
            "artifact": "module-candidates.json",
            "sha256": artifact_digests.get("module-candidates.json", ""),
            **_bounded(list(candidates.get("candidates", [])),
                       key=lambda row: row.get("candidate_id", ""),
                       limit=_CANDIDATE_LIMIT),
            "candidate_count": candidates.get("candidate_count", 0),
            "full_universe_required_for_module_map": True,
        },
        "module_map": (None if not module_map else {
            "artifact": "module-map.json",
            "modules": _bounded(list(module_map.get("modules", [])),
                                key=lambda row: row.get("module_id", "")),
            "candidate_dispositions": _bounded(
                list(module_map.get("candidate_dispositions", [])),
                key=lambda row: row.get("candidate_id", ""),
                limit=_CANDIDATE_LIMIT),
        }),
        "integration_candidates": _bounded(
            list(targets.get("integration_candidates", [])),
            key=lambda row: row.get("candidate_id", ""),
            limit=_CANDIDATE_LIMIT),
        "route_liveness": (None if not discovery.get("route_liveness") else {
            **{key: value for key, value in discovery["route_liveness"].items()
               if key not in {"rows", "calls_by_base"}},
            "calls_by_base": _bounded(
                [{"base": key,
                  "calls": _bounded([{"path": path} for path in value],
                                    key=lambda row: row["path"],
                                    limit=_CANDIDATE_LIMIT)}
                 for key, value in (discovery["route_liveness"].get(
                     "calls_by_base", {}) or {}).items()],
                key=lambda row: row["base"], limit=_CANDIDATE_LIMIT),
            "rows": _bounded(
                list(discovery["route_liveness"].get("rows", [])),
                key=lambda row: (str(row.get("repo_id", "")),
                                 str(row.get("method", "")),
                                 str(row.get("path", ""))),
                limit=_CANDIDATE_LIMIT),
        }),
        "role_catalog_by_repo": _bounded(
            [{"repo_id": key, "roles": value}
             for key, value in discovery.get("role_catalog_by_repo", {}).items()],
            key=lambda row: row["repo_id"]),
        "not_targeted": _bounded(
            [{"value": value} for value in discovery.get("not_targeted", [])],
            key=lambda row: row["value"]),
        "reduced_coverage_targets": _bounded(
            [{"value": value}
             for value in discovery.get("reduced_coverage_targets", [])],
            key=lambda row: row["value"]),
        "lens_findings": {
            "status": "supplied separately by the existing grouped lens stage",
            "interpretation_is_effort_dependent": True,
            "required_shape": "lenses/_shared.md",
        },
        "evidence_categories": {
            "interfaces": {"source": "graph.nodes.route",
                           "status": model.get("coverage", {}).get("routes", {}).get(
                               "status", "unavailable")},
            "data": {"source": "graph.nodes.data-store",
                     "status": model.get("coverage", {}).get("tables", {}).get(
                         "status", "unavailable")},
            "access": {"source": "repositories[].access_model",
                       "status": model.get("coverage", {}).get("access_model", {}).get(
                           "status", "unavailable")},
            "external_boundaries": {"source": "integration_candidates + graph",
                                    "status": model.get("coverage", {}).get(
                                        "external_boundaries", {}).get(
                                        "status", "unavailable")},
            "background_execution": {
                "source": "targeted bounded source reads when selected for a journey",
                "status": "unavailable",
                "reason": "no deterministic background-job producer in this version",
            },
            "ui_entry_labels": {
                "source": "targeted bounded source reads for selected journeys",
                "status": "unavailable",
                "reason": "no deterministic UI-entry producer in this version",
            },
            "quality": {"source": "signal_views", "status": "complete"},
        },
        "limits": {
            "per_graph_node_kind": _LIST_LIMIT,
            "highest_degree_nodes": _HUB_LIMIT,
            "per_signal_view_lines": _VIEW_LINE_LIMIT,
            "signals": _SIGNAL_LIMIT,
            "module_and_integration_candidates_in_packet": _CANDIDATE_LIMIT,
            "maximum_characters_per_view_line": _TEXT_LINE_LIMIT,
            "surfaced_candidate_universe_truncated_in_authoritative_artifact": False,
            "interpretation_added": False,
        },
    }


def write(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / "synthesis-input.json"
    replace_artifact_text(
        out, sanitize_text(json.dumps(build(run), indent=2, sort_keys=True) + "\n"))
    return out
