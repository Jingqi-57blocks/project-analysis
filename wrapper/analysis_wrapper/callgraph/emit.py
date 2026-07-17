"""Call-graph emit — select a lane per repo, write edges + coverage.

For each repo (sorted for determinism) this picks the Go and/or JS/TS lane by
stack, writes one JSON Lines file of sorted call edges to
``<out>/callgraph/<repo_id>.jsonl``, and collects a per-repo/lane coverage
entry into ``<out>/callgraph-coverage.json``. A repo can drive both lanes
(a fullstack repo); its edges are merged and sorted together.

Output is bounded and deterministic: raw multi-megabyte tool output is parsed
and dropped (never written into the run tree), edges are sorted and
de-duplicated, and everything is passed through the shared sanitizer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from ..executor import create_stage_dir, write_new_text
from ..sanitize import sanitize_text
from ..status import Status, aggregate
from ..targetspec import RepoTarget, TargetSpec
from . import go_lane, js_lane
from .contract import CallEdge, CoverageReport, RepoCoverage

_STATUS_MAP = {
    "complete": Status.COMPLETE,
    "partial": Status.PARTIAL,
    "failed": Status.FAILED,
    "unavailable": Status.SKIPPED,
}
_JS_STACKS = {"js", "ts", "tsx", "javascript", "typescript"}


def select_lanes(target: RepoTarget) -> list[str]:
    """The call-graph lanes applicable to a repo, by stack + manifest presence."""
    stacks = {s.lower() for s in target.stacks}
    root = Path(target.path)
    lanes: list[str] = []
    if "go" in stacks or (root / "go.mod").is_file():
        lanes.append("go")
    if stacks & _JS_STACKS or (root / "package.json").is_file():
        lanes.append("js")
    return lanes


def _write_jsonl(path: Path, edges: list[CallEdge]) -> None:
    ordered = sorted(set(edges), key=lambda e: e.sort_key())
    body = "".join(edge.to_json_line() + "\n" for edge in ordered)
    write_new_text(path, sanitize_text(body))


def run_callgraph(spec: TargetSpec, out_dir: str | Path, scan_date: str, *,
                  allow_network: bool = False,
                  run: Callable[..., subprocess.CompletedProcess] = subprocess.run
                  ) -> CoverageReport:
    """Run the call-graph stage over a TargetSpec, writing artifacts under
    ``out_dir`` and returning the coverage report."""
    out = Path(out_dir).expanduser().resolve()
    cg_dir = create_stage_dir(out / "callgraph")   # never write THROUGH a symlink
    coverages: list[RepoCoverage] = []
    for target in sorted(spec.repos, key=lambda r: r.repo_id):
        lanes = select_lanes(target)
        if not lanes:
            continue
        edges: list[CallEdge] = []
        for lane in lanes:
            if lane == "go":
                lane_edges, cov = go_lane.analyze(target, allow_network=allow_network, run=run)
            else:
                lane_edges, cov = js_lane.analyze(target, run=run)
            edges.extend(lane_edges)
            coverages.append(cov)
        _write_jsonl(cg_dir / f"{target.repo_id}.jsonl", edges)
    report = CoverageReport(scan_date=scan_date, repos=coverages)
    write_new_text(out / "callgraph-coverage.json", sanitize_text(report.to_json()))
    return report


def aggregate_status(report: CoverageReport) -> Status:
    """Worst per-lane status, for the CLI exit code (empty -> nothing ran)."""
    return aggregate([_STATUS_MAP[c.status] for c in report.repos])
