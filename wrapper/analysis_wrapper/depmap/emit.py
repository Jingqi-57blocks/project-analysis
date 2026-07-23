"""Dependency-map emit — bundled providers + coverage assembler (57B-81 PR2).

Production path: :mod:`analysis_wrapper.profiles.providers` wraps the Go and
JS/TS lanes as bundled ``CapabilityProvider``s (``depmap-go`` / ``depmap-js``)
selected by FACET, not by stack strings or manifest sniffing. Each provider
writes the FINAL per-repo/lane map file directly under ``<out>/imports/``
(``<artifact-key>.golist.json`` / ``<artifact-key>.depcruise.json`` — a
repo's Go and JS/TS maps never collide, so there is nothing to merge) plus a
coverage-only fragment under ``<out>/imports/.fragments/``. :func:`assemble`
rolls every fragment's coverage row into ``<out>/imports/depmap-coverage.json``.
This module never branches on a language name itself: "go"/"js" only ever
appear here as DATA read back out of a fragment.

:func:`run_depmap` is a legacy, single-process entry point kept ONLY for the
symlink-containment regression (``tests/test_depmap_containment.py`` calls it
directly and must keep passing unmodified); production runs (the CLI's
``callgraph``/``dependency-map`` subcommands and ``prepare-overview``) go
through the provider loop + :func:`assemble` instead.
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
_JS_PROFILES = frozenset({"language.javascript", "language.typescript"})


def _lanes(target: RepoTarget) -> list[str]:
    """The dependency-map lanes a repo's DETECTED facets make applicable —
    the legacy-compatible, facet-only successor to the old stack/manifest-based
    lane selector, used only by :func:`run_depmap` below."""
    profiles = set(target.profiles_for_capability("dependency-map"))
    lanes: list[str] = []
    if "language.go" in profiles:
        lanes.append("go")
    if profiles & _JS_PROFILES:
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
    """Legacy single-process dependency-map stage (see module docstring) —
    run a repo's applicable lanes directly and write each lane's map +
    coverage in one pass, with no fragment intermediary."""
    out = Path(out_dir).expanduser().resolve()
    identities = identities or identity.load(out)
    imports_dir = create_stage_dir(out / "imports")   # never write THROUGH a symlink
    config_root = out / ".depmap-config"
    coverages: list[RepoDepCoverage] = []
    for target in sorted(spec.repos, key=lambda r: r.repo_id):
        repo_identity = identities.repository(target.repo_id)
        for lane in _lanes(target):
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


# ---------------------------------------------------------------------------
# Coverage assembler — the production path. Map files are already final when
# a provider writes them; only the per-repo/lane coverage doc is merged here
# from the fragments providers wrote. Technology-neutral: "go"/"js" only ever
# appear as DATA inside a fragment, never as a branching literal here.
# ---------------------------------------------------------------------------

def _repo_dep_coverage_from_row(row: dict) -> RepoDepCoverage:
    """Reconstruct a :class:`RepoDepCoverage` from a fragment's
    ``coverage_row`` — contract.py has no ``from_dict`` for this type, so the
    round trip is done here rather than widening the contract."""
    return RepoDepCoverage(
        repository_ref=row["repository_ref"], lane=row["lane"], status=row["status"],
        tool=row.get("tool", ""), tool_version=row.get("tool_version", ""),
        reason=row.get("reason", ""), map_file=row.get("map_file", ""),
        units=row.get("units", 0), warm_cache=row.get("warm_cache", "n/a"),
        notes=row.get("notes", ""),
        reference_counts=dict(row.get("reference_counts") or {}),
    )


def assemble(out_dir: str | Path, scan_date: str) -> DepMapReport:
    """Merge every ``imports/.fragments/*.json`` coverage fragment a provider
    wrote into the run's final ``imports/depmap-coverage.json`` — the map
    files themselves are already final when a provider writes them, so only
    coverage is assembled here. Zero fragments still produce the empty,
    legacy-shaped coverage doc.
    """
    out = Path(out_dir).expanduser().resolve()
    imports_dir = create_stage_dir(out / "imports")
    fragments_dir = imports_dir / ".fragments"
    fragment_paths = sorted(fragments_dir.glob("*.json")) if fragments_dir.is_dir() else []
    coverages = [
        _repo_dep_coverage_from_row(json.loads(path.read_text("utf-8"))["coverage_row"])
        for path in fragment_paths
    ]
    report = DepMapReport(scan_date=scan_date, repos=coverages)
    write_new_text(imports_dir / "depmap-coverage.json",
                   sanitize_text(report.to_json()))
    return report


def aggregate_status(report: DepMapReport) -> Status:
    """Worst per-lane status, for the CLI exit code (empty -> nothing ran)."""
    return aggregate([_STATUS_MAP[c.status] for c in report.repos])
