"""Canonical workspace-wide counts and ratios for overview synthesis.

The model may explain these values but never recomputes them.  Metrics are
derived only from canonical, non-overlapping targets and deterministic machine
artifacts, with stable refs that reports can cite verbatim.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

from .executor import replace_artifact_text
from .sanitize import sanitize_text

SCHEMA_VERSION = "1.0.0"
FILENAME = "workspace-metrics.json"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _percent(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _metric(ref: str, name: str, *, repo_ids: list[str], unit: str,
            value: int | float | None, source_refs: list[str],
            numerator: int | None = None, denominator: int | None = None,
            scope_details: dict | None = None,
            denominator_metric_ref: str = "") -> dict:
    row = {
        "metric_ref": ref,
        "name": name,
        "scope": {"repo_ids": sorted(repo_ids), **dict(scope_details or {})},
        "unit": unit,
        "value": value,
        "source_refs": sorted(set(source_refs)),
    }
    if numerator is not None:
        row["numerator"] = numerator
    if denominator is not None:
        row["denominator"] = denominator
    if denominator_metric_ref:
        row["denominator_metric_ref"] = denominator_metric_ref
    return row


def _scc_metrics(run: Path, summary: dict, target_ids: list[str]) -> tuple[list[dict], dict]:
    per_repo: dict[str, int] = {}
    refs: dict[str, list[str]] = defaultdict(list)
    scopes: dict[str, dict] = {}
    for signal in summary.get("signals", []):
        if signal.get("tool") != "scc" or signal.get("status") != "complete":
            continue
        repo_id = str(signal.get("repo_id", ""))
        rel = str(signal.get("manifest", ""))
        path = run / "signals" / rel
        if not repo_id or not rel or not path.is_file():
            continue
        manifest = _load(path)
        structured = manifest.get("structured_metrics", {})
        if structured.get("kind") != "scc":
            continue
        code = int(structured.get("totals", {}).get("code", 0))
        refs[repo_id].append(f"signals/{rel}#structured_metrics")
        scopes[repo_id] = {
            "scope_ref": f"signals/{rel}#scope",
            "analysis_roots_and_exclusions": "see scope_ref",
        }
        per_repo[repo_id] = code

    included = sorted(per_repo)
    total = sum(per_repo.values())
    metrics = [_metric(
        "code.analyzed-scope.total", "code lines in analyzed SCC scope",
        repo_ids=included, unit="code_lines", value=total,
        source_refs=[ref for repo_id in included for ref in refs[repo_id]])]
    for repo_id in included:
        metrics.extend([
            _metric(f"code.repo.{repo_id}.total", "code lines in repository SCC scope",
                    repo_ids=[repo_id], unit="code_lines", value=per_repo[repo_id],
                    source_refs=refs[repo_id], scope_details=scopes[repo_id]),
            _metric(f"code.repo.{repo_id}.share", "repository share of analyzed code",
                    repo_ids=included, unit="percent",
                    value=_percent(per_repo[repo_id], total),
                    numerator=per_repo[repo_id], denominator=total,
                    source_refs=refs[repo_id], scope_details=scopes[repo_id],
                    denominator_metric_ref="code.analyzed-scope.total"),
        ])
    coverage = {
        "status": "complete" if set(included) == set(target_ids) else "partial",
        "included_repo_ids": included,
        "missing_repo_ids": sorted(set(target_ids) - set(included)),
    }
    return metrics, coverage


def _dependency_metrics(model: dict, depmap: dict) -> tuple[list[dict], dict]:
    node_repos = {str(node.get("id", "")): str(node.get("repo_id", ""))
                  for node in model.get("nodes", [])}
    producer_lanes = {"dependency-cruiser": "js", "go-list": "go"}
    counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for edge in model.get("edges", []):
        if edge.get("type") != "dependency":
            continue
        lane = producer_lanes.get(str(edge.get("producer", "")))
        if not lane:
            continue
        repo_id = node_repos.get(str(edge.get("src", "")), "")
        if not repo_id:
            continue
        bucket = "unresolved" if edge.get("status") == "unresolved" else "resolved"
        counts[(lane, repo_id)][bucket] += 1

    coverage_row = model.get("coverage", {}).get("dependency_imports", {})
    coverage_repos = list(depmap.get("repos", []))
    lane_repos = sorted({(str(row.get("lane", "")), str(row.get("repo_id", "")))
                         for row in coverage_repos
                         if row.get("lane") and row.get("repo_id")
                         and row.get("status") == "complete" and row.get("map_file")})
    metrics: list[dict] = []
    for lane, repo_id in lane_repos:
        resolved = counts[(lane, repo_id)]["resolved"]
        unresolved = counts[(lane, repo_id)]["unresolved"]
        total = resolved + unresolved
        refs = [f"system-model.json#dependency-edges:{lane}:{repo_id}"]
        prefix = f"dependency-graph.lane.{lane}.repo.{repo_id}"
        metrics.extend([
            _metric(f"{prefix}.total", f"{lane} classified dependency references",
                    repo_ids=[repo_id],
                    unit="edges", value=total, source_refs=refs),
            _metric(f"{prefix}.internal", f"{lane} internal dependency edges",
                    repo_ids=[repo_id], unit="edges", value=resolved, source_refs=refs),
            _metric(f"{prefix}.external-or-unresolved",
                    f"{lane} external or unresolvable dependency references",
                    repo_ids=[repo_id], unit="edges", value=unresolved, source_refs=refs),
            _metric(f"{prefix}.internal-percent", f"{lane} internal dependency edge share",
                    repo_ids=[repo_id], unit="percent", value=_percent(resolved, total),
                    numerator=resolved, denominator=total, source_refs=refs),
            _metric(f"{prefix}.external-or-unresolved-percent",
                    f"{lane} external or unresolvable dependency reference share",
                    repo_ids=[repo_id], unit="percent", value=_percent(unresolved, total),
                    numerator=unresolved, denominator=total, source_refs=refs),
        ])
    lane_coverage = []
    for lane in sorted({lane for lane, _ in lane_repos}):
        repos = sorted(repo_id for row_lane, repo_id in lane_repos if row_lane == lane)
        resolved = sum(counts[(lane, repo_id)]["resolved"] for repo_id in repos)
        unresolved = sum(counts[(lane, repo_id)]["unresolved"] for repo_id in repos)
        total = resolved + unresolved
        refs = [f"system-model.json#dependency-edges:{lane}"]
        prefix = f"dependency-graph.lane.{lane}.analyzed-scope"
        metrics.extend([
            _metric(f"{prefix}.total", f"{lane} classified dependency references",
                    repo_ids=repos, unit="edges", value=total, source_refs=refs),
            _metric(f"{prefix}.internal", f"{lane} internal dependency edges",
                    repo_ids=repos, unit="edges", value=resolved, source_refs=refs),
            _metric(f"{prefix}.external-or-unresolved",
                    f"{lane} external or unresolvable dependency references",
                    repo_ids=repos, unit="edges", value=unresolved, source_refs=refs),
            _metric(f"{prefix}.internal-percent", f"{lane} internal dependency edge share",
                    repo_ids=repos, unit="percent", value=_percent(resolved, total),
                    numerator=resolved, denominator=total, source_refs=refs),
            _metric(f"{prefix}.external-or-unresolved-percent",
                    f"{lane} external or unresolvable dependency reference share",
                    repo_ids=repos, unit="percent", value=_percent(unresolved, total),
                    numerator=unresolved, denominator=total, source_refs=refs),
        ])
        lane_coverage.append({
            "lane": lane,
            "included_repo_ids": repos,
            "incomplete_repo_ids": sorted({
                str(row.get("repo_id", "")) for row in coverage_repos
                if row.get("lane") == lane and row.get("repo_id")
                and row.get("status") != "complete"}),
        })
    completed_lanes = {row["lane"] for row in lane_coverage}
    for lane in sorted({str(row.get("lane", "")) for row in coverage_repos
                        if row.get("lane")} - completed_lanes):
        lane_coverage.append({
            "lane": lane,
            "included_repo_ids": [],
            "incomplete_repo_ids": sorted({
                str(row.get("repo_id", "")) for row in coverage_repos
                if row.get("lane") == lane and row.get("repo_id")}),
        })
    lane_coverage.sort(key=lambda row: row["lane"])
    for row in sorted(coverage_repos, key=lambda item: (
            str(item.get("repo_id", "")), str(item.get("lane", "")))):
        repo_id = str(row.get("repo_id", ""))
        lane = str(row.get("lane", ""))
        for key, value in sorted((row.get("reference_counts") or {}).items()):
            metrics.append(_metric(
                f"dependency-lane.repo.{repo_id}.{lane}.{key.replace('_', '-')}",
                f"{lane} dependency lane {key.replace('_', ' ')}",
                repo_ids=[repo_id], unit="references", value=int(value),
                source_refs=[f"imports/depmap-coverage.json#repo:{repo_id}:{lane}"]))
    return metrics, {
        "status": str(coverage_row.get("status", "unavailable")),
        "lanes": lane_coverage,
        "stdlib_imports_omitted": coverage_row.get("counts", {}).get(
            "stdlib_imports_omitted", 0),
    }


def _load_lens_map(skill_root: Path) -> dict:
    path = skill_root / "lenses" / "coverage-map.json"
    return _load(path) if path.is_file() else {"lenses": []}


def _signal_counts(run: Path, summary: dict,
                   lens_map: dict) -> tuple[list[dict], list[dict]]:
    by_tool: dict[str, list[dict]] = defaultdict(list)
    for signal in summary.get("signals", []):
        by_tool[str(signal.get("tool", ""))].append(signal)
    tools = []
    for tool, rows in sorted(by_tool.items()):
        statuses = Counter(str(row.get("status", "failed")) for row in rows)
        repo_ids = set()
        for row in rows:
            manifest_path = run / "signals" / str(row.get("manifest", ""))
            if manifest_path.is_file():
                manifest = _load(manifest_path)
                repo_ids.update(str(repo.get("repo_id", ""))
                                for repo in manifest.get("repos", [])
                                if repo.get("repo_id"))
            elif row.get("repo_id"):
                repo_ids.add(str(row["repo_id"]))
        tools.append({
            "tool": tool,
            "invocation_count": len(rows),
            "target_count": len(repo_ids),
            "repo_ids": sorted(repo_ids),
            "status_counts": dict(sorted(statuses.items())),
        })
    lenses = []
    tool_rows = {row["tool"]: row for row in tools}
    for lens in sorted(lens_map.get("lenses", []), key=lambda row: row.get("lens_id", "")):
        mapped_tools = sorted(set(str(tool) for tool in lens.get("tools", [])))
        rows = [row for tool in mapped_tools for row in by_tool.get(tool, [])]
        statuses = Counter(str(row.get("status", "failed")) for row in rows)
        repo_ids = sorted({repo_id for tool in mapped_tools
                           for repo_id in tool_rows.get(tool, {}).get("repo_ids", [])})
        lenses.append({
            "lens_id": str(lens.get("lens_id", "")),
            "tools": mapped_tools,
            "invocation_count": len(rows),
            "target_count": len(repo_ids),
            "repo_ids": repo_ids,
            "status_counts": dict(sorted(statuses.items())),
            "note": "counts observed signals only; capability coverage remains authoritative",
        })
    return tools, lenses


def build(run_dir: str | Path, *, skill_root: str | Path | None = None) -> dict:
    run = Path(run_dir).expanduser().resolve()
    root = (Path(skill_root).expanduser().resolve() if skill_root else
            Path(__file__).resolve().parents[2])
    targets = _load(run / "targets.json")
    summary = _load(run / "signals" / "run-summary.json")
    model = _load(run / "system-model.json")
    depmap = _load(run / "imports" / "depmap-coverage.json")
    target_ids = sorted(str(row.get("repo_id", "")) for row in targets.get("repos", []))
    code_metrics, code_coverage = _scc_metrics(run, summary, target_ids)
    dependency_metrics, dependency_coverage = _dependency_metrics(model, depmap)
    tool_counts, lens_counts = _signal_counts(run, summary, _load_lens_map(root))
    metrics = sorted(code_metrics + dependency_metrics,
                     key=lambda row: row["metric_ref"])
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {"repo_ids": target_ids, "target_count": len(target_ids)},
        "coverage": {
            "code": code_coverage,
            "dependency": dependency_coverage,
        },
        "metrics": metrics,
        "tool_signal_counts": tool_counts,
        "lens_signal_counts": lens_counts,
        "rules": {
            "percent_rounding": "one decimal, half-even Python round",
            "dependency_graph_total": ("internal + external-or-unresolvable static "
                                       "dependency references; Go stdlib omitted"),
            "dependency_lane_counts": "lane-specific counts are never blended across JS and Go",
            "report_usage": "quote metric_ref; do not recompute or change scope",
        },
    }


def write(run_dir: str | Path, *, skill_root: str | Path | None = None) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / FILENAME
    replace_artifact_text(
        out, sanitize_text(json.dumps(build(run, skill_root=skill_root),
                                      indent=2, sort_keys=True) + "\n"))
    return out
