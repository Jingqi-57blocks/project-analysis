"""Dependency-map emit — select a lane per repo, write import maps + coverage.

For each repo (sorted for determinism) this picks the Go and/or JS/TS lane by
stack, produces the dependency map, and writes it into ``<out>/imports/``:
``<artifact-key>.golist.json`` (Go) / ``<artifact-key>.depcruise.json`` (JS/TS). Per-repo
lane outcomes are collected into ``<out>/imports/depmap-coverage.json``.

Output is bounded, leak-free, and deterministic: the Go lane writes a projected
map (never the raw ``go list`` stream with its absolute paths), the JS lane sorts
the module graph, and everything is passed through the shared sanitizer. The
system-model assembler consumes ``imports/`` into ``dependency`` edges.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from ..executor import create_stage_dir, write_new_text
from .. import identity
from ..sanitize import sanitize_text
from ..status import Status, aggregate
from ..targetspec import RepoTarget, TargetSpec
from . import go_lane, js_lane
from .contract import DepMapReport, RepoDepCoverage

_STATUS_MAP = {
    "complete": Status.COMPLETE,
    "partial": Status.PARTIAL,
    "failed": Status.FAILED,
    "unavailable": Status.SKIPPED,
}
_JS_STACKS = {"js", "ts", "tsx", "javascript", "typescript"}


def select_lanes(target: RepoTarget) -> list[str]:
    """The dependency-map lanes applicable to a repo, by stack + manifest
    presence. This is the SINGLE source of truth for which repos are expected to
    produce a map (coverage reads it to decide complete vs partial)."""
    stacks = {s.lower() for s in target.stacks}
    root = Path(target.path)
    lanes: list[str] = []
    if "go" in stacks or (root / "go.mod").is_file():
        lanes.append("go")
    if stacks & _JS_STACKS or (root / "package.json").is_file():
        lanes.append("js")
    return lanes


def _write_map(path: Path, payload: dict) -> None:
    write_new_text(
        path, sanitize_text(json.dumps(payload, indent=2, sort_keys=True) + "\n"))


def run_depmap(spec: TargetSpec, out_dir: str | Path, scan_date: str, *,
               allow_network: bool = False,
               identities: identity.IdentityMap | None = None,
               run: Callable[..., subprocess.CompletedProcess] = subprocess.run
               ) -> DepMapReport:
    """Run the dependency-map stage over a TargetSpec, writing per-repo maps under
    ``<out>/imports/`` and returning the coverage report."""
    out = Path(out_dir).expanduser().resolve()
    identities = identities or identity.load(out)
    imports_dir = create_stage_dir(out / "imports")   # never write THROUGH a symlink
    config_root = out / ".depmap-config"
    coverages: list[RepoDepCoverage] = []
    for target in sorted(spec.repos, key=lambda r: r.repo_id):
        repo_identity = identities.repository(target.repo_id)
        for lane in select_lanes(target):
            if lane == "go":
                payload, cov = go_lane.analyze(
                    target, repository_ref=repo_identity.reference,
                    artifact_key=repo_identity.artifact_key,
                    allow_network=allow_network, run=run)
                suffix = "golist"
            else:
                create_stage_dir(config_root)
                config_dir = create_stage_dir(
                    config_root / repo_identity.artifact_key)
                payload, cov = js_lane.analyze(
                    target, config_dir,
                    repository_ref=repo_identity.reference,
                    artifact_key=repo_identity.artifact_key, run=run)
                suffix = "depcruise"
            if payload is not None:
                _write_map(
                    imports_dir / f"{repo_identity.artifact_key}.{suffix}.json",
                    payload)
            coverages.append(cov)
    report = DepMapReport(scan_date=scan_date, repos=coverages)
    write_new_text(imports_dir / "depmap-coverage.json",
                   sanitize_text(report.to_json()))
    return report


def aggregate_status(report: DepMapReport) -> Status:
    """Worst per-lane status, for the CLI exit code (empty -> nothing ran)."""
    return aggregate([_STATUS_MAP[c.status] for c in report.repos])
